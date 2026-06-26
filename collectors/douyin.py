"""抖音搞笑视频采集器（CDP 复用浏览器登录态）。

通过 CDP Proxy 注入 JS 至已登录的 douyin.com 标签页，
调用抖音搜索 API 搜索搞笑关键词，返回 raw video dict 列表。
不逆向签名 —— 在浏览器上下文内发 XHR，cookie 天然正确。
"""
import json
import uuid
from datetime import datetime, timezone

import requests

from utils.errors import CDPConnectionError, CollectorError, LoginExpiredError
from utils.http import retry_session
from utils.log import get_logger

logger = get_logger(__name__)

_SEARCH_KEYWORDS = ["搞笑", "沙雕", "鬼畜"]
_DEFAULT_CDP = "http://localhost:3456"


class DouyinCollector:
    """抖音 CDP 采集器，搜索搞笑关键词并映射为 video dict。"""

    def __init__(self, cdp_proxy: str = _DEFAULT_CDP):
        self.cdp_proxy = cdp_proxy
        self._session = retry_session()
        self._target_id: str = ""

    def _resolve_target(self) -> str:
        """在 CDP targets 中定位 douyin.com 标签页。

        Raises:
            CDPConnectionError: CDP Proxy 不可达
            LoginExpiredError: 未找到 douyin tab
        """
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
                logger.info("抖音搜索 [%s]: %d 条（去重后）", kw, len(new_videos))
            except LoginExpiredError:
                raise
            except CollectorError as e:
                logger.warning("抖音搜索 [%s] 失败: %s", kw, e)
                continue

        logger.info("抖音采集完成，共 %d 条", len(results))
        return results

    def _search_keyword(self, keyword: str, count: int = 20) -> list[dict]:
        """通过 CDP 在浏览器内执行同步 XHR 调用抖音搜索 API。"""
        search_id = str(uuid.uuid4())
        keyword_escaped = json.dumps(keyword, ensure_ascii=False)  # 安全注入 JS 字符串

        js_code = f"""
(() => {{
    const kw = encodeURIComponent({keyword_escaped});
    const url = 'https://www.douyin.com/aweme/v1/web/search/item/'
        + '?keyword=' + kw
        + '&count={count}'
        + '&offset=0'
        + '&search_id={search_id}'
        + '&search_source=normal_search'
        + '&is_filter_search=0'
        + '&aid=6383';
    const xhr = new XMLHttpRequest();
    xhr.open('GET', url, false);
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
            raise CollectorError(f"抖音搜索 [{keyword}] CDP 请求失败: {e}") from e

        return self._parse_search_result(raw_data, keyword)

    def _parse_search_result(self, raw_data: dict, keyword: str) -> list[dict]:
        """解析抖音搜索 API 返回，过滤出视频并映射字段。"""
        if not raw_data:
            return []

        status_code = raw_data.get("status_code", 0)
        if status_code != 0:
            msg = raw_data.get("status_msg", "")
            # 8/9 通常表示登录态异常
            if status_code in (8, 9) or "login" in str(msg).lower():
                raise LoginExpiredError(
                    f"抖音登录态过期: status_code={status_code} msg={msg}，去浏览器刷新 douyin.com"
                )
            logger.warning("抖音搜索 [%s] 返回非 0: status_code=%s msg=%s", keyword, status_code, msg)
            return []

        videos: list[dict] = []
        for item in raw_data.get("data", []):
            # 搜索结果有两种包裹：video 类型的 item 或 aweme_info 直接存在
            aweme = item.get("aweme_info") or item
            if aweme.get("aweme_type") not in (None, 0):  # 0 = 普通视频
                continue

            v = _map_video(aweme, keyword)
            if v:
                videos.append(v)

        return videos


# ── 模块级快捷函数 ──────────────────────────────────


def _map_video(aweme: dict, keyword: str = "") -> dict | None:
    """把抖音 aweme_info / 搜索结果条目映射为 videos 表字段。"""
    aweme_id = aweme.get("aweme_id", "")
    if not aweme_id:
        return None

    author = aweme.get("author", {})
    video_info = aweme.get("video", {})
    stats = aweme.get("statistics", {})
    now = datetime.now(timezone.utc).isoformat()

    # 过滤短视频（<3s 大概率广告/无内容）
    duration_ms = video_info.get("duration", 0)
    if duration_ms and duration_ms < 3000:
        return None

    # 封面取第一张
    cover_list = video_info.get("cover", {}).get("url_list", [])
    cover_url = cover_list[0] if cover_list else ""

    # 有的返回用 desc，有的用 preview_title
    title = aweme.get("desc") or aweme.get("preview_title") or ""

    # 过滤空标题
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
        "embed_url": None,  # 抖音禁止 iframe 嵌入
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
    """模块级入口，供 run.py 统一调用。

    Args:
        pages: 保留参数兼容 B站接口，抖音采集不使用（关键词驱动）
    """
    collector = DouyinCollector()
    return collector.fetch_funny_videos()
