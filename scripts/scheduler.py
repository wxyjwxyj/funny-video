"""定时调度器：每5分钟被 launchd 触发，读 schedule.yaml 决定是否执行。

fire-and-forget 模式：脚本检查时间 → 匹配就跑 → 退出。
OS (launchd) 负责周期触发，无需常驻进程。
"""
import fcntl
import os
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from run_topic import run_pipeline
from storage.db import init_db
from topics.registry import list_topics
from utils.config import get_claude_config
from utils.log import get_logger

logger = get_logger(__name__)

_ROOT = Path(__file__).parent.parent
_DB = _ROOT / "video.db"
_SCHEDULE = _ROOT / "schedule.yaml"
_RAN_DIR = _ROOT / ".ran"
_EXPECTED_FILES = ["funny_wall.html", "ai_wall.html", "funny_archive/", "ai_archive/", "index.html"]
_LOG_PATH = Path.home() / "funny_video_launchd.log"
_RUN_LOCK = Path(tempfile.gettempdir()) / f"funny-video-scheduler-{os.getuid()}.lock"


def _load_schedule() -> list[dict]:
    with open(_SCHEDULE) as f:
        return yaml.safe_load(f).get("runs", [])


def _find_run(runs: list[dict], now: datetime, tolerance: int = 30) -> dict | None:
    """匹配最近的计划时间；成功标记可阻止 30 分钟窗口内重复执行。"""
    for run in runs:
        h, m = map(int, run["time"].split(":"))
        target = now.replace(hour=h, minute=m, second=0, microsecond=0)
        delay = now - target
        if timedelta(0) <= delay <= timedelta(minutes=tolerance):
            return run
    return None


def _network_endpoints() -> list[tuple[str, int]]:
    """返回主链路实际依赖的网络端点。"""
    endpoints = {("api.bilibili.com", 443), ("github.com", 443)}
    _, base_url, _ = get_claude_config()
    ai_host = urlparse(base_url).hostname if base_url else None
    if ai_host:
        endpoints.add((ai_host, 443))
    return sorted(endpoints)


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


def _terminal_notifier_path() -> Path | None:
    """定位 terminal-notifier；launchd 的 PATH 通常不含 Homebrew。"""
    found = shutil.which("terminal-notifier")
    if found:
        return Path(found)
    for candidate in (
        Path("/opt/homebrew/bin/terminal-notifier"),
        Path("/usr/local/bin/terminal-notifier"),
    ):
        if candidate.is_file():
            return candidate
    return None


def _retry_command() -> str:
    """返回通知点击后执行的安全补跑命令，输出追加到原 launchd 日志。"""
    command = shlex.join([sys.executable, str(Path(__file__).resolve()), "--once"])
    return f"{command} >> {shlex.quote(str(_LOG_PATH))} 2>&1"


def _notify(title: str, message: str, *, retry: bool = False) -> None:
    """发送 macOS 通知；retry=True 时点击通知立即补跑一次。"""
    display_message = f"{message}（点击重跑）" if retry else message
    notifier = _terminal_notifier_path()
    if notifier:
        args = [
            str(notifier),
            "-title", title,
            "-message", display_message,
            "-group", "funny-video-status",
        ]
        if retry:
            args.extend(["-execute", _retry_command()])
        try:
            result = subprocess.run(args, capture_output=True, timeout=5)
            if result.returncode == 0:
                return
        except Exception:
            pass

    # terminal-notifier 不可用或调用失败时，降级为不可点击的系统通知。
    if retry:
        display_message = f"{message}（通知组件不可用，请手动执行 scheduler.py --once）"
    script = (
        "on run argv\n"
        "display notification (item 2 of argv) with title (item 1 of argv)\n"
        "end run"
    )
    try:
        subprocess.run(
            ["osascript", "-e", script, "--", title, display_message],
            capture_output=True, timeout=5,
        )
    except Exception:
        pass


@contextmanager
def _run_lock():
    """进程级非阻塞锁，防止通知补跑和 launchd 定时任务并发执行。"""
    lock_file = _RUN_LOCK.open("a+")
    try:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    finally:
        lock_file.close()


def _preflight_check() -> bool:
    """运行前环境检查。返回 False 表示关键依赖不满足，应跳过本次运行。"""
    issues: list[str] = []
    warnings: list[str] = []

    # 1. 检查实际依赖；单个端点异常时继续运行，由对应步骤自行重试/降级。
    endpoints = _network_endpoints()
    unreachable: list[str] = []
    for host, port in endpoints:
        try:
            with socket.create_connection((host, port), timeout=3):
                pass
        except OSError:
            unreachable.append(f"{host}:{port}")
    if endpoints and len(unreachable) == len(endpoints):
        issues.append("关键网络端点均不可达")
    elif unreachable:
        warnings.append(f"部分网络端点不可达 ({', '.join(unreachable)})")

    # 2. CDP proxy（抖音/小红书依赖，不通只是降级）
    try:
        with socket.create_connection(("localhost", 3456), timeout=3):
            pass
    except OSError:
        warnings.append("CDP proxy 不可用，搜索采集器将降级")

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
        _notify("搞笑视频墙 ❌", f"本次跳过: {' | '.join(issues)}", retry=True)
        return False

    logger.info("preflight 通过（关键网络/CDP/DB 检查完成）")
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


def _has_unpushed_wall_commit() -> bool:
    """检查本地相对上游是否有涉及视频墙文件的未推送提交。"""
    result = subprocess.run(
        ["git", "log", "--format=%H", "@{u}..HEAD", "--", *_EXPECTED_FILES],
        capture_output=True, text=True, cwd=_ROOT,
    )
    if result.returncode != 0:
        logger.warning("检查待补推提交失败: %s", result.stderr.strip())
        return False
    return bool(result.stdout.strip())


def _push_current_branch() -> None:
    """推送当前分支；失败时抛错，让调度窗口内的下一轮继续补推。"""
    push = subprocess.run(["git", "push"], cwd=_ROOT, capture_output=True, text=True)
    if push.returncode != 0:
        message = f"GitHub Pages 推送失败: {push.stderr.strip()}"
        logger.error(message)
        _notify("搞笑视频墙 ⚠️", "push 失败，下次运行会重试", retry=True)
        raise RuntimeError(message)
    logger.info("GitHub Pages 推送完成")


def _push_walls() -> None:
    changed: set[str] = set()
    commands = (
        ["git", "diff", "--name-only"],
        ["git", "diff", "--cached", "--name-only"],
        ["git", "ls-files", "--others", "--exclude-standard"],
    )
    for args in commands:
        r = subprocess.run(args, capture_output=True, text=True, cwd=_ROOT)
        if r.returncode != 0:
            message = f"Git 状态检查失败: {r.stderr.strip()}"
            logger.error(message)
            _notify("搞笑视频墙 ⚠️", message)
            raise RuntimeError(message)
        changed.update(f for f in r.stdout.strip().split("\n") if f)

    targets = sorted(
        f for f in changed if any(f == p or f.startswith(p) for p in _EXPECTED_FILES)
    )
    if not targets:
        if _has_unpushed_wall_commit():
            logger.info("发现上次未推送的视频墙提交，立即补推")
            _push_current_branch()
            return
        logger.info("无视频墙文件变更，跳过推送")
        return

    logger.info("推送文件: %s", targets)
    add = subprocess.run(
        ["git", "add"] + targets, cwd=_ROOT, capture_output=True, text=True,
    )
    if add.returncode != 0:
        message = f"git add 失败: {add.stderr.strip()}"
        logger.error(message)
        _notify("搞笑视频墙 ⚠️", message)
        raise RuntimeError(message)

    result = subprocess.run(
        ["git", "commit", "-m", f"content: {time.strftime('%Y-%m-%d')} video walls --auto"],
        capture_output=True, text=True, cwd=_ROOT,
    )
    commit_output = result.stdout + result.stderr
    if result.returncode != 0 and "nothing to commit" in commit_output.lower():
        logger.info("无实际变更，跳过 commit")
        return
    if result.returncode != 0:
        message = f"git commit 失败: {commit_output.strip()}"
        logger.error(message)
        _notify("搞笑视频墙 ⚠️", message)
        raise RuntimeError(message)

    _push_current_branch()


def run_all(skip_collect: bool = False, skip_tag: bool = False) -> None:
    init_db(_DB)
    topics = list_topics()

    # topic 链路并行；CDPCollector 内部按平台加锁，避免共享 tab 导航串墙。
    with ThreadPoolExecutor(max_workers=len(topics)) as pool:
        fut_to_topic = {
            pool.submit(run_pipeline, t, skip_collect=skip_collect, skip_tag=skip_tag): t
            for t in topics
        }
        results: list[dict] = []
        pipeline_errors: list[tuple[str, Exception]] = []
        for future in as_completed(fut_to_topic):
            t = fut_to_topic[future]
            try:
                results.append(future.result())
            except Exception as e:
                logger.exception("[%s] 链路异常，跳过继续", t)
                pipeline_errors.append((t, e))
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
        _notify(
            "搞笑视频墙 ⚠️",
            f"{stats_str}  失败: {fail_parts}",
            retry=bool(pipeline_errors),
        )
    else:
        _notify("搞笑视频墙 ✅",
                f"{stats_str}  {time.strftime('%H:%M')}")

    if pipeline_errors:
        failed_topics = ", ".join(topic for topic, _ in pipeline_errors)
        raise RuntimeError(f"topic 流水线失败，等待调度补跑: {failed_topics}")


def main() -> None:
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--once", action="store_true", help="直接跑一次（跳过时间检查）")
    p.add_argument("--no-collect", action="store_true")
    p.add_argument("--no-tag", action="store_true")
    args = p.parse_args()

    _rotate_launchd_log()

    if args.once:
        with _run_lock() as acquired:
            if not acquired:
                logger.warning("已有调度任务正在运行，忽略重复补跑")
                _notify("搞笑视频墙 ⏳", "已有任务正在运行，无需重复启动")
                return
            started_at = datetime.now()
            matched = _find_run(_load_schedule(), started_at)
            if not _preflight_check():
                sys.exit(1)
            run_all(skip_collect=args.no_collect, skip_tag=args.no_tag)
            # 点击通知通常发生在计划时间后的补跑窗口内。成功后写标记，
            # 避免下一次 5 分钟 launchd 触发再次执行同一计划。
            if matched is not None:
                _mark_ran(matched["time"], started_at)
        return

    now = datetime.now()
    matched = _find_run(_load_schedule(), now)
    if matched is None:
        sys.exit(0)

    with _run_lock() as acquired:
        if not acquired:
            logger.info("已有调度任务正在运行，本轮跳过")
            sys.exit(0)
        # 获锁后重新检查，避免另一个进程刚完成并写入成功标记。
        if _already_ran(matched["time"], now):
            sys.exit(0)

        # 时间匹配后才做 preflight，避免每5分钟都检查一遍
        if not _preflight_check():
            sys.exit(1)

        run_all()
        _mark_ran(matched["time"], now)


if __name__ == "__main__":
    main()
