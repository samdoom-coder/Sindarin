"""
Twitch public profile scraper.

Prefers the official Helix API (https://dev.twitch.tv/console) when a Client
ID is provided via the ``TWITCH_CLIENT_ID`` environment variable. Falls back
to a best-effort public homepage scrape otherwise. Never attempts to defeat
anti-bot or to scrape a logged-in area.

The Helix ``/users`` endpoint does **not** return follower/following/post
counts without an OAuth token, so even with a Client ID those fields may be
``None``. The HTML fallback parses structured JSON-LD data that Twitch embeds
in its public pages.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import re
from typing import Any, Dict, Optional

from . import _http
from .schemas import new_profile, validate

log = logging.getLogger("sindarin.scrapers.twitch")

CLIENT_ID_ENV = "TWITCH_CLIENT_ID"
HELIX_USERS = "https://api.twitch.tv/helix/users"
_CHANNEL_URL = "https://www.twitch.tv/{login}"

# Regex to find the JSON-LD blob Twitch embeds in <script type="application/ld+json">
_JSONLD_RE = re.compile(
    r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
    re.DOTALL,
)

# Suffixes that Twitch appends to the og:title (e.g. " - Live on Twitch")
_TITLE_SUFFIX_RE = re.compile(r" - (?:Live on )?Twitch$", re.IGNORECASE)

# Twitch appends live viewer info to the meta description, e.g.
# "...my bio text | Streaming valorant for 4365 viewers."
_LIVE_VIEWERS_RE = re.compile(r"\s*\|\s*Streaming\s+.+?\s+for\s+\d+\s+viewers?\.\s*$", re.IGNORECASE)


def scrape(login: str, session: Optional[_http.Session] = None) -> Dict[str, Any]:
    login = (login or "").strip().lstrip("@")
    if not login:
        raise ValueError("login required")
    source_url = _CHANNEL_URL.format(login=login)
    p = new_profile("twitch", source_url, login)

    own_session = session is None
    if own_session:
        session = _http.Session()
    try:
        client_id = os.environ.get(CLIENT_ID_ENV, "").strip()
        if client_id:
            _via_helix(p, session, client_id)
        # Run HTML fallback to fill gaps (profile_image, links, etc.) that the
        # Helix /users endpoint does not return, or when no Client ID is set.
        if not p["profile_image"] or not p["links"] or (p["full_name"] is None and p["bio"] is None):
            _via_html(p, session)
    finally:
        if own_session:
            session.close()

    # Extract email / phone from bio text if not already found
    if p["bio"]:
        if not p["email"]:
            m = _http.email_regex().search(p["bio"])
            if m:
                p["email"] = m.group(0)
        if not p["phone"]:
            m = _http.phone_regex().search(p["bio"])
            if m:
                p["phone"] = m.group(0)

    p["scraped_at"] = _now_iso()
    validate(p)
    return p


# --- Helix ---------------------------------------------------------------------

def _via_helix(p: Dict[str, Any], session: _http.Session, client_id: str) -> None:
    headers = {
        "Client-Id": client_id,
        "Accept": "application/json",
    }
    try:
        resp = session.get(HELIX_USERS, headers=headers, params={"login": p["username"]}, want_json=True)
    except Exception as e:
        log.debug("Helix users request failed: %s", e)
        return
    if resp.status_code != 200:
        log.warning("Helix returned %d for %s — falling back to HTML", resp.status_code, p["username"])
        return
    data = resp.json().get("data", [])
    if not data:
        log.info("Helix: no user %s", p["username"])
        return
    u = data[0]
    p["full_name"] = u.get("display_name")
    p["bio"] = u.get("description")
    p["profile_image"] = u.get("profile_image_url")
    p["raw"]["broadcaster_type"] = u.get("broadcaster_type")
    p["raw"]["type"] = u.get("type")
    p["raw"]["view_count"] = u.get("view_count")


# --- HTML fallback --------------------------------------------------------------

def _via_html(p: Dict[str, Any], session: _http.Session) -> None:
    try:
        resp = session.get(p["source_url"], headers={"Accept-Language": "en-US,en;q=0.9"})
    except Exception as e:
        log.debug("Twitch HTML fetch failed: %s", e)
        return
    if resp.status_code != 200:
        log.warning("Twitch HTML returned %d for %s", resp.status_code, p["username"])
        return
    html = resp.text

    _parse_jsonld(p, html)
    _parse_meta_tags(p, html)

    # Legacy regex fallbacks for fields not always present in JSON-LD/meta
    if not p["profile_image"]:
        m = re.search(r'"profile_image_url"\s*:\s*"([^"]+)"', html)
        if m:
            p["profile_image"] = m.group(1).replace("\\u0026", "&")
    if not p["full_name"]:
        bl = re.search(r'"display_name"\s*:\s*"([^"]+)"', html)
        if bl:
            p["full_name"] = bl.group(1)


def _parse_jsonld(p: Dict[str, Any], html: str) -> None:
    """Extract profile_image, bio, and social links from Twitch's JSON-LD blob.

    Twitch embeds structured data as ``<script type="application/ld+json">``
    containing a ``@graph`` array with a ``Person`` node (image, sameAs links)
    and a ``VideoObject`` node (description/bio, name).
    """
    m = _JSONLD_RE.search(html)
    if not m:
        return
    try:
        data = json.loads(m.group(1))
    except (ValueError, json.JSONDecodeError):
        log.debug("Twitch JSON-LD parse failed")
        return

    graph = data.get("@graph") if isinstance(data, dict) else None
    if not isinstance(graph, list):
        return

    for node in graph:
        if not isinstance(node, dict):
            continue
        ntype = node.get("@type", "")

        if ntype == "Person":
            if node.get("image") and not p["profile_image"]:
                p["profile_image"] = node["image"]
            if node.get("alternateName") and not p["full_name"]:
                alt = node["alternateName"]
                if alt != p["username"]:
                    p["full_name"] = alt
            same_as = node.get("sameAs")
            if isinstance(same_as, list) and same_as:
                if not p["links"]:
                    p["links"] = []
                for link in same_as:
                    if isinstance(link, str) and link.startswith(("http://", "https://")):
                        if link not in p["links"]:
                            p["links"].append(link)
                p["links"] = p["links"][:25]

        elif ntype == "VideoObject":
            if node.get("description") and not p["bio"]:
                p["bio"] = node["description"]


def _parse_meta_tags(p: Dict[str, Any], html: str) -> None:
    """Extract full_name from og:title and fill remaining gaps from meta tags."""
    if not p["full_name"]:
        m = re.search(r'<meta[^>]*property="og:title"[^>]*content="([^"]+)"', html)
        if not m:
            m = re.search(r'<meta[^>]*name="twitter:title"[^>]*content="([^"]+)"', html)
        if m:
            title = m.group(1)
            title = _TITLE_SUFFIX_RE.sub("", title)
            title = title.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
            if title and title != p["username"]:
                p["full_name"] = title

    # Bio fallback: Twitch puts the channel description in <meta name="description">
    if not p["bio"]:
        m = re.search(r'<meta[^>]*name="description"[^>]*content="([^"]+)"', html, re.IGNORECASE)
        if not m:
            m = re.search(r'<meta[^>]*property="og:description"[^>]*content="([^"]+)"', html, re.IGNORECASE)
        if m:
            desc = m.group(1)
            desc = _LIVE_VIEWERS_RE.sub("", desc).strip()
            desc = desc.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
            if desc:
                p["bio"] = desc


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
