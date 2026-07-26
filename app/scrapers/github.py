"""
GitHub scraper — uses the official public REST API.

Rate limits:
    60 requests/hour unauthenticated, 5000/hour with a token. Sindarin never
    evades limits; if the API returns 403 with rate-limit exhaustion the
    scraper surfaces the error honestly.
"""

from __future__ import annotations

import datetime as _dt
import logging
import os
import re
from typing import Any, Dict, Optional

from . import _http
from .schemas import new_profile, validate

log = logging.getLogger("sindarin.scrapers.github")

API_BASE = "https://api.github.com"
GITHUB_TOKEN_ENV = "GITHUB_TOKEN"


def scrape(username: str, session: Optional[_http.Session] = None) -> Dict[str, Any]:
    """Scrape a single GitHub user by login. Returns a Profile dict."""
    if not username:
        raise ValueError("username required")
    username = username.strip().lstrip("@")

    own_session = session is None
    if own_session:
        session = _http.Session()

    source_url = f"https://github.com/{username}"
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get(GITHUB_TOKEN_ENV, "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        resp = session.get(
            f"{API_BASE}/users/{username}",
            headers=headers,
            want_json=True,
        )
    finally:
        if own_session:
            session.close()

    if resp.status_code == 404:
        log.info("GitHub user %s not found", username)
        return new_profile("github", source_url, username)
    if resp.status_code != 200:
        log.warning(
            "GitHub API %d for %s — body: %s",
            resp.status_code, username, resp.text[:200],
        )
        return new_profile("github", source_url, username)

    data = resp.json()
    p = new_profile("github", data.get("html_url") or source_url, data.get("login") or username)
    p["full_name"] = data.get("name")
    p["bio"] = data.get("bio")
    p["follower_count"] = data.get("followers")
    p["following_count"] = data.get("following")
    p["post_count"] = data.get("public_repos")
    p["email"] = data.get("email")
    p["website"] = _normalize_blog(data.get("blog"))
    p["location"] = data.get("location")
    p["company"] = data.get("company")
    p["profile_image"] = data.get("avatar_url")
    p["scraped_at"] = _now_iso()

    # Light enrichment: if no public email on profile, look for a contact email
    # in bio text only (no SMTP probing, no guess-and-verify).
    if not p["email"] and p["bio"]:
        m = _http.email_regex().search(p["bio"])
        if m:
            p["email"] = m.group(0)

    p["raw"] = {
        "twitter": data.get("twitter_username"),
        "hireable": data.get("hireable"),
        "type": data.get("type"),
    }
    validate(p)
    return p


def _normalize_blog(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    url = url.strip()
    if not url:
        return None
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
