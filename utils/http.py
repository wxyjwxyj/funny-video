"""带自动重试的 HTTP 工具。"""
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


def retry_session(retries: int = 3, backoff: float = 1.0, backoff_max: float = 120.0, pool_maxsize: int = 20) -> requests.Session:
    """返回一个带指数退避重试的 requests Session。"""
    s = requests.Session()
    retry = Retry(
        total=retries,
        backoff_factor=backoff,
        backoff_max=backoff_max,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"],
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=pool_maxsize, pool_maxsize=pool_maxsize)
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    return s
