"""所有主题的注册表。新增主题只需在这里添加一条 TopicConfig。

采集器用注册表字符串名引用（base._collector_registry），禁用函数指针。
只依赖 topics.config，不 import 任何 collectors。"""

from collectors.bilibili import FUNNY_CATEGORIES
from topics.config import CollectorDef, TopicConfig

# ── 关键词 ──────────────────────────────────────────────────
_DOUYIN_FUNNY_KW = ["搞笑配音", "沙雕动画", "整活", "整蛊", "脱口秀"]
_XHS_FUNNY_KW = ["整活", "搞笑合集", "搞笑日常", "沙雕"]

_AI_KW = ["DeepSeek", "Kimi", "通义千问", "ChatGPT", "Claude", "Gemini", "AI Agent",
          "AI工具", "AI视频", "AI绘画", "大模型"]
_XHS_AI_KW = ["可灵AI", "即梦AI", "AI绘画", "Midjourney", "Suno", "DeepSeek"]


def _build_topics() -> dict[str, TopicConfig]:
    return {
        "funny": TopicConfig(
            topic="funny",
            display_name="🎬 搞笑视频墙",
            collectors=[
                CollectorDef("bilibili_popular",
                             {"pages": 5, "categories": FUNNY_CATEGORIES},
                             platform="bilibili"),
                CollectorDef("douyin_search",
                             {"keywords": _DOUYIN_FUNNY_KW},
                             skip_flag="douyin"),
                CollectorDef("xiaohongshu_search",
                             {"keywords": _XHS_FUNNY_KW},
                             skip_flag="xhs"),
            ],
            score_type="funny_score",
            min_score=7,
        ),
        "ai": TopicConfig(
            topic="ai",
            display_name="🤖 AI 视频墙",
            collectors=[
                CollectorDef("bilibili_search",
                             {"keywords": _AI_KW,
                              "content_hash_prefix": "bilibili_ai"},
                             platform="bilibili"),
                CollectorDef("douyin_search",
                             {"keywords": _AI_KW},
                             skip_flag="douyin"),
                CollectorDef("xiaohongshu_search",
                             {"keywords": _XHS_AI_KW},
                             skip_flag="xhs"),
            ],
            score_type="funny_score",
            min_score=5,
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
