"""B站视频采集器 — API热门 + CDP搜索。"""
import urllib.parse

import requests

from collectors.base import (
    BaseCollector, CDPCollector, CollectorError, LoginExpiredError,
    _ts_to_iso, make_video, register_collector, create_collector,
)
from utils.http import retry_session
from utils.log import get_logger

logger = get_logger(__name__)

# ── 类目白名单（搞笑墙用） ──────────────────────────────────────
FUNNY_CATEGORIES = frozenset({
    "搞笑", "鬼畜剧场", "日常", "小剧场", "娱乐杂谈", "明星综合",
    "综艺", "美食制作", "美食记录", "美食测评", "美食侦探",
    "喵星人", "汪星人", "动物综合", "动物二创",
    "手工", "绘画", "同人·手书",
    "MAD·AMV", "人力VOCALOID", "GMV", "宅舞", "乐评盘点",
})

_BASE_URL = "https://api.bilibili.com/x/web-interface/popular"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": "https://www.bilibili.com",
}


# ═══════════════════════════════════════════════════════════════════
# 公开热门 API 采集器
# ═══════════════════════════════════════════════════════════════════

@register_collector("bilibili_popular")
class BilibiliPopularCollector(BaseCollector):
    """B站综合热门，调用公开 API，免登录。"""

    pages: int = 5
    page_size: int = 20
    categories: frozenset[str] | None = None  # None=全收
    content_hash_prefix: str = "bilibili"     # 允许 registry 覆盖为 topic 前缀

    def collect(self) -> list[dict]:
        session = retry_session()
        results: list[dict] = []
        skipped = 0

        for pn in range(1, self.pages + 1):
            try:
                resp = session.get(_BASE_URL, params={"ps": self.page_size, "pn": pn},
                                   headers=_HEADERS, timeout=10)
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                logger.warning("B站热门第 %d 页请求失败: %s", pn, e)
                continue

            if data.get("code") != 0:
                logger.warning("B站热门第 %d 页 API 报错: code=%s msg=%s", pn, data.get("code"), data.get("message"))
                continue

            items = data.get("data", {}).get("list", [])
            page_videos = [v for item in items if (v := self._map(item))]
            results.extend(page_videos)
            skipped += len(items) - len(page_videos)
            logger.info("B站热门第 %d 页: %d/%d 条", pn, len(page_videos), len(items))

        logger.info("B站热门采集完成，共 %d 条（跳过 %d 条）", len(results), skipped)
        return results

    def _map(self, item: dict) -> dict | None:
        if item.get("is_ogv") or not item.get("bvid"):
            return None
        tname = item.get("tname", "")
        if self.categories is not None and tname not in self.categories:
            return None

        bvid = item["bvid"]
        stat = item.get("stat", {})
        owner = item.get("owner", {})

        # pubdate 是 Unix 时间戳，转为 ISO8601 存储
        v = make_video(
            platform="bilibili", platform_video_id=bvid,
            content_hash_prefix=self.content_hash_prefix or "bilibili", topic=self.topic,
            title=item.get("title", ""),
            author=owner.get("name", ""),
            author_id=str(owner.get("mid", "")),
            cover_url=item.get("pic", ""),
            duration=item.get("duration"),
            play_count=stat.get("view"),
            like_count=stat.get("like"),
            category=tname,
            page_url=f"https://www.bilibili.com/video/{bvid}",
            embed_url=f"https://player.bilibili.com/player.html?bvid={bvid}&autoplay=0",
            published_at=_ts_to_iso(item.get("pubdate")),
            extra={
                "coin": stat.get("coin"),
                "favorite": stat.get("favorite"),
                "danmaku": stat.get("danmaku"),
                "rcmd_reason": item.get("rcmd_reason", {}).get("content", ""),
            },
        )
        v["content_hash"] = f"{self.content_hash_prefix or 'bilibili'}:{bvid}"
        return v


# ═══════════════════════════════════════════════════════════════════
# CDP 搜索采集器（AI 视频墙等用）
# ═══════════════════════════════════════════════════════════════════

@register_collector("bilibili_search")
class BilibiliSearchCollector(CDPCollector):
    """B站搜索 CDP 采集器。keywords + 最新排序。"""

    domain_pattern = "bilibili.com"
    default_keywords = ["DeepSeek", "Kimi", "通义千问", "ChatGPT", "Claude", "Gemini",
                        "AI Agent", "AI工具", "AI视频", "AI绘画", "大模型"]
    per_keyword = 5
    request_delay = 3.0
    page_wait = 3.0
    content_hash_prefix = "bilibili_search"
    keywords: list[str] = []  # 运行时覆盖

    _EXTRACT_JS = (
        "(function(){"
        "var cards=document.querySelectorAll('.bili-video-card__wrap');"
        "var results=[];"
        "for(var i=0;i<cards.length;i++){"
        "var c=cards[i];var a=c.querySelector('a');var href=a?a.href:'';"
        "var m=href.match(/video.(BV[A-Za-z0-9]+)/);var bvid=m?m[1]:'';"
        "var t=c.querySelector('.bili-video-card__info--tit');"
        "var title=t?(t.title||t.textContent.trim()):'';"
        "var au=c.querySelector('.bili-video-card__info--author');"
        "var author=au?au.textContent.trim():'';"
        "var img=c.querySelector('img');var cover=img?img.src:'';"
        "var st=c.querySelectorAll('.bili-video-card__stats--item span');"
        "var play=st[0]?st[0].textContent.trim():'';"
        "if(bvid)results.push({bvid:bvid,title:title,author:author,cover:cover,play:play});"
        "}return results;})()"
    )

    def _search(self, keyword: str) -> list[dict]:
        encoded = urllib.parse.quote(keyword)
        url = f"https://search.bilibili.com/all?keyword={encoded}&search_type=video&order=2"
        self._navigate(url)
        items = self._eval(self._EXTRACT_JS, timeout=15) or []
        return items[:self.per_keyword]

    def collect(self) -> list[dict]:
        """采集后批量补全 published_at / like_count 等字段（CDP DOM 无法直接获取）。"""
        results = super().collect()
        if not results:
            return results
        # 找出需要补全的视频（like_count 或 published_at 缺失）
        missing = [v["platform_video_id"] for v in results
                   if not v.get("published_at") or v.get("like_count") is None]
        if missing:
            logger.info("[BilibiliSearch] 补全视频详情: %d 条", len(missing))
            enriched = self._enrich_video_info(missing)
            for v in results:
                pid = v["platform_video_id"]
                if pid in enriched:
                    info = enriched[pid]
                    if not v.get("published_at") and info.get("published_at"):
                        v["published_at"] = info["published_at"]
                    if v.get("like_count") is None and info.get("like_count") is not None:
                        v["like_count"] = info["like_count"]
                    if not v.get("duration") and info.get("duration"):
                        v["duration"] = info["duration"]
                    if not v.get("category") and info.get("category"):
                        v["category"] = info["category"]
        return results

    def _enrich_video_info(self, bvids: list[str]) -> dict[str, dict]:
        """批量获取视频详情（发布时间/点赞数/时长/分区），使用 B站公开 API。

        失败静默忽略，返回 {bvid: {published_at, like_count, duration, category}}。
        """
        session = retry_session()
        result: dict[str, dict] = {}
        for bvid in bvids:
            try:
                resp = session.get(
                    "https://api.bilibili.com/x/web-interface/view",
                    params={"bvid": bvid},
                    headers=_HEADERS, timeout=8,
                )
                if resp.status_code == 200:
                    data = resp.json().get("data", {})
                    if resp.json().get("code") == 0 and data:
                        stat = data.get("stat", {})
                        result[bvid] = {
                            "published_at": _ts_to_iso(data.get("pubdate")),
                            "like_count":   stat.get("like"),
                            "duration":     data.get("duration"),
                            "category":     data.get("tname"),
                        }
            except Exception:
                pass
        return result

    def _map_item(self, item: dict, keyword: str) -> dict | None:
        bvid = item.get("bvid", "")
        title = item.get("title", "").strip()
        if not bvid or not title:
            return None
        cover = item.get("cover", "")
        if cover.startswith("//"):
            cover = "https:" + cover
        v = make_video(
            platform="bilibili", platform_video_id=bvid,
            content_hash_prefix=self.content_hash_prefix, topic=self.topic,
            title=title, author=item.get("author", ""), cover_url=cover,
            play_count=_parse_play(item.get("play", "")),
            page_url=f"https://www.bilibili.com/video/{bvid}",
            embed_url=f"https://player.bilibili.com/player.html?bvid={bvid}&autoplay=0",
            extra={"search_keyword": keyword},
        )
        v["content_hash"] = f"{self.content_hash_prefix}:{bvid}"
        return v


def _parse_play(text: str) -> int | None:
    if not text:
        return None
    text = text.replace(",", "").strip()
    try:
        if "万" in text:
            return int(float(text.replace("万", "")) * 10000)
        return int(text)
    except ValueError:
        return None


# ── 兼容旧入口 ─────────────────────────────────────────────────
def fetch_popular(pages: int = 5, page_size: int = 20,
                  categories: frozenset[str] | None = None) -> list[dict]:
    return BilibiliPopularCollector(pages=pages, page_size=page_size, categories=categories).collect()


def fetch_ai_videos(keywords: list[str] | None = None) -> list[dict]:
    return BilibiliSearchCollector(
        topic="ai", content_hash_prefix="bilibili_ai",
        keywords=keywords or [],
    ).collect()
