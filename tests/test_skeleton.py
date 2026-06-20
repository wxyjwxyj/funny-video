"""阶段 0 骨架冒烟测试：utils/storage 能导入、能建库、schema 关键字段在位。"""
import contextlib

from storage import db
from utils import claude, errors, log  # noqa: F401  确保可导入
from utils.config import get_claude_config
from utils.http import retry_session


def test_utils_importable():
    """底层模块都能导入，关键符号存在。"""
    assert callable(retry_session)
    assert callable(claude.claude_call_tool)
    assert errors.FunnyVideoError.__name__ == "FunnyVideoError"
    assert errors.CDPConnectionError.exit_code == 10
    assert log.get_logger("smoke").name == "smoke"


def test_claude_config_shape():
    """配置读取返回三元组（值可能为空，取决于 .env / cc-switch）。"""
    cfg = get_claude_config()
    assert isinstance(cfg, tuple) and len(cfg) == 3


def test_db_init_and_schema(tmp_path):
    """用 schema.sql 建库，videos 表与去重键 content_hash 在位。"""
    dbfile = tmp_path / "test.db"
    db.init_db(dbfile)
    with contextlib.closing(db.get_connection(dbfile)) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(videos)").fetchall()}
    assert "content_hash" in cols
    assert {"platform", "platform_video_id", "funny_score", "tags"} <= cols
