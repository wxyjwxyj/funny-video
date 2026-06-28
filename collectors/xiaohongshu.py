"""小红书视频采集器（CDP DOM 抓取）。"""
import urllib.parse

from collectors.base import (
    CDPCollector, LoginExpiredError,
    make_video, register_collector,
)
from utils.log import get_logger

logger = get_logger(__name__)


@register_collector("xiaohongshu_search")
class XiaohongshuCollector(CDPCollector):
    """小红书搜索 CDP 采集器。"""

    domain_pattern = "xiaohongshu.com"
    default_keywords = ["搞笑", "沙雕"]
    per_keyword = 20
    request_delay = 2.0
    page_wait = 3.0
    content_hash_prefix = "xiaohongshu"
    keywords: list[str] = []

    _EXTRACT_JS = (
        "Array.from(document.querySelectorAll('section.note-item')).map("
        "function(c){"
        "var a=c.querySelector('a.cover');var href=a?a.href:'';"
        "var seg=href.split('/search_result/')[1]||href.split('/explore/')[1]||'';"
        "var noteId=seg?seg.split('?')[0]:'';"
        "var img=c.querySelector('img');"
        "var t=c.querySelector('.title span');"
        "var auth=c.querySelector('.author-wrapper .name');"
        "var lk=c.querySelector('.count');"
        "return {noteId:noteId,pageUrl:href,"
        "title:t?t.textContent.trim():'',"
        "author:auth?auth.textContent.trim():'',"
        "likes:lk?lk.textContent.trim():'',"
        "cover:img?img.src:''};"
        "}).filter(function(x){return x.noteId&&x.title;})"
    )

    def _search(self, keyword: str) -> list[dict]:
        encoded = urllib.parse.quote(keyword)
        url = (f"https://www.xiaohongshu.com/search_result"
               f"?keyword={encoded}&source=web_search_result_notes&type=51")
        self._navigate(url)
        items = self._eval(self._EXTRACT_JS, timeout=15) or []
        if not items:
            url_check = self._eval("location.href", timeout=5) or ""
            if "login" in str(url_check) or "signin" in str(url_check):
                raise LoginExpiredError("小红书登录态过期，请在浏览器重新登录")
        return items[:self.per_keyword]

    def _map_item(self, item: dict, keyword: str) -> dict | None:
        note_id = item.get("noteId", "")
        title = item.get("title", "").strip()
        if not note_id or not title:
            return None

        like_count = None
        likes_str = item.get("likes", "").replace(",", "")
        if likes_str:
            try:
                like_count = (int(float(likes_str.replace("万", "")) * 10000)
                              if "万" in likes_str else int(likes_str))
            except ValueError:
                pass

        return make_video(
            platform="xiaohongshu", platform_video_id=note_id,
            content_hash_prefix="xiaohongshu", topic=self.topic,
            title=title, author=item.get("author", ""),
            cover_url=item.get("cover", ""), like_count=like_count,
            page_url=f"https://www.xiaohongshu.com/search_result?keyword={urllib.parse.quote(title)}&type=51",
            extra={"search_keyword": keyword},
        )


# ── 兼容旧入口 ─────────────────────────────────────────────────
def fetch_popular(pages: int | None = None, keywords: list[str] | None = None,
                  topic: str = "funny") -> list[dict]:
    return XiaohongshuCollector(topic=topic, keywords=keywords or []).collect()
