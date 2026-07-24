"""调度器发布和失败重试行为测试。"""
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from scripts import scheduler


def _result(returncode: int = 0, stdout: str = "", stderr: str = ""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def test_push_walls_includes_untracked_archives(monkeypatch):
    calls: list[list[str]] = []
    responses = iter([
        _result(),
        _result(),
        _result(stdout="funny_archive/2026-07-13.html\n"),
        _result(),
        _result(stdout="committed"),
        _result(stdout="pushed"),
    ])

    def fake_run(args, **kwargs):
        calls.append(args)
        return next(responses)

    monkeypatch.setattr(scheduler.subprocess, "run", fake_run)
    monkeypatch.setattr(scheduler, "_notify", lambda *args, **kwargs: None)

    scheduler._push_walls()

    assert ["git", "ls-files", "--others", "--exclude-standard"] in calls
    assert ["git", "add", "funny_archive/2026-07-13.html"] in calls


def test_push_walls_stops_when_commit_fails(monkeypatch):
    calls: list[list[str]] = []
    responses = iter([
        _result(stdout="funny_wall.html\n"),
        _result(),
        _result(),
        _result(),
        _result(returncode=1, stderr="commit hook failed"),
    ])

    def fake_run(args, **kwargs):
        calls.append(args)
        return next(responses)

    monkeypatch.setattr(scheduler.subprocess, "run", fake_run)
    monkeypatch.setattr(scheduler, "_notify", lambda *args, **kwargs: None)

    with pytest.raises(RuntimeError, match="git commit 失败"):
        scheduler._push_walls()
    assert ["git", "push"] not in calls


def test_push_walls_never_stages_unrelated_worktree_files(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append(args)
        if args[:3] == ["git", "diff", "--name-only"]:
            return _result(stdout="run_topic.py\nAGENTS.md\n")
        if args[:4] == ["git", "ls-files", "--others", "--exclude-standard"]:
            return _result(stdout="verify_screenshot.png\n")
        return _result()

    monkeypatch.setattr(scheduler.subprocess, "run", fake_run)

    scheduler._push_walls()

    assert not any(call[:2] == ["git", "add"] for call in calls)


def test_push_walls_retries_unpushed_wall_commit_when_files_are_clean(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append(args)
        if args[:3] == ["git", "log", "--format=%H"]:
            return _result(stdout="deadbeef\n")
        return _result()

    monkeypatch.setattr(scheduler.subprocess, "run", fake_run)
    monkeypatch.setattr(scheduler, "_notify", lambda *args, **kwargs: None)

    scheduler._push_walls()

    assert ["git", "push"] in calls


def test_push_walls_propagates_push_failure_for_later_retry(monkeypatch):
    calls: list[list[str]] = []
    responses = iter([
        _result(stdout="funny_wall.html\n"),
        _result(),
        _result(),
        _result(),
        _result(stdout="committed"),
        _result(returncode=1, stderr="network down"),
    ])

    def fake_run(args, **kwargs):
        calls.append(args)
        return next(responses)

    monkeypatch.setattr(scheduler.subprocess, "run", fake_run)
    monkeypatch.setattr(scheduler, "_notify", lambda *args, **kwargs: None)

    with pytest.raises(RuntimeError, match="推送失败"):
        scheduler._push_walls()

    assert calls[-1] == ["git", "push"]


def test_scheduled_run_marks_only_after_success(monkeypatch, tmp_path):
    marked: list[tuple] = []
    monkeypatch.setattr(scheduler, "_RUN_LOCK", tmp_path / "scheduler.lock")
    monkeypatch.setattr(sys, "argv", ["scheduler.py"])
    monkeypatch.setattr(scheduler, "_rotate_launchd_log", lambda: None)
    monkeypatch.setattr(scheduler, "_load_schedule", lambda: [{"time": "13:00"}])
    monkeypatch.setattr(scheduler, "_find_run", lambda runs, now: runs[0])
    monkeypatch.setattr(scheduler, "_already_ran", lambda *args: False)
    monkeypatch.setattr(scheduler, "_preflight_check", lambda: True)
    monkeypatch.setattr(scheduler, "_mark_ran", lambda *args: marked.append(args))
    monkeypatch.setattr(
        scheduler, "run_all", lambda: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    with pytest.raises(RuntimeError, match="boom"):
        scheduler.main()
    assert marked == []


def test_once_marks_starting_schedule_even_if_run_finishes_after_window(monkeypatch, tmp_path):
    marked: list[tuple] = []
    clock = {"now": datetime(2026, 7, 24, 8, 28)}

    class FakeDateTime:
        @classmethod
        def now(cls):
            return clock["now"]

    def run_all(**kwargs):
        clock["now"] = datetime(2026, 7, 24, 8, 31)

    monkeypatch.setattr(sys, "argv", ["scheduler.py", "--once"])
    monkeypatch.setattr(scheduler, "_RUN_LOCK", tmp_path / "scheduler.lock")
    monkeypatch.setattr(scheduler, "datetime", FakeDateTime)
    monkeypatch.setattr(scheduler, "_rotate_launchd_log", lambda: None)
    monkeypatch.setattr(scheduler, "_preflight_check", lambda: True)
    monkeypatch.setattr(scheduler, "run_all", run_all)
    monkeypatch.setattr(scheduler, "_load_schedule", lambda: [{"time": "08:00"}])
    monkeypatch.setattr(scheduler, "_mark_ran", lambda *args: marked.append(args))

    scheduler.main()

    assert marked == [("08:00", datetime(2026, 7, 24, 8, 28))]


def test_run_all_reports_topic_failure_after_publishing_successful_topic(monkeypatch):
    published: list[bool] = []
    monkeypatch.setattr(scheduler, "init_db", lambda _: None)
    monkeypatch.setattr(scheduler, "list_topics", lambda: ["funny", "ai"])
    monkeypatch.setattr(scheduler, "_cleanup_old_videos", lambda: None)
    monkeypatch.setattr(scheduler, "_push_walls", lambda: published.append(True))
    monkeypatch.setattr(scheduler, "_notify", lambda *args, **kwargs: None)

    def run_pipeline(topic, **kwargs):
        if topic == "ai":
            raise RuntimeError("AI tagging unavailable")
        return {
            "topic": topic,
            "inserted": 2,
            "tagged": 2,
            "platforms": {"bilibili": 2},
            "failed": [],
        }

    monkeypatch.setattr(scheduler, "run_pipeline", run_pipeline)

    with pytest.raises(RuntimeError, match="ai"):
        scheduler.run_all()

    assert published == [True]


def test_find_run_keeps_retry_window():
    runs = [{"time": "13:00"}]
    assert scheduler._find_run(runs, datetime(2026, 7, 22, 13, 25)) == runs[0]
    assert scheduler._find_run(runs, datetime(2026, 7, 22, 12, 45)) is None
    assert scheduler._find_run(runs, datetime(2026, 7, 22, 13, 31)) is None


def test_preflight_allows_partial_network_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(scheduler, "_DB", tmp_path / "video.db")
    monkeypatch.setattr(scheduler, "_network_endpoints", lambda: [("a", 443), ("b", 443)])
    monkeypatch.setattr(scheduler, "_notify", lambda *args, **kwargs: None)

    def connect(address, timeout):
        if address == ("a", 443):
            raise OSError("down")
        return nullcontext()

    monkeypatch.setattr(scheduler.socket, "create_connection", connect)
    assert scheduler._preflight_check() is True


def test_preflight_rejects_when_all_network_endpoints_fail(monkeypatch, tmp_path):
    monkeypatch.setattr(scheduler, "_DB", tmp_path / "video.db")
    monkeypatch.setattr(scheduler, "_network_endpoints", lambda: [("a", 443), ("b", 443)])
    monkeypatch.setattr(scheduler, "_notify", lambda *args, **kwargs: None)

    def connect(address, timeout):
        if address[0] in {"a", "b"}:
            raise OSError("down")
        return nullcontext()

    monkeypatch.setattr(scheduler.socket, "create_connection", connect)
    assert scheduler._preflight_check() is False


def test_network_failure_notification_is_clickable_retry(monkeypatch, tmp_path):
    notifications: list[tuple[str, str, bool]] = []
    monkeypatch.setattr(scheduler, "_DB", tmp_path / "video.db")
    monkeypatch.setattr(scheduler, "_network_endpoints", lambda: [("down", 443)])
    monkeypatch.setattr(
        scheduler,
        "_notify",
        lambda title, message, *, retry=False: notifications.append(
            (title, message, retry)
        ),
    )
    monkeypatch.setattr(
        scheduler.socket,
        "create_connection",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("offline")),
    )

    assert scheduler._preflight_check() is False
    assert notifications[-1][2] is True


def test_retry_notification_executes_scheduler_once(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(
        scheduler, "_terminal_notifier_path",
        lambda: Path("/mock/terminal-notifier"),
    )
    monkeypatch.setattr(scheduler, "_retry_command", lambda: "retry-command")
    monkeypatch.setattr(
        scheduler.subprocess,
        "run",
        lambda args, **kwargs: calls.append(args) or _result(),
    )

    scheduler._notify("运行失败", "网络不可达", retry=True)

    args = calls[0]
    assert args[0] == "/mock/terminal-notifier"
    assert args[args.index("-execute") + 1] == "retry-command"
    assert "点击重跑" in args[args.index("-message") + 1]


def test_scheduler_lock_rejects_duplicate_runner(monkeypatch, tmp_path):
    monkeypatch.setattr(scheduler, "_RUN_LOCK", tmp_path / "scheduler.lock")

    with scheduler._run_lock() as first:
        with scheduler._run_lock() as second:
            assert first is True
            assert second is False
