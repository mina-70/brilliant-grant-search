"""
Bulk ingest: Grants.gov (all US federal funding opportunities).

Uses the free public Grants.gov Search2 API — no API key required.
Pulls currently *posted* (open) opportunities from science-relevant agencies
and inserts/updates them in grants.db with origin='grants_gov'.

Usage:
    python ingest_grantsgov.py             # science agencies (default)
    python ingest_grantsgov.py --all       # every agency, every category

Re-running updates existing entries (matched by opportunity number).
Curated grants (from the xlsx) are never touched.

NOTE: This talks to a live government API. If Grants.gov changes their API,
the field names in `parse_hit` may need small adjustments — the API docs
live at https://www.grants.gov/api/
"""

import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

DB_PATH = Path(__file__).parent / "grants.db"
API_URL = "https://api.grants.gov/v1/api/search2"
PAGE_SIZE = 100  # rows per request

# Science-heavy agencies (Grants.gov agency codes). --all skips this filter.
SCIENCE_AGENCIES = ["HHS", "NSF", "DOE", "USDA", "NASA", "DOC", "EPA", "DOD", "ED", "DOI"]


def parse_date(s):
    """Grants.gov dates arrive as 'MM/DD/YYYY'."""
    if not s:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return None


def parse_hit(h):
    """Map one API result to our grants schema."""
    opp_number = h.get("number") or h.get("opportunityNumber") or ""
    title = (h.get("title") or "").strip()
    agency = h.get("agencyName") or h.get("agency") or "US Federal"
    close_date = parse_date(h.get("closeDate"))
    opp_id = h.get("id") or ""
    link = f"https://www.grants.gov/search-results-detail/{opp_id}" if opp_id else "https://www.grants.gov/"
    return {
        "id": f"USG-{opp_number}" if opp_number else f"USG-{opp_id}",
        "title": title,
        "funding_body": agency,
        "source": "Grants.gov",
        "region": "USA",
        "category": h.get("category") or "Federal funding",
        "career_stage": "",
        "description": (h.get("synopsis") or "")[:600],
        "amount": h.get("awardCeiling") and f"Up to ${h['awardCeiling']}" or "See announcement",
        "duration": "",
        "eligibility": "",
        "deadline_text": h.get("closeDate") or "See announcement",
        "deadline_date": close_date,
        "deadline_type": "fixed" if close_date else "check_site",
        "application_mode": "Grants.gov Workspace",
        "interview": "",
        "success_rate": "",
        "link": link,
        "notes": f"Opportunity {opp_number}. Auto-imported from Grants.gov.",
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
            :interview,:success_rate,:link,:notes,'USA','','All disciplines','For research','grants_gov',date('now'))
        ON CONFLICT(id) DO UPDATE SET
            title=excluded.title, deadline_text=excluded.deadline_text,
            deadline_date=excluded.deadline_date, deadline_type=excluded.deadline_type,
            description=excluded.description, amount=excluded.amount,
            link=excluded.link, last_checked=date('now')
    """, g)


def main():
    everything = "--all" in sys.argv
    con = sqlite3.connect(DB_PATH)

    total, start = 0, 0
    while True:
        payload = {
            "rows": PAGE_SIZE,
            "startRecordNum": start,
            "oppStatuses": "posted",       # only currently open opportunities
            "keyword": "",
        }
        if not everything:
            payload["agencies"] = "|".join(SCIENCE_AGENCIES)

        resp = requests.post(API_URL, json=payload, timeout=30,
                             headers={"User-Agent": "GrantSearchBot/0.1 (personal tracker)"})
        resp.raise_for_status()
        data = resp.json().get("data", {})
        hits = data.get("oppHits", [])
        if not hits:
            break

        for h in hits:
            g = parse_hit(h)
            if not g["title"]:
                continue
            upsert(con, g)
            total += 1
        con.commit()

        hit_count = data.get("hitCount", 0)
        start += len(hits)
        print(f"  fetched {start}/{hit_count}...")
        if start >= hit_count:
            break
        time.sleep(1)  # be polite

    # Remove auto rows whose deadline passed >60 days ago (keep DB tidy)
    con.execute("""
        DELETE FROM grants
        WHERE origin='grants_gov' AND deadline_date IS NOT NULL
          AND deadline_date < date('now', '-60 days')
    """)
    con.commit()
    n = con.execute("SELECT COUNT(*) FROM grants WHERE origin='grants_gov'").fetchone()[0]
    print(f"Done. {total} opportunities processed; {n} grants_gov entries in DB.")
    con.close()


if __name__ == "__main__":
    main()
