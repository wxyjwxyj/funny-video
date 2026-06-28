"""采集器基类 — 开闭原则：新增平台只需注册子类，不改已有代码。

- BaseCollector: 所有采集器的统一接口 collect() -> list[dict]
- CDPCollector: CDP 采集器模板（target 发现、关键词循环、去重、限速、降级）
- 注册表: register_collector / create_collector，registry 用名字引用而非函数指针
"""

import time
import urllib.parse
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

import requests

from utils.errors import CDPConnectionError, CollectorError, LoginExpiredError
from utils.log import get_logger

logger = get_logger(__name__)

# ═══════════════════════════════════════════════════════════════════
# 采集器注册表
# ═══════════════════════════════════════════════════════════════════

_collector_registry: dict[str, type["BaseCollector"]] = {}


def register_collector(name: str):
    """装饰器：把采集器类注册到全局表中。"""
    def decorator(cls):
        _collector_registry[name] = cls
        return cls
    return decorator


def create_collector(name: str, **kwargs: Any) -> "BaseCollector":
    """工厂方法：按名字创建采集器实例。"""
    cls = _collector_registry.get(name)
    if cls is None:
        raise ValueError(f"未知采集器: {name!r}，可用: {list(_collector_registry.keys())}")
    return cls(**kwargs)


def list_collectors() -> list[str]:
    return list(_collector_registry.keys())


# ═══════════════════════════════════════════════════════════════════
# 统一接口
# ═══════════════════════════════════════════════════════════════════

class BaseCollector(ABC):
    """所有采集器基类。collect() 是唯一暴露给 registry 的入口。"""

    def __init__(self, topic: str = "", **kwargs: Any):
        self.topic = topic
        for key, val in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, val)

    @abstractmethod
    def collect(self) -> list[dict]:
        """返回标准 videos 表 dict 列表，每条 dict 必须含 content_hash。"""
        ...


# ═══════════════════════════════════════════════════════════════════
# CDP 采集器模板
# ═══════════════════════════════════════════════════════════════════

class CDPCollector(BaseCollector):
    """CDP 采集器模板方法 — 子类只写 _search() 和 _map_item()。

    基类负责: CDP 连接、登录态检测、多关键词去重、限速、降级。
    """

    # ── 子类覆盖 ──
    domain_pattern: str = ""
    default_keywords: list[str] = []
    per_keyword: int = 15
    request_delay: float = 1.5
    page_wait: float = 3.0
    content_hash_prefix: str = ""

    def __init__(self, cdp_proxy: str = "http://localhost:3456", **kwargs: Any):
        super().__init__(**kwargs)
        self.cdp_proxy = cdp_proxy
        self._session = requests.Session()
        self._target_id: str = ""

    # ── 模板方法 ──

    def collect(self) -> list[dict]:
        keywords: list[str] = getattr(self, "keywords", None) or self.default_keywords
        if not self._target_id:
            self._resolve_target()

        seen: set[str] = set()
        results: list[dict] = []

        for i, kw in enumerate(keywords):
            if i > 0:
                time.sleep(self.request_delay)
            try:
                items = self._search(kw)
                for item in items:
                    v = self._map_item(item, kw)
                    if v is None:
                        continue
                    v.setdefault("topic", self.topic)
                    if "content_hash" not in v:
                        pid = v.get("platform_video_id", "")
                        v["content_hash"] = f"{self.content_hash_prefix}:{pid}"
                    if v["content_hash"] not in seen:
                        seen.add(v["content_hash"])
                        results.append(v)
            except LoginExpiredError:
                raise
            except CollectorError as e:
                logger.warning("[%s] %s 失败: %s", self.__class__.__name__, kw, e)

        logger.info("[%s] 采集完成，共 %d 条", self.__class__.__name__, len(results))
        return results

    # ── CDP 基础设施 ──

    def _resolve_target(self) -> str:
        try:
            targets = self._session.get(f"{self.cdp_proxy}/targets", timeout=10).json()
        except (requests.RequestException, ValueError) as e:
            raise CDPConnectionError(f"获取 CDP targets 失败: {e}") from e
        for t in targets:
            if self.domain_pattern in t.get("url", ""):
                self._target_id = t["targetId"]
                logger.info("[%s] 已定位 tab: %s", self.__class__.__name__, self._target_id)
                return self._target_id
        raise LoginExpiredError(
            f"未找到含 {self.domain_pattern!r} 的标签页，请在 Chrome 打开并登录"
        )

    def _navigate(self, url: str) -> None:
        nav_js = f"window.location.href={urllib.parse.quote(url)!r}; 'ok'"
        try:
            self._session.post(
                f"{self.cdp_proxy}/eval?target={self._target_id}",
                data=nav_js.encode(), timeout=10,
            )
        except requests.RequestException as e:
            raise CollectorError(f"CDP 导航失败: {e}") from e
        time.sleep(self.page_wait)

    def _eval(self, js: str, timeout: int = 15) -> Any:
        try:
            resp = self._session.post(
                f"{self.cdp_proxy}/eval?target={self._target_id}",
                data=js.encode(), timeout=timeout,
            )
            resp.raise_for_status()
            return resp.json().get("value")
        except Exception as e:
            raise CollectorError(f"CDP eval 失败: {e}") from e

    # ── 子类必须实现 ──

    @abstractmethod
    def _search(self, keyword: str) -> list[dict]:
        ...

    @abstractmethod
    def _map_item(self, item: dict, keyword: str) -> dict | None:
        ...


# ═══════════════════════════════════════════════════════════════════
# 工具函数（所有采集器共用）
# ═══════════════════════════════════════════════════════════════════

def make_video(*, platform: str, platform_video_id: str, title: str,
               content_hash_prefix: str, topic: str = "",
               author: str = "", author_id: str = "",
               cover_url: str = "", duration: int | None = None,
               play_count: int | None = None, like_count: int | None = None,
               category: str | None = None, embed_url: str | None = None,
               page_url: str = "", extra: dict | None = None) -> dict:
    """构造标准 videos 表 dict，所有采集器共用。"""
    now = datetime.now(timezone.utc).isoformat()
    return {
        "topic": topic,
        "platform": platform,
        "platform_video_id": platform_video_id,
        "title": title,
        "author": author,
        "author_id": author_id,
        "cover_url": cover_url,
        "page_url": page_url,
        "embed_url": embed_url,
        "play_url": None,
        "duration": duration,
        "play_count": play_count,
        "like_count": like_count,
        "category": category,
        "tags": None,
        "funny_score": None,
        "extra": extra or {},
        "content_hash": f"{content_hash_prefix}:{platform_video_id}",
        "status": "active",
        "fetched_at": now,
        "created_at": now,
    }
