"""视频墙生成器测试。"""
import contextlib
import json
from pathlib import Path

import pytest

from storage.db import init_db, get_connection
from storage import repository
from publishers.generate_wall import _render_card, _render_featured_card, generate
from utils.errors import PipelineError

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


def test_generate_creates_file(seeded_db, tmp_path, monkeypatch):
    import publishers.generate_wall as gw
    monkeypatch.setattr(
        gw, "_update_index_time",
        lambda _: pytest.fail("临时生成不应更新真实首页"),
    )
    out = generate(output=tmp_path / "wall.html", date=_TEST_DATE,
                   archive_dir=tmp_path / "archive", update_index=False)
    assert out.exists()
    content = out.read_text(encoding="utf-8")
    assert "搞笑视频BVaaa" in content
    assert "player.bilibili.com" in content


def test_generate_min_score_filter(seeded_db, tmp_path):
    out = generate(min_score=7, output=tmp_path / "wall.html", date=_TEST_DATE,
                   archive_dir=tmp_path / "archive", update_index=False)
    content = out.read_text(encoding="utf-8")
    assert "BVaaa" in content        # score=8，应在
    assert "BVbbb" not in content    # score=5，应被过滤


def test_featured_card_contains_all_filter_dimensions():
    html = _render_featured_card({
        "funny_score": 9,
        "title": "精选",
        "page_url": "https://example.com/video",
        "cover_url": "https://example.com/cover.jpg",
        "category": "搞笑",
        "platform": "douyin",
        "content_hash": "douyin_funny:1",
        "published_at": _TEST_FETCHED_AT,
    })
    assert 'data-score="9"' in html
    assert 'data-cat="搞笑"' in html
    assert 'data-platform="douyin"' in html
    assert 'data-age=' in html


def test_template_filters_featured_cards():
    template = (Path(__file__).parent.parent / "publishers/templates/wall.html").read_text(
        encoding="utf-8"
    )
    assert "document.querySelectorAll('.feat-card')" in template
    assert "featuredSection.style.display" in template


def test_fail_on_empty_preserves_existing_output(tmp_path, monkeypatch):
    dbfile = tmp_path / "empty.db"
    init_db(dbfile)
    import publishers.generate_wall as gw
    monkeypatch.setattr(gw, "_DB_PATH", dbfile)
    out = tmp_path / "wall.html"
    out.write_text("previous wall", encoding="utf-8")

    with pytest.raises(PipelineError, match="保留上一版"):
        generate(
            output=out,
            date=_TEST_DATE,
            archive_dir=tmp_path / "archive",
            update_index=False,
            fail_on_empty=True,
        )

    assert out.read_text(encoding="utf-8") == "previous wall"
    assert not (tmp_path / "archive").exists()


def test_generate_enforces_topic_status_score_and_like_filters(seeded_db, tmp_path):
    rows = [
        ("funny", "valid", "应展示", 9, 6000, "active"),
        ("ai", "wrong-topic", "错误主题", 10, 9000, "active"),
        ("funny", "inactive", "已停用", 10, 9000, "inactive"),
        ("funny", "low-like", "点赞不足", 10, 4999, "active"),
    ]
    with contextlib.closing(get_connection(seeded_db)) as conn:
        with conn:
            conn.executemany(
                "INSERT INTO videos(topic, platform, platform_video_id, title, "
                "like_count, funny_score, status, content_hash, fetched_at, created_at) "
                "VALUES(?, 'bilibili', ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (topic, vid, title, likes, score, status, f"{topic}:{vid}",
                     _TEST_FETCHED_AT, _TEST_FETCHED_AT)
                    for topic, vid, title, score, likes, status in rows
                ],
            )

    out = generate(
        topic="funny",
        min_score=7,
        min_like_count=5000,
        output=tmp_path / "wall.html",
        date=_TEST_DATE,
        archive_dir=tmp_path / "archive",
        update_index=False,
    )
    content = out.read_text(encoding="utf-8")

    assert "应展示" in content
    assert "错误主题" not in content
    assert "已停用" not in content
    assert "点赞不足" not in content


def test_render_card_escapes_metadata_and_rejects_script_urls():
    html = _render_card({
        "topic": "funny",
        "funny_score": 9,
        "title": "<script>alert(1)</script>",
        "author": "<img src=x onerror=alert(1)>",
        "tags": json.dumps(["<b>标签</b>"]),
        "page_url": "javascript:alert(1)",
        "cover_url": "javascript:alert(2)",
        "category": "搞笑\" data-evil=\"1",
        "platform": "douyin",
        "content_hash": "douyin_funny:1",
    })

    assert "<script>" not in html
    assert "<img src=x" not in html
    assert "<b>标签</b>" not in html
    assert "javascript:" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert 'data-href=""' in html
