"""小红书搞笑视频采集器（CDP 复用浏览器登录态）。

通过 CDP Proxy 注入 JS 至已登录的 xiaohongshu.com 标签页，
调用小红书搜索 API 搜索搞笑关键词，过滤视频笔记，返回 raw video dict 列表。
"""
import json
import uuid
from datetime import datetime, timezone

import requests

from utils.errors import CDPConnectionError, CollectorError, LoginExpiredError
from utils.http import retry_session
from utils.log import get_logger

logger = get_logger(__name__)

_SEARCH_KEYWORDS = ["搞笑", "沙雕", "搞笑视频"]
_DEFAULT_CDP = "http://localhost:3456"


class XiaohongshuCollector:
    """小红书 CDP 采集器，搜索搞笑关键词并过滤视频笔记。"""

    def __init__(self, cdp_proxy: str = _DEFAULT_CDP):
        self.cdp_proxy = cdp_proxy
        self._session = retry_session()
        self._target_id: str = ""

    def _resolve_target(self) -> str:
        """在 CDP targets 中定位 xiaohongshu.com 标签页。"""
        try:
            resp = self._session.get(f"{self.cdp_proxy}/targets", timeout=10)
            resp.raise_for_status()
            targets = resp.json()
        except (requests.RequestException, ValueError) as e:
            raise CDPConnectionError(f"获取 CDP targets 失败: {e}") from e

        for t in targets:
            url = t.get("url", "")
            if "xiaohongshu.com" in url or "xhslink.com" in url:
                self._target_id = t["targetId"]
                logger.info("已定位 小红书 tab: %s", self._target_id)
                return self._target_id

        raise LoginExpiredError(
            "未找到 xiaohongshu.com 标签页。请在 Chrome 打开 https://www.xiaohongshu.com 登录后重试"
        )

    def fetch_funny_videos(self, per_keyword: int = 20) -> list[dict]:
        """多关键词搜索，返回去重后的 video dict 列表。"""
        if not self._target_id:
            self._resolve_target()

        seen: set[str] = set()
        results: list[dict] = []

        for kw in _SEARCH_KEYWORDS:
            try:
                videos = self._search_keyword(kw, count=per_keyword)
                new_videos = [v for v in videos if v["content_hash"] not in seen]
                for v in new_videos:
                    seen.add(v["content_hash"])
                results.extend(new_videos)
                logger.info("小红书搜索 [%s]: %d 条（去重后）", kw, len(new_videos))
            except LoginExpiredError:
                raise
            except CollectorError as e:
                logger.warning("小红书搜索 [%s] 失败: %s", kw, e)
                continue

        logger.info("小红书采集完成，共 %d 条", len(results))
        return results

    def _search_keyword(self, keyword: str, count: int = 20) -> list[dict]:
        """通过 CDP 在浏览器内发同步 XHR 调用小红书搜索 API。"""
        search_id = str(uuid.uuid4())
        keyword_escaped = json.dumps(keyword, ensure_ascii=False)

        js_code = f"""
(() => {{
    const kw = encodeURIComponent({keyword_escaped});
    const url = 'https://www.xiaohongshu.com/web_api/sns/v1/search/notes'
        + '?keyword=' + kw
        + '&page_size={count}'
        + '&sort=general'
        + '&note_type=0'
        + '&page=1'
        + '&search_id={search_id}';
    const xhr = new XMLHttpRequest();
    xhr.open('GET', url, false);
    xhr.setRequestHeader('Referer', 'https://www.xiaohongshu.com/');
    xhr.setRequestHeader('X-Requested-With', 'XMLHttpRequest');
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
            raise CollectorError(f"小红书搜索 [{keyword}] CDP 请求失败: {e}") from e

        return self._parse_search_result(raw_data, keyword)

    def _parse_search_result(self, raw_data: dict, keyword: str) -> list[dict]:
        """解析小红书搜索 API 返回，只取视频笔记。"""
        if not raw_data:
            return []

        code = raw_data.get("code", -1)
        if code != 0:
            msg = raw_data.get("msg", "")
            if code == -100 or "login" in str(msg).lower():
                raise LoginExpiredError(
                    f"小红书登录态过期: code={code} msg={msg}，去浏览器刷新 xiaohongshu.com"
                )
            logger.warning("小红书搜索 [%s] 返回非 0: code=%s msg=%s", keyword, code, msg)
            return []

        videos: list[dict] = []
        for item in raw_data.get("data", {}).get("items", []):
            note = item.get("note_card") or item
            # 只取视频笔记（type=video）
            if note.get("type") != "video":
                continue

            v = _map_video(note, keyword)
            if v:
                videos.append(v)

        return videos


def _map_video(note: dict, keyword: str = "") -> dict | None:
    """把小红书 note_card 映射为 videos 表字段。"""
    note_id = note.get("note_id") or note.get("id", "")
    if not note_id:
        return None

    user = note.get("user", {})
    interact = note.get("interact_info", {})
    cover_info = note.get("cover", {})
    cover_url = ""
    if isinstance(cover_info, dict):
        cover_url = cover_info.get("url_default") or cover_info.get("url", "")
    now = datetime.now(timezone.utc).isoformat()

    title = note.get("display_title") or note.get("title") or ""
    if not title.strip():
        return None

    return {
        "platform": "xiaohongshu",
        "platform_video_id": note_id,
        "title": title,
        "author": user.get("nickname") or user.get("name", ""),
        "author_id": str(user.get("user_id") or user.get("id", "")),
        "cover_url": cover_url,
        "page_url": f"https://www.xiaohongshu.com/explore/{note_id}",
        "embed_url": None,  # 小红书也不给 iframe
        "play_url": None,
        "duration": note.get("video_duration"),
        "play_count": None,
        "like_count": interact.get("liked_count"),
        "category": None,
        "tags": None,
        "funny_score": None,
        "extra": {
            "comment_count": interact.get("comment_count"),
            "share_count": interact.get("share_count"),
            "collect_count": interact.get("collected_count"),
            "search_keyword": keyword,
        },
        "content_hash": f"xiaohongshu:{note_id}",
        "status": "active",
        "fetched_at": now,
        "created_at": now,
    }


def fetch_popular(pages: int | None = None) -> list[dict]:
    """模块级入口，供 run.py 统一调用。

    Args:
        pages: 保留参数兼容接口，小红书采集不使用（关键词驱动）
    """
    collector = XiaohongshuCollector()
    return collector.fetch_funny_videos()
