"""AI 打标签流水线：给 funny_score=NULL 的视频调 Claude 评分并回写。

使用批量调用（每批 BATCH_SIZE 条），显著减少 API 调用次数。
prompt 全英文（避免代理拦截），输出 tags 允许中文。
"""
import json
from concurrent.futures import ThreadPoolExecutor, as_completed

from storage import repository
from utils.claude import claude_call, extract_json
from utils.log import get_logger

logger = get_logger(__name__)

BATCH_SIZE = 10  # 每批视频数；太大 JSON 解析失败风险增加


def _build_prompt(videos: list[dict], topic: str) -> str:
    if topic == "ai":
        score_desc = (
            "quality/relevance score: "
            "0=clickbait or AI keyword is merely a hashtag, "
            "5=decent AI/tech content, 10=must-watch insight or tutorial"
        )
    else:
        score_desc = (
            "humor/entertainment score: "
            "0=not funny or unsafe/NSFW, 5=moderately funny, 10=extremely hilarious"
        )

    lines = []
    for i, v in enumerate(videos):
        like = v.get("like_count") or 0
        play = v.get("play_count") or 1
        lines.append(
            f"{i+1}. title: {v['title']}"
            f"  author: {v.get('author','')}"
            f"  duration: {v.get('duration',0)}s"
            f"  likes: {like:,}  plays: {play:,}  ratio: {like/play:.1%}"
        )

    return (
        f"Rate each video's {score_desc}.\n"
        f"Also assign 3-5 Chinese content tags per video.\n"
        f"Return ONLY valid JSON, no other text:\n"
        f'{{\"results\": [{{\"id\": 1, \"score\": 7, \"tags\": [\"标签A\"], \"is_unsafe\": false}}, ...]}}\n\n'
        f"Videos:\n" + "\n".join(lines)
    )


def _call_batch(videos: list[dict], topic: str) -> list[dict]:
    """调用 Claude 为一批视频评分，返回与 videos 等长的结果列表。"""
    raw = claude_call(_build_prompt(videos, topic), max_tokens=2048)
    data = extract_json(raw)
    results = data.get("results", [])

    output = [{"score": 5, "tags": [], "is_unsafe": False} for _ in videos]
    for r in results:
        idx = r.get("id", 0) - 1
        if 0 <= idx < len(videos):
            output[idx] = {
                "score": int(r.get("score", 5)),
                "tags": r.get("tags", [])[:5],
                "is_unsafe": bool(r.get("is_unsafe", False)),
            }
    return output


def run(batch_size: int | None = None, workers: int = 4, topic: str = "funny", tag_prompt: str | None = None) -> int:
    """批量给未打标签的视频评分，返回处理条数。batch_size=None 表示全部处理。"""
    videos = repository.list_untagged(limit=batch_size, topic=topic)
    if not videos:
        logger.info("tagging: 无待处理视频 (topic=%s)", topic)
        return 0

    effective_topic = tag_prompt or topic
    batches = [videos[i:i+BATCH_SIZE] for i in range(0, len(videos), BATCH_SIZE)]
    logger.info("tagging: %d 条 → %d 批 (batch_size=%d, workers=%d)", len(videos), len(batches), BATCH_SIZE, workers)

    success = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        fut_to_batch = {pool.submit(_call_batch, b, effective_topic): b for b in batches}
        for future in as_completed(fut_to_batch):
            batch = fut_to_batch[future]
            try:
                results = future.result()
                for v, r in zip(batch, results):
                    repository.update_tags(
                        v["content_hash"], r["tags"], r["score"], r["is_unsafe"]
                    )
                    logger.info("tagging: %s → score=%d tags=%s", v["title"][:35], r["score"], r["tags"])
                    success += 1
            except Exception as e:
                logger.warning("tagging: 批次失败 (%s)，跳过 %d 条", e, len(batch))

    logger.info("tagging: 完成 %d/%d 条", success, len(videos))
    return success
