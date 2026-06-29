"""抖音视频采集器（CDP 复用浏览器登录态）。"""
import json
import re
import uuid

from collectors.base import (
    CDPCollector, CollectorError, LoginExpiredError,
    make_video, register_collector,
)
from utils.log import get_logger

logger = get_logger(__name__)


@register_collector("douyin_search")
class DouyinCollector(CDPCollector):
    """抖音搜索 CDP 采集器。"""

    domain_pattern = "douyin.com"
    default_keywords = ["搞笑", "沙雕", "鬼畜"]
    per_keyword = 15
    request_delay = 3.5
    page_wait = 0
    content_hash_prefix = "douyin"
    keywords: list[str] = []

    def _search(self, keyword: str) -> list[dict]:
        kw_js = json.dumps(keyword, ensure_ascii=False)
        sid = str(uuid.uuid4())
        js = (
            f"(function(){{"
            f"var kw=encodeURIComponent({kw_js});"
            f"var url='https://www.douyin.com/aweme/v1/web/search/item/'"
            f"+'?keyword='+kw+'&count={self.per_keyword}&offset=0'"
            f"+'&search_id={sid}&search_source=normal_search&is_filter_search=0&aid=6383';"
            f"var xhr=new XMLHttpRequest();"
            f"xhr.open('GET',url,false);"
            f"xhr.setRequestHeader('Referer','https://www.douyin.com/');"
            f"xhr.send();return JSON.parse(xhr.responseText);"
            f"}})()"
        )
        raw = self._eval(js, timeout=15) or {}
        code = raw.get("status_code", 0)
        if code != 0:
            nil = (raw.get("search_nil_info") or {}).get("search_nil_type", "")
            if nil == "verify_check" or code in (2483, 8, 9):
                raise LoginExpiredError(f"抖音触发人机验证 (code={code})")
            raise CollectorError(f"抖音搜索返回 status_code={code}")
        # 新版 API 把视频信息嵌套在 aweme_info 里，提取到顶层兼容 _map_item
        items = raw.get("data") or []
        return [{**item.get("aweme_info", {}), **{k: v for k, v in item.items() if k != "aweme_info"}} for item in items]

    def _map_item(self, item: dict, keyword: str) -> dict | None:
        aweme_id = item.get("aweme_id", "")
        if not aweme_id:
            return None
        video_info = item.get("video") or {}
        duration_ms = video_info.get("duration") or 0
        if duration_ms and duration_ms < 3000:
            return None
        author = item.get("author") or {}
        stats = item.get("statistics") or {}
        title = item.get("desc") or item.get("preview_title") or ""
        if not title.strip():
            return None
        # 去掉hashtag后没有实质内容的过滤掉（如 "#搞笑配音" 无法显示给用户）
        if len(re.sub(r'#\S+', '', title).strip()) < 3:
            return None
        covers = (video_info.get("cover") or {}).get("url_list") or []
        return make_video(
            platform="douyin", platform_video_id=aweme_id,
            content_hash_prefix="douyin", topic=self.topic,
            title=title, author=author.get("nickname", ""),
            author_id=str(author.get("uid") or author.get("sec_uid", "")),
            cover_url=covers[0] if covers else "",
            duration=duration_ms // 1000 if duration_ms else None,
            play_count=stats.get("play_count"),
            like_count=stats.get("digg_count"),
            page_url=f"https://www.douyin.com/video/{aweme_id}",
            extra={"comment_count": stats.get("comment_count"),
                   "share_count": stats.get("share_count"),
                   "search_keyword": keyword},
        )


# ── 兼容旧入口 ─────────────────────────────────────────────────
def fetch_popular(pages: int | None = None, keywords: list[str] | None = None,
                  topic: str = "funny") -> list[dict]:
    return DouyinCollector(topic=topic, keywords=keywords or []).collect()
