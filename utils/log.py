"""统一的 logging 配置。

使用方式：
    from utils.log import get_logger
    logger = get_logger(__name__)
    logger.info("✓ 采集完成")
    logger.warning("⚠ 未找到数据")
    logger.error("✗ 请求失败: %s", e)

日志格式：[时间] 级别 模块名 - 消息
默认同时输出到 stderr 和 .claude/logs/YYYY-MM-DD.log（按天滚动）。
若环境变量 FUNNY_LOG_NO_FILE_HANDLER=1，则跳过 FileHandler——
用于上层（如 scheduler）已经把 stderr 重定向到同一日志文件的场景，避免双倍写入。
"""
import logging
import os
import sys
from datetime import date
from pathlib import Path

# 全局只配置一次
_configured = False

# 日志目录：项目根目录下的 .claude/logs/
_LOG_DIR = Path(__file__).parent.parent / ".claude" / "logs"


def get_logger(name: str) -> logging.Logger:
    """获取带统一格式的 logger。"""
    global _configured
    if not _configured:
        _configure_root()
        _configured = True
    return logging.getLogger(name)


def _configure_root() -> None:
    """配置 root logger：输出到 stderr + 按天滚动的日志文件。"""
    fmt = logging.Formatter(
        fmt="[%(asctime)s] %(levelname)s %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # stderr handler
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not root.handlers:
        root.addHandler(stderr_handler)
        # 文件 handler（按天，文件名 YYYY-MM-DD.log）
        # 上层（如 scheduler）若已用 shell 重定向把 stderr 写入同一文件，
        # 设置 FUNNY_LOG_NO_FILE_HANDLER=1 跳过文件 handler，避免双倍写入。
        if os.environ.get("FUNNY_LOG_NO_FILE_HANDLER") != "1":
            _LOG_DIR.mkdir(parents=True, exist_ok=True)
            log_file = _LOG_DIR / f"{date.today().isoformat()}.log"
            file_handler = logging.FileHandler(log_file, encoding="utf-8")
            file_handler.setFormatter(fmt)
            root.addHandler(file_handler)
