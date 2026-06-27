"""视频号搞笑视频采集器（TikHub API）。

TikHub 已逆向 channels.weixin.qq.com 的 HTTP 接口，
通过统一搜索 API (fetch_search, business_type=video) 关键词搜索视频号内容。
纯 HTTP 请求，不需要 Hook/MITM/特定微信版本。
"""
import json
import os
import re
from datetime import datetime, timezone

import requests

from utils.errors import CollectorError, LoginExpiredError
from utils.http import retry_session
from utils.log import get_logger

logger = get_logger(__name__)

_KEYWORDS = ["搞笑", "沙雕", "鬼畜"]
_PER_KEYWORD = 20  # 搜索 API 返回条数较少，多翻几页

# 搜索接口一次返回 10 条，翻页取
_PAGE_SIZE = 10
_MAX_PAGES = 2  # 每关键词翻 2 页 = 20 条

_SEARCH_URL = "/api/v1/wechat_search/v2/fetch_search"


def _get_client():
    from dotenv import load_dotenv
    load_dotenv()
    token = os.getenv("TIKHUB_API_TOKEN", "")
    base_url = os.getenv("TIKHUB_BASE_URL", "https://api.tikhub.dev")
    if not token:
        raise CollectorError("TIKHUB_API_TOKEN 未配置，请在 .env 设置")
    return token, base_url


def _parse_likes_count(text: str | None) -> int | None:
    """把 '1.2万' 或 '1234' 转为整数。"""
    if not text:
        return None
    text = text.replace(",", "").strip()
    try:
        if "万" in text:
            return int(float(text.replace("万", "")) * 10000)
        return int(text)
    except ValueError:
        return None


def _map_video(item: dict, keyword: str) -> dict | None:
    """把 TikHub 搜索结果条目映射为 videos 表字段。"""
    export_id = item.get("exportId", "")
    if not export_id:
        return None

    jump = item.get("jumpInfo") or {}
    # extInfo 是 JSON 字符串，解析出 feedNonceId
    ext_str = jump.get("extInfo", "") or ""
    try:
        ext = json.loads(ext_str) if isinstance(ext_str, str) else ext_str
        feed_nonce_id = ext.get("feedNonceId", "")
    except Exception:
        feed_nonce_id = ""
    title = item.get("title", "")
    # 去掉高亮标签 <em class="highlight">
    import re
    title = re.sub(r"<[^>]+>", "", title).strip()
    if not title:
        return None

    author = jump.get("nickName", "")
    # 时长 "01:59" → 秒数
    duration_raw = item.get("duration", "")
    duration = None
    if duration_raw:
        parts = duration_raw.split(":")
        try:
            duration = int(parts[0]) * 60 + int(parts[1])
        except (ValueError, IndexError):
            pass

    now = datetime.now(timezone.utc).isoformat()

    return {
        "platform": "wechat_video",
        "platform_video_id": export_id.split("/")[-1][:64],
        "title": title,
        "author": author,
        "author_id": jump.get("userName", ""),
        "cover_url": item.get("image", ""),
        "page_url": (
            f"https://channels.weixin.qq.com/web/pages/feed?feedNonceId={feed_nonce_id}"
            if feed_nonce_id else
            f"https://channels.weixin.qq.com/web/pages/search?keyword={requests.utils.quote(title)}"
        ),
        "embed_url": None,
        "play_url": None,
        "duration": duration,
        "play_count": None,  # 搜索结果不含播放量，需调详情接口
        "like_count": None,
        "category": None,
        "tags": None,
        "funny_score": None,
        "extra": {
            "date_time": item.get("dateTime", ""),
            "search_keyword": keyword,
            "export_id": export_id,
        },
        "content_hash": f"wechat_video:{export_id.split('/')[-1][:64]}",
        "status": "active",
        "fetched_at": now,
        "created_at": now,
    }


def fetch_popular(pages: int | None = None) -> list[dict]:
    """按关键词搜索视频号搞笑内容，返回去重后的 video dict 列表。

    Args:
        pages: 保留参数，视频号采集不使用（关键词驱动）
    """
    token, base_url = _get_client()
    session = retry_session()
    session.headers.update({
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    })

    seen: set[str] = set()
    results: list[dict] = []

    for kw in _KEYWORDS:
        for page in range(_MAX_PAGES):
            offset = page * _PAGE_SIZE
            try:
                resp = session.post(
                    f"{base_url}{_SEARCH_URL}",
                    json={
                        "keyword": kw,
                        "business_type": "video",
                        "offset": offset,
                        "raw": False,
                    },
                    timeout=30,
                )
                resp.raise_for_status()
                data = resp.json()

                if data.get("code") == 401:
                    raise LoginExpiredError("TikHub API Token 无效或过期")
                if data.get("code") == 402:
                    raise CollectorError("TikHub 余额不足，请充值")
                if data.get("code") != 200:
                    raise CollectorError(f"TikHub 返回 code={data.get('code')}")

                items = (data.get("data") or {}).get("items") or []
                new = [v for item in items if (v := _map_video(item, kw)) and v["content_hash"] not in seen]
                seen.update(v["content_hash"] for v in new)
                results.extend(new)
                logger.info("视频号搜索 [%s] 第%d页: %d 条", kw, page + 1, len(new))

                # 无更多数据则提前退出翻页
                if not data.get("data", {}).get("continue_flag"):
                    break

            except (LoginExpiredError, CollectorError):
                raise
            except Exception as e:
                logger.warning("视频号搜索 [%s] 第%d页失败: %s", kw, page + 1, e)
                break

    logger.info("视频号采集完成，共 %d 条", len(results))
    return results
