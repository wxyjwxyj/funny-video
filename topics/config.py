"""Topic 配置数据类。"""
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class CollectorDef:
    """单个采集器描述。"""
    fn: Callable
    kwargs: dict = field(default_factory=dict)
    skip_flag: str = ""     # CLI --skip-xxx 对应的 flag（如 "douyin"）
    optional: bool = True   # True=失败降级，False=必须成功


@dataclass
class TopicConfig:
    """一个内容主题的完整配置。"""
    topic: str                              # DB topic 值（funny/ai/...）
    display_name: str                       # 视频墙标题（含 emoji）
    collectors: list[CollectorDef]          # 采集器列表，按顺序执行
    tag_prompt: str                         # 传给 tagging.run 的 prompt key
    platform_buttons: list[tuple[str, str]] # [("bilibili","B站"), ...]
    min_score: int = 5
