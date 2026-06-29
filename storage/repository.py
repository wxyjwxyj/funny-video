"""videos 表数据库操作层。"""
import json
import contextlib
from pathlib import Path

from storage.db import get_connection

_DB_PATH = Path(__file__).parent.parent / "video.db"


def get_db():
    return get_connection(_DB_PATH)


def upsert_video(video: dict) -> str:
    """插入或更新一条视频（按 content_hash 去重）。

    冲突时更新除 created_at 之外的所有字段（封面/播放量等会变化）。
    extra/tags 字段自动序列化为 JSON 字符串。

    Returns:
        "inserted" | "updated"
    """
    row = {**video}
    for field in ("extra", "tags"):
        if isinstance(row.get(field), (dict, list)):
            row[field] = json.dumps(row[field], ensure_ascii=False)

    cols = list(row.keys())
    placeholders = ", ".join(["?"] * len(cols))
    update_cols = [c for c in cols if c not in ("content_hash", "created_at")]
    update_set = ", ".join(f"{c} = excluded.{c}" for c in update_cols)

    sql = f"""
        INSERT INTO videos ({', '.join(cols)})
        VALUES ({placeholders})
        ON CONFLICT(content_hash) DO UPDATE SET {update_set}
    """

    with contextlib.closing(get_db()) as conn:
        with conn:  # 同一事务内 SELECT + INSERT，SQLite 写锁保证原子性
            exists = conn.execute(
                "SELECT 1 FROM videos WHERE content_hash=?", (row["content_hash"],)
            ).fetchone()
            conn.execute(sql, [row[c] for c in cols])
            return "updated" if exists else "inserted"


def upsert_videos(videos: list[dict]) -> dict[str, int]:
    """批量 upsert，返回 {"inserted": N, "updated": M}。"""
    counts = {"inserted": 0, "updated": 0}
    for v in videos:
        result = upsert_video(v)
        counts[result] += 1
    return counts


def list_untagged(limit: int | None = 50, topic: str | None = None) -> list[dict]:
    """返回 funny_score 为 NULL 的视频，用于打标签流水线。limit=None 表示全部。"""
    sql = "SELECT * FROM videos WHERE funny_score IS NULL AND status='active'"
    params: list = []
    if topic:
        sql += " AND topic=?"
        params.append(topic)
    sql += " ORDER BY created_at DESC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    with contextlib.closing(get_db()) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def update_tags(content_hash: str, tags: list[str], funny_score: int, is_unsafe: bool = False) -> None:
    """回写 Claude 打标结果。is_unsafe=True 时直接将视频标为 inactive，不展示。"""
    status = "inactive" if is_unsafe else "active"
    with contextlib.closing(get_db()) as conn:
        with conn:
            conn.execute(
                "UPDATE videos SET tags=?, funny_score=?, is_unsafe=?, status=? WHERE content_hash=?",
                (json.dumps(tags, ensure_ascii=False), funny_score, is_unsafe, status, content_hash),
            )
