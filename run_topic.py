"""统一运行入口：python run_topic.py --topic funny|ai

新增主题三步: 1) topics/registry.py 加 TopicConfig
              2) 如有新采集器，collectors/ 注册子类
              3) 完 — 不用改这个文件。
"""
import argparse
from pathlib import Path
import time

from collectors.base import create_collector
import collectors.douyin  # noqa: F401 - 触发 @register_collector
import collectors.xiaohongshu  # noqa: F401 - 触发 @register_collector
from pipeline import dedup, tagging
from publishers.generate_wall import generate
from storage.db import init_db
from topics.registry import get_topic, list_topics
from utils.log import get_logger

logger = get_logger(__name__)
_DB = Path(__file__).parent / "video.db"


def run_pipeline(topic_name: str, *, tag_batch: int | None = None,
                 skip_collect: bool = False, skip_tag: bool = False,
                 skip_flags: set[str] | None = None,
                 min_score: int | None = None) -> dict:
    """跑一个 topic 的完整链路：采集 → 去重 → 打标签 → 生成视频墙。

    所有逻辑由 TopicConfig 驱动，topic 之间零特殊处理。
    返回本次运行统计字典，供 scheduler 组装通知用。
    """
    config = get_topic(topic_name)
    min_score = min_score if min_score is not None else config.min_score
    skip_flags = skip_flags or set()

    total_inserted = 0
    platform_stats: dict[str, int] = {}   # platform → 新增条数
    failed_collectors: list[str] = []

    # ── 采集 ──
    if not skip_collect:
        for cdef in config.collectors:
            if cdef.skip_flag and cdef.skip_flag in skip_flags:
                logger.info("跳过采集器 %s (--skip-%s)", cdef.name, cdef.skip_flag)
                continue

            last_err: Exception | None = None
            for attempt in range(2):           # 最多尝试2次（失败后等5秒重试）
                try:
                    coll = create_collector(cdef.name, topic=config.topic, **cdef.kwargs)
                    videos = coll.collect()
                    if videos:
                        counts = dedup.run(videos)
                        inserted = counts.get("inserted", 0)
                        total_inserted += inserted
                        platform = cdef.platform or cdef.name
                        platform_stats[platform] = platform_stats.get(platform, 0) + inserted
                        logger.info("[%s] %s 采集 %d 条，去重: %s",
                                    topic_name, cdef.name, len(videos), counts)
                    last_err = None
                    break                      # 成功，不重试
                except Exception as e:
                    last_err = e
                    if attempt == 0:
                        logger.warning("[%s] %s 第1次失败，5秒后重试: %s",
                                       topic_name, cdef.name, e)
                        time.sleep(5)

            if last_err is not None:
                if cdef.optional:
                    logger.warning("[%s] %s 采集失败（降级）: %s",
                                   topic_name, cdef.name, last_err)
                    failed_collectors.append(cdef.name)
                else:
                    raise last_err

    # ── 打标签 ──
    tagged = 0
    if not skip_tag:
        tagged = tagging.run(batch_size=tag_batch, topic=config.topic, tag_prompt=config.topic)
        logger.info("[%s] 打标签: %d 条", topic_name, tagged)

    # ── 生成 ──
    out = generate(topic=config.topic, min_score=min_score,
                   min_like_count=config.min_like_count, display_name=config.display_name)
    logger.info("[%s] 完成，视频墙: %s", topic_name, out)

    return {
        "topic": topic_name,
        "inserted": total_inserted,
        "tagged": tagged,
        "platforms": platform_stats,
        "failed": failed_collectors,
        "wall": str(out),
    }


def main() -> None:
    p = argparse.ArgumentParser(description="视频聚合墙通用流水线")
    p.add_argument("--topic", default="funny", help=f"主题，可选: {list_topics()}")
    p.add_argument("--tag-batch", type=int, default=None,
                   help="打标签条数上限，不填则处理全部未打标签的")
    p.add_argument("--min-score", type=int, default=None)
    p.add_argument("--skip-collect", action="store_true")
    p.add_argument("--skip-tag", action="store_true")
    p.add_argument("--skip-douyin", action="store_true")
    p.add_argument("--skip-xhs", action="store_true")
    args = p.parse_args()

    skip_flags = {f for f in ("douyin", "xhs") if getattr(args, f"skip_{f}", False)}
    init_db(_DB)
    run_pipeline(args.topic, tag_batch=args.tag_batch,
                 skip_collect=args.skip_collect, skip_tag=args.skip_tag,
                 skip_flags=skip_flags, min_score=args.min_score)


if __name__ == "__main__":
    main()
