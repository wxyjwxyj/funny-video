"""AI 打标重试与评分约束测试。"""

from pipeline import tagging


def _video() -> dict:
    return {
        "title": "这才是剪纸！",
        "author": "作者",
        "category": "手工",
        "duration": 30,
        "like_count": 100000,
        "play_count": 200000,
    }


def test_funny_prompt_requires_explicit_humor():
    prompt = tagging._build_prompt([_video()], "funny")
    assert "Popularity, cuteness, spectacle, craftsmanship" in prompt
    assert "Scores 7-8 require an explicit comedic premise" in prompt
    assert "category: 手工" in prompt


def test_call_batch_retries_and_clamps_score(monkeypatch):
    calls = 0

    def fake_call(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("未收到 ToolUseBlock")
        return {
            "results": [{"id": 1, "score": 99, "tags": ["手工"], "is_unsafe": False}]
        }

    monkeypatch.setattr(tagging, "claude_call_tool", fake_call)
    monkeypatch.setattr(tagging.time, "sleep", lambda _: None)

    result = tagging._call_batch([_video()], "funny")

    assert calls == 2
    assert result[0]["score"] == 10


def test_run_writes_only_received_results_and_leaves_missing_for_retry(monkeypatch):
    videos = [
        {"title": "已返回", "content_hash": "funny:1"},
        {"title": "未返回", "content_hash": "funny:2"},
    ]
    writes: list[tuple[str, list[str], int, bool]] = []
    monkeypatch.setattr(tagging.repository, "list_untagged", lambda **kwargs: videos)
    monkeypatch.setattr(
        tagging,
        "_call_batch",
        lambda batch, topic: [
            {"score": 8, "tags": ["搞笑"], "is_unsafe": False},
            None,
        ],
    )
    monkeypatch.setattr(tagging, "batch_update_tags", lambda items: writes.extend(items))

    tagged = tagging.run(workers=1, topic="funny")

    assert tagged == 1
    assert writes == [("funny:1", ["搞笑"], 8, False)]
