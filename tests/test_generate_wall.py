"""视频墙生成器测试。"""
import json
from pathlib import Path

import pytest

from storage.db import init_db, get_connection
from storage import repository
from publishers.generate_wall import generate

# 固定过去日期，避免测试数据污染今日真实归档目录
_TEST_DATE = "2020-01-01"
_TEST_FETCHED_AT = f"{_TEST_DATE}T00:00:00+00:00"


@pytest.fixture()
def seeded_db(tmp_path, monkeypatch):
    """建一个含 2 条已打标签视频的临时 DB，patch 掉 generate_wall 的路径。"""
    dbfile = tmp_path / "test.db"
    init_db(dbfile)
    monkeypatch.setattr(repository, "get_db", lambda: get_connection(dbfile))

    import publishers.generate_wall as gw
    monkeypatch.setattr(gw, "_DB_PATH", dbfile)

    # fetched_at 与 _TEST_DATE 对齐，确保 generate(date=_TEST_DATE) 能查到数据
    for bvid, score, cat in [("BVaaa", 8, "鬼畜"), ("BVbbb", 5, "生活")]:
        repository.upsert_video({
            "platform": "bilibili", "platform_video_id": bvid,
            "title": f"搞笑视频{bvid}", "author": "UP", "author_id": "1",
            "cover_url": "http://example.com/cover.jpg",
            "page_url": f"https://www.bilibili.com/video/{bvid}",
            "embed_url": f"https://player.bilibili.com/player.html?bvid={bvid}&autoplay=0",
            "play_url": None, "duration": 60, "play_count": 50000, "like_count": 3000,
            "category": cat, "tags": json.dumps(["搞笑", cat]), "funny_score": score,
            "extra": {}, "content_hash": f"bilibili:{bvid}",
            "status": "active", "topic": "funny",
            "fetched_at": _TEST_FETCHED_AT, "created_at": _TEST_FETCHED_AT,
        })
    return dbfile


def test_generate_creates_file(seeded_db, tmp_path):
    out = generate(output=tmp_path / "wall.html", date=_TEST_DATE,
                   archive_dir=tmp_path / "archive")
    assert out.exists()
    content = out.read_text(encoding="utf-8")
    assert "搞笑视频BVaaa" in content
    assert "player.bilibili.com" in content


def test_generate_min_score_filter(seeded_db, tmp_path):
    out = generate(min_score=7, output=tmp_path / "wall.html", date=_TEST_DATE,
                   archive_dir=tmp_path / "archive")
    content = out.read_text(encoding="utf-8")
    assert "BVaaa" in content        # score=8，应在
    assert "BVbbb" not in content    # score=5，应被过滤
