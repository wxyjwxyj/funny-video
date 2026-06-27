"""B站 AI 资讯视频采集器（CDP DOM 抓取）。

B站搜索 API 需要 wbi 签名，改为 CDP 导航搜索页 + DOM 提取卡片。
复用已登录的 bilibili.com 浏览器标签页。
"""
import time
import urllib.parse
from datetime import datetime, timezone

import requests

from utils.errors import CDPConnectionError, CollectorError, LoginExpiredError
from utils.http import retry_session
from utils.log import get_logger

logger = get_logger(__name__)

_KEYWORDS = [
    "AI大模型", "DeepSeek", "Kimi AI", "Claude AI",
    "可灵AI", "Vidu AI", "即梦AI",
    "人工智能教程", "大模型应用",
]
_PER_KEYWORD = 20   # 每关键词最多取20条
_PAGE_WAIT = 3.0
_REQUEST_DELAY = 1.5
_DEFAULT_CDP = "http://localhost:3456"

# 提取卡片的 JS（单行，避免多行解析问题）
_EXTRACT_JS = (
    "(function(){"
    "var cards=document.querySelectorAll('.bili-video-card__wrap');"
    "var results=[];"
    "for(var i=0;i<cards.length;i++){"
    "var c=cards[i];"
    "var a=c.querySelector('a');"
    "var href=a?a.href:'';"
    "var m=href.match(/video.(BV[A-Za-z0-9]+)/);"
    "var bvid=m?m[1]:'';"
    "var titleEl=c.querySelector('.bili-video-card__info--tit');"
    "var title=titleEl?(titleEl.title||titleEl.textContent.trim()):'';"
    "var authorEl=c.querySelector('.bili-video-card__info--author');"
    "var author=authorEl?authorEl.textContent.trim():'';"
    "var img=c.querySelector('img');"
    "var cover=img?img.src:'';"
    "var stats=c.querySelectorAll('.bili-video-card__stats--item span');"
    "var play=stats[0]?stats[0].textContent.trim():'';"
    "if(bvid)results.push({bvid:bvid,title:title,author:author,cover:cover,play:play});"
    "}"
    "return results;"
    "})()"
)


def _parse_play(text: str) -> int | None:
    """把 '13.4万' 或 '1234' 转为整数。"""
    if not text:
        return None
    text = text.replace(",", "").strip()
    try:
        if "万" in text:
            return int(float(text.replace("万", "")) * 10000)
        return int(text)
    except ValueError:
        return None


class BilibiliAICollector:

    def __init__(self, cdp_proxy: str = _DEFAULT_CDP):
        self.cdp_proxy = cdp_proxy
        self._session = requests.Session()
        self._target_id: str = ""

    def _resolve_target(self) -> str:
        try:
            targets = self._session.get(f"{self.cdp_proxy}/targets", timeout=10).json()
        except (requests.RequestException, ValueError) as e:
            raise CDPConnectionError(f"获取 CDP targets 失败: {e}") from e

        for t in targets:
            if "bilibili.com" in t.get("url", ""):
                self._target_id = t["targetId"]
                logger.info("已定位 bilibili tab: %s", self._target_id)
                return self._target_id

        raise LoginExpiredError("未找到 bilibili.com 标签页，请在 Chrome 打开并登录")

    def fetch_ai_videos(self) -> list[dict]:
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
                logger.info("B站AI搜索 [%s]: %d 条", kw, len(new))
            except LoginExpiredError:
                raise
            except CollectorError as e:
                logger.warning("B站AI搜索 [%s] 失败: %s", kw, e)

        logger.info("B站AI采集完成，共 %d 条", len(results))
        return results

    def _search(self, keyword: str) -> list[dict]:
        encoded = urllib.parse.quote(keyword)
        url = f"https://search.bilibili.com/all?keyword={encoded}&search_type=video"
        nav_js = f"window.location.href='{url}'; 'ok'"

        try:
            self._session.post(
                f"{self.cdp_proxy}/eval?target={self._target_id}",
                data=nav_js.encode(), timeout=10,
            )
        except requests.RequestException as e:
            raise CollectorError(f"B站导航失败: {e}") from e

        time.sleep(_PAGE_WAIT)

        try:
            resp = self._session.post(
                f"{self.cdp_proxy}/eval?target={self._target_id}",
                data=_EXTRACT_JS.encode(), timeout=15,
            )
            resp.raise_for_status()
            items = resp.json().get("value") or []
        except Exception as e:
            raise CollectorError(f"B站DOM提取失败: {e}") from e

        return [v for item in items[:_PER_KEYWORD] if (v := _map_video(item, keyword))]


def _map_video(item: dict, keyword: str) -> dict | None:
    bvid = item.get("bvid", "")
    title = item.get("title", "").strip()
    if not bvid or not title:
        return None

    cover = item.get("cover", "")
    if cover.startswith("//"):
        cover = "https:" + cover

    now = datetime.now(timezone.utc).isoformat()
    return {
        "topic": "ai",
        "platform": "bilibili",
        "platform_video_id": bvid,
        "title": title,
        "author": item.get("author", ""),
        "author_id": "",
        "cover_url": cover,
        "page_url": f"https://www.bilibili.com/video/{bvid}",
        "embed_url": f"https://player.bilibili.com/player.html?bvid={bvid}&autoplay=0",
        "play_url": None,
        "duration": None,
        "play_count": _parse_play(item.get("play", "")),
        "like_count": None,
        "category": None,
        "tags": None,
        "funny_score": None,
        "extra": {"search_keyword": keyword},
        "content_hash": f"bilibili_ai:{bvid}",
        "status": "active",
        "fetched_at": now,
        "created_at": now,
    }


def fetch_ai_videos(keywords: list[str] | None = None) -> list[dict]:
    """模块级入口，供 run_ai.py 调用。"""
    collector = BilibiliAICollector()
    return collector.fetch_ai_videos()
