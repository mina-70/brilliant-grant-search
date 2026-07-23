"""
Step 2: Search API + web UI for the grants database.

Usage:
    python import_grants.py          # once, to build grants.db
    uvicorn app:app --reload         # then open http://localhost:8000
"""

import sqlite3
from datetime import date, timedelta
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

BASE = Path(__file__).parent
DB_PATH = BASE / "grants.db"

app = FastAPI(title="Grant Search")

CLOSING_SOON_DAYS = 45


def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def compute_status(row, today):
    """open / closing_soon / passed / rolling / check_site"""
    dtype, ddate = row["deadline_type"], row["deadline_date"]
    if dtype == "rolling":
        return "open"   # rolling submissions count as open
    if dtype == "check_site" or not ddate:
        return "check_site"
    d = date.fromisoformat(ddate)
    if d < today:
        return "passed"
    if d <= today + timedelta(days=CLOSING_SOON_DAYS):
        return "closing_soon"
    return "open"


@app.get("/api/grants")
def list_grants(
    q: str = Query("", description="Free-text search"),
    region: str = "",
    career: str = "",
    category: str = "",
    status: str = "",
    origin: str = "",
    audience: str = "",
    sort: str = Query("deadline", pattern="^(deadline|title|funder)$"),
    limit: int = Query(300, ge=1, le=2000),
):
    con = db()
    where, params = [], []
    if q:
        like = f"%{q}%"
        where.append(
            "(title LIKE ? OR funding_body LIKE ? OR description LIKE ? "
            "OR eligibility LIKE ? OR category LIKE ? OR notes LIKE ? OR region LIKE ?)"
        )
        params += [like] * 7
    if region:
        where.append("COALESCE(NULLIF(region_clean,''), region) = ?")
        params.append(region)
    if career:
        where.append("career_clean LIKE ?")
        params.append(f"%{career}%")
    if category:
        where.append("category_clean LIKE ?")
        params.append(f"%{category}%")
    if origin:
        where.append("origin = ?")
        params.append(origin)
    if audience:
        where.append("audience LIKE ?")
        params.append(f"{audience}%")   # 'For startups' prefix also matches the bridge label

    sql = "SELECT * FROM grants"
    if where:
        sql += " WHERE " + " AND ".join(where)

    rows = con.execute(sql, params).fetchall()
    con.close()

    today = date.today()
    grants = []
    for r in rows:
        g = dict(r)
        g["status"] = compute_status(r, today)
        if g["deadline_date"]:
            g["days_left"] = (date.fromisoformat(g["deadline_date"]) - today).days
        else:
            g["days_left"] = None
        grants.append(g)

    if status:
        grants = [g for g in grants if g["status"] == status]

    if sort == "title":
        grants.sort(key=lambda g: g["title"].lower())
    elif sort == "funder":
        grants.sort(key=lambda g: (g["funding_body"] or "").lower())
    else:  # deadline: dated first (soonest upcoming first), then rolling, then check_site, passed last
        order = {"closing_soon": 0, "open": 0, "approx": 0, "check_site": 2, "passed": 3}
        grants.sort(
            key=lambda g: (
                order.get(g["status"], 2),
                g["deadline_date"] or "9998-12-31",
                g["deadline_type"] != "rolling",  # dated first, then rolling, then rest
            )
        )

    return {"count": len(grants), "grants": grants[:limit], "shown": min(limit, len(grants))}


@app.get("/api/stats")
def stats(origin: str = "", category: str = "", audience: str = ""):
    """Lightweight counters for the stats band (no full payload)."""
    con = db()
    clauses, params = [], []
    if origin:
        clauses.append("origin = ?"); params.append(origin)
    if category:
        clauses.append("category_clean LIKE ?"); params.append(f"%{category}%")
    if audience:
        clauses.append("audience LIKE ?"); params.append(f"{audience}%")
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    rows = con.execute(f"SELECT deadline_type, deadline_date FROM grants{where}", params).fetchall()
    con.close()
    today = date.today()
    total, open_n, closing = len(rows), 0, 0
    for r in rows:
        s = compute_status(r, today)
        if s == "open":
            open_n += 1
        elif s == "closing_soon":
            open_n += 1
            closing += 1
    return {"total": total, "open": open_n, "closing_soon": closing}


@app.get("/api/meta")
def meta():
    """Distinct values for the filter dropdowns + dataset stats."""
    con = db()
    def distinct(col):
        return [r[0] for r in con.execute(
            f"SELECT DISTINCT {col} FROM grants WHERE {col} != '' ORDER BY {col}"
        )]
    CAT_ORDER = ["All disciplines", "Life Sciences & Medicine", "Natural Sciences & Engineering",
                 "Social Sciences & Humanities", "Mobility & Career"]
    cats_present = set()
    for (c,) in con.execute("SELECT DISTINCT category_clean FROM grants WHERE category_clean != ''"):
        cats_present.update(c.split(";"))
    CAREER_ORDER = ["Master", "PhD", "Early Postdoc", "Senior Postdoc", "Any / Established"]
    careers_present = set()
    for (c,) in con.execute("SELECT DISTINCT career_clean FROM grants WHERE career_clean != ''"):
        careers_present.update(c.split(";"))
    out = {
        "regions": [r[0] for r in con.execute(
            "SELECT DISTINCT COALESCE(NULLIF(region_clean,''), region) AS rc FROM grants WHERE rc != '' ORDER BY rc")],
        "careers": [c for c in CAREER_ORDER if c in careers_present],
        "categories": [c for c in CAT_ORDER if c in cats_present],
        "total": con.execute("SELECT COUNT(*) FROM grants").fetchone()[0],
        "origins": {r[0]: r[1] for r in con.execute(
            "SELECT origin, COUNT(*) FROM grants GROUP BY origin")},
        "last_checked": con.execute("SELECT MAX(last_checked) FROM grants").fetchone()[0],
    }
    con.close()
    return out


app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")


@app.get("/")
def index():
    return FileResponse(BASE / "static" / "index.html")
