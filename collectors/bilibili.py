"""B站综合热门视频采集器。

调用 api.bilibili.com/x/web-interface/popular，无需登录态。
每次采集指定页数，返回 raw video dict 列表（tags/funny_score 留空，由 pipeline 填）。
"""
from datetime import datetime, timezone

from utils.http import retry_session
from utils.log import get_logger

logger = get_logger(__name__)

_BASE_URL = "https://api.bilibili.com/x/web-interface/popular"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bilibili.com",
}


def _map_video(item: dict) -> dict | None:
    """把 popular API 单条 item 映射成 videos 表字段。

    is_ogv=True（番剧/影视）跳过——非 UGC，搞笑概率极低且无 bvid embed。
    """
    if item.get("is_ogv") or not item.get("bvid"):
        return None

    bvid = item["bvid"]
    stat = item.get("stat", {})
    owner = item.get("owner", {})
    now = datetime.now(timezone.utc).isoformat()

    return {
        "platform": "bilibili",
        "platform_video_id": bvid,
        "title": item.get("title", ""),
        "author": owner.get("name", ""),
        "author_id": str(owner.get("mid", "")),
        "cover_url": item.get("pic", ""),
        "page_url": f"https://www.bilibili.com/video/{bvid}",
        "embed_url": f"https://player.bilibili.com/player.html?bvid={bvid}&autoplay=0",
        "play_url": None,
        "duration": item.get("duration"),
        "play_count": stat.get("view"),
        "like_count": stat.get("like"),
        "category": item.get("tname", ""),
        "tags": None,
        "funny_score": None,
        "extra": {
            "coin": stat.get("coin"),
            "favorite": stat.get("favorite"),
            "danmaku": stat.get("danmaku"),
            "rcmd_reason": item.get("rcmd_reason", {}).get("content", ""),
        },
        "content_hash": f"bilibili:{bvid}",
        "status": "active",
        "fetched_at": now,
        "created_at": now,
    }


def fetch_popular(pages: int = 5, page_size: int = 20) -> list[dict]:
    """采集综合热门视频，返回 raw video dict 列表。

    Args:
        pages: 采集页数（B站热门约 5 页 = 100 条足够每日刷）
        page_size: 每页条数，API 最大 50
    """
    session = retry_session()
    results: list[dict] = []

    for pn in range(1, pages + 1):
        try:
            resp = session.get(
                _BASE_URL,
                params={"ps": page_size, "pn": pn},
                headers=_HEADERS,
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.warning("B站热门第 %d 页请求失败: %s", pn, e)
            continue

        if data.get("code") != 0:
            logger.warning("B站热门第 %d 页 API 报错: code=%s msg=%s", pn, data.get("code"), data.get("message"))
            continue

        items = data.get("data", {}).get("list", [])
        page_videos = [v for item in items if (v := _map_video(item))]
        results.extend(page_videos)
        logger.info("B站热门第 %d 页: %d/%d 条有效（非番剧 UGC）", pn, len(page_videos), len(items))

    logger.info("B站热门采集完成，共 %d 条", len(results))
    return results
