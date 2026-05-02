import os
from dataclasses import dataclass

@dataclass(frozen=True)
class Settings:
    db_path: str
    site_title: str
    site_url: str

def get_settings() -> Settings:
    return Settings(
        db_path=os.environ.get("DB_PATH", "./data/rome.sqlite3"),
        site_title=os.environ.get("SITE_TITLE", "Explore Rome Tours"),
        site_url=os.environ.get(
            "SITE_URL",
            "https://" + os.environ["VERCEL_URL"] if "VERCEL_URL" in os.environ else "http://localhost:8000"
        ),
    )
