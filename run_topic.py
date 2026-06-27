"""统一 topic 运行入口：python run_topic.py --topic funny|ai|...

新增主题只需在 topics/registry.py 里加一条 TopicConfig，无需改这个文件。
"""
import argparse
from pathlib import Path

from pipeline import dedup, tagging
from publishers.generate_wall import generate
from storage.db import init_db
from topics.registry import get_topic, list_topics
from utils.log import get_logger

logger = get_logger(__name__)
_DB = Path(__file__).parent / "video.db"


def main() -> None:
    p = argparse.ArgumentParser(description="视频聚合墙通用流水线")
    p.add_argument("--topic", default="funny", help=f"主题，可选: {list_topics()}")
    p.add_argument("--tag-batch", type=int, default=50)
    p.add_argument("--min-score", type=int, default=None, help="覆盖 TopicConfig.min_score")
    p.add_argument("--skip-collect", action="store_true")
    p.add_argument("--skip-tag", action="store_true")
    p.add_argument("--skip-douyin", action="store_true")
    p.add_argument("--skip-xhs", action="store_true")
    args = p.parse_args()

    config = get_topic(args.topic)
    min_score = args.min_score if args.min_score is not None else config.min_score
    # 哪些 skip_flag 被激活
    skipped = {flag for flag in ("douyin", "xhs") if getattr(args, f"skip_{flag.replace('-','_')}", False)}

    init_db(_DB)

    if not args.skip_collect:
        for cdef in config.collectors:
            if cdef.skip_flag and cdef.skip_flag in skipped:
                logger.info("跳过采集器 (--skip-%s)", cdef.skip_flag)
                continue
            try:
                videos = cdef.fn(**cdef.kwargs)
                # 确保 topic 字段正确
                for v in videos:
                    v.setdefault("topic", config.topic)
                if videos:
                    counts = dedup.run(videos)
                    logger.info("[%s] 采集 %d 条，去重: %s", cdef.fn.__module__, len(videos), counts)
            except Exception as e:
                if cdef.optional:
                    logger.warning("[%s] 采集失败（降级）: %s", cdef.fn.__module__, e)
                else:
                    raise

    if not args.skip_tag:
        tagged = tagging.run(batch_size=args.tag_batch, topic=config.topic, tag_prompt=config.tag_prompt)
        logger.info("打标签: %d 条", tagged)

    out = generate(
        min_score=min_score,
        topic=config.topic,
        platform_buttons=config.platform_buttons,
    )
    logger.info("完成，视频墙已生成: %s", out)


if __name__ == "__main__":
    main()
