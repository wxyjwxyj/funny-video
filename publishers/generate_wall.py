"""视频墙 HTML 生成器。

从 DB 读取有 funny_score 的视频，渲染成可直接用浏览器打开的单文件 HTML。
模板在 publishers/templates/wall.html，Python 只做数据填充，不内联 CSS/JS。
每次生成同时存档到 archive/YYYY-MM-DD.html，并更新 archive/index.html。
"""
import contextlib
import html as _html
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from storage.db import get_connection, init_db
from utils.log import get_logger

logger = get_logger(__name__)

_TEMPLATE = Path(__file__).parent / "templates" / "wall.html"
_DB_PATH = Path(__file__).parent.parent / "video.db"
_ARCHIVE_DIR = Path(__file__).parent.parent / "archive"


def _update_index_time(now: str) -> None:
    """更新首页 index.html 副标题中的最后更新时间。"""
    index_path = Path(__file__).parent.parent / "index.html"
    if not index_path.exists():
        return
    content = index_path.read_text(encoding="utf-8")
    # 替换 <p class="sub">...</p> 内容，保留缩进
    updated = re.sub(
        r'(<p class="sub">).*?(</p>)',
        rf'\g<1>B站 · 抖音 · 小红书 · 更新于 {now}\g<2>',
        content,
    )
    if updated != content:
        index_path.write_text(updated, encoding="utf-8")


def _update_archive_index(archive_dir: Path, wall_path: Path, date_str: str, count: int) -> None:
    """重新生成 archive/index.html，列出所有历史日期。

    条数从 counts.json 读取，避免每次重读所有历史 HTML 文件。
    """
    counts_file = archive_dir / "counts.json"
    # 读现有条数索引，追加本次新条目
    try:
        counts_data: dict[str, int] = json.loads(counts_file.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        counts_data = {}
    counts_data[date_str] = count
    counts_file.write_text(json.dumps(counts_data, ensure_ascii=False, indent=2), encoding="utf-8")

    # 只枚举文件名，不读文件内容
    files = sorted(archive_dir.glob("????-??-??.html"), reverse=True)
    icon = "🤖" if "ai" in archive_dir.name else "📼"
    name_labels = {"funny": "搞笑", "ai": "AI"}
    base = archive_dir.name.replace("_archive", "")
    title = f"{icon} {name_labels.get(base, base)}归档"
    rows = ""
    for f in files:
        d = f.stem
        cnt = counts_data.get(d, "?")
        rows += (
            f'<a href="{f.name}" class="row">'
            f'<span class="date">{d}</span>'
            f'<span class="count">{cnt} 条</span>'
            f'<span class="arrow">→</span>'
            f'</a>\n'
        )

    back_name = wall_path.name
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
:root{{--bg:#0a0a0f;--surface:#13131a;--surface-2:#1c1c28;--border:#2a2a3a;--text:#e4e4f0;--text-sub:#9090b0;--text-muted:#5a5a7a;--accent:#fb7299}}
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
body{{background:var(--bg);color:var(--text);font-family:-apple-system,system-ui,sans-serif;min-height:100vh}}
header{{position:sticky;top:0;z-index:10;background:rgba(10,10,15,.88);backdrop-filter:blur(16px);border-bottom:1px solid var(--border);padding:0 20px;display:flex;align-items:center;gap:12px;height:52px}}
.back-link{{color:var(--text-muted);text-decoration:none;font-size:13px}}
.back-link:hover{{color:var(--accent)}}
.header-title{{font-size:16px;font-weight:700}}
.header-count{{margin-left:auto;background:var(--surface-2);border:1px solid var(--border);border-radius:20px;padding:3px 10px;font-size:12px;color:var(--text-sub)}}
.list{{max-width:520px;margin:0 auto;padding:20px 16px 48px}}
.row{{display:flex;align-items:center;gap:12px;padding:14px 16px;background:var(--surface);border:1px solid var(--border);border-radius:10px;text-decoration:none;color:inherit;transition:all .15s;margin-bottom:8px}}
.row:hover{{border-color:rgba(251,114,153,.35);background:var(--surface-2);transform:translateX(2px)}}
.date{{font-size:14px;font-weight:600;color:var(--text);font-variant-numeric:tabular-nums}}
.count{{font-size:13px;color:var(--text-muted);margin-left:auto}}
.arrow{{font-size:14px;color:var(--accent);opacity:.6}}
</style>
</head>
<body>
<header>
  <a href="../{back_name}" class="back-link">← 返回今日</a>
  <span class="header-title">{title}</span>
  <span class="header-count">共 {len(files)} 天</span>
</header>
<div class="list">
{rows}</div>
</body>
</html>"""
    (archive_dir / "index.html").write_text(html, encoding="utf-8")


def _format_num(n: int | None) -> str:
    if n is None:
        return "?"
    if n >= 10000:
        return f"{n/10000:.1f}万"
    return str(n)


def _format_age(published_at: str | None) -> str:
    """将 ISO8601 发布时间转为「3天前」等相对时间标签，无时间返回空字符串。"""
    if not published_at:
        return ""
    try:
        from datetime import datetime, timezone
        pub = datetime.fromisoformat(published_at)
        if pub.tzinfo is None:
            pub = pub.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - pub
        hours = delta.total_seconds() / 3600
        if hours < 1:
            return "刚刚"
        if hours < 24:
            return f"{int(hours)}小时前"
        days = int(hours / 24)
        if days <= 7:
            return f"{days}天前"
        if days <= 30:
            return f"{days // 7}周前"
        return pub.strftime("%m/%d")
    except Exception:
        return ""


def _safe_url(url: str) -> str:
    """过滤非 http(s) URL，防止 javascript: 注入。"""
    url = (url or "").strip()
    return url if url.startswith("http") else ""


def _render_featured_card(v: dict) -> str:
    """渲染精华条（score≥9）里的紧凑卡片，用于顶部横向滚动区。"""
    score = v.get("funny_score") or 0
    title = _html.escape(v.get("title") or "")
    embed = _html.escape(_safe_url(v.get("embed_url") or ""))
    page_url = _html.escape(_safe_url(v.get("page_url") or ""))
    cover_url = _html.escape(_safe_url(v.get("cover_url") or ""))
    platform = _html.escape(v.get("platform") or "")
    data_attr = f'data-embed="{embed}"' if embed else f'data-href="{page_url}"'
    return (
        f'<div class="feat-card" {data_attr} data-platform="{platform}">'
        f'<div class="feat-thumb">'
        f'<img loading="lazy" referrerpolicy="no-referrer" src="{cover_url}" alt="{title}">'
        f'<span class="feat-score">⭐ {score}</span>'
        f'</div>'
        f'<div class="feat-title">{title}</div>'
        f'</div>'
    )


def _render_card(v: dict) -> str:
    tags = json.loads(v["tags"]) if v.get("tags") else []
    tag_html = "".join(f'<span class="tag">{_html.escape(t)}</span>' for t in tags[:3])
    score = v.get("funny_score") or 0
    score_icon = "🤖" if v.get("topic") != "funny" else "😂"
    title = _html.escape(v.get("title") or "")
    embed = _html.escape(_safe_url(v.get("embed_url") or ""))
    page_url = _html.escape(_safe_url(v.get("page_url") or ""))
    cover_url = _html.escape(_safe_url(v.get("cover_url") or ""))
    category = _html.escape(v.get("category") or "")
    platform = _html.escape(v.get("platform") or "")
    author = _html.escape(v.get("author") or "")
    vid = _html.escape(v.get("content_hash") or "")

    # 平台显示名称
    platform_labels = {"bilibili": "B站", "douyin": "抖音", "xiaohongshu": "小红书"}
    platform_label = platform_labels.get(platform, platform)

    # 评分徽章 CSS 类（按分段着色）
    score_cls = "s9" if score >= 9 else ("s8" if score >= 8 else "s7")

    # 如果有 embed_url（B站 iframe）→ 内嵌播放
    # 否则（抖音禁止 iframe）→ 点卡片外跳原站
    data_attr = f'data-embed="{embed}"' if embed else f'data-href="{page_url}"'

    # 发布时间：计算相对标签和天数（用于筛选）
    age_label = _format_age(v.get("published_at"))
    try:
        from datetime import datetime, timezone
        pub = v.get("published_at")
        if pub:
            dt = datetime.fromisoformat(pub)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            age_days = (datetime.now(timezone.utc) - dt).days
        else:
            age_days = 9999
    except Exception:
        age_days = 9999

    age_html = f'<span class="pub-age">{age_label}</span>' if age_label else ""

    return (
        f'<div class="card" {data_attr} data-score="{score}" data-cat="{category}" data-platform="{platform}" data-age="{age_days}" data-vid="{vid}">'
        f'<div class="thumb">'
        f'<img loading="lazy" referrerpolicy="no-referrer" src="{cover_url}" alt="{title}">'
        f'<span class="platform-icon">{platform_label}</span>'
        f'<span class="score-badge {score_cls}">{score_icon} {score}</span>'
        f'</div>'
        f'<div class="card-body">'
        f'<div class="title">{title}</div>'
        f'<div class="card-meta">'
        f'<span class="author">{author}</span>'
        f'<span class="stats">'
        f'{age_html}'
        f'<span>👍 {_format_num(v.get("like_count"))}</span>'
        f'</span>'
        f'<button class="like-btn" aria-label="喜欢">🤍</button>'
        f'</div>'
        f'<div class="tags">{tag_html}</div>'
        f'</div></div>'
    )


def generate(topic: str = "funny", min_score: int = 7, min_like_count: int = 0,
             output: Path | None = None,
             date: str | None = None, display_name: str | None = None,
             max_published_days: int | None = None) -> Path:
    """生成视频墙 HTML 文件。

    Args:
        topic: 内容主题（对应 DB videos.topic 字段）
        min_score: 最低上墙 funny_score
        min_like_count: 最低点赞数（0=不限）
        output: 自定义输出路径，不传则按 topic 自动命名
        date: 筛选日期（YYYY-MM-DD），默认今天
        display_name: 页面标题，不传则从 topic 推导
        max_published_days: 只展示最近 N 天内发布的内容（None=不限）；
                            AI 类内容时效性强建议设 30，搞笑内容可留 None
    """
    init_db(_DB_PATH)

    # 根据 topic 决定输出文件和归档目录
    root = Path(__file__).parent.parent
    if output:
        out = output
        archive_dir = _ARCHIVE_DIR
    else:
        safe = re.sub(r"[^a-zA-Z0-9_-]", "_", topic)
        out = root / f"{safe}_wall.html"
        archive_dir = root / f"{safe}_archive"

    template = _TEMPLATE.read_text(encoding="utf-8")
    date_str = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    sql = "SELECT * FROM videos WHERE funny_score >= ? AND status='active' AND topic=?"
    params: list = [min_score, topic]
    if min_like_count > 0:
        sql += " AND like_count >= ?"
        params.append(min_like_count)
    sql += " AND date(fetched_at) = ?"
    params.append(date_str)
    if max_published_days is not None:
        # published_at 为 NULL 的记录不过滤（兼容历史未采集发布时间的数据）
        sql += " AND (published_at IS NULL OR published_at >= date('now', ?))"
        params.append(f"-{max_published_days} days")

    with contextlib.closing(get_connection(_DB_PATH)) as conn:
        rows = conn.execute(
            f"{sql} ORDER BY funny_score DESC, fetched_at DESC", params,
        ).fetchall()

    videos = [dict(r) for r in rows]
    if not videos:
        logger.warning("generate_wall: 无已打标签的视频，生成空页面")

    cards_html = "\n".join(_render_card(v) for v in videos)

    # 精华条：score≥9 的视频单独生成顶部横向滚动区
    featured = [v for v in videos if (v.get("funny_score") or 0) >= 9]
    if featured:
        feat_cards_html = "\n".join(_render_featured_card(v) for v in featured)
        featured_section = (
            f'<section class="featured">'
            f'<div class="featured-hd">'
            f'<span class="featured-label">⭐ 今日精选</span>'
            f'<span class="featured-count">{len(featured)} 条</span>'
            f'</div>'
            f'<div class="featured-scroll">{feat_cards_html}</div>'
            f'</section>'
        )
    else:
        featured_section = ""

    # 分类过滤按钮（取出现次数 >= 2 的分区）
    cat_count: dict[str, int] = {}
    for v in videos:
        c = v.get("category") or ""
        if c:
            cat_count[c] = cat_count.get(c, 0) + 1
    cat_buttons = "".join(
        f'<button data-min="0" data-cat="{_html.escape(c)}">{_html.escape(c)}</button>'
        for c, cnt in sorted(cat_count.items(), key=lambda x: -x[1])
        if cnt >= 2
    )

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 标题：有外部传入用外部，否则从 topic 推导（display_name 在 registry 已定义）
    page_title = display_name or f"🎬 {topic} 视频墙"

    # 平台切换按钮：页面内置默认值
    pb_list = [("bilibili", "B站"), ("douyin", "抖音"), ("xiaohongshu", "小红书")]
    pb_html = "".join(f'<button data-platform="{k}">{v}</button>' for k, v in pb_list)

    html = (
        template
        .replace("{{page_title}}", page_title)
        .replace("{{generated_at}}", now)
        .replace("{{total}}", str(len(videos)))
        .replace("{{date_label}}", date_str)
        .replace("{{platform_buttons}}", pb_html)
        .replace("{{category_buttons}}", cat_buttons)
        .replace("{{featured_section}}", featured_section)
        .replace("{{cards}}", cards_html)
    )

    out.write_text(html, encoding="utf-8")
    logger.info("generate_wall: 已写入 %s（%d 条，日期=%s）", out, len(videos), date_str)

    # 更新首页 index.html 的副标题时间戳
    _update_index_time(now)

    # 每次生成同步存档到 archive/YYYY-MM-DD.html
    archive_dir.mkdir(exist_ok=True)
    archive_file = archive_dir / f"{date_str}.html"
    archive_file.write_text(html, encoding="utf-8")
    _update_archive_index(archive_dir, out, date_str, len(videos))
    logger.info("generate_wall: 已存档 %s", archive_file)

    return out
