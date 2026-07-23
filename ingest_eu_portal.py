"""
Bulk ingest: EU Funding & Tenders Portal (Horizon Europe, ERC, MSCA, EIC,
Digital Europe, LIFE, etc. — all EU research & innovation calls).

Uses the public SEDIA search API that powers the official portal at
https://ec.europa.eu/info/funding-tenders/opportunities/portal/

Pulls currently open (status 31094502) and forthcoming (31094501) grant
topics and inserts/updates them with origin='eu_portal'.

Usage:
    python ingest_eu_portal.py

NOTE: This is an unofficial-but-public API used by the portal's own
frontend. If the EU changes it, the query or field names below may need
adjustment — check the network tab on the portal's search page to see
the current request format.
"""

import json
import sqlite3
import time
from datetime import datetime
from pathlib import Path

import requests

DB_PATH = Path(__file__).parent / "grants.db"
API_URL = "https://api.tech.ec.europa.eu/search-api/prod/rest/search"
API_KEY = "SEDIA"          # the portal's own public key
PAGE_SIZE = 100

STATUS_OPEN = "31094502"
STATUS_FORTHCOMING = "31094501"


def parse_epoch_ms(v):
    """Portal deadlines arrive as epoch milliseconds or ISO strings."""
    if not v:
        return None
    if isinstance(v, list):
        v = v[0] if v else None
        if not v:
            return None
    try:
        return datetime.fromtimestamp(int(v) / 1000).date().isoformat()
    except (ValueError, TypeError, OSError):
        pass
    try:
        return datetime.fromisoformat(str(v)[:10]).date().isoformat()
    except ValueError:
        return None


def first(v):
    if isinstance(v, list):
        return v[0] if v else ""
    return v or ""


def parse_hit(h):
    meta = h.get("metadata", {})
    identifier = first(meta.get("identifier")) or h.get("reference", "")
    title = (h.get("content") or first(meta.get("title")) or "").strip()
    deadline = parse_epoch_ms(meta.get("deadlineDate"))
    frame = first(meta.get("frameworkProgramme")) or "EU"
    url = (
        "https://ec.europa.eu/info/funding-tenders/opportunities/portal/screen/"
        f"opportunities/topic-details/{identifier.lower()}" if identifier else
        "https://ec.europa.eu/info/funding-tenders/opportunities/portal/"
    )
    return {
        "id": f"EU-{identifier}" if identifier else None,
        "title": title,
        "funding_body": "European Commission",
        "source": frame,
        "region": "EU + Associated Countries",
        "category": first(meta.get("callTitle")) or "EU call topic",
        "career_stage": "",
        "description": (first(meta.get("descriptionByte")) or "")[:600],
        "amount": "See call budget",
        "duration": "",
        "eligibility": "",
        "deadline_text": deadline or "See call page",
        "deadline_date": deadline,
        "deadline_type": "fixed" if deadline else "check_site",
        "application_mode": "EU Funding & Tenders Portal",
        "interview": "",
        "success_rate": "",
        "link": url,
        "notes": f"Topic {identifier}. Auto-imported from EU Funding & Tenders Portal.",
    }


def upsert(con, g):
    con.execute("""
        INSERT INTO grants (id, title, funding_body, source, region, category,
            career_stage, description, amount, duration, eligibility,
            deadline_text, deadline_date, deadline_type, application_mode,
            interview, success_rate, link, notes, region_clean, career_clean,
            category_clean, audience, origin, last_checked)
        VALUES (:id,:title,:funding_body,:source,:region,:category,
            :career_stage,:description,:amount,:duration,:eligibility,
            :deadline_text,:deadline_date,:deadline_type,:application_mode,
            :interview,:success_rate,:link,:notes,'EU','','All disciplines','For research','eu_portal',date('now'))
        ON CONFLICT(id) DO UPDATE SET
            title=excluded.title, deadline_text=excluded.deadline_text,
            deadline_date=excluded.deadline_date, deadline_type=excluded.deadline_type,
            description=excluded.description, link=excluded.link,
            last_checked=date('now')
    """, g)


def main():
    con = sqlite3.connect(DB_PATH)
    session = requests.Session()
    session.headers["User-Agent"] = "GrantSearchBot/0.1 (personal tracker)"

    query = {
        "bool": {
            "must": [
                {"terms": {"type": ["1", "2"]}},  # grant topics
                {"terms": {"status": [STATUS_OPEN, STATUS_FORTHCOMING]}},
            ]
        }
    }

    total, page = 0, 1
    while True:
        params = {"apiKey": API_KEY, "text": "***", "pageSize": PAGE_SIZE, "pageNumber": page}
        files = {
            "query": (None, json.dumps(query), "application/json"),
            "languages": (None, json.dumps(["en"]), "application/json"),
        }
        resp = session.post(API_URL, params=params, files=files, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", [])
        if not results:
            break

        for h in results:
            g = parse_hit(h)
            if not g["id"] or not g["title"]:
                continue
            upsert(con, g)
            total += 1
        con.commit()

        total_hits = data.get("totalResults", 0)
        print(f"  page {page}: {total}/{total_hits} processed...")
        if page * PAGE_SIZE >= total_hits:
            break
        page += 1
        time.sleep(1)

    con.execute("""
        DELETE FROM grants
        WHERE origin='eu_portal' AND deadline_date IS NOT NULL
          AND deadline_date < date('now', '-60 days')
    """)
    con.commit()
    n = con.execute("SELECT COUNT(*) FROM grants WHERE origin='eu_portal'").fetchone()[0]
    print(f"Done. {total} topics processed; {n} eu_portal entries in DB.")
    con.close()


if __name__ == "__main__":
    main()
