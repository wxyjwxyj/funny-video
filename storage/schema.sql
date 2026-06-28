-- 搞笑视频聚合墙数据库 schema
-- SQLite，WAL 模式（连接参数在 storage/db.py 统一设置）

CREATE TABLE IF NOT EXISTS videos (
    id                INTEGER PRIMARY KEY,
    topic             TEXT DEFAULT 'funny',     -- funny / ai
    platform          TEXT NOT NULL,        -- bilibili / douyin / xiaohongshu
    platform_video_id TEXT NOT NULL,        -- 平台原始 ID（B站 bvid 等）
    title             TEXT,
    author            TEXT,
    author_id         TEXT,
    cover_url         TEXT,                 -- 封面图
    page_url          TEXT,                 -- 原视频页链接（跳转用，可变字段）
    embed_url         TEXT,                 -- 内嵌播放器 URL（B站 iframe）
    play_url          TEXT,                 -- 直链流地址（可能时效，可空）
    duration          INTEGER,              -- 时长（秒）
    play_count        INTEGER,
    like_count        INTEGER,
    category          TEXT,                 -- 平台分区（鬼畜/生活等）
    tags              TEXT,                 -- JSON array，Claude 打的标签
    funny_score       INTEGER,              -- 0-10，Claude 评分
    extra             TEXT,                 -- JSON，平台特有字段（投币/收藏等）
    content_hash      TEXT UNIQUE,          -- 去重键（冲突键，不用 page_url）
    status            TEXT DEFAULT 'active',-- active / hidden / inactive
    is_unsafe         INTEGER DEFAULT 0,     -- Claude 判定的不安全内容标记
    is_liked          INTEGER DEFAULT 0,    -- 个人标记：喜欢
    is_watched        INTEGER DEFAULT 0,    -- 个人标记：已看
    fetched_at        TEXT,
    created_at        TEXT
);

CREATE INDEX IF NOT EXISTS idx_videos_topic      ON videos(topic);
CREATE INDEX IF NOT EXISTS idx_videos_platform   ON videos(platform);
CREATE INDEX IF NOT EXISTS idx_videos_funny       ON videos(funny_score);
CREATE INDEX IF NOT EXISTS idx_videos_fetched     ON videos(fetched_at);
CREATE INDEX IF NOT EXISTS idx_videos_status      ON videos(status);
