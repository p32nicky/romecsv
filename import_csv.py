"""
Load rome_pinterest_upload_ExploreRome.csv into the database.
Usage:
  1. Copy .env.example to .env, fill in DATABASE_URL
  2. Run: .venv\Scripts\python import_csv.py [path/to/csv]
"""
import csv
import os
import re
import sys
from datetime import datetime, timezone

# Load .env
env_path = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

from app.config import get_settings
from app.db import init_db, upsert_tours


def slugify(title: str) -> str:
    s = title.lower()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_]+", "-", s).strip("-")
    return s[:80]


def main():
    csv_path = sys.argv[1] if len(sys.argv) > 1 else \
        r"C:\Users\nickd\Downloads\rome_pinterest_upload_ExploreRome.csv"

    if not os.path.exists(csv_path):
        print(f"CSV not found: {csv_path}")
        sys.exit(1)

    settings = get_settings()
    print("Initialising DB...")
    init_db(settings.db_path)

    items = []
    now = datetime.now(timezone.utc).isoformat()

    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            title = row.get("Title", "").strip()
            if not title:
                continue
            slug = slugify(title)
            items.append({
                "title": title,
                "slug": slug,
                "image_url": row.get("Media URL", "").strip(),
                "description": row.get("Description", "").strip() or f"Explore Rome: {title}",
                "link": row.get("Link", "").strip(),
                "keywords": row.get("Keywords", "").strip(),
                "publish_date": row.get("Publish date", "").strip() or now,
                "first_seen_at": now,
            })

    print(f"Parsed {len(items)} tours from CSV")
    inserted = upsert_tours(settings.db_path, items)
    print(f"Inserted {inserted} new tours. Done.")


if __name__ == "__main__":
    main()
