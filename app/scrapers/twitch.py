"""
Twitch public profile scraper.

Prefers the official Helix API (https://dev.twitch.tv/console) when a Client
ID is provided via the ``TWITCH_CLIENT_ID`` environment variable. Falls back
to a best-effort public homepage scrape otherwise. Never attempts to defeat
anti-bot or to scrape a logged-in area.
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
        if p["full_name"] is None and p["bio"] is None:
            _via_html(p, session)
    finally:
        if own_session:
            session.close()

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
    # Twitch embeds a "channel" object inside the homepage's JS. Search by
    # keys we care about — best effort, never aggressive.
    m = re.search(r'"profile_image_url"\s*:\s*"([^"]+)"', html)
    if m and not p["profile_image"]:
        p["profile_image"] = m.group(1).replace("\\u0026", "&")
    m = re.search(r'"description"\s*:\s*"([^"]*)"', html)
    if m and not p["bio"]:
        p["bio"] = m.group(1)
    bl = re.search(r'"display_name"\s*:\s*"([^"]+)"', html)
    if bl and not p["full_name"]:
        p["full_name"] = bl.group(1)
    # Pull any obviously-public social links Twitch shows in channel panels
    social = re.findall(r'href="(https?://(?:twitter|x|youtube|instagram|tiktok)\.com/[^"]+)"', html)
    if social:
        p["links"] = list(dict.fromkeys(social))[:25]


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
