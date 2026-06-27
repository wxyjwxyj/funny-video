"""主入口：多平台全链路（采集 → 去重 → 打标签 → 生成视频墙）。

用法：
    python run.py [--pages N] [--tag-batch N] [--min-score N] [--skip-collect] [--skip-tag] [--skip-douyin]
"""
import argparse

from collectors.bilibili import fetch_popular
from pipeline import dedup, tagging
from publishers.generate_wall import generate
from storage.db import init_db
from utils.log import get_logger

logger = get_logger(__name__)

_DB = __import__("pathlib").Path(__file__).parent / "video.db"


def main() -> None:
    p = argparse.ArgumentParser(description="搞笑视频聚合墙全链路")
    p.add_argument("--pages", type=int, default=5, help="B站热门采集页数（默认5=100条）")
    p.add_argument("--tag-batch", type=int, default=30, help="单次打标签批量（默认30）")
    p.add_argument("--min-score", type=int, default=5, help="视频墙最低分（默认5）")
    p.add_argument("--skip-collect", action="store_true", help="跳过采集，只打标签+生成")
    p.add_argument("--skip-tag", action="store_true", help="跳过打标签，只采集+生成")
    p.add_argument("--skip-douyin", action="store_true", help="跳过抖音采集（CDP 不可用时）")
    p.add_argument("--skip-xhs", action="store_true", help="跳过小红书采集（CDP 不可用时）")
    args = p.parse_args()

    init_db(_DB)

    if not args.skip_collect:
        # B站（公开 API，始终可用）
        bilibili_videos = fetch_popular(pages=args.pages)
        dedup.run(bilibili_videos)

        # 抖音（CDP，可选）
        if not args.skip_douyin:
            try:
                from collectors.douyin import fetch_popular as fetch_douyin
                douyin_videos = fetch_douyin()
                if douyin_videos:
                    dedup.run(douyin_videos)
            except Exception as e:
                logger.warning("抖音采集失败（降级跳过）: %s", e)

        # 小红书（CDP，可选）
        if not args.skip_xhs:
            try:
                from collectors.xiaohongshu import fetch_popular as fetch_xhs
                xhs_videos = fetch_xhs()
                if xhs_videos:
                    dedup.run(xhs_videos)
            except Exception as e:
                logger.warning("小红书采集失败（降级跳过）: %s", e)

    if not args.skip_tag:
        tagging.run(batch_size=args.tag_batch)

    out = generate(min_score=args.min_score)
    logger.info("完成，视频墙已生成: %s", out)


if __name__ == "__main__":
    main()
