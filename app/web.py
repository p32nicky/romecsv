import logging
import os
from datetime import datetime, timezone, timedelta
from xml.etree.ElementTree import Element, SubElement, tostring

import httpx
from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import get_settings
from app.db import init_db, list_tours, get_latest_tours, get_tour_by_slug, save_article

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


STOP_WORDS = {"a","an","the","and","or","but","in","on","at","to","for","of","with","from","by","as","is","it","this","that","was","are","be","been","has","have","had","not","its"}

def _build_article_html(tour: dict, api_key: str) -> str:
    kw = tour.get("keywords", "") or ""
    raw = [w.strip() for w in kw.split(",") if w.strip()]
    good = [w.replace(" ", "") for w in raw if w.strip().lower() not in STOP_WORDS and len(w.strip()) > 2]
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
- Include specific details about what visitors will see and experience
- Mention ideal visitor types (families, couples, solo travellers, history buffs, food lovers, etc.)
- Practical tips section (what to wear, when to arrive, what to bring)
- Use <strong> tags to bold key phrases and keywords
- Write in HTML using ONLY <h1> <h2> <p> <strong> <ul> <li> tags
- Do NOT include <html> <head> <body> tags
- Conversational but authoritative tone"""

    resp = httpx.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "content-type": "application/json"},
        json={"model": "llama-3.3-70b-versatile", "max_tokens": 1800, "messages": [{"role": "user", "content": prompt}]},
        timeout=55,
    )
    if resp.status_code != 200:
        raise RuntimeError(resp.text[:300])
    article_html = resp.json()["choices"][0]["message"]["content"]
    article_html += f"""
<hr/>
<h2>Book This Tour Today</h2>
<p>Don't miss out! <strong><a href="{tour['link']}" target="_blank" rel="nofollow noopener">Book {tour['title']} on Viator →</a></strong></p>
<p>Secure your spot now — spaces fill up fast!</p>
<p class="hashtags">{tags}</p>"""
    return article_html


@app.get("/tour/{slug}/article", response_class=HTMLResponse)
async def tour_article(request: Request, slug: str):
    tour = get_tour_by_slug(settings.db_path, slug)
    if not tour:
        return HTMLResponse("Tour not found", status_code=404)
    existing = (dict(tour).get("article_text") or "").strip()
    if existing:
        return templates.TemplateResponse("article.html", {
            "request": request, "t": tour,
            "article_html": existing,
            "site_title": settings.site_title,
        })
    return templates.TemplateResponse("article_loading.html", {
        "request": request, "t": tour, "site_title": settings.site_title,
    })


@app.post("/api/generate-article/{slug}")
async def generate_article(slug: str):
    tour = get_tour_by_slug(settings.db_path, slug)
    if not tour:
        return JSONResponse({"error": "not found"}, status_code=404)
    api_key = os.environ.get("GROQ_API_KEY", "")
    if not api_key:
        return JSONResponse({"error": "GROQ_API_KEY not set"}, status_code=500)
    try:
        html = _build_article_html(dict(tour), api_key)
        save_article(settings.db_path, slug, html)
        return JSONResponse({"status": "done"})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/clear-article/{slug}")
async def clear_article(slug: str):
    tour = get_tour_by_slug(settings.db_path, slug)
    if not tour:
        return JSONResponse({"error": "not found"}, status_code=404)
    save_article(settings.db_path, slug, "")
    return JSONResponse({"status": "cleared"})


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

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for idx, t in enumerate(tours):
        item = SubElement(channel, "item")
        tour_url = f"{settings.site_url}/tour/{t['slug']}"
        unique_guid = f"{tour_url}?d={today}"
        SubElement(item, "title").text = t["title"]
        SubElement(item, "link").text = tour_url
        SubElement(item, "guid", isPermaLink="false").text = unique_guid
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

        # Today's date + space pins 2 hours apart
        dt = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0) + timedelta(hours=idx * 2)
        SubElement(item, "pubDate").text = dt.strftime("%a, %d %b %Y %H:%M:%S +0000")

    xml_str = '<?xml version="1.0" encoding="UTF-8"?>\n' + tostring(rss, encoding="unicode")
    return Response(content=xml_str, media_type="application/rss+xml")


@app.get("/sitemap.xml")
async def sitemap():
    from app.db import _get_conn, USE_POSTGRES
    with _get_conn(settings.db_path) as conn:
        if USE_POSTGRES:
            cur = conn.cursor()
            cur.execute("SELECT slug FROM tours ORDER BY first_seen_at DESC")
            slugs = [r["slug"] for r in cur.fetchall()]
        else:
            slugs = [r[0] for r in conn.execute("SELECT slug FROM tours ORDER BY first_seen_at DESC").fetchall()]
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    lines.append(f'  <url><loc>{settings.site_url}/</loc><changefreq>daily</changefreq><priority>1.0</priority></url>')
    for slug in slugs:
        lines.append(f'  <url><loc>{settings.site_url}/tour/{slug}</loc><changefreq>weekly</changefreq><priority>0.8</priority></url>')
    lines.append('</urlset>')
    return Response("\n".join(lines), media_type="application/xml")


@app.get("/api/status")
async def status():
    _, total = list_tours(settings.db_path, per_page=1)
    return JSONResponse({"total_tours": total})
