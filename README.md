# Grant Search — A Brilliant Mind

A searchable database of scientific grants and startup funding, hosted free
on GitHub Pages and kept current by a daily agent.

Live site: `https://mina-70.github.io/grant-search/`

## How it works

    data/grants.xlsx   (you edit this — the source of truth)
           |
           v  python import_grants.py
    grants.db          (local SQLite, not committed)
           |
           v  python build_static.py
    docs/grants.json   (committed — what the website reads)
    docs/index.html    (the website itself)

The website is pure HTML/JS: it loads `grants.json` and does all searching
and filtering in the browser. No server, no database, nothing to pay for.

## Two modes

- **Scientific grants** — filters by country, career stage (Master / PhD /
  Early Postdoc / Senior Postdoc / Any), and field.
- **Startup grants** — filters by country, funding stage (Pre-seed / Seed /
  Growth), whether the company exists yet, and funding type (grant, equity,
  soft loan, incubation, advisory).

Each grant is labelled **For research**, **For startups**, or
**For startups doing research**.

## Setup (one time)

1. Push this folder to GitHub.
2. Repo **Settings -> Pages -> Source: "Deploy from a branch",
   Branch: `main`, Folder: `/docs`** -> Save.
3. Wait ~1 minute. Your site is live at
   `https://<username>.github.io/<repo>/`.

## Updating the data

```bash
# 1. edit data/grants.xlsx (add rows, fix links, correct deadlines)
pip install -r requirements.txt
python import_grants.py     # xlsx -> grants.db
python crawler.py           # optional: fact-check against official pages
python build_static.py      # -> docs/grants.json
# 2. commit and push. The live site updates within a minute.
```

Or just edit the spreadsheet on GitHub and let the daily agent do the rest.

## The daily agent

`.github/workflows/daily-update.yml` runs every morning at 07:00 Austria
time: re-imports the spreadsheet, fact-checks every grant against its
official page, rebuilds `docs/grants.json`, and commits the result.
Run it manually anytime from the **Actions** tab -> Daily update ->
Run workflow.

## Adding a grant

Append a row to `data/grants.xlsx` with the next ID. For startup entries
also fill **Startup Stage** (`Pre-seed`, `Seed`, `Growth` — semicolons for
several), **Company Status**, and **Funding Type**. If unsure about a
deadline write "Annual call (check site)" — honest beats wrong.

## Custom domain (optional)

Settings -> Pages -> Custom domain -> `grants.abrilliantmind.blog`, then add
a CNAME record at your DNS provider (Domain.com) pointing `grants` to
`<username>.github.io`.

## Bulk feeds (optional)

`ingest_grantsgov.py` and `ingest_eu_portal.py` pull thousands of live US
federal and EU calls into the database. Run them before `build_static.py`
if you want them included — note this makes `grants.json` much larger.

## Files

| File | Purpose |
|---|---|
| `data/grants.xlsx` | Source of truth — edit this |
| `import_grants.py` | Spreadsheet -> SQLite, normalises countries/stages/fields |
| `crawler.py` | Fact-checks grants against their official pages |
| `build_static.py` | SQLite -> `docs/` (the website) |
| `docs/` | The published website (GitHub Pages serves this) |
| `app.py` | Optional local FastAPI server (not needed for Pages) |
