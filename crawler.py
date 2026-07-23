"""
Fact-checking crawler.

For every grant, fetches the official page at run time and:

  DEADLINES  - extracts dates near "deadline"/"closing"/"apply by";
               silently updates deadline_date + deadline_text when the page
               shows exactly one unambiguous future deadline.
  AMOUNTS    - extracts money figures (EUR/USD/GBP/CHF/DKK/SEK/NOK/CAD/AUD/PLN/JPY);
               confirms the stored amount if its figure appears on the page;
               silently updates it when the page clearly shows a single
               different headline figure near funding keywords.
  LINKS      - follows redirects; permanently-moved links are updated;
               dead links are recorded.
  CLOSED     - detects "call is closed / no longer accepting" wording.

NOTHING is written into user-visible text as a label. Every action
(confirmed / updated / mismatch / no_data / link_dead) is logged with a
full timestamp to the `verifications` table, and `last_checked` is set to
the exact check time. Ambiguous cases are logged, never guessed.

Usage:
    python crawler.py                # crawl all grants (curated first)
    python crawler.py G03 G25        # specific grants
    python crawler.py --curated      # only hand-curated entries
    python crawler.py --report       # no crawling; show the latest log

Scheduling: run daily via cron / Task Scheduler. The crawl is polite
(identified user-agent, 2s delay) — a full pass over N grants takes ~3N sec.
"""

import re
import sqlite3
import sys
import time
from datetime import date, datetime
from pathlib import Path

import requests

DB_PATH = Path(__file__).parent / "grants.db"
USER_AGENT = "GrantSearchBot/0.2 (fact-checking crawler; A Brilliant Mind blog)"
DELAY = 2.0

MONTHS = {m: i + 1 for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"])}
MONTHS.update({m[:3]: v for m, v in MONTHS.items()})

MON = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
DMY = re.compile(rf"\b(\d{{1,2}})\.?\s+{MON}\.?\s+(20\d\d)\b", re.I)
MDY = re.compile(rf"\b{MON}\.?\s+(\d{{1,2}}),?\s+(20\d\d)\b", re.I)
NUMERIC = re.compile(r"\b(20\d\d)-(\d{2})-(\d{2})\b")

DEADLINE_WORDS = re.compile(r"deadline|closing date|closes on|apply by|submission date|cut-?off", re.I)
CLOSED_WORDS = re.compile(r"call (?:is |has )?closed|no longer accept|applications? (?:are )?closed|submission closed", re.I)

MONEY = re.compile(
    r"(€|EUR|US?\$|USD|£|GBP|CHF|DKK|SEK|NOK|CAD|AUD|PLN|¥|JPY)\s?"
    r"([\d][\d.,\s]*\d|\d)\s*"
    r"(million|mio|mln|m\b|k\b|thousand|billion)?", re.I)
FUNDING_WORDS = re.compile(r"funding|budget|amount|grant|award|up to|maximum|stipend|salary", re.I)


# ---------- helpers ----------

def strip_html(html):
    html = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", text)


def safe_date(y, m, d):
    try:
        return date(y, m, d).isoformat()
    except ValueError:
        return None


def extract_deadline_dates(text, today):
    """Future ISO dates appearing within 140 chars after a deadline keyword."""
    found = set()
    for m in DEADLINE_WORDS.finditer(text):
        win = text[m.end(): m.end() + 140]
        for dm in DMY.finditer(win):
            d = safe_date(int(dm.group(3)), MONTHS[dm.group(2).lower()], int(dm.group(1)))
            if d and d >= today:
                found.add(d)
        for dm in MDY.finditer(win):
            d = safe_date(int(dm.group(3)), MONTHS[dm.group(1).lower()], int(dm.group(2)))
            if d and d >= today:
                found.add(d)
        for dm in NUMERIC.finditer(win):
            d = safe_date(int(dm.group(1)), int(dm.group(2)), int(dm.group(3)))
            if d and d >= today:
                found.add(d)
    return sorted(found)


def money_to_number(num_str, scale):
    n = float(re.sub(r"[\s,]", "", num_str.replace(",", "")) or 0)
    s = (scale or "").lower()
    if s in ("million", "mio", "mln", "m"):
        n *= 1_000_000
    elif s in ("k", "thousand"):
        n *= 1_000
    elif s == "billion":
        n *= 1_000_000_000
    return n


def extract_amounts(text):
    """Money figures that appear near funding-related words -> set of (currency, value)."""
    out = []
    for m in MONEY.finditer(text):
        ctx = text[max(0, m.start() - 60): m.end() + 60]
        if not FUNDING_WORDS.search(ctx):
            continue
        cur = m.group(1).upper().replace("US$", "USD").replace("$", "USD").replace("€", "EUR").replace("£", "GBP").replace("¥", "JPY")
        try:
            val = money_to_number(m.group(2), m.group(3))
        except ValueError:
            continue
        if val >= 1000:  # ignore trivial figures
            out.append((cur, val))
    return out


def stored_amount_values(amount_text):
    """Numeric figures mentioned in our stored amount field."""
    vals = []
    for m in MONEY.finditer(amount_text or ""):
        try:
            vals.append(money_to_number(m.group(2), m.group(3)))
        except ValueError:
            pass
    return vals


def fmt_amount(cur, val):
    sym = {"EUR": "€", "USD": "$", "GBP": "£", "JPY": "¥"}.get(cur, cur + " ")
    if val >= 1_000_000:
        v = val / 1_000_000
        return f"Up to {sym}{v:g}M"
    if val >= 1_000:
        return f"Up to {sym}{val / 1000:g}k"
    return f"Up to {sym}{val:g}"


# ---------- DB ----------

def ensure_tables(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS verifications (
            grant_id   TEXT,
            checked_at TEXT,     -- full timestamp of this check
            field      TEXT,     -- deadline | amount | link | status
            action     TEXT,     -- confirmed | updated | mismatch | no_data | link_dead | closed_detected
            stored     TEXT,
            page_value TEXT
        )
    """)


def log(con, gid, ts, field, action, stored, page_value=""):
    con.execute(
        "INSERT INTO verifications VALUES (?,?,?,?,?,?)",
        (gid, ts, field, action, str(stored or "")[:300], str(page_value or "")[:300]),
    )


# ---------- main crawl ----------

def crawl_grant(con, g, session, today, ts):
    url = g["link"]
    try:
        resp = session.get(url, timeout=25, allow_redirects=True)
        final_url = resp.url
        resp.raise_for_status()
    except Exception as e:
        log(con, g["id"], ts, "link", "link_dead", url, str(e)[:200])
        return "link_dead"

    # Update permanently moved links (silently)
    if final_url.rstrip("/") != url.rstrip("/") and resp.history and resp.history[0].status_code in (301, 308):
        con.execute("UPDATE grants SET link=? WHERE id=?", (final_url, g["id"]))
        log(con, g["id"], ts, "link", "updated", url, final_url)

    text = strip_html(resp.text)
    outcome = []

    # --- deadline ---
    if g["deadline_type"] == "rolling":
        log(con, g["id"], ts, "deadline", "confirmed", "rolling")
    else:
        dates = extract_deadline_dates(text, today)
        stored = g["deadline_date"]
        if len(dates) == 1:
            new = dates[0]
            if new == stored:
                log(con, g["id"], ts, "deadline", "confirmed", stored)
            else:
                d = date.fromisoformat(new)
                nice = f"{d.day} {d.strftime('%b')} {d.year}"
                con.execute(
                    "UPDATE grants SET deadline_date=?, deadline_type='fixed', deadline_text=? WHERE id=?",
                    (new, nice, g["id"]),
                )
                log(con, g["id"], ts, "deadline", "updated", stored, new)
                outcome.append("deadline updated")
        elif len(dates) > 1:
            log(con, g["id"], ts, "deadline", "mismatch", stored, ", ".join(dates[:4]))
        else:
            if stored and stored < today:
                log(con, g["id"], ts, "deadline", "mismatch", stored, "passed; no new date on page")
            else:
                log(con, g["id"], ts, "deadline", "no_data", stored)

    # --- amount ---
    page_amounts = extract_amounts(text)
    stored_vals = stored_amount_values(g["amount"])
    if stored_vals and page_amounts:
        page_vals = {round(v) for _, v in page_amounts}
        if any(round(sv) in page_vals for sv in stored_vals):
            log(con, g["id"], ts, "amount", "confirmed", g["amount"])
        else:
            # page's dominant (largest, most-repeated) figure
            top = max(set(page_amounts), key=lambda cv: (page_amounts.count(cv), cv[1]))
            distinct = {round(v) for _, v in page_amounts}
            if len(distinct) == 1:
                new_amount = fmt_amount(*top)
                con.execute("UPDATE grants SET amount=? WHERE id=?", (new_amount, g["id"]))
                log(con, g["id"], ts, "amount", "updated", g["amount"], new_amount)
                outcome.append("amount updated")
            else:
                log(con, g["id"], ts, "amount", "mismatch", g["amount"],
                    "; ".join(fmt_amount(c, v) for c, v in sorted(set(page_amounts), key=lambda x: -x[1])[:4]))
    elif page_amounts:
        log(con, g["id"], ts, "amount", "no_data", g["amount"],
            "; ".join(fmt_amount(c, v) for c, v in sorted(set(page_amounts), key=lambda x: -x[1])[:3]))
    else:
        log(con, g["id"], ts, "amount", "no_data", g["amount"])

    # --- closed detection ---
    if CLOSED_WORDS.search(text):
        log(con, g["id"], ts, "status", "closed_detected", g["deadline_text"])
        outcome.append("closed wording on page")

    con.execute("UPDATE grants SET last_checked=? WHERE id=?", (ts, g["id"]))
    return "; ".join(outcome) if outcome else "ok"


def report(con, limit=200):
    ts = con.execute("SELECT MAX(checked_at) FROM verifications").fetchone()[0]
    if not ts:
        print("No verification runs logged yet.")
        return
    run_day = ts[:10]
    rows = con.execute("""
        SELECT grant_id, field, action, stored, page_value FROM verifications
        WHERE checked_at LIKE ? ORDER BY
        CASE action WHEN 'updated' THEN 0 WHEN 'closed_detected' THEN 1
                    WHEN 'mismatch' THEN 2 WHEN 'link_dead' THEN 3
                    WHEN 'no_data' THEN 4 ELSE 5 END
        LIMIT ?""", (run_day + "%", limit)).fetchall()
    counts = {}
    for r in rows:
        counts[r[2]] = counts.get(r[2], 0) + 1
    print(f"=== Latest crawl ({run_day}) ===  " + "  ".join(f"{k}:{v}" for k, v in counts.items()))
    for gid, field, action, stored, page in rows:
        if action == "confirmed":
            continue
        print(f"  {gid:14} {field:8} {action:15} stored={stored[:50]!r}  page={page[:60]!r}")


def main():
    args = [a for a in sys.argv[1:]]
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    ensure_tables(con)

    if "--report" in args:
        report(con)
        return

    curated_only = "--curated" in args
    ids = {a for a in args if not a.startswith("--")}

    q = "SELECT * FROM grants"
    if curated_only:
        q += " WHERE origin='curated'"
    q += " ORDER BY CASE origin WHEN 'curated' THEN 0 ELSE 1 END, id"
    grants = con.execute(q).fetchall()

    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT

    today = date.today().isoformat()
    n = 0
    for g in grants:
        if ids and g["id"] not in ids:
            continue
        if not g["link"]:
            continue
        ts = datetime.now().isoformat(timespec="seconds")
        result = crawl_grant(con, g, session, today, ts)
        con.commit()
        n += 1
        print(f"  {g['id']:14} {result}")
        time.sleep(DELAY)

    print(f"\nCrawled {n} grants.")
    report(con)
    con.close()


if __name__ == "__main__":
    main()
