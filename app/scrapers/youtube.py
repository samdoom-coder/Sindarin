"""
YouTube scraper — reads a channel's public About page.

Identifies the channel either by handle (``@Handle``), channel ID
(``UC...``), or a full URL. Pulls the public description, subscriber count
range, and any contact email the channel owner has made public on that page.

Note: YouTube's About page no longer reliably exposes the subscriber count as
an integer; YouTube itself rounds it to "1.2M subscribers" strings. We surface
that string as-is in ``raw.subscriber_text`` and leave ``follower_count``
None unless we can parse a clean integer.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import re
from typing import Any, Dict, Optional

from . import _http
from .schemas import new_profile, validate

log = logging.getLogger("sindarin.scrapers.youtube")

ABOUT_URL_TEMPLATES = [
    "https://www.youtube.com/{handle}/about",
    "https://www.youtube.com/channel/{id}/about",
]


def scrape(channel: str, session: Optional[_http.Session] = None) -> Dict[str, Any]:
    """Scrape a YouTube channel's public About page."""
    if not channel:
        raise ValueError("channel required")
    channel = channel.strip()
    if channel.startswith(("http://", "https://")):
        # Already a URL — strip trailing path so we can append /about
        url = channel.rstrip("/")
        if not url.endswith("/about"):
            url += "/about"
    else:
        name = channel.lstrip("@")
        url = ABOUT_URL_TEMPLATES[0].format(handle="@" + name)

    own_session = session is None
    if own_session:
        session = _http.Session()
    try:
        resp = session.get(url, headers={"Accept-Language": "en-US,en;q=0.9"})
    finally:
        if own_session:
            session.close()

    p = new_profile("youtube", url, channel.lstrip("@"))

    if resp.status_code != 200:
        log.warning("YouTube About page returned %d for %s", resp.status_code, channel)
        validate(p)
        return p

    html = resp.text
    p["scraped_at"] = _now_iso()

    # YouTube embeds an initialData JSON blob inside a <script> var.
    blob = _extract_initial_data(html)
    if blob:
        _parse_about_data(p, blob)

    # Plain-text fallbacks for email/links in HTML
    if not p["email"]:
        m = _http.email_regex().search(html)
        if m:
            p["email"] = m.group(0)
    if not p["links"]:
        p["links"] = _extract_external_links(html, p["source_url"])

    validate(p)
    return p


# --- helpers --------------------------------------------------------------------

_INITIAL_DATA_RE = re.compile(
    r'var ytInitialData = (\{.*?\});</script>', re.DOTALL
)


def _extract_initial_data(html: str) -> Optional[dict]:
    m = _INITIAL_DATA_RE.search(html)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except (ValueError, json.JSONDecodeError):
        return None


def _parse_about_data(p: Dict[str, Any], data: dict) -> None:
    """Best-effort walk over ytInitialData to find the About metadata."""
    try:
        # The structure is deep and changes; we dig with .get chains and
        # tolerate missing keys.
        contents = (
            data.get("contents", {})
                .get("twoColumnBrowseResultsRenderer", {})
                .get("tabs", [])
        )
        about_tab = None
        for tab in contents:
            r = tab.get("tabRenderer", {})
            if r.get("title", "").lower() in ("about",):
                about_tab = r
                break
        if not about_tab:
            return
        section = (about_tab.get("content", {})
                            .get("sectionListRenderer", {})
                            .get("contents", []))
        for s in section:
            contents2 = (s.get("itemSectionRenderer", {})
                          .get("contents", []))
            for c in contents2:
                meta = c.get("channelAboutFullMetadataRenderer")
                if not meta:
                    continue
                p["full_name"] = meta.get("title") or p.get("full_name")
                p["bio"] = meta.get("description", {}).get("simpleText") if isinstance(meta.get("description"), dict) else meta.get("description")
                sub_text = meta.get("subscriberCountText", {}).get("simpleText")
                if sub_text:
                    p["raw"]["subscriber_text"] = sub_text
                    digit = re.sub(r"[^\d]", "", sub_text)
                    if digit.isdigit():
                        p["follower_count"] = int(digit)
                photo = meta.get("avatar", {}).get("thumbnails", [{}])[-1].get("url")
                if photo:
                    p["profile_image"] = photo
                links = meta.get("primaryLinks", [])
                out_links = []
                for ln in links:
                    runs = (ln.get("title", {}) or {}).get("simpleText") or (ln.get("navigationEndpoint", {}).get("urlEndpoint", {}).get("url"))
                    if runs:
                        out_links.append(runs if isinstance(runs, str) else str(runs))
                if out_links:
                    p["links"] = out_links
    except Exception as e:
        log.debug("ytInitialData parse failed: %s", e)


def _extract_external_links(html: str, base: str) -> list:
    hrefs = re.findall(r'href="(https?://[^"]+)"', html)
    keep = []
    for href in hrefs:
        if "google.com" in href:
            continue
        if "youtube.com" in href or "youtu.be" in href:
            continue
        if href.startswith(("mailto:", "tel:")):
            continue
        keep.append(href)
    return list(dict.fromkeys(keep))[:25]


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
