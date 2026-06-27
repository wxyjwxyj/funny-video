"""定时调度：每隔指定小时数跑一次完整链路。

用法：
    python scripts/scheduler.py              # 默认每 24 小时跑一次
    python scripts/scheduler.py --interval 6 # 每 6 小时跑一次
    python scripts/scheduler.py --once       # 跑一次后退出（调试用）
"""
import argparse
import sys
import time
from pathlib import Path

# 确保项目根目录在 path 里
sys.path.insert(0, str(Path(__file__).parent.parent))

from collectors.bilibili import fetch_popular
from pipeline import dedup, tagging
from publishers.generate_wall import generate
from storage.db import init_db
from utils.log import get_logger

logger = get_logger(__name__)
_DB = Path(__file__).parent.parent / "video.db"


def run_pipeline(pages: int = 5, tag_batch: int = 30, min_score: int = 5) -> None:
    """跑一次完整链路：采集 → 去重 → 打标签 → 生成视频墙。"""
    logger.info("==== 开始调度任务 ====")
    init_db(_DB)

    videos = fetch_popular(pages=pages)
    counts = dedup.run(videos)
    logger.info("去重结果: %s", counts)

    tagged = tagging.run(batch_size=tag_batch)
    logger.info("本次打标签: %d 条", tagged)

    out = generate(min_score=min_score)
    logger.info("==== 调度任务完成，视频墙: %s ====", out)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--interval", type=float, default=24, help="间隔小时数（默认 24）")
    p.add_argument("--pages", type=int, default=5)
    p.add_argument("--tag-batch", type=int, default=30)
    p.add_argument("--min-score", type=int, default=5)
    p.add_argument("--once", action="store_true", help="只跑一次后退出")
    args = p.parse_args()

    run_pipeline(pages=args.pages, tag_batch=args.tag_batch, min_score=args.min_score)

    if args.once:
        return

    interval_sec = args.interval * 3600
    while True:
        logger.info("下次运行在 %.1f 小时后", args.interval)
        time.sleep(interval_sec)
        run_pipeline(pages=args.pages, tag_batch=args.tag_batch, min_score=args.min_score)


if __name__ == "__main__":
    main()
