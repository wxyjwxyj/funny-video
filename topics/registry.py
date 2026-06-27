"""所有主题的注册表。新增主题只需在这里添加一条 TopicConfig。"""
from topics.config import CollectorDef, TopicConfig

# ── AI 视频专用关键词 ────────────────────────────────
_DOUYIN_AI_KW = ["AI大模型", "DeepSeek", "可灵AI", "Claude AI", "人工智能"]
_XHS_AI_KW = ["AI大模型", "DeepSeek", "可灵AI", "即梦AI"]


def _build_topics() -> dict[str, TopicConfig]:
    # 延迟导入采集器，避免循环依赖和 import 时 CDP 初始化
    import collectors.bilibili as bili
    import collectors.bilibili_ai as bili_ai
    import collectors.douyin as dy
    import collectors.xiaohongshu as xhs

    return {
        "funny": TopicConfig(
            topic="funny",
            display_name="🎬 搞笑视频墙",
            collectors=[
                CollectorDef(bili.fetch_popular, {"pages": 5}),
                CollectorDef(dy.fetch_popular, {}, skip_flag="douyin"),
                CollectorDef(xhs.fetch_popular, {}, skip_flag="xhs"),
            ],
            tag_prompt="funny",
            platform_buttons=[
                ("bilibili", "B站"),
                ("douyin", "抖音"),
                ("wechat_video", "视频号"),
                ("xiaohongshu", "小红书"),
            ],
        ),
        "ai": TopicConfig(
            topic="ai",
            display_name="🤖 AI 视频墙",
            collectors=[
                CollectorDef(bili_ai.fetch_ai_videos, {}),
                CollectorDef(dy.fetch_popular, {"keywords": _DOUYIN_AI_KW, "topic": "ai"}, skip_flag="douyin"),
                CollectorDef(xhs.fetch_popular, {"keywords": _XHS_AI_KW, "topic": "ai"}, skip_flag="xhs"),
            ],
            tag_prompt="ai",
            platform_buttons=[
                ("bilibili", "B站"),
                ("douyin", "抖音"),
                ("xiaohongshu", "小红书"),
            ],
        ),
    }


_cache: dict[str, TopicConfig] | None = None


def get_topic(name: str) -> TopicConfig:
    global _cache
    if _cache is None:
        _cache = _build_topics()
    if name not in _cache:
        raise ValueError(f"未知 topic: {name!r}，可选: {list(_cache.keys())}")
    return _cache[name]


def list_topics() -> list[str]:
    global _cache
    if _cache is None:
        _cache = _build_topics()
    return list(_cache.keys())
