"""小红书搞笑视频采集器（CDP DOM 抓取）。

小红书搜索 API 需要 x-s/x-t 签名，直接调用会被拒。
改为：CDP 导航到搜索结果页 → 等页面渲染 → 从 DOM 提取笔记卡片数据。
只取视频笔记（封面链接含 /search_result/ 的卡片）。
"""
import time
import urllib.parse
from datetime import datetime, timezone

import requests

from utils.errors import CDPConnectionError, CollectorError, LoginExpiredError
from utils.http import retry_session
from utils.log import get_logger

logger = get_logger(__name__)

_KEYWORDS = ["搞笑", "沙雕"]
_PER_KEYWORD = 20
_PAGE_WAIT = 3.0        # 搜索页渲染等待秒数
_REQUEST_DELAY = 2.0    # 关键词间隔
_DEFAULT_CDP = "http://localhost:3456"

# 单行 JS：从 DOM 提取笔记卡片（避免多行/optional-chaining 解析问题）
_EXTRACT_JS = (
    "Array.from(document.querySelectorAll('section.note-item')).map("
    "function(c){"
    "var a=c.querySelector('a.cover');"
    "var href=a?a.href:'';"
    "var seg=href.split('/search_result/')[1]||href.split('/explore/')[1]||'';"
    "var noteId=seg?seg.split('?')[0]:'';"
    "var img=c.querySelector('img');"
    "var t=c.querySelector('.title span');"
    "var auth=c.querySelector('.author-wrapper .name');"
    "var lk=c.querySelector('.count');"
    "return {noteId:noteId,"
    "pageUrl:href,"
    "title:t?t.textContent.trim():'',"
    "author:auth?auth.textContent.trim():'',"
    "likes:lk?lk.textContent.trim():'',"
    "cover:img?img.src:''};"
    "}).filter(function(x){return x.noteId&&x.title;})"
)


class XiaohongshuCollector:

    def __init__(self, cdp_proxy: str = _DEFAULT_CDP):
        self.cdp_proxy = cdp_proxy
        self._session = retry_session()
        self._target_id: str = ""

    def _resolve_target(self) -> str:
        try:
            targets = self._session.get(f"{self.cdp_proxy}/targets", timeout=10).json()
        except (requests.RequestException, ValueError) as e:
            raise CDPConnectionError(f"获取 CDP targets 失败: {e}") from e

        for t in targets:
            if "xiaohongshu.com" in t.get("url", ""):
                self._target_id = t["targetId"]
                logger.info("已定位 小红书 tab: %s", self._target_id)
                return self._target_id

        raise LoginExpiredError("未找到 xiaohongshu.com 标签页，请在 Chrome 打开并登录")

    def fetch_funny_videos(self) -> list[dict]:
        if not self._target_id:
            self._resolve_target()

        seen: set[str] = set()
        results: list[dict] = []

        for i, kw in enumerate(_KEYWORDS):
            if i > 0:
                time.sleep(_REQUEST_DELAY)
            try:
                videos = self._search(kw)
                new = [v for v in videos if v["content_hash"] not in seen]
                seen.update(v["content_hash"] for v in new)
                results.extend(new)
                logger.info("小红书搜索 [%s]: %d 条", kw, len(new))
            except LoginExpiredError:
                raise
            except CollectorError as e:
                logger.warning("小红书搜索 [%s] 失败: %s", kw, e)

        logger.info("小红书采集完成，共 %d 条", len(results))
        return results

    def _search(self, keyword: str) -> list[dict]:
        """导航到搜索页，等待渲染，从 DOM 提取笔记卡片。"""
        import urllib.parse
        encoded = urllib.parse.quote(keyword)
        url = f"https://www.xiaohongshu.com/search_result?keyword={encoded}&source=web_search_result_notes&type=51"

        # 导航
        nav_js = f"window.location.href='{url}'; 'ok'"
        try:
            self._session.post(
                f"{self.cdp_proxy}/eval?target={self._target_id}",
                data=nav_js.encode(), timeout=10,
            )
        except requests.RequestException as e:
            raise CollectorError(f"小红书导航失败: {e}") from e

        time.sleep(_PAGE_WAIT)

        # 提取卡片
        try:
            resp = self._session.post(
                f"{self.cdp_proxy}/eval?target={self._target_id}",
                data=_EXTRACT_JS.encode(), timeout=15,
            )
            resp.raise_for_status()
            items = resp.json().get("value") or []
        except (requests.RequestException, Exception) as e:
            raise CollectorError(f"小红书 DOM 提取失败: {e}") from e

        if not isinstance(items, list):
            raise CollectorError(f"小红书 DOM 返回格式异常: {items}")

        # 检查是否被踢出登录
        if not items:
            url_check = self._session.post(
                f"{self.cdp_proxy}/eval?target={self._target_id}",
                data=b"location.href", timeout=5,
            ).json().get("value", "")
            if "login" in url_check or "signin" in url_check:
                raise LoginExpiredError("小红书登录态过期，请在浏览器重新登录")

        return [v for item in items if (v := _map_video(item, keyword))]


def _map_video(item: dict, keyword: str = "") -> dict | None:
    note_id = item.get("noteId", "")
    title = item.get("title", "").strip()
    if not note_id or not title:
        return None

    # 把"1.2万"转为整数
    likes_str = item.get("likes", "").replace(",", "")
    like_count = None
    if likes_str:
        try:
            like_count = int(float(likes_str.replace("万", "")) * 10000) if "万" in likes_str else int(likes_str)
        except ValueError:
            pass

    now = datetime.now(timezone.utc).isoformat()
    return {
        "platform": "xiaohongshu",
        "platform_video_id": note_id,
        "title": title,
        "author": item.get("author", ""),
        "author_id": "",
        "cover_url": item.get("cover", ""),
        "page_url": f"https://www.xiaohongshu.com/search_result?keyword={urllib.parse.quote(title)}&type=51",
        "embed_url": None,
        "play_url": None,
        "duration": None,
        "play_count": None,
        "like_count": like_count,
        "category": None,
        "tags": None,
        "funny_score": None,
        "extra": {"search_keyword": keyword},
        "content_hash": f"xiaohongshu:{note_id}",
        "status": "active",
        "fetched_at": now,
        "created_at": now,
    }


def fetch_popular(pages: int | None = None) -> list[dict]:
    return XiaohongshuCollector().fetch_funny_videos()
