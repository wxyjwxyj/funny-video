"""抖音搞笑视频采集器（CDP 复用浏览器登录态）。

走搜索接口精准拉"搞笑/沙雕/鬼畜"，风控措施：
- 每关键词最多 10 条，总量控制在 30 以内
- 关键词之间 sleep 2s，降低触发人机验证概率
- 遇到 verify_check 立刻抛 LoginExpiredError，由调用方决策
"""
import json
import time
import uuid
from datetime import datetime, timezone

import requests

from utils.errors import CDPConnectionError, CollectorError, LoginExpiredError
from utils.http import retry_session
from utils.log import get_logger

logger = get_logger(__name__)

_KEYWORDS = ["搞笑", "沙雕", "鬼畜"]
_PER_KEYWORD = 10       # 每关键词最多取 10 条
_REQUEST_DELAY = 2.0    # 关键词间隔（秒）
_DEFAULT_CDP = "http://localhost:3456"


class DouyinCollector:

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
            if "douyin.com" in t.get("url", ""):
                self._target_id = t["targetId"]
                logger.info("已定位 douyin tab: %s", self._target_id)
                return self._target_id

        raise LoginExpiredError("未找到 douyin.com 标签页，请在 Chrome 打开并登录")

    def fetch_funny_videos(self) -> list[dict]:
        """按关键词搜索，返回去重后的 video dict 列表。"""
        if not self._target_id:
            self._resolve_target()

        seen: set[str] = set()
        results: list[dict] = []

        for i, kw in enumerate(_KEYWORDS):
            if i > 0:
                time.sleep(_REQUEST_DELAY)  # 限速，避免触发人机验证
            try:
                videos = self._search(kw)
                new = [v for v in videos if v["content_hash"] not in seen]
                seen.update(v["content_hash"] for v in new)
                results.extend(new)
                logger.info("抖音搜索 [%s]: %d 条", kw, len(new))
            except LoginExpiredError:
                raise  # 遇到验证直接抛出，由调用方处理
            except CollectorError as e:
                logger.warning("抖音搜索 [%s] 失败: %s", kw, e)

        logger.info("抖音采集完成，共 %d 条", len(results))
        return results

    def _search(self, keyword: str) -> list[dict]:
        kw_js = json.dumps(keyword, ensure_ascii=False)
        search_id = str(uuid.uuid4())
        js = f"""
(() => {{
    const kw = encodeURIComponent({kw_js});
    const url = 'https://www.douyin.com/aweme/v1/web/search/item/'
        + '?keyword=' + kw + '&count={_PER_KEYWORD}&offset=0'
        + '&search_id={search_id}&search_source=normal_search&is_filter_search=0&aid=6383';
    const xhr = new XMLHttpRequest();
    xhr.open('GET', url, false);
    xhr.setRequestHeader('Referer', 'https://www.douyin.com/');
    xhr.send();
    return JSON.parse(xhr.responseText);
}})()
"""
        try:
            raw = self._session.post(
                f"{self.cdp_proxy}/eval?target={self._target_id}",
                data=js.encode(), timeout=15,
            ).json().get("value", {})
        except (requests.RequestException, json.JSONDecodeError) as e:
            raise CollectorError(f"抖音搜索 [{keyword}] 失败: {e}") from e

        code = raw.get("status_code", 0)
        if code != 0:
            nil = (raw.get("search_nil_info") or {}).get("search_nil_type", "")
            if nil == "verify_check" or code in (2483, 8, 9):
                raise LoginExpiredError(f"抖音触发人机验证，请在浏览器完成验证后重试 (code={code})")
            raise CollectorError(f"抖音搜索返回 status_code={code}")

        return [v for item in (raw.get("data") or [])
                if (v := _map_video(item.get("aweme_info") or {}, keyword))]


def _map_video(aweme: dict, keyword: str = "") -> dict | None:
    aweme_id = aweme.get("aweme_id", "")
    if not aweme_id:
        return None

    author = aweme.get("author") or {}
    video_info = aweme.get("video") or {}
    stats = aweme.get("statistics") or {}
    duration_ms = video_info.get("duration") or 0
    if duration_ms and duration_ms < 3000:
        return None

    title = aweme.get("desc") or aweme.get("preview_title") or ""
    if not title.strip():
        return None

    cover_url = ((video_info.get("cover") or {}).get("url_list") or [""])[0]
    now = datetime.now(timezone.utc).isoformat()

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
            "search_keyword": keyword,
        },
        "content_hash": f"douyin:{aweme_id}",
        "status": "active",
        "fetched_at": now,
        "created_at": now,
    }


def fetch_popular(pages: int | None = None) -> list[dict]:
    return DouyinCollector().fetch_funny_videos()
