"""
LinkedIn profile scraper — REQUIRES user-supplied session cookie.

LinkedIn requires authentication to view profile pages. Sindarin does not
attempt to bypass this. The user must provide their own ``li_at`` session
cookie via the ``LINKEDIN_COOKIE`` environment variable, obtained in
compliance with LinkedIn's Terms of Service.

Sindarin sends that cookie along only when explicitly configured. If no
cookie is provided the scraper refuses to run.

Important: automating access to LinkedIn may violate their Terms of Service
in your jurisdiction. The user is solely responsible for compliance.
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

log = logging.getLogger("sindarin.scrapers.linkedin")

COOKIE_ENV = "LINKEDIN_COOKIE"
_PROFILE_URL = "https://www.linkedin.com/in/{slug}/"


def scrape(slug: str, session: Optional[_http.Session] = None) -> Dict[str, Any]:
    """Scrape a LinkedIn profile given its vanity slug.

    Raises ``RuntimeError`` if no cookie is available — Sindarin will not
    attempt to view logged-out profiles by spoofing a UA chain.
    """
    slug = (slug or "").strip()
    slug = slug.lstrip("/").removeprefix("linkedin.com/in/").removeprefix("www.linkedin.com/in/")
    if not slug:
        raise ValueError("slug required")
    if "/" in slug:
        slug = slug.split("/", 1)[0]
    if slug.startswith(("http://", "https://")):
        from urllib.parse import urlparse
        slug = urlparse(slug).path.strip("/").removeprefix("in/")
        slug = slug.split("/", 1)[0]

    cookie = os.environ.get(COOKIE_ENV, "").strip()
    if not cookie:
        raise RuntimeError(
            f"LinkedIn scraping requires the {COOKIE_ENV} env var. Sindarin "
            "refuses to scrape logged-out LinkedIn. See README for setup and "
            "compliance notes."
        )

    url = _PROFILE_URL.format(slug=slug)
    cookies = {"li_at": cookie}
    own_session = session is None
    if own_session:
        session = _http.Session()

    p = new_profile("linkedin", url, slug)

    try:
        resp = session.get(
            url,
            headers={
                "Accept": "text/html",
                "Accept-Language": "en-US,en;q=0.9",
            },
            cookies=cookies,
        )
    finally:
        if own_session:
            session.close()

    p["scraped_at"] = _now_iso()
    if resp.status_code != 200:
        log.warning("LinkedIn returned %d for %s (cookie may be expired)", resp.status_code, slug)
        p["raw"]["http_status"] = resp.status_code
        validate(p)
        return p

    html = resp.text
    _parse(p, html)
    if not p.get("email") and p["bio"]:
        m = _http.email_regex().search(p["bio"])
        if m:
            p["email"] = m.group(0)
    validate(p)
    return p


# --- parsing --------------------------------------------------------------------

def _parse(p: Dict[str, Any], html: str) -> None:
    # LinkedIn includes the profile data in an excluded <code> CDATA block
    # designed to be hidden from rendering. Best effort to extract.
    blocks = re.findall(
        r'<code[^>]*>(<!\[CDATA\[)?(.*?)(\]\]>)?</code>', html, re.DOTALL,
    )
    for _leading, payload, _trailing in blocks:
        if '"included"' in payload and '"headline"' in payload:
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                continue
            # Walk the included array for a Person-ish entry
            for entry in data.get("included", []):
                if "firstName" not in entry:
                    continue
                p["full_name"] = (entry.get("firstName") + " " + entry.get("lastName")).strip()
                headline = entry.get("headline")
                if headline:
                    p["raw"]["headline"] = headline
                    # Public "headline" often reads "SWE @ Acme"; use it as company signal.
                    if not p["company"]:
                        m = re.search(r"\bat\s+(.+?)(?:\s*[·\-|]|$)", headline, re.IGNORECASE)
                        if m:
                            p["company"] = m.group(1).strip()
                if not p["bio"]:
                    p["bio"] = entry.get("summary")
                p["location"] = entry.get("locationName")
                if entry.get("geoCountryName"):
                    p["location"] = p["location"] or entry.get("geoCountryName")
                if entry.get("industryName"):
                    p["raw"]["industry"] = entry.get("industryName")
                img = entry.get("pictureUrl") or entry.get("backgroundImage")
                if img:
                    p["profile_image"] = img
                break

    # Fallback: og: meta tags
    if not p["full_name"]:
        m = re.search(r'<meta property="og:title" content="([^"]+)"', html)
        if m:
            p["full_name"] = m.group(1)
    if not p["bio"]:
        m = re.search(r'<meta property="og:description" content="([^"]+)"', html)
        if m:
            p["bio"] = m.group(1)


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
