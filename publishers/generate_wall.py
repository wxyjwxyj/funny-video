"""视频墙 HTML 生成器。

从 DB 读取有 funny_score 的视频，渲染成可直接用浏览器打开的单文件 HTML。
模板在 publishers/templates/wall.html，Python 只做数据填充，不内联 CSS/JS。
每次生成同时存档到 archive/YYYY-MM-DD.html，并更新 archive/index.html。
"""
import contextlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from storage.db import get_connection, init_db
from utils.log import get_logger

logger = get_logger(__name__)

_TEMPLATE = Path(__file__).parent / "templates" / "wall.html"
_DB_PATH = Path(__file__).parent.parent / "video.db"
_OUTPUT = Path(__file__).parent.parent / "wall.html"
_ARCHIVE_DIR = Path(__file__).parent.parent / "archive"


def _update_archive_index() -> None:
    """重新生成 archive/index.html，列出所有历史日期。"""
    files = sorted(_ARCHIVE_DIR.glob("????-??-??.html"), reverse=True)
    rows = ""
    for f in files:
        date = f.stem
        m = re.search(r"(\d+) 条", f.read_text(encoding="utf-8"))
        count = m.group(1) if m else "?"
        rows += f"<tr><td><a href='{f.name}'>{date}</a></td><td>{count} 条</td></tr>\n"

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>搞笑视频归档</title>
<style>
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#0f0f0f;color:#e0e0e0;font-family:system-ui,sans-serif}}
.container{{max-width:480px;margin:40px auto;background:#1a1a1a;border-radius:12px;overflow:hidden}}
.header{{background:#1e1e1e;padding:24px;border-bottom:1px solid #2a2a2a}}
.header h1{{font-size:18px;font-weight:600}}
.header p{{font-size:13px;color:#888;margin-top:4px}}
table{{width:100%;border-collapse:collapse}}
th{{padding:10px 24px;text-align:left;font-size:11px;color:#666;font-weight:600;text-transform:uppercase;letter-spacing:.5px;border-bottom:1px solid #2a2a2a}}
td{{padding:14px 24px;border-bottom:1px solid #1e1e1e;font-size:14px;color:#aaa}}
td a{{color:#fb7299;text-decoration:none;font-weight:600}}
td a:hover{{text-decoration:underline}}
tr:hover td{{background:#1e1e1e}}
.back{{display:block;padding:12px 24px;font-size:13px;color:#666;text-decoration:none}}
.back:hover{{color:#fb7299}}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>📼 搞笑视频归档</h1>
    <p>共 {len(files)} 天 · 点击日期查看当天内容</p>
  </div>
  <a href="../wall.html" class="back">← 返回今日</a>
  <table>
    <thead><tr><th>日期</th><th>视频数</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
</div>
</body>
</html>"""
    (_ARCHIVE_DIR / "index.html").write_text(html, encoding="utf-8")


def _format_num(n: int | None) -> str:
    if n is None:
        return "?"
    if n >= 10000:
        return f"{n/10000:.1f}万"
    return str(n)


def _render_card(v: dict) -> str:
    tags = json.loads(v["tags"]) if v.get("tags") else []
    tag_html = "".join(f'<span class="tag">{t}</span>' for t in tags[:3])
    score = v.get("funny_score") or 0
    title = v.get("title", "").replace('"', "&quot;").replace("<", "&lt;")
    embed = v.get("embed_url") or ""
    page_url = v.get("page_url") or ""
    category = (v.get("category") or "").replace('"', "&quot;")
    platform = v.get("platform", "")

    # 如果有 embed_url（B站 iframe）→ 内嵌播放
    # 否则（抖音禁止 iframe）→ 点卡片外跳原站
    data_attr = (
        f'data-embed="{embed}"'
        if embed
        else f'data-href="{page_url}"'
    )

    return (
        f'<div class="card" {data_attr} data-score="{score}" data-cat="{category}" data-platform="{platform}">'
        f'<div class="thumb">'
        f'<img loading="lazy" referrerpolicy="no-referrer" src="{v.get("cover_url","")}" alt="{title}">'
        f'<span class="score-badge">😂 {score}</span>'
        f'</div>'
        f'<div class="card-body">'
        f'<div class="title">{title}</div>'
        f'<div class="meta">'
        f'<span>{v.get("author","")}</span>'
        f'<span>▶ {_format_num(v.get("play_count"))}</span>'
        f'<span>👍 {_format_num(v.get("like_count"))}</span>'
        f'</div>'
        f'<div class="meta" style="margin-top:4px">{tag_html}</div>'
        f'</div></div>'
    )


def generate(min_score: int = 0, output: Path | None = None,
             date: str | None = None) -> Path:
    """生成视频墙 HTML 文件。

    Args:
        min_score: 只展示 funny_score >= min_score 的视频（0 = 全部已打标签的）
        output: 输出路径，默认项目根目录 wall.html
        date: 按 fetched_at 过滤日期（'YYYY-MM-DD'），None 默认今天
    """
    init_db(_DB_PATH)
    out = output or _OUTPUT
    template = _TEMPLATE.read_text(encoding="utf-8")
    date_str = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    sql = "SELECT * FROM videos WHERE funny_score >= ? AND status='active'"
    params: list = [min_score]
    # wall.html 默认只展示今天；archive 传 date 读取历史
    sql += " AND date(fetched_at) = ?"
    params.append(date_str)

    with contextlib.closing(get_connection(_DB_PATH)) as conn:
        rows = conn.execute(
            f"{sql} ORDER BY funny_score DESC, fetched_at DESC", params,
        ).fetchall()

    videos = [dict(r) for r in rows]
    if not videos:
        logger.warning("generate_wall: 无已打标签的视频，生成空页面")

    cards_html = "\n".join(_render_card(v) for v in videos)

    # 分类过滤按钮（取出现次数 >= 2 的分区）
    cat_count: dict[str, int] = {}
    for v in videos:
        c = v.get("category") or ""
        if c:
            cat_count[c] = cat_count.get(c, 0) + 1
    cat_buttons = "".join(
        f'<button data-min="0" data-cat="{c}">{c}</button>'
        for c, cnt in sorted(cat_count.items(), key=lambda x: -x[1])
        if cnt >= 2
    )

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    html = (
        template
        .replace("{{generated_at}}", now)
        .replace("{{total}}", str(len(videos)))
        .replace("{{date_label}}", date_str)
        .replace("{{category_buttons}}", cat_buttons)
        .replace("{{cards}}", cards_html)
    )

    out.write_text(html, encoding="utf-8")
    logger.info("generate_wall: 已写入 %s（%d 条，日期=%s）", out, len(videos), date_str)

    # 每次生成同步存档到 archive/YYYY-MM-DD.html
    _ARCHIVE_DIR.mkdir(exist_ok=True)
    archive_file = _ARCHIVE_DIR / f"{date_str}.html"
    archive_file.write_text(html, encoding="utf-8")
    _update_archive_index()
    logger.info("generate_wall: 已存档 %s", archive_file)

    return out
