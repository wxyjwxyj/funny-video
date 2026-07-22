"""videos 表数据库操作层。"""
import json
import contextlib
from pathlib import Path

from storage.db import get_connection

_DB_PATH = Path(__file__).parent.parent / "video.db"

# 冲突更新只刷新采集侧的动态元数据。以下字段属于视频身份、AI 处理结果或
# 用户交互状态，重新采集时必须保留；对应状态应走专用更新函数修改。
_PRESERVE_ON_CONFLICT = {
    "topic", "platform", "platform_video_id", "content_hash",
    "tags", "funny_score", "status", "is_unsafe", "is_liked", "is_watched",
    "created_at", "fetched_at",
}
_COALESCE_ON_CONFLICT = {"published_at", "like_count", "duration", "category"}


def _build_upsert(row: dict) -> tuple[str, list]:
    """构造 videos upsert SQL 与参数，统一单条和批量写入规则。"""
    cols = list(row.keys())
    placeholders = ", ".join(["?"] * len(cols))
    update_cols = [c for c in cols if c not in _PRESERVE_ON_CONFLICT]
    update_set = ", ".join(
        f"{c} = COALESCE(excluded.{c}, {c})" if c in _COALESCE_ON_CONFLICT
        else f"{c} = excluded.{c}"
        for c in update_cols
    )
    sql = f"INSERT INTO videos ({', '.join(cols)}) VALUES ({placeholders})"
    if update_set:
        sql += f" ON CONFLICT(content_hash) DO UPDATE SET {update_set}"
    else:
        sql += " ON CONFLICT(content_hash) DO NOTHING"
    return sql, [row[c] for c in cols]


def get_db():
    return get_connection(_DB_PATH)


def upsert_video(video: dict) -> str:
    """插入或更新一条视频（按 content_hash 去重）。

    冲突时更新封面/播放量/点赞等动态字段，但保留 created_at 和 fetched_at。
    保留 fetched_at 是关键：热门视频每天都会被采集器重新抓到，若刷新
    fetched_at 则视频会在视频墙每天重复出现；锁住首次采集时间可避免此问题。
    extra/tags 字段自动序列化为 JSON 字符串。

    Returns:
        "inserted" | "updated"
    """
    row = {**video}
    for field in ("extra", "tags"):
        if isinstance(row.get(field), (dict, list)):
            row[field] = json.dumps(row[field], ensure_ascii=False)

    sql, params = _build_upsert(row)

    with contextlib.closing(get_db()) as conn:
        with conn:  # 同一事务内 SELECT + INSERT，SQLite 写锁保证原子性
            exists = conn.execute(
                "SELECT 1 FROM videos WHERE content_hash=?", (row["content_hash"],)
            ).fetchone()
            conn.execute(sql, params)
            return "updated" if exists else "inserted"


def upsert_videos(videos: list[dict]) -> dict[str, int]:
    """批量 upsert，单事务写完整个列表，返回 {"inserted": N, "updated": M}。"""
    counts = {"inserted": 0, "updated": 0}
    if not videos:
        return counts

    # 在同一个连接里完成所有行的 SELECT + INSERT，避免 N 次连接开销
    with contextlib.closing(get_db()) as conn:
        with conn:
            for v in videos:
                row = {**v}
                for field in ("extra", "tags"):
                    if isinstance(row.get(field), (dict, list)):
                        row[field] = json.dumps(row[field], ensure_ascii=False)

                sql, params = _build_upsert(row)
                exists = conn.execute(
                    "SELECT 1 FROM videos WHERE content_hash=?", (row["content_hash"],)
                ).fetchone()
                conn.execute(sql, params)
                counts["updated" if exists else "inserted"] += 1

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


def count_untagged(topic: str | None = None) -> int:
    """返回仍处于 active 的未打标签视频数。"""
    sql = "SELECT COUNT(*) FROM videos WHERE funny_score IS NULL AND status='active'"
    params: list = []
    if topic:
        sql += " AND topic=?"
        params.append(topic)
    with contextlib.closing(get_db()) as conn:
        return int(conn.execute(sql, params).fetchone()[0])


def update_tags(content_hash: str, tags: list[str], funny_score: int, is_unsafe: bool = False) -> None:
    """回写 Claude 打标结果。is_unsafe=True 时直接将视频标为 inactive，不展示。"""
    batch_update_tags([(content_hash, tags, funny_score, is_unsafe)])


def batch_update_tags(items: list[tuple[str, list[str], int, bool]]) -> None:
    """批量回写打标结果，单事务写完整个批次，减少连接开销。

    Args:
        items: [(content_hash, tags, funny_score, is_unsafe), ...]
    """
    if not items:
        return
    rows = [
        (json.dumps(tags, ensure_ascii=False), score, unsafe, "inactive" if unsafe else "active", ch)
        for ch, tags, score, unsafe in items
    ]
    with contextlib.closing(get_db()) as conn:
        with conn:
            conn.executemany(
                "UPDATE videos SET tags=?, funny_score=?, is_unsafe=?, status=? WHERE content_hash=?",
                rows,
            )
