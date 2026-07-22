"""主题流水线失败保护测试。"""
from pathlib import Path

import pytest

import run_topic
from topics.config import CollectorDef, TopicConfig
from utils.errors import AIApiError


def _config(collectors: list[CollectorDef] | None = None) -> TopicConfig:
    return TopicConfig(
        topic="funny",
        display_name="搞笑",
        collectors=collectors or [],
    )


def test_zero_collector_result_retries_and_reports_failure(monkeypatch, tmp_path):
    calls = 0

    class EmptyCollector:
        def collect(self):
            nonlocal calls
            calls += 1
            return []

    monkeypatch.setattr(
        run_topic, "get_topic",
        lambda name: _config([CollectorDef("empty", optional=True)]),
    )
    monkeypatch.setattr(run_topic, "create_collector", lambda *args, **kwargs: EmptyCollector())
    monkeypatch.setattr(run_topic.time, "sleep", lambda _: None)
    monkeypatch.setattr(run_topic, "generate", lambda **kwargs: Path(tmp_path / "wall.html"))

    result = run_topic.run_pipeline("funny", skip_tag=True)

    assert calls == 2
    assert result["failed"] == [("empty", "CollectorError")]


def test_total_tagging_failure_preserves_previous_wall(monkeypatch):
    monkeypatch.setattr(run_topic, "get_topic", lambda name: _config())
    monkeypatch.setattr(run_topic.repository, "count_untagged", lambda topic: 4)
    monkeypatch.setattr(run_topic.tagging, "run", lambda **kwargs: 0)
    monkeypatch.setattr(
        run_topic, "generate",
        lambda **kwargs: pytest.fail("打标全失败时不应覆盖视频墙"),
    )

    with pytest.raises(AIApiError, match="全部失败"):
        run_topic.run_pipeline("funny", skip_collect=True)
