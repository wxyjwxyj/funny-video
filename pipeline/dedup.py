"""去重流水线：把采集器输出批量落库，依赖 content_hash UNIQUE 去重。

去重逻辑本身在 DB 层（ON CONFLICT DO UPDATE），这里只负责调度和汇报结果。
"""
from storage import repository
from utils.log import get_logger

logger = get_logger(__name__)


def run(videos: list[dict]) -> dict[str, int]:
    """把 raw video list 落库，返回 {"inserted": N, "updated": M}。"""
    if not videos:
        logger.info("dedup: 无数据，跳过")
        return {"inserted": 0, "updated": 0}

    counts = repository.upsert_videos(videos)
    logger.info("dedup: 共 %d 条 → 新增 %d，更新 %d", len(videos), counts["inserted"], counts["updated"])
    return counts
