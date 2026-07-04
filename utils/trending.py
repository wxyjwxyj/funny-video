"""实时热搜词获取工具。无需登录，直接公开 API。"""
import requests

from utils.log import get_logger

logger = get_logger(__name__)

_DOUYIN_HOT_URL = "https://www.douyin.com/aweme/v1/web/hot/search/list/"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": "https://www.douyin.com/",
}


def fetch_douyin_trending(top_n: int = 5) -> list[str]:
    """获取抖音热搜榜前 top_n 个词，失败时返回空列表。

    API 无需登录，热搜词代表当日最热话题。
    搜笑类内容创作者通常会围绕热点出视频，用热词搜索往往比固定词更新鲜。
    """
    try:
        resp = requests.get(
            _DOUYIN_HOT_URL,
            params={"device_platform": "webapp", "aid": "6383", "channel": "channel_pc_web"},
            headers=_HEADERS,
            timeout=8,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("status_code", -1) != 0:
            logger.warning("抖音热搜 API 返回异常: %s", data.get("status_code"))
            return []
        words = data.get("data", {}).get("word_list", [])
        result = [w["word"] for w in words[:top_n] if w.get("word")]
        logger.info("抖音热搜词（前%d）: %s", top_n, result)
        return result
    except Exception as e:
        logger.warning("获取抖音热搜失败，将使用兜底关键词: %s", e)
        return []
