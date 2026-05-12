"""
Post Rome tours to Substack - run locally from your machine.
Usage: python post_to_substack.py

Refreshes cookies when they expire:
  1. Log in to Substack in Chrome
  2. Open DevTools → Application → Cookies → copy the values below
"""
import time
import os
import re
from datetime import datetime, timezone
from curl_cffi import requests

from app.config import get_settings
from app.db import get_next_unposted_substack, mark_substack_posted, save_article, USE_POSTGRES, _get_conn

settings = get_settings()
DB = settings.db_path

PUBLICATION = "explorerometours.substack.com"
API_BASE = f"https://{PUBLICATION}/api/v1"
BATCH = 5  # posts per run

# ── Refresh these cookies when posting stops working ──────────────────────────
# Get them from Chrome DevTools → Application → Cookies → substack.com
SESSION_COOKIE = "s%3A6U8sY0X79qmz7bF6SqbPQxrUstG_QvvX.S0cXmMQL4NrdAtrGYf%2FtOiOWFWP5FVPL2acb%2FXmQdbo"
CF_CLEARANCE   = "qndFGDPP3BPyya2PaWEF0B5k4rUXm7I0MtYgExvbms8-1778418260-1.2.1.1-NzB3QbIGQx4PK1dx8pwuqRZPfYFLWnKrU3B30k__xBaOJnNigbHfupYHUM3K2wu2DWyrKpWVegNTx.vWM2.pYox..SQ.eBQamv7fqp4v_cnwtYHm5DHlzNLMhQwuF8gZTWiiafHrZgX5UgBX2vr7kl2WkuQ1fZL14BsBC6kaLkqBLatmunrKL84O4i_3U6x_wyqI0QvOxhuqNfQ7MyPVuFCey_fqafhHQA0wWB0qe7pKe2emuq.tap0CAkA8S9FUw4jByI.J1.joknwPCEUJ.or1VbpqY086JFoxmSu0aFJBqgw20sBb8cE5vKuhAJbZEqzhEfegmF6fDh5rydimzQ"
CF_BM          = "CG5OJBbc5DtIm.a2Yq3Levt0h95wQVBtPTgrtyQIpF0-1778425806.6798036-1.0.1.1-18xrl5kddFJ6wAGcDUneJcAigzKEQsO4K.WkE7G_54aUtQ_JV0V4uzJvEuXP9uwnkrpONYhLXamDEPmS4OugeShWkmdZaV8m_CfPW2MoudyXJbINX4yy273VeM4e.ein"
SUBSTACK_LLI   = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1c2VySWQiOjczNDI5MTAwLCJpYXQiOjE3Nzg0MTg0MzQsImV4cCI6MTc4MTAxMDQzNCwiYXVkIjoibGlrZWx5LWxvZ2dlZC1pbiJ9.3AoowJgtxltu6TaEnWaVb3UjoU8DKL4kmL9Ty99y0_c"
# ──────────────────────────────────────────────────────────────────────────────


def get_session():
    from urllib.parse import unquote
    s = requests.Session(impersonate="chrome120")
    s.cookies.update({
        "substack.sid": unquote(SESSION_COOKIE),
        "cf_clearance": CF_CLEARANCE,
        "__cf_bm": CF_BM,
        "substack.lli": SUBSTACK_LLI,
    })
    s.headers.update({
        "Referer": f"https://{PUBLICATION}/publish/post",
        "Origin": f"https://{PUBLICATION}",
    })
    r = s.get(f"https://{PUBLICATION}/publish/post", timeout=15)
    csrf = re.search(r'"csrf_token"\s*:\s*"([^"]+)"', r.text)
    if csrf:
        s.headers["X-CSRF-Token"] = csrf.group(1)
        print("  CSRF: found")
    else:
        print(f"  CSRF: not found (status={r.status_code})")
    return s


def html_to_substack(html_str: str) -> str:
    """Convert HTML to Substack ProseMirror doc JSON."""
    import json
    import html as html_mod
    from html.parser import HTMLParser

    class Parser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.nodes = []
            self._current = []
            self._marks = []
            self._list_items = []
            self._in_list = False

        def _text_node(self, text):
            if not text:
                return None
            node = {"type": "text", "text": text}
            if self._marks:
                node["marks"] = list(self._marks)
            return node

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag in ("h1", "h2", "h3", "h4"):
                self._current = []
            elif tag == "p":
                self._current = []
            elif tag == "ul":
                self._in_list = True
                self._list_items = []
            elif tag == "li":
                self._current = []
            elif tag in ("strong", "b"):
                self._marks.append({"type": "bold"})
            elif tag == "a":
                href = attrs.get("href", "")
                self._marks.append({"type": "link", "attrs": {"href": href}})

        def handle_endtag(self, tag):
            if tag in ("h1", "h2", "h3", "h4"):
                level = int(tag[1])
                content = [n for n in self._current if n]
                if content:
                    self.nodes.append({"type": "heading", "attrs": {"level": level}, "content": content})
                self._current = []
            elif tag == "p":
                content = [n for n in self._current if n]
                if content:
                    self.nodes.append({"type": "paragraph", "content": content})
                self._current = []
            elif tag == "li":
                content = [n for n in self._current if n]
                if content:
                    self._list_items.append({"type": "listItem", "content": [{"type": "paragraph", "content": content}]})
                self._current = []
            elif tag == "ul":
                if self._list_items:
                    self.nodes.append({"type": "bulletList", "content": self._list_items})
                self._list_items = []
                self._in_list = False
            elif tag in ("strong", "b"):
                self._marks = [m for m in self._marks if m["type"] != "bold"]
            elif tag == "a":
                self._marks = [m for m in self._marks if m["type"] != "link"]

        def handle_data(self, data):
            data = html_mod.unescape(data)
            if data.strip():
                node = self._text_node(data)
                if node:
                    self._current.append(node)

    p = Parser()
    p.feed(html_str)
    doc = {
        "type": "doc",
        "content": p.nodes or [{"type": "paragraph", "content": [{"type": "text", "text": " "}]}],
    }
    return json.dumps(doc)


def post_tour(tour: dict, session, user_id=None) -> dict:
    title = tour["title"]
    description = re.sub(r"<[^>]+>", "", tour.get("description", ""))[:300]
    image_url = tour.get("image_url", "")
    article_html = tour.get("article_text", "") or f"<p>{description}</p>"
    article_html += f'<p><a href="{tour["link"]}">👉 Book this Rome tour on Viator</a></p>'

    body_doc = html_to_substack(article_html)

    draft_payload = {
        "draft_title": title,
        "draft_subtitle": description,
        "draft_body": body_doc,
        "audience": "everyone",
        "section_chosen": False,
        "draft_bylines": [{"id": user_id, "is_guest": False}] if user_id else [],
        "cover_image": image_url or None,
    }

    resp = session.post(f"{API_BASE}/drafts", json=draft_payload, timeout=20)
    print(f"  Draft status: {resp.status_code}")
    if resp.status_code not in (200, 201):
        return {"error": f"{resp.status_code}: {resp.text[:200]}"}

    draft_id = resp.json().get("id")
    if not draft_id:
        return {"error": "No draft ID returned"}

    pub = session.post(f"{API_BASE}/drafts/{draft_id}/publish",
                       json={"send_email": False}, timeout=20)
    if pub.status_code in (200, 201):
        url = pub.json().get("url", f"https://{PUBLICATION}/p/{draft_id}")
        return {"url": url}
    return {"error": f"Publish {pub.status_code}: {pub.text[:200]}"}


def main():
    session = get_session()

    # Auth check + get user ID
    test = session.get(f"https://{PUBLICATION}/api/v1/publication", timeout=10)
    print(f"Auth check: {test.status_code}")
    pub_data = test.json() if test.status_code == 200 else {}
    user_id = pub_data.get("author_id") or pub_data.get("user_id")
    if not user_id:
        u = session.get(f"https://{PUBLICATION}/api/v1/user", timeout=10)
        user_id = u.json().get("id") if u.status_code == 200 else None
    print(f"User ID: {user_id}")

    tours = get_next_unposted_substack(DB, BATCH)
    if not tours:
        print("All tours posted — resetting and restarting from beginning...")
        with _get_conn(DB) as conn:
            if USE_POSTGRES:
                conn.cursor().execute("UPDATE tours SET substack_posted_at=NULL")
            else:
                conn.execute("UPDATE tours SET substack_posted_at=NULL")
        tours = get_next_unposted_substack(DB, BATCH)
    if not tours:
        print("No tours found!")
        return

    print(f"\nPosting {len(tours)} tours to Substack...")
    posted = 0
    for i, tour in enumerate(tours):
        print(f"\n[{i+1}/{len(tours)}] {tour['title'][:60]}")
        if not tour.get("article_text"):
            print("  ⚠️  No article — run bulk_generate.py first, or skip")
            continue
        result = post_tour(tour, session, user_id)
        if "url" in result:
            mark_substack_posted(DB, tour["slug"])
            posted += 1
            print(f"  ✅ {result['url']}")
        else:
            print(f"  ❌ {result['error']}")
        time.sleep(3)

    remaining = get_next_unposted_substack(DB, 9999)
    print(f"\nDone. Posted {posted}/{len(tours)}. {len(remaining)} tours remaining.")
    print("Run again to post next batch.")


if __name__ == "__main__":
    main()
