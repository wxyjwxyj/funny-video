"""AI 打标签流水线：给 funny_score=NULL 的视频调 Claude 评分并回写。

使用 claude_call_tool（tool_use 协议），不用 "Output JSON only" 文本解析。
prompt 全英文（避免代理拦截），输出 tags 允许中文。
"""
import json

from storage import repository
from utils.claude import claude_call_tool
from utils.log import get_logger

logger = get_logger(__name__)

_TOOL_NAME = "tag_video"
_TOOL_DESC = (
    "Analyze a short video's metadata and return content tags "
    "plus a humor score indicating how funny/entertaining it likely is."
)
_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "tags": {
            "type": "array",
            "items": {"type": "string"},
            "description": "3-5 content tags in Chinese, e.g. ['搞笑', '鬼畜', '日常']",
        },
        "funny_score": {
            "type": "integer",
            "minimum": 0,
            "maximum": 10,
            "description": (
                "Humor score 0-10. "
                "0=not funny at all, 5=moderately funny, 10=extremely hilarious. "
                "Score 0 if content is gore, bloody, horror, sexually suggestive, "
                "vulgar, or otherwise unsafe/NSFW. "
                "Base on title, category, and play/like ratio."
            ),
        },
        "reason": {
            "type": "string",
            "description": "One sentence explaining the score (Chinese ok). If score=0 due to unsafe content, explain why.",
        },
        "is_unsafe": {
            "type": "boolean",
            "description": (
                "True if content appears to contain gore, extreme violence/blood, "
                "horror, sexually suggestive, vulgar/lowbrow, or NSFW material."
            ),
        },
    },
    "required": ["tags", "funny_score", "reason", "is_unsafe"],
}


def _build_prompt(video: dict) -> str:
    like = video.get("like_count") or 0
    play = video.get("play_count") or 1
    return (
        f"Video title: {video['title']}\n"
        f"Category: {video.get('category', 'unknown')}\n"
        f"Author: {video.get('author', 'unknown')}\n"
        f"Duration: {video.get('duration', 0)}s\n"
        f"Play count: {play:,}  Like count: {like:,}  "
        f"Like ratio: {like/play:.2%}\n\n"
        "Rate how funny/entertaining this video likely is based on the metadata above. "
        "Focus on whether it belongs to humor, parody, meme, or entertainment genres.\n"
        "IMPORTANT: First check for unsafe content. If the title or category suggests "
        "gore, extreme violence, blood, horror, sexually suggestive material, vulgar jokes, "
        "or NSFW content, set is_unsafe=true AND funny_score=0. "
        "Safe comedy is about wit, absurdity, parody, and lighthearted entertainment — "
        "not shock value or crude content."
    )


def _build_ai_prompt(video: dict) -> str:
    """AI 视频专用 prompt：评分维度是内容质量和信息量，而非搞笑程度。"""
    like = video.get("like_count") or 0
    play = video.get("play_count") or 1
    return (
        f"Video title: {video['title']}\n"
        f"Category: {video.get('category', 'unknown')}\n"
        f"Author: {video.get('author', 'unknown')}\n"
        f"Duration: {video.get('duration', 0)}s\n"
        f"Play count: {play:,}  Like count: {like:,}  "
        f"Like ratio: {like/play:.2%}\n\n"
        "Rate the quality and relevance of this AI/tech video. "
        "Use the funny_score field as a quality score (0-10): "
        "0=irrelevant or low quality, 5=decent AI/tech content, 10=must-watch insight or tutorial. "
        "Focus on whether it provides genuine AI knowledge, news, tools, or analysis."
    )


_PROMPT_FNS = {
    "funny": _build_prompt,
    "ai": _build_ai_prompt,
}


def run(batch_size: int | None = None, workers: int = 5, topic: str = "funny", tag_prompt: str | None = None) -> int:
    """并发给未打标签的视频评分，返回处理条数。batch_size=None 表示全部处理。"""
    videos = repository.list_untagged(limit=batch_size, topic=topic)
    if not videos:
        logger.info("tagging: 无待处理视频 (topic=%s)", topic)
        return 0

    prompt_fn = _PROMPT_FNS.get(tag_prompt or topic, _build_prompt)

    def _tag_one(v: dict) -> bool:
        try:
            result = claude_call_tool(
                prompt_fn(v),
                tool_name=_TOOL_NAME,
                tool_description=_TOOL_DESC,
                input_schema=_INPUT_SCHEMA,
            )
            repository.update_tags(v["content_hash"], result.get("tags", []), result.get("funny_score", 0), result.get("is_unsafe", False))
            logger.info("tagging: %s → score=%d unsafe=%s tags=%s", v["title"][:30], result.get("funny_score", 0), result.get("is_unsafe", False), result.get("tags", []))
            return True
        except Exception as e:
            logger.warning("tagging: %s 失败: %s", v.get("content_hash"), e)
            return False

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(_tag_one, videos))

    success = sum(results)
    logger.info("tagging: 完成 %d/%d 条", success, len(videos))
    return success
