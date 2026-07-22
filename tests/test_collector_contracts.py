"""采集器的降级、登录态与字段映射契约测试。"""
from collections.abc import Iterator

import pytest

from collectors.base import CDPCollector, CollectorError, LoginExpiredError, make_video
from collectors.bilibili import BilibiliSearchCollector
from collectors.douyin import DouyinCollector
from collectors.xiaohongshu import XiaohongshuCollector


class _SequencedCollector(CDPCollector):
    domain_pattern = "contracts.example.com"
    default_keywords = ["broken", "first", "second"]
    content_hash_prefix = "contract"
    request_delay = 0

    def _resolve_target(self) -> str:
        self._target_id = "target"
        return self._target_id

    def _search(self, keyword: str) -> list[dict]:
        if keyword == "broken":
            raise CollectorError("单关键词页面异常")
        if keyword == "first":
            return [{"id": "shared"}, {"id": "first-only"}]
        return [{"id": "shared"}, {"id": "second-only"}]

    def _map_item(self, item: dict, keyword: str) -> dict:
        return make_video(
            platform="contract",
            platform_video_id=item["id"],
            title=item["id"],
            content_hash_prefix=self.content_hash_prefix,
            topic=self.topic,
            extra={"search_keyword": keyword},
        )


class _ExpiredCollector(_SequencedCollector):
    domain_pattern = "expired.example.com"
    default_keywords = ["expired", "must-not-run"]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.searched: list[str] = []

    def _search(self, keyword: str) -> list[dict]:
        self.searched.append(keyword)
        raise LoginExpiredError("登录态过期")


def test_keyword_failure_degrades_and_cross_keyword_duplicates_are_removed():
    videos = _SequencedCollector(topic="funny").collect()

    assert [v["platform_video_id"] for v in videos] == [
        "shared", "first-only", "second-only",
    ]
    assert all(v["topic"] == "funny" for v in videos)


def test_login_expiry_aborts_remaining_keywords_immediately():
    collector = _ExpiredCollector(topic="funny")

    with pytest.raises(LoginExpiredError, match="登录态过期"):
        collector.collect()

    assert collector.searched == ["expired"]


@pytest.mark.parametrize(
    ("state", "message"),
    [
        ({"blocked": True, "needLogin": False}, "验证码"),
        ({"blocked": False, "needLogin": True}, "登录态过期"),
    ],
)
def test_douyin_empty_results_report_auth_state(monkeypatch, state, message):
    collector = DouyinCollector(topic="funny")
    responses: Iterator[object] = iter([[], state])
    monkeypatch.setattr(collector, "_navigate", lambda _: None)
    monkeypatch.setattr(collector, "_eval", lambda *args, **kwargs: next(responses))

    with pytest.raises(LoginExpiredError, match=message):
        collector._search("搞笑")


def test_bilibili_enrichment_fills_only_missing_fields(monkeypatch):
    collector = BilibiliSearchCollector(topic="ai")
    raw = [{
        "platform_video_id": "BV1",
        "published_at": "2026-01-01T00:00:00+00:00",
        "like_count": None,
        "duration": None,
        "category": "已有分区",
    }]
    monkeypatch.setattr(CDPCollector, "collect", lambda self: raw)
    monkeypatch.setattr(
        collector,
        "_enrich_video_info",
        lambda _: {"BV1": {
            "published_at": "2025-01-01T00:00:00+00:00",
            "like_count": 1234,
            "duration": 61,
            "category": "新分区",
        }},
    )

    result = collector.collect()[0]

    assert result["published_at"] == "2026-01-01T00:00:00+00:00"
    assert result["category"] == "已有分区"
    assert result["like_count"] == 1234
    assert result["duration"] == 61


def test_douyin_mapping_enforces_minimum_duration_and_parses_like_threshold():
    collector = DouyinCollector(topic="funny", content_hash_prefix="douyin_funny")
    base = {"vid": "1", "title": "视频", "likes": "27.6万"}

    assert collector._map_item({**base, "duration": "00:02"}, "搞笑") is None

    video = collector._map_item({**base, "duration": "07:12"}, "搞笑")
    assert video["duration"] == 432
    assert video["like_count"] == 276_000
    assert video["content_hash"] == "douyin_funny:1"


def test_xiaohongshu_empty_results_detect_login_redirect(monkeypatch):
    collector = XiaohongshuCollector(topic="funny")
    responses: Iterator[object] = iter([[], "https://www.xiaohongshu.com/login"])
    monkeypatch.setattr(collector, "_navigate", lambda _: None)
    monkeypatch.setattr(collector, "_eval", lambda *args, **kwargs: next(responses))

    with pytest.raises(LoginExpiredError, match="登录态过期"):
        collector._search("搞笑")
