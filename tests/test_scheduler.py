"""调度器发布和失败重试行为测试。"""
from datetime import datetime
import logging
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
import requests

from scripts import scheduler


def _result(returncode: int = 0, stdout: str = "", stderr: str = ""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


class _Response:
    def __init__(self, status_code: int = 200, payload=None):
        self.status_code = status_code
        self._payload = payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class _Session:
    def __init__(self, responses: dict):
        self.responses = responses
        self.calls: list[tuple[str, dict]] = []
        self.trust_env = True
        self.closed = False

    def get(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        response = self.responses[url]
        if isinstance(response, Exception):
            raise response
        return response

    def close(self):
        self.closed = True


def _install_preflight_sessions(monkeypatch, network: _Session, cdp: _Session) -> None:
    sessions = iter([network, cdp])
    monkeypatch.setattr(
        scheduler,
        "retry_session",
        lambda **kwargs: next(sessions),
    )


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
    monkeypatch.setattr(scheduler, "_is_interactive_session", lambda: True)
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


def test_find_run_catches_up_until_next_schedule():
    runs = [{"time": "19:00"}, {"time": "08:00"}, {"time": "13:00"}]

    assert scheduler._find_run(runs, datetime(2026, 7, 22, 7, 59)) is None
    assert scheduler._find_run(runs, datetime(2026, 7, 22, 12, 59))["time"] == "08:00"
    assert scheduler._find_run(runs, datetime(2026, 7, 22, 13, 0))["time"] == "13:00"
    assert scheduler._find_run(runs, datetime(2026, 7, 22, 18, 59))["time"] == "13:00"
    assert scheduler._find_run(runs, datetime(2026, 7, 22, 19, 0))["time"] == "19:00"


@pytest.mark.parametrize(
    ("user_is_active", "expected"),
    [(0, False), (1, True)],
)
def test_closed_lid_only_runs_when_user_is_active(monkeypatch, user_is_active, expected):
    def fake_run(args, **kwargs):
        if args[0] == "ioreg":
            return _result(stdout='"AppleClamshellState" = Yes\n')
        if args[0] == "pmset":
            return _result(stdout=f"   UserIsActive {user_is_active}\n")
        raise AssertionError(args)

    monkeypatch.setattr(scheduler.subprocess, "run", fake_run)

    assert scheduler._is_interactive_session() is expected


def test_scheduled_run_defers_silently_during_darkwake(monkeypatch, tmp_path):
    events: list[str] = []
    now = datetime(2026, 7, 28, 8, 10)

    class FakeDateTime:
        @classmethod
        def now(cls):
            return now

    monkeypatch.setattr(sys, "argv", ["scheduler.py"])
    monkeypatch.setattr(scheduler, "datetime", FakeDateTime)
    monkeypatch.setattr(scheduler, "_RUN_LOCK", tmp_path / "scheduler.lock")
    monkeypatch.setattr(scheduler, "_rotate_launchd_log", lambda: None)
    monkeypatch.setattr(scheduler, "_load_schedule", lambda: [{"time": "08:00"}])
    monkeypatch.setattr(scheduler, "_already_ran", lambda *args: False)
    monkeypatch.setattr(scheduler, "_is_interactive_session", lambda: False)
    monkeypatch.setattr(
        scheduler, "_preflight_check", lambda: events.append("preflight") or True,
    )
    monkeypatch.setattr(scheduler, "run_all", lambda: events.append("run"))
    monkeypatch.setattr(scheduler, "_mark_ran", lambda *args: events.append("mark"))
    monkeypatch.setattr(
        scheduler, "_notify", lambda *args, **kwargs: events.append("notify"),
    )

    scheduler.main()

    assert events == []


def test_preflight_allows_partial_network_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(scheduler, "_DB", tmp_path / "video.db")
    monkeypatch.setattr(
        scheduler,
        "_network_endpoints",
        lambda: [("A", "https://a.test"), ("B", "https://b.test")],
    )
    monkeypatch.setattr(scheduler, "_notify", lambda *args, **kwargs: None)
    network = _Session({
        "https://a.test": requests.ConnectionError("down"),
        "https://b.test": _Response(),
    })
    cdp = _Session({"http://localhost:3456/targets": _Response(payload=[])})
    _install_preflight_sessions(monkeypatch, network, cdp)

    assert scheduler._preflight_check() is True
    assert [url for url, _ in network.calls] == [
        "https://a.test",
        "https://b.test",
    ]


def test_preflight_rejects_when_all_network_endpoints_fail(monkeypatch, tmp_path):
    monkeypatch.setattr(scheduler, "_DB", tmp_path / "video.db")
    monkeypatch.setattr(
        scheduler,
        "_network_endpoints",
        lambda: [("A", "https://a.test"), ("B", "https://b.test")],
    )
    monkeypatch.setattr(scheduler, "_notify", lambda *args, **kwargs: None)
    network = _Session({
        "https://a.test": requests.ConnectionError("down"),
        "https://b.test": requests.ConnectionError("down"),
    })
    cdp = _Session({"http://localhost:3456/targets": _Response(payload=[])})
    _install_preflight_sessions(monkeypatch, network, cdp)

    assert scheduler._preflight_check() is False


def test_network_failure_notification_is_clickable_retry(monkeypatch, tmp_path):
    notifications: list[tuple[str, str, bool]] = []
    monkeypatch.setattr(scheduler, "_DB", tmp_path / "video.db")
    monkeypatch.setattr(
        scheduler,
        "_network_endpoints",
        lambda: [("down", "https://down.test")],
    )
    monkeypatch.setattr(
        scheduler,
        "_notify",
        lambda title, message, *, retry=False: notifications.append(
            (title, message, retry)
        ),
    )
    network = _Session({
        "https://down.test": requests.ConnectionError("offline"),
    })
    cdp = _Session({"http://localhost:3456/targets": _Response(payload=[])})
    _install_preflight_sessions(monkeypatch, network, cdp)

    assert scheduler._preflight_check() is False
    assert notifications[-1][2] is True


def test_preflight_logs_cdp_degradation_without_notifying(monkeypatch, tmp_path, caplog):
    notifications: list[tuple[str, str, bool]] = []
    monkeypatch.setattr(scheduler, "_DB", tmp_path / "video.db")
    monkeypatch.setattr(
        scheduler,
        "_network_endpoints",
        lambda: [("A", "https://a.test")],
    )
    monkeypatch.setattr(
        scheduler,
        "_notify",
        lambda title, message, *, retry=False: notifications.append(
            (title, message, retry)
        ),
    )
    network = _Session({"https://a.test": _Response()})
    cdp = _Session({
        "http://localhost:3456/targets": _Response(payload={"unexpected": True}),
    })
    _install_preflight_sessions(monkeypatch, network, cdp)

    with caplog.at_level(logging.WARNING):
        assert scheduler._preflight_check() is True

    assert notifications == []
    assert "CDP proxy 不可用" in caplog.text
    assert cdp.trust_env is False


def test_retry_notification_executes_scheduler_once(monkeypatch, caplog):
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

    with caplog.at_level(logging.INFO):
        scheduler._notify("运行失败", "网络不可达", retry=True)

    args = calls[0]
    assert args[0] == "/mock/terminal-notifier"
    assert args[args.index("-execute") + 1] == "retry-command"
    assert "点击重跑" in args[args.index("-message") + 1]
    assert "backend=terminal-notifier" in caplog.text
    assert "clickable=True" in caplog.text


def test_retry_notification_logs_nonclickable_fallback(monkeypatch, caplog):
    calls: list[list[str]] = []
    monkeypatch.setattr(
        scheduler, "_terminal_notifier_path", lambda: Path("/mock/terminal-notifier"),
    )

    def fake_run(args, **kwargs):
        calls.append(args)
        return _result(returncode=1, stderr="broken") if len(calls) == 1 else _result()

    monkeypatch.setattr(scheduler.subprocess, "run", fake_run)

    with caplog.at_level(logging.INFO):
        scheduler._notify("运行失败", "网络不可达", retry=True)

    assert "请手动执行 scheduler.py --once" in calls[1][-1]
    assert "backend=osascript" in caplog.text
    assert "clickable=False" in caplog.text


def test_scheduler_lock_rejects_duplicate_runner(monkeypatch, tmp_path):
    monkeypatch.setattr(scheduler, "_RUN_LOCK", tmp_path / "scheduler.lock")

    with scheduler._run_lock() as first:
        with scheduler._run_lock() as second:
            assert first is True
            assert second is False
