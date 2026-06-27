"""AI视频采集主入口：B站+抖音+小红书 AI关键词 → 去重 → 打标签 → 生成AI视频墙。

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

_DOUYIN_AI_KEYWORDS = ["AI大模型", "DeepSeek", "可灵AI", "Claude AI", "人工智能"]
_XHS_AI_KEYWORDS = ["AI大模型", "DeepSeek", "可灵AI", "即梦AI"]


def main() -> None:
    p = argparse.ArgumentParser(description="AI视频聚合墙全链路")
    p.add_argument("--tag-batch", type=int, default=50)
    p.add_argument("--min-score", type=int, default=5)
    p.add_argument("--skip-collect", action="store_true")
    p.add_argument("--skip-tag", action="store_true")
    p.add_argument("--skip-douyin", action="store_true")
    p.add_argument("--skip-xhs", action="store_true")
    args = p.parse_args()

    init_db(_DB)

    if not args.skip_collect:
        # B站 AI 搜索
        videos = fetch_ai_videos()
        dedup.run(videos)

        # 抖音 AI 关键词
        if not args.skip_douyin:
            try:
                from collectors.douyin import fetch_popular as fetch_douyin
                douyin_videos = fetch_douyin(keywords=_DOUYIN_AI_KEYWORDS, topic="ai")
                if douyin_videos:
                    dedup.run(douyin_videos)
                    logger.info("抖音AI采集: %d 条", len(douyin_videos))
            except Exception as e:
                logger.warning("抖音AI采集失败（降级跳过）: %s", e)

        # 小红书 AI 关键词
        if not args.skip_xhs:
            try:
                from collectors.xiaohongshu import fetch_popular as fetch_xhs
                xhs_videos = fetch_xhs(keywords=_XHS_AI_KEYWORDS, topic="ai")
                if xhs_videos:
                    dedup.run(xhs_videos)
                    logger.info("小红书AI采集: %d 条", len(xhs_videos))
            except Exception as e:
                logger.warning("小红书AI采集失败（降级跳过）: %s", e)

    if not args.skip_tag:
        tagged = tagging.run(batch_size=args.tag_batch, topic="ai")
        logger.info("AI视频打标签: %d 条", tagged)

    out = generate(min_score=args.min_score, topic="ai")
    logger.info("完成，AI视频墙已生成: %s", out)


if __name__ == "__main__":
    main()
