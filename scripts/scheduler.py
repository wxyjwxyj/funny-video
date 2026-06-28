"""定时调度：每隔指定小时数跑一次完整链路（所有 topic）。

用法：
    python scripts/scheduler.py              # 默认每 6 小时跑一次
    python scripts/scheduler.py --interval 24
    python scripts/scheduler.py --once       # 跑一次后退出（调试用）

scheduler 是薄层，核心逻辑复用 run_topic.run_pipeline()。
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from run_topic import run_pipeline
from storage.db import init_db
from topics.registry import list_topics
from utils.log import get_logger

logger = get_logger(__name__)
_DB = Path(__file__).parent.parent / "video.db"


def run_all(tag_batch: int = 50, min_score: int | None = None) -> None:
    """跑所有 topic 的完整链路。"""
    init_db(_DB)
    for topic_name in list_topics():
        try:
            run_pipeline(topic_name, tag_batch=tag_batch, min_score=min_score)
        except Exception:
            logger.exception("[%s] 链路异常，跳过继续", topic_name)
    logger.info("==== 所有 topic 完成 ====")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--interval", type=float, default=6, help="间隔小时数（默认 6）")
    p.add_argument("--tag-batch", type=int, default=50)
    p.add_argument("--min-score", type=int, default=None, help="覆盖 TopicConfig.min_score")
    p.add_argument("--once", action="store_true", help="只跑一次后退出")
    args = p.parse_args()

    run_all(tag_batch=args.tag_batch, min_score=args.min_score)

    if args.once:
        return

    interval_sec = args.interval * 3600
    while True:
        logger.info("下次运行在 %.1f 小时后", args.interval)
        time.sleep(interval_sec)
        run_all(tag_batch=args.tag_batch, min_score=args.min_score)


if __name__ == "__main__":
    main()
