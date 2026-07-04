"""小红书视频采集器（CDP DOM 抓取）。"""
import re
import urllib.parse
from datetime import datetime, timedelta, timezone

from collectors.base import (
    CDPCollector, LoginExpiredError,
    make_video, register_collector,
)
from utils.log import get_logger

logger = get_logger(__name__)


def _parse_xhs_time(text: str) -> str | None:
    """将小红书 DOM 里的时间文字转成 ISO8601 UTC 字符串。

    支持格式：
      - "05-12"        → 当年5月12日（如已过则当年，否则上一年）
      - "昨天 15:45"   → 昨天
      - "刚刚"         → 当前时刻
      - "X小时前"      → X小时前
      - "X天前"        → X天前
      - "2026-07-04"   → 完整日期
    """
    if not text:
        return None
    text = text.strip()
    now = datetime.now(timezone.utc)

    # 完整日期 YYYY-MM-DD
    m = re.match(r'^(\d{4})-(\d{2})-(\d{2})$', text)
    if m:
        try:
            return datetime(int(m[1]), int(m[2]), int(m[3]), tzinfo=timezone.utc).isoformat()
        except ValueError:
            pass

    # 月-日 MM-DD（不带年份）
    m = re.match(r'^(\d{1,2})-(\d{2})$', text)
    if m:
        month, day = int(m[1]), int(m[2])
        year = now.year
        try:
            dt = datetime(year, month, day, tzinfo=timezone.utc)
            # 如果推算日期比今天还晚，说明是去年的
            if dt > now:
                dt = dt.replace(year=year - 1)
            return dt.isoformat()
        except ValueError:
            pass

    # 昨天 HH:MM
    m = re.match(r'^昨天\s*(\d{1,2}):(\d{2})$', text)
    if m:
        yesterday = now - timedelta(days=1)
        try:
            dt = yesterday.replace(hour=int(m[1]), minute=int(m[2]), second=0, microsecond=0)
            return dt.isoformat()
        except ValueError:
            pass

    # 刚刚
    if text == "刚刚":
        return now.isoformat()

    # X小时前
    m = re.match(r'^(\d+)小时前$', text)
    if m:
        return (now - timedelta(hours=int(m[1]))).isoformat()

    # X天前
    m = re.match(r'^(\d+)天前$', text)
    if m:
        return (now - timedelta(days=int(m[1]))).isoformat()

    return None


@register_collector("xiaohongshu_search")
class XiaohongshuCollector(CDPCollector):
    """小红书搜索 CDP 采集器。"""

    domain_pattern = "xiaohongshu.com"
    default_keywords = ["搞笑", "沙雕"]
    per_keyword = 10
    request_delay = 3.5
    page_wait = 4.0
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
        "var timeEl=c.querySelector('div.time');"
        "return {noteId:noteId,pageUrl:href,"
        "title:t?t.textContent.trim():'',"
        "author:auth?auth.textContent.trim():'',"
        "likes:lk?lk.textContent.trim():'',"
        "pubTime:timeEl?timeEl.textContent.trim():'',"
        "cover:img?img.src:''};"
        "}).filter(function(x){return x.noteId&&x.title;})"
    )

    def _search(self, keyword: str) -> list[dict]:
        encoded = urllib.parse.quote(keyword)
        # sort=time_desc 按发布时间倒序，优先抓最新内容
        url = (f"https://www.xiaohongshu.com/search_result"
               f"?keyword={encoded}&source=web_search_result_notes&type=51&sort=time_desc")
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

        published_at = _parse_xhs_time(item.get("pubTime", ""))

        return make_video(
            platform="xiaohongshu", platform_video_id=note_id,
            content_hash_prefix="xiaohongshu", topic=self.topic,
            title=title, author=item.get("author", ""),
            cover_url=item.get("cover", ""), like_count=like_count,
            page_url=item.get("pageUrl", ""),
            published_at=published_at,
            extra={"search_keyword": keyword},
        )


# ── 兼容旧入口 ─────────────────────────────────────────────────
def fetch_popular(pages: int | None = None, keywords: list[str] | None = None,
                  topic: str = "funny") -> list[dict]:
    return XiaohongshuCollector(topic=topic, keywords=keywords or []).collect()
