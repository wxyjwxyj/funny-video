"""定时调度器：每5分钟被 launchd 触发，读 schedule.yaml 决定是否执行。

fire-and-forget 模式：脚本检查时间 → 匹配就跑 → 退出。
OS (launchd) 负责周期触发，无需常驻进程。
"""
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from run_topic import run_pipeline
from storage.db import init_db
from topics.registry import list_topics
from utils.log import get_logger

logger = get_logger(__name__)

_ROOT = Path(__file__).parent.parent
_DB = _ROOT / "video.db"
_SCHEDULE = _ROOT / "schedule.yaml"
_RAN_DIR = _ROOT / ".ran"
_EXPECTED_FILES = ["funny_wall.html", "ai_wall.html", "funny_archive/", "ai_archive/", "index.html"]


def _load_schedule() -> list[dict]:
    with open(_SCHEDULE) as f:
        return yaml.safe_load(f).get("runs", [])


def _find_run(runs: list[dict], now: datetime, tolerance: int = 2) -> dict | None:
    for run in runs:
        h, m = map(int, run["time"].split(":"))
        target = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if abs(now - target) <= timedelta(minutes=tolerance):
            return run
    return None


def _already_ran(time_str: str, now: datetime) -> bool:
    return (_RAN_DIR / f"{now.strftime('%Y-%m-%d')}_{time_str.replace(':', '-')}").exists()


def _mark_ran(time_str: str, now: datetime) -> None:
    _RAN_DIR.mkdir(exist_ok=True)
    (_RAN_DIR / f"{now.strftime('%Y-%m-%d')}_{time_str.replace(':', '-')}").touch()
    # 清理3天前的标记
    cutoff = (now - timedelta(days=3)).timestamp()
    for f in _RAN_DIR.iterdir():
        if f.stat().st_mtime < cutoff:
            f.unlink()


def _notify(title: str, message: str) -> None:
    """发送 macOS 系统通知。"""
    script = f'display notification "{message}" with title "{title}"'
    subprocess.run(["osascript", "-e", script], check=False)


def _push_walls() -> None:
    changed: set[str] = set()
    for args in (["git", "diff", "--name-only"], ["git", "diff", "--cached", "--name-only"]):
        r = subprocess.run(args, capture_output=True, text=True, cwd=_ROOT)
        changed.update(f for f in r.stdout.strip().split("\n") if f)

    targets = [f for f in changed if any(f == p or f.startswith(p) for p in _EXPECTED_FILES)]
    if not targets:
        logger.info("无视频墙文件变更，跳过推送")
        return

    logger.info("推送文件: %s", targets)
    subprocess.run(["git", "add"] + targets, cwd=_ROOT, check=False)
    result = subprocess.run(
        ["git", "commit", "-m", f"content: {time.strftime('%Y-%m-%d')} video walls --auto"],
        capture_output=True, text=True, cwd=_ROOT,
    )
    if "nothing to commit" in (result.stdout + result.stderr):
        logger.info("无实际变更，跳过 commit")
        return
    push = subprocess.run(["git", "push"], cwd=_ROOT, capture_output=True, text=True)
    if push.returncode != 0:
        logger.error("GitHub Pages 推送失败: %s", push.stderr.strip())
        _notify("搞笑视频墙 ⚠️", f"push 失败，下次运行会重试")
    else:
        logger.info("GitHub Pages 推送完成")


def run_all(skip_collect: bool = False, skip_tag: bool = False) -> None:
    init_db(_DB)
    failed: list[str] = []
    for topic_name in list_topics():
        try:
            run_pipeline(topic_name, skip_collect=skip_collect, skip_tag=skip_tag)
        except Exception:
            logger.exception("[%s] 链路异常，跳过继续", topic_name)
            failed.append(topic_name)
    logger.info("==== 所有 topic 完成 ====")
    _push_walls()
    # macOS 通知：成功/部分失败均告知
    if failed:
        _notify("搞笑视频墙 ⚠️", f"部分 topic 失败: {', '.join(failed)}")
    else:
        _notify("搞笑视频墙 ✅", f"已更新 {time.strftime('%H:%M')}")


def main() -> None:
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--once", action="store_true", help="直接跑一次（跳过时间检查）")
    p.add_argument("--no-collect", action="store_true")
    p.add_argument("--no-tag", action="store_true")
    args = p.parse_args()

    if args.once:
        run_all(skip_collect=args.no_collect, skip_tag=args.no_tag)
        return

    now = datetime.now()
    matched = _find_run(_load_schedule(), now)
    if matched is None:
        sys.exit(0)

    if _already_ran(matched["time"], now):
        sys.exit(0)

    _mark_ran(matched["time"], now)
    run_all()


if __name__ == "__main__":
    main()
