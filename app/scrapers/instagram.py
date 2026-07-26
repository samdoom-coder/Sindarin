"""
Instagram public profile scraper — no login.

Instagram heavily rotates its public HTML and frequently inserts an
explicit login wall. This scraper reads only the public profile page and the
embedded JSON in <script type="application/json"> tags. If Instagram returns
a login interstitial (HTTP 200 with no profile JSON) we surface an empty
profile rather than attempting to bypass anything.

Sindarin never tries CAPTCHA solutions, login deception, or session
spoofing. If Instagram says "you must log in", the scraper reports that.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import re
from typing import Any, Dict, Optional

from . import _http
from .schemas import new_profile, validate

log = logging.getLogger("sindarin.scrapers.instagram")

_PROFILE_URL = "https://www.instagram.com/{username}/"


def scrape(username: str, session: Optional[_http.Session] = None) -> Dict[str, Any]:
    username = (username or "").strip().lstrip("@")
    if not username:
        raise ValueError("username required")
    url = _PROFILE_URL.format(username=username)

    own_session = session is None
    if own_session:
        session = _http.Session()
    try:
        resp = session.get(url, headers={"Accept-Language": "en-US,en;q=0.9"})
    finally:
        if own_session:
            session.close()

    p = new_profile("instagram", url, username)
    p["scraped_at"] = _now_iso()

    if resp.status_code != 200:
        log.warning("Instagram returned %d for %s", resp.status_code, username)
        validate(p)
        return p

    html = resp.text
    data = _extract_shared_data(html)
    if data:
        # The structure has varied historically; be tolerant of either shape.
        graph = (((data.get("entry_data", {}) or {})
                   .get("ProfilePage", [{}]) or [{}])[0]
                  .get("graphql", {}) or {}).get("user", {})
        if graph:
            _fill_from_graph(p, graph)

    # Bio fallbacks
    if not p["email"] and p["bio"]:
        m = _http.email_regex().search(p["bio"])
        if m:
            p["email"] = m.group(0)
    if not p["phone"] and p["bio"]:
        m = _http.phone_regex().search(p["bio"])
        if m:
            p["phone"] = m.group(0)

    validate(p)
    return p


# --- helpers --------------------------------------------------------------------

_SHARED_DATA_RE = re.compile(
    r'<script type="application/json"[^>]*>(.*?)</script>',
    re.DOTALL,
)


def _extract_shared_data(html: str) -> Optional[dict]:
    """Find the first top-level window._sharedData or similar JSON blob."""
    match = re.search(r'window\._sharedData\s*=\s*(\{.*?\});', html, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            return None
    match = re.search(
        r'<script type="application/ld\+json">(\{.*?\})</script>',
        html,
        re.DOTALL,
    )
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            return None
    return None


def _fill_from_graph(p: Dict[str, Any], user: dict) -> None:
    p["full_name"] = user.get("full_name")
    p["bio"] = user.get("biography")
    p["follower_count"] = (user.get("edge_followed_by", {}) or {}).get("count")
    p["following_count"] = (user.get("edge_follow", {}) or {}).get("count")
    p["post_count"] = (user.get("edge_owner_to_timeline_media", {}) or {}).get("count")
    p["profile_image"] = user.get("profile_pic_url_hd") or user.get("profile_pic_url")
    p["website"] = user.get("external_url")
    is_business = user.get("is_business_account")
    business = user.get("business_category_name") or user.get("category_name")
    if business:
        p["company"] = business
    p["raw"]["is_private"] = bool(user.get("is_private"))
    p["raw"]["is_verified"] = bool(user.get("is_verified"))


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
