"""所有主题的注册表。新增主题只需在这里添加一条 TopicConfig。

采集器用注册表字符串名引用（base._collector_registry），禁用函数指针。
只依赖 topics.config，不 import 任何 collectors。"""

from datetime import date as _date

from collectors.bilibili import FUNNY_CATEGORIES
from topics.config import CollectorDef, TopicConfig
from utils.trending import fetch_douyin_trending

# ── 关键词轮换 ────────────────────────────────────────────────
# 每 GROUP_SIZE 个词为一组，按今日日期选当天用的组，4天轮一圈。
# 好处：每天搜不同词，覆盖面扩大，不靠人工猜哪个词好。
_GROUP_SIZE = 4


def _daily_rotate(pool: list[str], size: int = _GROUP_SIZE) -> list[str]:
    """按今日日期从 pool 里取一组关键词，每 len(pool)//size 天轮一圈。"""
    groups = max(len(pool) // size, 1)
    offset = (_date.today().toordinal() % groups) * size
    return pool[offset: offset + size]


# 抖音搞笑关键词池（16词，每天取4个）
_DOUYIN_FUNNY_KW_POOL = [
    "搞笑配音", "沙雕动画", "整活", "整蛊",       # 组0：原有词
    "脱口秀", "爆笑", "憋笑", "笑cry",             # 组1：反应类
    "动物搞笑", "萌宠日常", "猫咪", "搞笑合集",    # 组2：萌宠类
    "职场搞笑", "情侣搞笑", "生活幽默", "绷不住",  # 组3：生活类
]

# 小红书搞笑关键词池（16词，每天取4个）
# 根据历史高分数据：动物/萌宠>爆笑>职场幽默>脱口秀>整活
_XHS_FUNNY_KW_POOL = [
    "整活", "沙雕", "搞笑合集", "搞笑日常",        # 组0：原有词
    "萌宠搞笑", "猫咪搞笑", "动物搞笑", "爆笑",    # 组1：动物/萌宠（历史高赞）
    "憋笑", "脱口秀", "职场搞笑", "情侣搞笑",      # 组2：细分搞笑
    "幽默", "有梗", "绷不住了", "搞笑视频",         # 组3：通用词
]

_BILIBILI_FUNNY_KW = ["整活", "搞笑视频", "沙雕", "搞笑合集", "搞笑日常"]

_AI_KW_BILIBILI = ["DeepSeek", "Kimi", "通义千问", "ChatGPT", "Claude", "Gemini", "AI Agent",
                   "AI工具", "AI视频", "AI绘画", "大模型"]
# 抖音关键词：只用高赞产品名，避免宽泛词和字节系产品
#   ChatGPT/Gemini/豆包 → 引流标签乱挂 → 删；DeepSeek/通义千问 → 抖音0高赞 → 删
_AI_KW_DOUYIN = ["Claude", "AI Agent", "可灵AI"]
_XHS_AI_KW = ["Claude", "即梦AI", "AI绘画", "DeepSeek", "可灵AI", "ChatGPT"]


def _douyin_funny_keywords() -> list[str]:
    """当日抖音搜索词：热搜榜前5词 + 2个轮换搞笑词，共7词。

    热搜词代表今天最热的话题，搞笑博主经常围绕热点出梗；
    轮换词兜底，确保搜到的内容里有明确的搞笑内容。
    失败时降级为纯轮换词。
    """
    trending = fetch_douyin_trending(top_n=5)
    funny_rotation = _daily_rotate(_DOUYIN_FUNNY_KW_POOL, size=2)
    return trending + funny_rotation if trending else _daily_rotate(_DOUYIN_FUNNY_KW_POOL)


def _build_topics() -> dict[str, TopicConfig]:
    return {
        "funny": TopicConfig(
            topic="funny",
            display_name="🎬 搞笑视频墙",
            collectors=[
                CollectorDef("bilibili_popular",
                             {"pages": 5, "categories": FUNNY_CATEGORIES},
                             platform="bilibili"),
                CollectorDef("bilibili_search",
                             {"keywords": _BILIBILI_FUNNY_KW,
                              "content_hash_prefix": "bilibili_funny"},
                             platform="bilibili"),
                CollectorDef("douyin_search",
                             {"keywords": _douyin_funny_keywords()},
                             skip_flag="douyin"),
                CollectorDef("xiaohongshu_search",
                             {"keywords": _daily_rotate(_XHS_FUNNY_KW_POOL)},
                             skip_flag="xhs"),
            ],
            score_type="funny_score",
            min_score=7,
            min_like_count=5000,
        ),
        "ai": TopicConfig(
            topic="ai",
            display_name="🤖 AI 视频墙",
            collectors=[
                CollectorDef("bilibili_search",
                             {"keywords": _AI_KW_BILIBILI,
                              "content_hash_prefix": "bilibili_ai"},
                             platform="bilibili"),
                CollectorDef("douyin_search",
                             {"keywords": _AI_KW_DOUYIN},
                             skip_flag="douyin"),
                CollectorDef("xiaohongshu_search",
                             {"keywords": _XHS_AI_KW},
                             skip_flag="xhs"),
            ],
            score_type="funny_score",
            min_score=5,
            min_like_count=1000,
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
