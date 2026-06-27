"""抖音搞笑视频采集器（CDP 复用浏览器登录态）。

通过 CDP Proxy 注入 JS 至已登录的 douyin.com 标签页，
调用推荐流接口（/tab/feed/）采集视频，由 tagging 流水线再筛是否搞笑。
不走搜索接口——抖音搜索有人机验证，推荐流无此限制。
"""
import json
from datetime import datetime, timezone

import requests

from utils.errors import CDPConnectionError, CollectorError, LoginExpiredError
from utils.http import retry_session
from utils.log import get_logger

logger = get_logger(__name__)

_DEFAULT_CDP = "http://localhost:3456"
_FEED_URL = "https://www.douyin.com/aweme/v1/web/tab/feed/?count=20&refresh_index=1&video_type_select=1"


class DouyinCollector:
    """抖音 CDP 采集器，拉推荐流并映射为 video dict。"""

    def __init__(self, cdp_proxy: str = _DEFAULT_CDP):
        self.cdp_proxy = cdp_proxy
        self._session = retry_session()
        self._target_id: str = ""

    def _resolve_target(self) -> str:
        """在 CDP targets 中定位 douyin.com 标签页。"""
        try:
            resp = self._session.get(f"{self.cdp_proxy}/targets", timeout=10)
            resp.raise_for_status()
            targets = resp.json()
        except (requests.RequestException, ValueError) as e:
            raise CDPConnectionError(f"获取 CDP targets 失败: {e}") from e

        for t in targets:
            if "douyin.com" in t.get("url", ""):
                self._target_id = t["targetId"]
                logger.info("已定位 douyin tab: %s", self._target_id)
                return self._target_id

        raise LoginExpiredError(
            "未找到 douyin.com 标签页。请在 Chrome 打开 https://www.douyin.com 登录后重试"
        )

    def fetch_funny_videos(self, batches: int = 3) -> list[dict]:
        """拉取多批推荐流，返回去重后的 video dict 列表。

        Args:
            batches: 拉取批次，每批 20 条，默认 3 批 = 约 60 条候选
        """
        if not self._target_id:
            self._resolve_target()

        seen: set[str] = set()
        results: list[dict] = []

        for i in range(batches):
            try:
                videos = self._fetch_feed()
                new_videos = [v for v in videos if v["content_hash"] not in seen]
                for v in new_videos:
                    seen.add(v["content_hash"])
                results.extend(new_videos)
                logger.info("抖音推荐流第 %d 批: %d 条（去重后）", i + 1, len(new_videos))
            except LoginExpiredError:
                raise
            except CollectorError as e:
                logger.warning("抖音推荐流第 %d 批失败: %s", i + 1, e)
                break

        logger.info("抖音采集完成，共 %d 条", len(results))
        return results

    def _fetch_feed(self) -> list[dict]:
        """通过 CDP 在浏览器内拉一批推荐流。"""
        js_code = f"""
(() => {{
    const xhr = new XMLHttpRequest();
    xhr.open('GET', '{_FEED_URL}', false);
    xhr.setRequestHeader('Referer', 'https://www.douyin.com/');
    xhr.send();
    return JSON.parse(xhr.responseText);
}})()
"""
        try:
            resp = self._session.post(
                f"{self.cdp_proxy}/eval?target={self._target_id}",
                data=js_code.encode("utf-8"),
                timeout=15,
            )
            resp.raise_for_status()
            raw_data = resp.json().get("value", {})
        except (requests.RequestException, json.JSONDecodeError) as e:
            raise CollectorError(f"抖音推荐流 CDP 请求失败: {e}") from e

        if not raw_data:
            return []

        status_code = raw_data.get("status_code", 0)
        if status_code != 0:
            msg = raw_data.get("status_msg", "")
            if "login" in str(msg).lower() or str(status_code) in ("2483", "8", "9"):
                raise LoginExpiredError(f"抖音登录态过期: status_code={status_code}")
            raise CollectorError(f"抖音推荐流返回非 0: status_code={status_code} msg={msg}")

        return [v for aweme in (raw_data.get("aweme_list") or []) if (v := _map_video(aweme))]


def _map_video(aweme: dict) -> dict | None:
    """把抖音 aweme_info 映射为 videos 表字段。"""
    aweme_id = aweme.get("aweme_id", "")
    if not aweme_id:
        return None

    author = aweme.get("author", {})
    video_info = aweme.get("video", {}) or {}
    stats = aweme.get("statistics", {}) or {}
    now = datetime.now(timezone.utc).isoformat()

    # 过滤 <3s（广告/无内容）
    duration_ms = video_info.get("duration", 0) or 0
    if duration_ms and duration_ms < 3000:
        return None

    cover_list = (video_info.get("cover") or {}).get("url_list") or []
    cover_url = cover_list[0] if cover_list else ""

    title = aweme.get("desc") or aweme.get("preview_title") or ""
    if not title.strip():
        return None

    return {
        "platform": "douyin",
        "platform_video_id": aweme_id,
        "title": title,
        "author": author.get("nickname", ""),
        "author_id": str(author.get("uid") or author.get("sec_uid", "")),
        "cover_url": cover_url,
        "page_url": f"https://www.douyin.com/video/{aweme_id}",
        "embed_url": None,
        "play_url": None,
        "duration": duration_ms // 1000 if duration_ms else None,
        "play_count": stats.get("play_count"),
        "like_count": stats.get("digg_count"),
        "category": None,
        "tags": None,
        "funny_score": None,
        "extra": {
            "comment_count": stats.get("comment_count"),
            "share_count": stats.get("share_count"),
        },
        "content_hash": f"douyin:{aweme_id}",
        "status": "active",
        "fetched_at": now,
        "created_at": now,
    }


def fetch_popular(pages: int | None = None) -> list[dict]:
    """模块级入口，供 run.py 统一调用。"""
    collector = DouyinCollector()
    return collector.fetch_funny_videos()
