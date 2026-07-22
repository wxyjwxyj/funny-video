"""CDP 采集器并发边界测试。"""
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from collectors.base import CDPCollector, CollectorError, make_video


class _LockedCollector(CDPCollector):
    domain_pattern = "example.com"
    default_keywords = ["test"]
    content_hash_prefix = "test"
    active = 0
    max_active = 0
    state_lock = threading.Lock()

    def _resolve_target(self) -> str:
        self._target_id = "target"
        return self._target_id

    def _search(self, keyword: str) -> list[dict]:
        with self.state_lock:
            type(self).active += 1
            type(self).max_active = max(type(self).max_active, type(self).active)
        time.sleep(0.03)
        with self.state_lock:
            type(self).active -= 1
        return [{"id": self.topic, "title": self.topic}]

    def _map_item(self, item: dict, keyword: str) -> dict:
        return make_video(
            platform="test",
            platform_video_id=item["id"],
            title=item["title"],
            content_hash_prefix=self.content_hash_prefix,
            topic=self.topic,
        )


class _EmptyCollector(_LockedCollector):
    domain_pattern = "empty.example.com"

    def _search(self, keyword: str) -> list[dict]:
        return []


def test_same_domain_collectors_are_serialized():
    _LockedCollector.active = 0
    _LockedCollector.max_active = 0
    collectors = [_LockedCollector(topic="funny"), _LockedCollector(topic="ai")]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda c: c.collect(), collectors))
    assert all(len(videos) == 1 for videos in results)
    assert _LockedCollector.max_active == 1


def test_empty_cdp_result_is_failure():
    with pytest.raises(CollectorError, match="均未采集到结果"):
        _EmptyCollector(topic="funny").collect()
