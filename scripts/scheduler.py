"""定时调度：采集 → 去重 → 打标签 → 生成视频墙 → 自动 git commit + push。

用法：
    python scripts/scheduler.py              # 默认每 6 小时跑一次
    python scripts/scheduler.py --interval 24
    python scripts/scheduler.py --once       # 跑一次后退出（调试用）

scheduler 是薄层，核心逻辑复用 run_topic.run_pipeline()。
"""
import argparse
import subprocess
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
_ROOT = Path(__file__).parent.parent

# 每次生成的输出文件（由 run_pipeline 生成，由 scheduler 负责提交推送）
_EXPECTED_FILES = ["funny_wall.html", "ai_wall.html", "funny_archive/", "ai_archive/", "index.html"]


def _get_changed_files() -> list[str]:
    """返回 git 工作区中修改/新增的文件路径。"""
    files: set[str] = set()
    for args in (
        ["git", "diff", "--name-only"],
        ["git", "diff", "--cached", "--name-only"],
    ):
        r = subprocess.run(args, capture_output=True, text=True, cwd=_ROOT)
        files.update(f for f in r.stdout.strip().split("\n") if f)
    return list(files)


def _push_walls() -> None:
    """将新生成的 wall 文件 commit 并 push 到 GitHub Pages。"""
    changed = _get_changed_files()
    # 只关心 wall/archive 相关文件
    targets = [f for f in changed if any(
        f.startswith(p.replace("/", "")) or p.replace("/", "") in f
        for p in _EXPECTED_FILES
    )]
    if not targets:
        logger.info("无视频墙文件变更，跳过推送")
        return

    logger.info("推送文件: %s", targets)
    subprocess.run(["git", "add"] + targets, cwd=_ROOT, check=False)
    today = time.strftime("%Y-%m-%d")
    result = subprocess.run(
        ["git", "commit", "-m", f"content: {today} video walls --auto"],
        capture_output=True, text=True, cwd=_ROOT,
    )
    if "nothing to commit" in (result.stdout + result.stderr):
        logger.info("无实际变更，跳过 commit")
        return
    subprocess.run(["git", "push"], cwd=_ROOT, check=False)
    logger.info("GitHub Pages 推送完成")


def run_all(tag_batch: int = 50, min_score: int | None = None) -> None:
    """跑所有 topic 的完整链路，然后推送到 GitHub Pages。"""
    init_db(_DB)
    for topic_name in list_topics():
        try:
            run_pipeline(topic_name, tag_batch=tag_batch, min_score=min_score)
        except Exception:
            logger.exception("[%s] 链路异常，跳过继续", topic_name)
    logger.info("==== 所有 topic 完成 ====")
    _push_walls()


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
