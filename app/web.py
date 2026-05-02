import logging
import os
from datetime import datetime, timezone, timedelta
from xml.etree.ElementTree import Element, SubElement, tostring

from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import get_settings
from app.db import init_db, list_tours, get_latest_tours, get_tour_by_slug

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

settings = get_settings()
init_db(settings.db_path)

app = FastAPI(title=settings.site_title)

BASE_DIR = os.path.dirname(__file__)
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))


@app.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    q: str = Query("", alias="q"),
    page: int = Query(1, ge=1),
):
    per_page = 24
    rows, total = list_tours(settings.db_path, query=q, page=page, per_page=per_page)
    total_pages = max(1, (total + per_page - 1) // per_page)
    return templates.TemplateResponse("index.html", {
        "request": request,
        "tours": rows,
        "query": q,
        "page": page,
        "total": total,
        "total_pages": total_pages,
        "site_title": settings.site_title,
    })


@app.get("/tour/{slug}", response_class=HTMLResponse)
async def tour_detail(request: Request, slug: str):
    tour = get_tour_by_slug(settings.db_path, slug)
    if not tour:
        return HTMLResponse("Tour not found", status_code=404)
    return templates.TemplateResponse("tour.html", {
        "request": request,
        "t": tour,
        "site_title": settings.site_title,
    })


@app.get("/feed.xml")
async def rss_feed():
    day = datetime.now(timezone.utc).timetuple().tm_yday
    daily_offset = (day - 1) * 10
    tours = get_latest_tours(settings.db_path, limit=10, offset=daily_offset)

    rss = Element("rss", version="2.0")
    rss.set("xmlns:media", "http://search.yahoo.com/mrss/")
    channel = SubElement(rss, "channel")
    SubElement(channel, "title").text = settings.site_title
    SubElement(channel, "link").text = settings.site_url
    SubElement(channel, "description").text = "The best Rome tours and experiences"
    SubElement(channel, "language").text = "en-us"
    SubElement(channel, "lastBuildDate").text = datetime.now(timezone.utc).strftime(
        "%a, %d %b %Y %H:%M:%S +0000"
    )

    for idx, t in enumerate(tours):
        item = SubElement(channel, "item")
        tour_url = f"{settings.site_url}/tour/{t['slug']}"
        SubElement(item, "title").text = t["title"]
        SubElement(item, "link").text = tour_url
        SubElement(item, "guid", isPermaLink="true").text = tour_url
        SubElement(item, "description").text = (
            f"{t['description']}<br/>"
            f'<a href="{t["link"]}">Book on Viator →</a>'
        )

        if t["image_url"]:
            enc = SubElement(item, "enclosure")
            enc.set("url", t["image_url"])
            enc.set("type", "image/png")
            enc.set("length", "0")
            media = SubElement(item, "media:content")
            media.set("url", t["image_url"])
            media.set("medium", "image")

        # Space pins 2 hours apart
        try:
            dt = datetime.fromisoformat(t["publish_date"].replace("Z", "+00:00"))
            dt = dt + timedelta(hours=idx * 2)
            pub_date = dt.strftime("%a, %d %b %Y %H:%M:%S +0000")
        except Exception:
            pub_date = ""
        SubElement(item, "pubDate").text = pub_date

    xml_str = '<?xml version="1.0" encoding="UTF-8"?>\n' + tostring(rss, encoding="unicode")
    return Response(content=xml_str, media_type="application/rss+xml")


@app.get("/api/status")
async def status():
    _, total = list_tours(settings.db_path, per_page=1)
    return JSONResponse({"total_tours": total})
