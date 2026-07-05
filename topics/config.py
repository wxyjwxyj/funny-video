"""Topic 配置数据类。纯数据，无运行时导入。"""

from dataclasses import dataclass, field


@dataclass
class CollectorDef:
    """单个采集器描述。name 是 base.register 的字符串名，kwargs 传给构造函数。"""
    name: str                               # 采集器名（"bilibili_popular" / "douyin_search" ...）
    kwargs: dict = field(default_factory=dict)
    skip_flag: str = ""                     # CLI --skip-xxx 对应的 flag
    optional: bool = True                   # True=失败降级
    platform: str = ""                      # 平台名（bilibili/douyin/xiaohongshu，供前端用）


@dataclass
class TopicConfig:
    """一个内容主题的完整配置。采集/标签/生成全链路由这一个配置驱动。"""
    topic: str                              # DB topic 值
    display_name: str                       # 视频墙标题（含 emoji）
    collectors: list[CollectorDef]          # 采集器列表，按顺序执行
    score_type: str = "funny_score"         # 已废弃，由 tag_prompt=config.topic 替代。保留仅为向后兼容
    min_score: int = 7                      # 最低上墙分数
    min_like_count: int = 0                 # 最低点赞数（0=不限）；play_count 抖音/小红书不可用
    max_published_days: int | None = None   # 最长发布时效（天），None=不限；AI 内容设 30，搞笑内容可留 None
