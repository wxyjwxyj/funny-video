# storage/db.py
import sqlite3
from contextlib import closing
from pathlib import Path

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def get_connection(db_path: Path | str) -> sqlite3.Connection:
    """获取数据库连接，开启外键约束并设置字典工厂。"""
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA busy_timeout = 5000;")
    return conn


def init_db(db_path: Path | str) -> None:
    """初始化数据库表结构。"""
    with closing(get_connection(db_path)) as conn:
        with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
            with conn:
                conn.executescript(f.read())
