"""collector + repository + pipeline 单元测试（不调真实 API 和 Claude）。"""
import json
import contextlib
import pytest

from storage.db import init_db, get_connection
from storage import repository
from pipeline import dedup
from collectors.bilibili import BilibiliPopularCollector, fetch_popular

_collector = BilibiliPopularCollector(topic="funny")

def _map_video(item: dict):
    return _collector._map(item)


# ── _map_video 单元测试 ────────────────────────────────────────────────────


_SAMPLE_ITEM = {
    "bvid": "BV1test12345",
    "title": "测试搞笑视频",
    "is_ogv": False,
    "pic": "http://example.com/cover.jpg",
    "duration": 120,
    "tname": "鬼畜",
    "owner": {"name": "UP主A", "mid": 12345},
    "stat": {"view": 100000, "like": 5000, "coin": 1000, "favorite": 2000, "danmaku": 300},
    "rcmd_reason": {"content": "百万播放"},
}


def test_map_video_basic():
    v = _map_video(_SAMPLE_ITEM)
    assert v is not None
    assert v["platform"] == "bilibili"
    assert v["platform_video_id"] == "BV1test12345"
    # 未传 content_hash_prefix 时用类默认值 "bilibili"
    assert v["content_hash"] == "bilibili:BV1test12345"
    assert "player.bilibili.com" in v["embed_url"]
    assert v["tags"] is None
    assert v["funny_score"] is None


def test_map_video_prefix_override():
    """registry 传入 content_hash_prefix 时，hash 格式正确变更。"""
    collector_funny = BilibiliPopularCollector(topic="funny", content_hash_prefix="bilibili_funny")
    v = collector_funny._map(_SAMPLE_ITEM)
    assert v["content_hash"] == "bilibili_funny:BV1test12345"


def test_map_video_skip_ogv():
    item = {**_SAMPLE_ITEM, "is_ogv": True}
    assert _map_video(item) is None


def test_map_video_skip_no_bvid():
    item = {**_SAMPLE_ITEM, "bvid": ""}
    assert _map_video(item) is None


# ── repository + dedup 测试（tmpdir 隔离）────────────────────────────────


@pytest.fixture()
def db_path(tmp_path, monkeypatch):
    """每个测试用独立 DB，monkeypatch 掉 repository._DB_PATH。"""
    dbfile = tmp_path / "test.db"
    init_db(dbfile)
    monkeypatch.setattr(repository, "get_db", lambda: get_connection(dbfile))
    return dbfile


def _make_video(bvid: str, title: str = "title", score: int | None = None) -> dict:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    return {
        "platform": "bilibili", "platform_video_id": bvid,
        "title": title, "author": "UP", "author_id": "1",
        "cover_url": "", "page_url": "", "embed_url": "", "play_url": None,
        "duration": 60, "play_count": 1000, "like_count": 100,
        "category": "搞笑", "tags": None, "funny_score": score,
        "extra": {}, "content_hash": f"bilibili:{bvid}",
        "status": "active", "fetched_at": now, "created_at": now,
    }


def test_upsert_insert(db_path):
    result = repository.upsert_video(_make_video("BVaaa"))
    assert result == "inserted"


def test_upsert_dedup(db_path):
    repository.upsert_video(_make_video("BVbbb", title="old"))
    result = repository.upsert_video(_make_video("BVbbb", title="new"))
    assert result == "updated"
    # 标题被更新
    with contextlib.closing(get_connection(db_path)) as conn:
        row = conn.execute("SELECT title FROM videos WHERE content_hash='bilibili:BVbbb'").fetchone()
    assert row["title"] == "new"


def test_upsert_fetched_at_preserved(db_path):
    """fetched_at 在 re-upsert 时不应被覆盖（保留首次采集日期）。"""
    import time
    v1 = _make_video("BVfetch")
    original_fetched = v1["fetched_at"]
    repository.upsert_video(v1)

    time.sleep(0.01)  # 确保时间推进
    v2 = {**_make_video("BVfetch"), "title": "updated title"}
    # 模拟新一次采集时 fetched_at 会是一个更新的时间
    from datetime import datetime, timezone
    v2["fetched_at"] = datetime.now(timezone.utc).isoformat()
    repository.upsert_video(v2)

    with contextlib.closing(get_connection(db_path)) as conn:
        row = conn.execute("SELECT fetched_at, title FROM videos WHERE content_hash='bilibili:BVfetch'").fetchone()
    assert row["fetched_at"] == original_fetched, "fetched_at 不应被覆盖"
    assert row["title"] == "updated title", "其他字段应被更新"


def test_upsert_coalesce_null(db_path):
    """NULL 值不应覆盖 DB 里已有的非 NULL 字段（COALESCE 保护）。"""
    v1 = {**_make_video("BVcoalesce"), "like_count": 9999, "category": "搞笑"}
    repository.upsert_video(v1)

    # 第二次 upsert：like_count=None, category=None（模拟 API 失败）
    v2 = {**_make_video("BVcoalesce"), "like_count": None, "category": None}
    repository.upsert_video(v2)

    with contextlib.closing(get_connection(db_path)) as conn:
        row = conn.execute("SELECT like_count, category FROM videos WHERE content_hash='bilibili:BVcoalesce'").fetchone()
    assert row["like_count"] == 9999, "like_count 不应被 NULL 覆盖"
    assert row["category"] == "搞笑", "category 不应被 NULL 覆盖"


def test_upsert_extra_serialized(db_path):
    v = _make_video("BVccc")
    v["extra"] = {"coin": 99}
    repository.upsert_video(v)
    with contextlib.closing(get_connection(db_path)) as conn:
        row = conn.execute("SELECT extra FROM videos WHERE content_hash='bilibili:BVccc'").fetchone()
    assert json.loads(row["extra"])["coin"] == 99


def test_dedup_run_counts(db_path):
    videos = [_make_video(f"BV{i:03d}") for i in range(5)]
    counts = dedup.run(videos)
    assert counts == {"inserted": 5, "updated": 0}

    # 重跑一遍：全部 updated，行数不变
    counts2 = dedup.run(videos)
    assert counts2 == {"inserted": 0, "updated": 5}
    with contextlib.closing(get_connection(db_path)) as conn:
        total = conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0]
    assert total == 5


def test_list_untagged(db_path):
    repository.upsert_video(_make_video("BVx", score=None))
    repository.upsert_video(_make_video("BVy", score=7))
    untagged = repository.list_untagged()
    hashes = {v["content_hash"] for v in untagged}
    assert "bilibili:BVx" in hashes
    assert "bilibili:BVy" not in hashes


def test_update_tags(db_path):
    repository.upsert_video(_make_video("BVz"))
    repository.update_tags("bilibili:BVz", ["搞笑", "鬼畜"], 8)
    with contextlib.closing(get_connection(db_path)) as conn:
        row = conn.execute("SELECT tags, funny_score FROM videos WHERE content_hash='bilibili:BVz'").fetchone()
    assert json.loads(row["tags"]) == ["搞笑", "鬼畜"]
    assert row["funny_score"] == 8
