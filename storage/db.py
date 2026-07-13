# storage/db.py
import sqlite3
from contextlib import closing
from pathlib import Path

SCHEMA_PATH = Path(__file__).with_name("schema.sql")

_TOPIC_HASH_MIGRATION = """
DELETE FROM videos
WHERE platform IN ('douyin', 'xiaohongshu')
  AND content_hash = platform || ':' || platform_video_id
  AND EXISTS (
      SELECT 1 FROM videos AS scoped
      WHERE scoped.content_hash = videos.platform || '_' ||
            COALESCE(NULLIF(videos.topic, ''), 'funny') || ':' || videos.platform_video_id
  );

UPDATE videos
SET content_hash = platform || '_' || COALESCE(NULLIF(topic, ''), 'funny') || ':' || platform_video_id,
    topic = COALESCE(NULLIF(topic, ''), 'funny')
WHERE platform IN ('douyin', 'xiaohongshu')
  AND content_hash = platform || ':' || platform_video_id;
"""


def get_connection(db_path: Path | str) -> sqlite3.Connection:
    """获取数据库连接，开启外键约束并设置字典工厂。

    journal_mode=WAL 是数据库级持久设置，由 init_db 在建库时写入一次；
    这里只设连接级参数，避免每次连接重复执行 WAL pragma。
    """
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA busy_timeout = 5000;")
    return conn


def init_db(db_path: Path | str) -> None:
    """初始化数据库表结构，并确保 WAL 模式已开启（持久生效，只需执行一次）。"""
    with closing(get_connection(db_path)) as conn:
        conn.execute("PRAGMA journal_mode = WAL;")
        with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
            with conn:
                conn.executescript(f.read())
                # 旧版抖音/小红书哈希未包含 topic，两个视频墙会互相覆盖。
                # 迁移幂等；新安装没有匹配行，不产生额外影响。
                conn.executescript(_TOPIC_HASH_MIGRATION)
