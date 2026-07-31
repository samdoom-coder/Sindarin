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

    # Track TikTok's internal status code for diagnostics (e.g. 10222 = limited
    # data for certain accounts)
    svc = _extract_status_code(html)
    if svc is not None:
        p["raw"]["tiktok_status_code"] = svc

    # Extract email / phone from bio text (not the entire HTML, which can
    # contain placeholder addresses like example@example.com from TikTok's
    # own templates)
    if p["bio"]:
        if not p["email"]:
            m = _http.email_regex().search(p["bio"])
            if m:
                p["email"] = m.group(0)
        if not p["phone"]:
            m = _http.phone_regex().search(p["bio"])
            if m:
                p["phone"] = m.group(0)

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


_STATUS_CODE_RE = re.compile(r'"statusCode"\s*:\s*(\d+)')


def _extract_status_code(html: str) -> Optional[int]:
    m = _STATUS_CODE_RE.search(html)
    return int(m.group(1)) if m else None


def _fill_from_block(p: Dict[str, Any], block: dict) -> None:
    user = None
    stats: Optional[dict] = None
    stats_v2: Optional[dict] = None
    # Rehydration path
    user_info = (((block.get("__DEFAULT_SCOPE__", {}) or {})
                  .get("webapp.user-detail", {}) or {})
                 .get("userInfo", {}) or {})
    if user_info:
        user = user_info.get("user") or {}
        stats = user_info.get("stats") or {}
        stats_v2 = user_info.get("statsV2") or {}
    if not user:
        # SIGI path — UserModule.users is a dict keyed by user ID
        users_map = (block.get("UserModule", {}) or {}).get("users", {})
        if isinstance(users_map, dict) and users_map:
            user = list(users_map.values())[0]
            stats = (user.get("stats") or {})
            stats_v2 = (user.get("statsV2") or {})
        elif isinstance(users_map, list) and users_map:
            user = users_map[0]
            stats = (user.get("stats") or {})
            stats_v2 = (user.get("statsV2") or {})
    if not user:
        return

    p["full_name"] = user.get("nickname") or user.get("uniqueId")
    p["bio"] = user.get("signature")
    # follower/following/post counts: prefer integer stats, fall back to string statsV2
    p["follower_count"] = (
        stats.get("followerCount")
        or stats_v2.get("followerCount")
    )
    if isinstance(p["follower_count"], str):
        p["follower_count"] = int(p["follower_count"]) if p["follower_count"].isdigit() else None
    p["following_count"] = (
        stats.get("followingCount")
        or stats_v2.get("followingCount")
    )
    if isinstance(p["following_count"], str):
        p["following_count"] = int(p["following_count"]) if p["following_count"].isdigit() else None
    p["post_count"] = (
        stats.get("videoCount")
        or stats_v2.get("videoCount")
    )
    if isinstance(p["post_count"], str):
        p["post_count"] = int(p["post_count"]) if p["post_count"].isdigit() else None
    p["profile_image"] = user.get("avatarLarger") or user.get("avatarMedium")
    link_obj = user.get("link")
    if isinstance(link_obj, dict):
        p["website"] = link_obj.get("link") or link_obj.get("webLink")
    p["raw"]["verified"] = bool(user.get("verified"))
    p["raw"]["private"] = bool(user.get("secret") or user.get("privateAccount"))


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
