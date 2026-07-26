"""
TikTok public profile scraper — no login.

TikTok serves different content depending on the User-Agent and may show an
empty interstitial to unknown clients. Sindarin sends a self-identifying UA
and parses the embedded ``SIGI_STATE`` / ``__UNIVERSAL_DATA_FOR_REHYDRATION__``
JSON blocks when present. If TikTok returns a captcha, the scraper surfaces
the empty result; it never solves the captcha.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import re
from typing import Any, Dict, Optional

from . import _http
from .schemas import new_profile, validate

log = logging.getLogger("sindarin.scrapers.tiktok")

_PROFILE_URL = "https://www.tiktok.com/@{username}"


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

    p = new_profile("tiktok", url, username)
    p["scraped_at"] = _now_iso()

    if resp.status_code != 200:
        log.warning("TikTok returned %d for %s", resp.status_code, username)
        validate(p)
        return p

    html = resp.text

    user_block = _extract_rehydration_block(html) or _extract_sigi_state(html)
    if user_block:
        _fill_from_block(p, user_block)

    # Fallback: pull anything that looks like an email from the raw HTML
    if not p["email"]:
        m = _http.email_regex().search(html)
        if m:
            p["email"] = m.group(0)

    validate(p)
    return p


# --- helpers --------------------------------------------------------------------

_REHYDRATE_RE = re.compile(
    r'<script[^>]*id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(\{.*?\})</script>',
    re.DOTALL,
)
_SIGI_RE = re.compile(r'window\.SIGI_STATE\s*=\s*(\{.*?\});', re.DOTALL)


def _extract_rehydration_block(html: str) -> Optional[dict]:
    m = _REHYDRATE_RE.search(html)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def _extract_sigi_state(html: str) -> Optional[dict]:
    m = _SIGI_RE.search(html)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def _fill_from_block(p: Dict[str, Any], block: dict) -> None:
    user = None
    # Rehydration path
    user = (((block.get("__DEFAULT_SCOPE__", {}) or {})
             .get("webapp.user-detail", {}) or {})
            .get("userInfo", {}) or {}).get("user") or {}
    if not user:
        # SIGI path
        user = (block.get("UserModule", {}) or {}).get("users", [{}])[0]
    if not user:
        return

    p["full_name"] = user.get("nickname") or user.get("uniqueId")
    p["bio"] = user.get("signature")
    p["follower_count"] = user.get("followerCount") or (user.get("stats", {}) or {}).get("followerCount")
    p["following_count"] = user.get("followingCount") or (user.get("stats", {}) or {}).get("followingCount")
    p["post_count"] = user.get("videoCount") or (user.get("stats", {}) or {}).get("videoCount")
    p["profile_image"] = user.get("avatarLarger") or user.get("avatarMedium")
    p["website"] = user.get("link", {}).get("link") if isinstance(user.get("link"), dict) else None
    p["raw"]["verified"] = bool(user.get("verified"))
    p["raw"]["private"] = bool(user.get("secret") or user.get("privateAccount"))


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
