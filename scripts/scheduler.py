"""定时调度器：每5分钟被 launchd 触发，读 schedule.yaml 决定是否执行。

fire-and-forget 模式：脚本检查时间 → 匹配就跑 → 退出。
OS (launchd) 负责周期触发，无需常驻进程。
"""
import socket
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
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


def _rotate_launchd_log(max_bytes: int = 2 * 1024 * 1024, keep_lines: int = 500) -> None:
    """launchd 日志超过 max_bytes 时，只保留末尾 keep_lines 行，防止无限增长。"""
    log_path = Path.home() / "funny_video_launchd.log"
    try:
        if not log_path.exists() or log_path.stat().st_size <= max_bytes:
            return
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        log_path.write_text("\n".join(lines[-keep_lines:]) + "\n", encoding="utf-8")
        logger.info("已轮转 launchd 日志（保留末尾 %d 行）", keep_lines)
    except Exception as e:
        logger.warning("launchd 日志轮转失败: %s", e)


def _notify(title: str, message: str) -> None:
    """发送 macOS 系统通知，失败静默忽略。"""
    script = f'display notification "{message}" with title "{title}"'
    try:
        subprocess.run(["osascript", "-e", script], capture_output=True, timeout=5)
    except Exception:
        pass


def _preflight_check() -> bool:
    """运行前环境检查。返回 False 表示关键依赖不满足，应跳过本次运行。"""
    issues: list[str] = []
    warnings: list[str] = []

    # 1. 网络连通性（8.8.8.8:53 DNS直连，基本不走代理）
    try:
        with socket.create_connection(("8.8.8.8", 53), timeout=5):
            pass
    except OSError:
        issues.append("网络不通 (8.8.8.8:53)")

    # 2. CDP proxy（抖音/小红书依赖，不通只是降级）
    try:
        with socket.create_connection(("localhost", 3456), timeout=3):
            pass
    except OSError:
        warnings.append("CDP proxy 不可用，抖音/小红书将跳过")

    # 3. DB 目录可写
    try:
        with tempfile.NamedTemporaryFile(dir=_DB.parent, delete=True):
            pass
    except OSError:
        issues.append(f"DB 目录不可写 ({_DB.parent})")

    if warnings:
        logger.warning("preflight 警告: %s", " | ".join(warnings))
        _notify("搞笑视频墙 ⚠️", " | ".join(warnings))

    if issues:
        logger.error("preflight 失败，跳过本次运行: %s", " | ".join(issues))
        _notify("搞笑视频墙 ❌", f"本次跳过: {' | '.join(issues)}")
        return False

    logger.info("preflight 通过（网络/CDP/DB 均正常）")
    return True


def _cleanup_old_videos() -> None:
    """14天前、funny_score<7 或 funny_score IS NULL 的视频标记为 inactive。

    减少主查询扫表量；标 inactive 不删数据。
    NULL score = 打标签一直失败，保留14天后放弃。
    每次 run_all 末尾调用；UPDATE 幂等，重复跑无副作用。
    """
    import contextlib
    from storage.db import get_connection
    with contextlib.closing(get_connection(_DB)) as conn:
        cur = conn.execute(
            "UPDATE videos SET status='inactive' "
            "WHERE fetched_at < date('now', '-14 days') "
            "  AND (funny_score < 7 OR funny_score IS NULL) "
            "  AND status='active'",
        )
        conn.commit()
    if cur.rowcount:
        logger.info("DB清理: 标记 %d 条旧低分/未标签视频为 inactive", cur.rowcount)


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
    topics = list_topics()

    # 两个 topic 独立采集、打标签、生成，无共享状态，并行跑节省一半时间
    # 注意：若多个 topic 同时用 CDP，偶发 tab 冲突时会降级（optional collector 不抛异常）
    with ThreadPoolExecutor(max_workers=len(topics)) as pool:
        fut_to_topic = {
            pool.submit(run_pipeline, t, skip_collect=skip_collect, skip_tag=skip_tag): t
            for t in topics
        }
        results: list[dict] = []
        for future in as_completed(fut_to_topic):
            t = fut_to_topic[future]
            try:
                results.append(future.result())
            except Exception:
                logger.exception("[%s] 链路异常，跳过继续", t)
                results.append({"topic": t, "inserted": 0, "tagged": 0,
                                 "platforms": {}, "failed": [(t, "异常")]})

    logger.info("==== 所有 topic 完成 ====")
    _cleanup_old_videos()
    _push_walls()

    # 汇总各 topic 的采集统计，组装通知
    total_inserted = sum(r.get("inserted", 0) for r in results)
    all_failed: list[tuple[str, str]] = [
        (name, reason) for r in results for name, reason in r.get("failed", [])
    ]
    platform_lines = []
    for r in results:
        for p, n in r.get("platforms", {}).items():
            if n > 0:
                label = {"bilibili": "B站", "douyin": "抖音", "xiaohongshu": "小红书"}.get(p, p)
                platform_lines.append(f"{label}+{n}")

    stats_str = f"新增 {total_inserted} 条"
    if platform_lines:
        stats_str += f"（{'  '.join(platform_lines)}）"

    if all_failed:
        # 格式示例："抖音(CDP连接)  小红书(超时)"，方便一眼定位原因
        fail_parts = "  ".join(f"{name}({reason})" for name, reason in all_failed)
        _notify("搞笑视频墙 ⚠️", f"{stats_str}  失败: {fail_parts}")
    else:
        _notify("搞笑视频墙 ✅",
                f"{stats_str}  {time.strftime('%H:%M')}")


def main() -> None:
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--once", action="store_true", help="直接跑一次（跳过时间检查）")
    p.add_argument("--no-collect", action="store_true")
    p.add_argument("--no-tag", action="store_true")
    args = p.parse_args()

    _rotate_launchd_log()

    if args.once:
        if not _preflight_check():
            sys.exit(1)
        run_all(skip_collect=args.no_collect, skip_tag=args.no_tag)
        return

    now = datetime.now()
    matched = _find_run(_load_schedule(), now)
    if matched is None:
        sys.exit(0)

    if _already_ran(matched["time"], now):
        sys.exit(0)

    # 时间匹配后才做 preflight，避免每5分钟都检查一遍
    if not _preflight_check():
        sys.exit(1)

    _mark_ran(matched["time"], now)
    run_all()


if __name__ == "__main__":
    main()
