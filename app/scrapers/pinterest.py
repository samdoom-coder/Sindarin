"""
Pinterest public profile scraper — no login.

Pinterest exposes a public profile JSON endpoint at ``/_/_profile_root/{user}/``
which Sindarin reads. We do not attempt to bypass login walls or to scrape
private boards.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import re
from typing import Any, Dict, Optional

from . import _http
from .schemas import new_profile, validate

log = logging.getLogger("sindarin.scrapers.pinterest")

_PROFILE_URL = "https://www.pinterest.com/{username}/"
_RESOURCE_BASE = "https://www.pinterest.com/_/_profile_root/{username}/"


def scrape(username: str, session: Optional[_http.Session] = None) -> Dict[str, Any]:
    username = (username or "").strip().lstrip("@")
    if not username:
        raise ValueError("username required")
    url = _PROFILE_URL.format(username=username)

    own_session = session is None
    if own_session:
        session = _http.Session()

    p = new_profile("pinterest", url, username)

    try:
        resp = session.get(url, headers={"Accept-Language": "en-US,en;q=0.9"})
        if resp.status_code == 200:
            _parse_html(p, resp.text)
        # The resource endpoint sometimes needs a X-App-Version header; if it
        # 403s we just keep what we got from the HTML page.
        if p["full_name"] is None and p["bio"] is None:
            try:
                res = session.get(
                    _RESOURCE_BASE.format(username=username),
                    headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
                    want_json=True,
                )
                if res.status_code == 200:
                    _parse_resource(p, res.json())
            except Exception as e:
                log.debug("Pinterest resource endpoint failed: %s", e)
    finally:
        if own_session:
            session.close()

    p["scraped_at"] = _now_iso()
    validate(p)
    return p


# --- HTML parsing (best-effort against inline JSON) ----------------------------

def _parse_html(p: Dict[str, Any], html: str) -> None:
    # Pinterest embeds profile info inside <script id="initial-state"> or
    # ld+json blocks. We probe both.
    ld = re.search(
        r'<script type="application/ld\+json">(\{.*?\})</script>',
        html, re.DOTALL,
    )
    if ld:
        try:
            obj = json.loads(ld.group(1))
            _maybe_fill_from_ld(p, obj)
        except json.JSONDecodeError:
            pass

    if not p["profile_image"]:
        m = re.search(r'<meta property="og:image" content="([^"]+)"', html)
        if m:
            p["profile_image"] = m.group(1)
    if not p["full_name"]:
        m = re.search(r'<meta property="og:title" content="([^"]+)"', html)
        if m:
            p["full_name"] = m.group(1)
    if not p["bio"]:
        m = re.search(r'<meta name="description" content="([^"]+)"', html)
        if m:
            p["bio"] = m.group(1)


def _maybe_fill_from_ld(p: Dict[str, Any], obj: dict) -> None:
    if not isinstance(obj, dict):
        return
    if obj.get("@type") in ("Person", "ProfilePage") or "name" in obj:
        p["full_name"] = obj.get("name") or p.get("full_name")
        p["bio"] = obj.get("description") or p.get("bio")
        p["website"] = obj.get("url") or p.get("website")
        img = obj.get("image")
        if isinstance(img, dict):
            p["profile_image"] = img.get("url")
        elif isinstance(img, str):
            p["profile_image"] = img


def _parse_resource(p: Dict[str, Any], data: dict) -> None:
    user = data.get("resource_responses", [{}])[0].get("data", {})

    if isinstance(user, dict):
        p["full_name"] = user.get("full_name") or user.get("name")
        p["bio"] = user.get("about") or user.get("description")
        p["follower_count"] = user.get("follower_count") or user.get("follower_counts", {}).get("count")
        p["profile_image"] = (user.get("image_xlarge_url") or user.get("image_large_url") or user.get("image_medium_url"))
        p["website"] = user.get("website_url")
        p["location"] = user.get("location")
        loc = user.get("domain_url") or user.get("website_url")
        if loc and not p["website"]:
            p["website"] = loc


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
