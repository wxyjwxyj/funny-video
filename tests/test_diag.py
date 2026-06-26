"""临时诊断：打印 cc-switch 当前激活 provider 的模型名与环境注入情况。"""
import os
import json
import sqlite3
from pathlib import Path


def test_diag_print_model_resolution():
    print("\n===== 环境变量 ANTHROPIC_* =====")
    for k, v in sorted(os.environ.items()):
        if k.startswith("ANTHROPIC"):
            shown = v if "KEY" not in k and "TOKEN" not in k else "<hidden>"
            print(f"  {k} = {shown}")

    print("\n===== cc-switch providers =====")
    db = Path.home() / ".cc-switch" / "cc-switch.db"
    print(f"  db exists: {db.exists()}")
    if db.exists():
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        for r in conn.execute("SELECT name, is_current, settings_config FROM providers WHERE app_type='claude'"):
            env = json.loads(r["settings_config"]).get("env", {})
            star = "★current" if r["is_current"] else "        "
            print(f"  {star} {r['name']:24} "
                  f"MODEL={env.get('ANTHROPIC_MODEL','')!r} "
                  f"SONNET={env.get('ANTHROPIC_DEFAULT_SONNET_MODEL','')!r} "
                  f"BASE={env.get('ANTHROPIC_BASE_URL','')!r}")
        conn.close()

    print("\n===== config.get_claude_config() 实际解析 =====")
    from utils.config import get_claude_config
    k, b, m = get_claude_config()
    print(f"  api_key: {'set' if k else 'EMPTY'}")
    print(f"  base_url: {b!r}")
    print(f"  model: {m!r}")
