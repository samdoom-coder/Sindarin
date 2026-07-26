"""
Linktree (and link-in-bio variants) public scraper — no login.

Recognizes Linktree, Stan, Bio.link, and Linkr-style pages. Reads only the
public profile page and the JSON embedded in the markup. No magic — if the
page returns an empty interstitial we report nothing.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import re
from typing import Any, Dict, Optional

from . import _http
from .schemas import new_profile, validate

log = logging.getLogger("sindarin.scrapers.linktree")

# Platform host -> identifier for slugfy
_HOSTS = {
    "linktr.ee": "linktree",
    "stan.store": "stan",
    "bio.link": "biolink",
    "linkr.bio": "linkr",
    "linkr.in": "linkr",
}


def scrape(url_or_slug: str, session: Optional[_http.Session] = None) -> Dict[str, Any]:
    """Scrape a link-in-bio page.

    ``url_or_slug`` may be a full URL (e.g. ``https://linktr.ee/foo``) or a
    bare slug (in which case it is treated as a Linktree slug).
    """
    if not url_or_slug:
        raise ValueError("url or slug required")

    target, platform = _normalize_target(url_or_slug)
    own_session = session is None
    if own_session:
        session = _http.Session()

    p = new_profile(platform, target, _slug_of(target))

    try:
        resp = session.get(target, headers={"Accept-Language": "en-US,en;q=0.9"})
    finally:
        if own_session:
            session.close()

    p["scraped_at"] = _now_iso()
    if resp.status_code != 200:
        log.warning("%s returned %d", platform, resp.status_code)
        validate(p)
        return p

    html = resp.text

    # Linktree embeds a __NEXT_DATA__ blob.
    blob = _extract_next_data(html)
    if platform == "linktree" and blob:
        _parse_linktree(p, blob)
    elif blob:
        _parse_generic(p, blob)

    # Last-resort link + email regexes on raw HTML
    if not p["links"]:
        hrefs = re.findall(r'href="(https?://[^"]+)"', html)
        hrefs = [h for h in hrefs if not _is_internal(target, h)]
        p["links"] = list(dict.fromkeys(hrefs))[:50]
    if not p["email"] and p["bio"]:
        m = _http.email_regex().search(p["bio"])
        if m:
            p["email"] = m.group(0)
    if not p["email"]:
        m = re.search(r'mailto:([^"\s]+)', html)
        if m:
            p["email"] = m.group(1)

    validate(p)
    return p


# --- helpers --------------------------------------------------------------------

def _normalize_target(s: str) -> tuple[str, str]:
    s = s.strip()
    if s.startswith(("http://", "https://")):
        from urllib.parse import urlparse
        host = urlparse(s).netloc.lower()
        plat = _HOSTS.get(host, "linkbio")
        return s.rstrip("/"), plat
    return f"https://linktr.ee/{s}", "linktree"


def _slug_of(url: str) -> str:
    from urllib.parse import urlparse
    path = urlparse(url).path.strip("/")
    return path.split("/", 1)[0] or url


def _is_internal(base: str, href: str) -> bool:
    from urllib.parse import urlparse
    return urlparse(base).netloc.lower() == urlparse(href).netloc.lower()


def _extract_next_data(html: str) -> Optional[dict]:
    m = re.search(
        r'<script id="__NEXT_DATA__"[^>]*>(\{.*?\})</script>',
        html, re.DOTALL,
    )
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def _parse_linktree(p: Dict[str, Any], blob: dict) -> None:
    account = (
        ((blob.get("props") or {}).get("pageProps") or {}).get("account") or {}
    )
    p["full_name"] = account.get("pageTitle") or account.get("name")
    p["bio"] = account.get("description")
    p["profile_image"] = account.get("profilePictureUrl")
    links = []
    for ln in account.get("links", []) or []:
        if isinstance(ln, dict):
            url = ln.get("url")
            if url:
                links.append(url)
    if links:
        p["links"] = links


def _parse_generic(p: Dict[str, Any], blob: dict) -> None:
    # Best effort — vary across Stan/Bio.link. We try the same shape; if
    # empty we fall through to HTML link scraping.
    _parse_linktree(p, blob)


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
