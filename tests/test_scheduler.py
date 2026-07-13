"""调度器发布和失败重试行为测试。"""
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
    monkeypatch.setattr(scheduler, "_notify", lambda *args: None)

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
    monkeypatch.setattr(scheduler, "_notify", lambda *args: None)

    with pytest.raises(RuntimeError, match="git commit 失败"):
        scheduler._push_walls()
    assert ["git", "push"] not in calls


def test_scheduled_run_marks_only_after_success(monkeypatch):
    marked: list[tuple] = []
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
