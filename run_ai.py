"""AI视频采集主入口：B站AI关键词 → 去重 → 打标签 → 生成AI视频墙。

用法：
    python run_ai.py [--tag-batch N] [--min-score N] [--skip-collect] [--skip-tag]
"""
import argparse
from pathlib import Path

from collectors.bilibili_ai import fetch_ai_videos
from pipeline import dedup, tagging
from publishers.generate_wall import generate
from storage.db import init_db
from utils.log import get_logger

logger = get_logger(__name__)
_DB = Path(__file__).parent / "video.db"


def main() -> None:
    p = argparse.ArgumentParser(description="AI视频聚合墙全链路")
    p.add_argument("--tag-batch", type=int, default=50, help="单次打标签批量（默认50）")
    p.add_argument("--min-score", type=int, default=5, help="视频墙最低分（默认5）")
    p.add_argument("--skip-collect", action="store_true", help="跳过采集")
    p.add_argument("--skip-tag", action="store_true", help="跳过打标签")
    args = p.parse_args()

    init_db(_DB)

    if not args.skip_collect:
        videos = fetch_ai_videos()
        counts = dedup.run(videos)
        logger.info("去重结果: %s", counts)

    if not args.skip_tag:
        tagged = tagging.run(batch_size=args.tag_batch, topic="ai")
        logger.info("AI视频打标签: %d 条", tagged)

    out = generate(min_score=args.min_score, topic="ai")
    logger.info("完成，AI视频墙已生成: %s", out)


if __name__ == "__main__":
    main()
