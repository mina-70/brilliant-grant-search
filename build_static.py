"""
Build the static site for GitHub Pages.

Reads grants.db and writes everything the browser needs into docs/:
    docs/grants.json   - all grant data (searched/filtered in the browser)
    docs/index.html    - the site itself (copied from static/)
    docs/*.jpg/png     - images

GitHub Pages then serves docs/ as a normal website — no server needed.

Usage:
    python import_grants.py      # xlsx -> grants.db
    python build_static.py       # grants.db -> docs/
"""

import json
import shutil
import sqlite3
from datetime import date
from pathlib import Path

BASE = Path(__file__).parent
DB_PATH = BASE / "grants.db"
DOCS = BASE / "docs"
STATIC = BASE / "static"


def main():
    DOCS.mkdir(exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT * FROM grants").fetchall()

    grants = []
    for r in rows:
        g = dict(r)
        # keep the payload small: drop fields the UI never shows
        g.pop("source", None)
        grants.append(g)
    con.close()

    payload = {
        "built": date.today().isoformat(),
        "count": len(grants),
        "grants": grants,
    }
    (DOCS / "grants.json").write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )

    # copy the site files
    for name in ["index.html", "logo.png", "img-library.jpg", "img-lab.jpg"]:
        src = STATIC / name
        if src.exists():
            shutil.copy2(src, DOCS / name)

    # tell GitHub Pages not to run Jekyll over our files
    (DOCS / ".nojekyll").write_text("")

    size = (DOCS / "grants.json").stat().st_size // 1024
    print(f"Built docs/ with {len(grants)} grants ({size} KB JSON)")


if __name__ == "__main__":
    main()
