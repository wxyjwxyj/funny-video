"""统一配置读取：.env → 环境变量 → cc-switch → config.json。

与 news1 的差异：news1 不加载 .env，配置全靠 cc-switch.db 直读。
funny-video 改为项目自包含——优先读项目根目录的 .env，让配置不依赖
全局 cc-switch 当前激活的 provider 是否正确，cc-switch 仅作兜底。
"""
import json
import os
import logging
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.json"
ENV_PATH = PROJECT_ROOT / ".env"
CC_SWITCH_DB = Path.home() / ".cc-switch" / "cc-switch.db"

logger = logging.getLogger(__name__)


def _load_dotenv() -> None:
    """零依赖加载项目根目录的 .env 到 os.environ（不覆盖已存在的环境变量）。

    只解析 KEY=VALUE 行，忽略空行与 # 注释，去掉值两端引号。
    已在环境中的变量优先级更高，故用 setdefault 不覆盖。
    """
    if not ENV_PATH.exists():
        return
    for raw in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


# 模块导入时即加载，确保后续 os.getenv 能读到 .env 的值
_load_dotenv()


def _load_config_file() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.warning("config.json 不存在，请复制 config.example.json 并填写")
        return {}
    except json.JSONDecodeError as e:
        logger.error("config.json 格式错误: %s", e)
        return {}


def _load_cc_switch_config() -> dict:
    """从 cc-switch 数据库读取当前激活的 provider 配置。

    cc-switch 会把 Claude 模型名翻译成 provider 实际模型（如 mimo-v2.5-pro），
    所以 ANTHROPIC_MODEL 未设置时，用 sonnet 映射作为默认模型。
    """
    if not CC_SWITCH_DB.exists():
        return {}
    try:
        conn = sqlite3.connect(f"file:{CC_SWITCH_DB}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT settings_config FROM providers WHERE app_type='claude' AND is_current=1"
        ).fetchone()
        conn.close()
        if not row:
            return {}
        cfg = json.loads(row["settings_config"])
        env = cfg.get("env", {})
        model = (
            env.get("ANTHROPIC_MODEL")
            or env.get("ANTHROPIC_DEFAULT_SONNET_MODEL", "")
        )
        return {
            "api_key": env.get("ANTHROPIC_AUTH_TOKEN", ""),
            "base_url": env.get("ANTHROPIC_BASE_URL", ""),
            "model": model,
        }
    except Exception as e:
        logger.debug("读取 cc-switch 配置失败: %s", e)
        return {}


def load_project_config() -> dict:
    """加载并校验项目配置，缺少必要字段时记录警告。"""
    cfg = _load_config_file()
    if not cfg.get("cdp_proxy"):
        logger.warning("config.json 缺少 cdp_proxy，将使用默认值 http://localhost:3456")
        cfg.setdefault("cdp_proxy", "http://localhost:3456")
    return cfg


def get_claude_config() -> tuple[str, str, str]:
    """返回 (api_key, base_url, model)。

    优先级：.env / 环境变量 → cc-switch → config.json → 空字符串
    """
    cc = _load_cc_switch_config()
    cfg = _load_config_file()

    api_key = (
        os.getenv("ANTHROPIC_API_KEY")
        or cc.get("api_key")
        or cfg.get("claude_api_key", "")
    )
    base_url = (
        os.getenv("ANTHROPIC_BASE_URL")
        or cc.get("base_url")
        or cfg.get("claude_base_url", "")
    )
    model = (
        os.getenv("ANTHROPIC_MODEL")
        or cc.get("model")
        or cfg.get("claude_model", "")
    )
    return api_key, base_url, model
