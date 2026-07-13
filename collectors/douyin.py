"""抖音视频采集器（CDP DOM 抓取，复用浏览器登录态）。

原方案在页面上下文里手动构造 XHR 直连内部搜索 API
(`/aweme/v1/web/search/item/`)，但该接口强制校验 a_bogus 签名，
未带签名会返回 search_nil_type=verify_check（人机验证）而拿不到数据。

现改为导航到搜索结果页、抓取渲染完成后的 DOM——让抖音自己的前端 JS
带正确签名去请求并渲染，我们只读页面结果，全程不碰签名。
与小红书采集器同一思路，符合「不逆向签名、复用登录态成本最低」的边界。
"""
import re
import urllib.parse
from datetime import datetime, timedelta, timezone

from collectors.base import (
    CDPCollector, LoginExpiredError,
    make_video, register_collector,
)
from utils.log import get_logger

logger = get_logger(__name__)


def _parse_douyin_time(text: str) -> str | None:
    """抖音 DOM 相对时间文字 → ISO8601 UTC 字符串。

    支持：刚刚 / X分钟前 / X小时前 / 昨天 HH:MM / X天前 / X周前 /
          MM-DD（不带年）/ YYYY-MM-DD。无法解析返回 None。
    """
    if not text:
        return None
    text = text.strip()
    now = datetime.now(timezone.utc)

    if text == "刚刚":
        return now.isoformat()

    m = re.match(r'^(\d+)\s*分钟前$', text)
    if m:
        return (now - timedelta(minutes=int(m[1]))).isoformat()

    m = re.match(r'^(\d+)\s*小时前$', text)
    if m:
        return (now - timedelta(hours=int(m[1]))).isoformat()

    m = re.match(r'^昨天\s*(\d{1,2}):(\d{2})$', text)
    if m:
        y = now - timedelta(days=1)
        try:
            return y.replace(hour=int(m[1]), minute=int(m[2]), second=0, microsecond=0).isoformat()
        except ValueError:
            return None

    m = re.match(r'^(\d+)\s*天前$', text)
    if m:
        return (now - timedelta(days=int(m[1]))).isoformat()

    m = re.match(r'^(\d+)\s*周前$', text)
    if m:
        return (now - timedelta(weeks=int(m[1]))).isoformat()

    # 完整日期 YYYY-MM-DD
    m = re.match(r'^(\d{4})-(\d{1,2})-(\d{1,2})$', text)
    if m:
        try:
            return datetime(int(m[1]), int(m[2]), int(m[3]), tzinfo=timezone.utc).isoformat()
        except ValueError:
            return None

    # 月-日 MM-DD（不带年）：晚于今天则算上一年
    m = re.match(r'^(\d{1,2})-(\d{1,2})$', text)
    if m:
        try:
            dt = datetime(now.year, int(m[1]), int(m[2]), tzinfo=timezone.utc)
            if dt > now:
                dt = dt.replace(year=now.year - 1)
            return dt.isoformat()
        except ValueError:
            return None

    return None


def _parse_like_count(text: str) -> int | None:
    """点赞文字 → 整数。支持 "27.6万" / "1.2亿" / 纯数字 "140"。"""
    text = (text or "").strip().replace(",", "")
    if not text:
        return None
    try:
        if "亿" in text:
            return int(float(text.replace("亿", "")) * 100_000_000)
        if "万" in text:
            return int(float(text.replace("万", "")) * 10_000)
        return int(float(text))
    except ValueError:
        return None


def _parse_duration(text: str) -> int | None:
    """时长文字 "07:12" / "01:02:03" → 秒。无法解析返回 None。"""
    text = (text or "").strip()
    parts = text.split(":")
    if not all(p.isdigit() for p in parts):
        return None
    if len(parts) == 2:
        return int(parts[0]) * 60 + int(parts[1])
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    return None


@register_collector("douyin_search")
class DouyinCollector(CDPCollector):
    """抖音搜索 CDP DOM 采集器。"""

    domain_pattern = "douyin.com"
    default_keywords = ["搞笑", "沙雕", "鬼畜"]
    per_keyword = 12
    request_delay = 5.0   # 抖音对搜索频率有风控，间隔放大以降低触发验证码概率
    page_wait = 5.0       # 导航后等待搜索结果 DOM 渲染完成
    content_hash_prefix = "douyin"
    keywords: list[str] = []

    # 0 条时用于区分「验证码拦截 / 登录失效 / 该词无结果」的探测脚本
    _STATE_JS = (
        "(function(){var s=(document.title||'')+(document.body.textContent||'').slice(0,2000);"
        "return {blocked:/验证码中间页|滑块|拖动以完成|安全验证|captcha/i.test(s),"
        "needLogin:/passport|\\/login/.test(location.href)};})()"
    )

    # 搜索结果卡片 .search-result-card 内，字段无语义类名（编译期随机 hash），
    # 故用内容特征提取：时长(mm:ss)、点赞(带万/亿)、时间(相对时间)靠正则识别，
    # "@" 为作者锚点（其后一个文本节点是作者名），标题取剩余最长文本节点。
    _EXTRACT_JS = r'''
Array.from(document.querySelectorAll('.search-result-card')).map(function(card){
  var a = card.querySelector('a[href*="/video/"]');
  var href = a ? a.href : '';
  var vid = href ? (href.split('/video/')[1]||'').split('?')[0] : '';
  var img = card.querySelector('img');
  var cover = img && img.src ? img.src : '';
  var texts = [];
  card.querySelectorAll('*').forEach(function(el){
    if(el.children.length===0){ var t=el.textContent.trim(); if(t) texts.push(t); }
  });
  var duration='', likes='', pubTime='', author='', title='';
  var durRe=/^\d{1,2}:\d{2}(:\d{2})?$/;
  var likeRe=/^[\d.]+[万亿]?$/;
  var timeRe=/(刚刚|昨天|分钟前|小时前|天前|周前|个月前|年前)$|^\d{1,2}-\d{1,2}$|^\d{4}-\d{1,2}-\d{1,2}$/;
  for(var i=0;i<texts.length;i++){
    var t=texts[i];
    if(!duration && durRe.test(t)){ duration=t; continue; }
    if(t==='@'){ if(!author) author=texts[i+1]||''; continue; }
    if(!pubTime && timeRe.test(t)){ pubTime=t; continue; }
    if(!likes && likeRe.test(t)){ likes=t; continue; }
  }
  var used={}; used[duration]=1; used[likes]=1; used[pubTime]=1; used[author]=1; used['@']=1; used['合集']=1;
  texts.forEach(function(t){ if(!used[t] && t.length>title.length) title=t; });
  return {vid:vid,pageUrl:href,cover:cover,title:title,author:author,likes:likes,duration:duration,pubTime:pubTime};
}).filter(function(x){return x.vid && x.title;})
'''

    def _search(self, keyword: str) -> list[dict]:
        encoded = urllib.parse.quote(keyword)
        self._navigate(f"https://www.douyin.com/search/{encoded}?type=video")
        items = self._eval(self._EXTRACT_JS, timeout=15) or []
        if not items:
            # 0 条：区分「验证码拦截 / 登录失效」与「该词确实无结果」
            state = self._eval(self._STATE_JS, timeout=5) or {}
            if state.get("blocked"):
                raise LoginExpiredError("抖音触发验证码中间页，需在浏览器手动过验证")
            if state.get("needLogin"):
                raise LoginExpiredError("抖音登录态过期，请在浏览器重新登录")
        return items[:self.per_keyword]

    def _map_item(self, item: dict, keyword: str) -> dict | None:
        vid = item.get("vid", "")
        title = (item.get("title") or "").strip()
        if not vid or not title:
            return None
        duration = _parse_duration(item.get("duration", ""))
        # 过滤 3 秒以下的极短片（多为无实质内容的封面卡）
        if duration is not None and duration < 3:
            return None
        return make_video(
            platform="douyin", platform_video_id=vid,
            content_hash_prefix=self.content_hash_prefix, topic=self.topic,
            title=title, author=item.get("author", ""),
            cover_url=item.get("cover", ""),
            duration=duration,
            like_count=_parse_like_count(item.get("likes", "")),
            page_url=item.get("pageUrl") or f"https://www.douyin.com/video/{vid}",
            published_at=_parse_douyin_time(item.get("pubTime", "")),
            extra={"search_keyword": keyword},
        )
