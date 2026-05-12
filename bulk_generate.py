"""
Bulk generate articles for all Rome tours that don't have one yet.
Run locally: python bulk_generate.py

Set XAI_API_KEY env var before running (free at console.x.ai):
  $env:XAI_API_KEY="xai-..."
  python bulk_generate.py

For Neon Postgres (optional), set DATABASE_URL env var.
Otherwise uses local SQLite at ./data/rome.sqlite3.
"""
import os
import time
import httpx

# Load .env if present
env_path = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

XAI_API_KEY = os.environ.get("XAI_API_KEY", "")
if not XAI_API_KEY:
    raise SystemExit("ERROR: Set XAI_API_KEY env var first. Get a free key at console.x.ai")

from app.config import get_settings
from app.db import _get_conn, _rows, save_article, USE_POSTGRES

settings = get_settings()
DB = settings.db_path

STOP = {"a","an","the","and","or","but","in","on","at","to","for","of","with","from","by","as","is","it","this","that","was","are","be","been","has","have","had","not","its"}


def generate(tour: dict) -> str:
    kw = tour.get("keywords", "") or ""
    raw = [w.strip() for w in kw.split(",") if w.strip()]
    good = [w.replace(" ", "") for w in raw if w.strip().lower() not in STOP and len(w.strip()) > 2]
    tags = " ".join(f"#{w.title()}" for w in good if w)[:200]
    if not tags:
        tags = "#Rome #Italy #Travel #RomeTours #VisitRome #ItalyTravel #Viator #TravelItaly #RomeExperiences #TravelGuide"

    prompt = f"""Write a highly detailed, SEO-optimised travel article about this Rome tour.
Tour Title: {tour['title']}
Description: {tour['description']}
Keywords: {kw}
Requirements:
- 900-1100 words total
- Catchy SEO-optimised <h1> title (include keywords like "Rome", "Italy", "best", "2025", etc.)
- Engaging hook intro paragraph that grabs attention
- At least 5 <h2> subheadings: overview, highlights, what to expect, tips for visitors, why book this tour
- Naturally weave in SEO keywords (Rome tours, things to do in Rome, best Rome experiences, etc.)
- Mention ideal visitor types (families, couples, solo travellers, history buffs, food lovers)
- Practical tips (what to wear, when to arrive, what to bring)
- Use <strong> tags to bold key phrases
- HTML only: <h1> <h2> <p> <strong> <ul> <li> — no <html><head><body> tags"""

    resp = httpx.post(
        "https://api.x.ai/v1/chat/completions",
        headers={"Authorization": f"Bearer {XAI_API_KEY}", "content-type": "application/json"},
        json={"model": "grok-3-mini", "max_tokens": 1800, "messages": [{"role": "user", "content": prompt}]},
        timeout=60,
    )
    if resp.status_code != 200:
        raise RuntimeError(resp.text[:200])

    html = resp.json()["choices"][0]["message"]["content"]
    html += f"""
<hr/>
<h2>Book This Tour Today</h2>
<p>Don't miss out! <strong><a href="{tour['link']}" target="_blank" rel="nofollow noopener">Book {tour['title']} on Viator →</a></strong></p>
<p>Secure your spot now — spaces fill up fast!</p>
<p class="hashtags">{tags}</p>"""
    return html


def main():
    # Ensure article_text column exists
    with _get_conn(DB) as conn:
        if USE_POSTGRES:
            conn.cursor().execute("ALTER TABLE tours ADD COLUMN IF NOT EXISTS article_text TEXT")
        else:
            try:
                conn.execute("ALTER TABLE tours ADD COLUMN article_text TEXT")
            except Exception:
                pass

    with _get_conn(DB) as conn:
        if USE_POSTGRES:
            cur = conn.cursor()
            cur.execute("SELECT slug, title, description, link, keywords FROM tours WHERE article_text IS NULL OR article_text = '' ORDER BY id")
            tours = [dict(zip([d[0] for d in cur.description], row)) for row in cur.fetchall()]
        else:
            rows = conn.execute("SELECT slug, title, description, link, keywords FROM tours WHERE article_text IS NULL OR article_text = '' ORDER BY id").fetchall()
            tours = [dict(r) for r in rows]

    print(f"{len(tours)} tours need articles")
    ok = 0
    for i, tour in enumerate(tours):
        try:
            html = generate(tour)
            save_article(DB, tour["slug"], html)
            ok += 1
            print(f"[{i+1}/{len(tours)}] ✅ {tour['title'][:60]}")
        except Exception as e:
            print(f"[{i+1}/{len(tours)}] ❌ {tour['title'][:60]} — {e}")
        time.sleep(0.5)

    print(f"\nDone. {ok}/{len(tours)} generated.")


if __name__ == "__main__":
    main()
