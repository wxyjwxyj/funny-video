"""视频墙 HTML 生成器。

从 DB 读取有 funny_score 的视频，渲染成可直接用浏览器打开的单文件 HTML。
模板在 publishers/templates/wall.html，Python 只做数据填充，不内联 CSS/JS。
"""
import contextlib
import json
from datetime import datetime, timezone
from pathlib import Path

from storage.db import get_connection, init_db
from utils.log import get_logger

logger = get_logger(__name__)

_TEMPLATE = Path(__file__).parent / "templates" / "wall.html"
_DB_PATH = Path(__file__).parent.parent / "video.db"
_OUTPUT = Path(__file__).parent.parent / "wall.html"


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
    embed = v.get("embed_url", "").replace('"', "%22")
    category = (v.get("category") or "").replace('"', "&quot;")
    return (
        f'<div class="card" data-embed="{embed}" data-score="{score}" data-cat="{category}">'
        f'<div class="thumb">'
        f'<img loading="lazy" src="{v.get("cover_url","")}" alt="{title}">'
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


def generate(min_score: int = 0, output: Path | None = None) -> Path:
    """生成视频墙 HTML 文件。

    Args:
        min_score: 只展示 funny_score >= min_score 的视频（0 = 全部已打标签的）
        output: 输出路径，默认项目根目录 wall.html
    """
    init_db(_DB_PATH)
    out = output or _OUTPUT
    template = _TEMPLATE.read_text(encoding="utf-8")

    with contextlib.closing(get_connection(_DB_PATH)) as conn:
        rows = conn.execute(
            "SELECT * FROM videos WHERE funny_score >= ? AND status='active' ORDER BY funny_score DESC, fetched_at DESC",
            (min_score,),
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
        .replace("{{category_buttons}}", cat_buttons)
        .replace("{{cards}}", cards_html)
    )

    out.write_text(html, encoding="utf-8")
    logger.info("generate_wall: 已写入 %s（%d 条）", out, len(videos))
    return out
