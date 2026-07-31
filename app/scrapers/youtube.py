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
        # Already a URL — strip trailing path so we can append /about, and
        # extract the handle from the path for a clean username field.
        url = channel.rstrip("/")
        if not url.endswith("/about"):
            url += "/about"
        handle = _extract_handle_from_url(url)
        if not handle:
            # Fallback: treat the whole URL as the identifier.
            handle = url
    else:
        handle = channel.lstrip("@")
        url = ABOUT_URL_TEMPLATES[0].format(handle="@" + handle)

    own_session = session is None
    if own_session:
        session = _http.Session()
    try:
        resp = session.get(url, headers={"Accept-Language": "en-US,en;q=0.9"})
    finally:
        if own_session:
            session.close()

    p = new_profile("youtube", url, handle)

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

def _extract_handle_from_url(url: str) -> Optional[str]:
    """Pull the channel handle from a youtube.com/@Handle or /channel/<id> URL.

    Returns the handle WITH a leading '@' if it is one (e.g. '@CarryisLive'),
    or a bare channel id/slug otherwise. Returns None if nothing matches.
    """
    from urllib.parse import urlparse
    path = urlparse(url).path or ""
    # /about is always appended by scrape(); work on the pre-/about segment.
    parts = [seg for seg in path.split("/") if seg]
    for seg in parts:
        if seg.startswith("@"):
            return seg.lstrip("@")
    return parts[0] if parts else None


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


def _unwrap_youtube_redirect(url: str) -> str:
    """YouTube wraps external channel links as ``/redirect?...&q=<url>``.

    Return the decoded destination so exported links are usable. URLs that
    don't match are returned unchanged.
    """
    from urllib.parse import parse_qs, unquote, urlparse
    parsed = urlparse(url)
    if parsed.path == "/redirect":
        qs = parse_qs(parsed.query).get("q")
        if qs:
            return unquote(qs[0])
    return url


def _find_first(obj, key):
    """Recursively return the first value for ``key`` in a nested dict/list tree.

    YouTube's ytInitialData is deep and changes between releases; locating the
    canonical view models by key (rather than a brittle fixed path) keeps us
    resilient to reshuffling.
    """
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key:
                return v
            r = _find_first(v, key)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for it in obj:
            r = _find_first(it, key)
            if r is not None:
                return r
    return None


def _render_runs(value):
    """Normalize YouTube's text wrappings into a plain string.

    Handles ``"plain string"``, ``{content: "..."}``, ``{simpleText: "..."}``,
    and ``{"runs": [{"text": "..."}]}`` shapes.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        if "content" in value:
            return value["content"]
        if "simpleText" in value:
            return value["simpleText"]
        if "runs" in value:
            return "".join(r.get("text", "") for r in value["runs"])
    return None


def _parse_count(text):
    """Parse a rounded YouTube count like '45.7M subscribers', '1.2K', '999'
    into an approximate integer. Returns None if unparseable.

    Note: the public About page only ever shows a *rounded* figure, so any
    integer here is an approximation, never an exact headcount.
    """
    if not text:
        return None
    m = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*([KMGTP]?)", text.strip(), re.IGNORECASE)
    if not m:
        return None
    num = float(m.group(1)) * {"": 1, "K": 1e3, "M": 1e6, "G": 1e9, "T": 1e12, "P": 1e15}.get(
        m.group(2).upper(), 1
    )
    return int(round(num))


def _parse_about_data(p: Dict[str, Any], data: dict) -> None:
    """Walk ytInitialData for the channel's public About metadata.

    Uses the modern view-model layout (``pageHeaderViewModel`` /
    ``aboutChannelViewModel`` / ``microformatDataRenderer``) with a fallback to
    the legacy ``channelAboutFullMetadataRenderer`` path. The subscriber count
    on the public About page is *rounded* (e.g. '45.7M'), so ``follower_count``
    is an approximation flagged in ``raw``.
    """
    try:
        # --- full name ---------------------------------------------------------
        ph = _find_first(data, "pageHeaderViewModel")
        if isinstance(ph, dict):
            title_obj = ph.get("title", {})
            if isinstance(title_obj, dict) and "dynamicTextViewModel" in title_obj:
                p["full_name"] = (
                    _find_first(title_obj.get("dynamicTextViewModel", {}), "content")
                    or p["full_name"]
                )

        if not p["full_name"]:
            mf = _find_first(data, "microformatDataRenderer")
            if isinstance(mf, dict):
                if mf.get("title") and not p["full_name"]:
                    p["full_name"] = _render_runs(mf["title"]) or mf.get("title")
                if mf.get("thumbnail") and not p["profile_image"]:
                    thumbs = (mf.get("thumbnail") or {}).get("thumbnails") or []
                    if thumbs:
                        p["profile_image"] = thumbs[-1].get("url")
                if mf.get("description") and not p["bio"]:
                    p["bio"] = _render_runs(mf["description"]) or mf.get("description")

        # --- aboutChannelViewModel: stats, bio, links, country ----------------
        vm = _find_first(data, "aboutChannelViewModel")
        if isinstance(vm, dict):
            if vm.get("description") and not p["bio"]:
                p["bio"] = _render_runs(vm["description"]) or vm.get("description")
            if vm.get("subscriberCountText"):
                sub = _render_runs(vm["subscriberCountText"]) or vm["subscriberCountText"]
                p["raw"]["subscriber_text"] = sub
                approx = _parse_count(sub)
                if approx is not None:
                    p["follower_count"] = approx
                    p["raw"]["follower_count_source"] = "rounded"
            if vm.get("videoCountText"):
                vids = _parse_count(_render_runs(vm["videoCountText"]) or vm["videoCountText"])
                if vids is not None:
                    p["post_count"] = vids
            if vm.get("viewCountText"):
                p["raw"]["total_views_text"] = (
                    _render_runs(vm["viewCountText"]) or vm["viewCountText"]
                )
            if vm.get("country") and not p["location"]:
                p["location"] = vm["country"]
            if vm.get("canonicalChannelUrl"):
                p["raw"]["canonical_channel_url"] = vm["canonicalChannelUrl"]
            if vm.get("channelId"):
                p["raw"]["channel_id"] = vm["channelId"]
            if vm.get("joinedDateText"):
                p["raw"]["joined"] = _render_runs(vm["joinedDateText"]) or vm["joinedDateText"]
            out_links = []
            for ln in vm.get("links", []) or []:
                cev = (ln.get("channelExternalLinkViewModel") or {}) if isinstance(ln, dict) else {}
                link_obj = cev.get("link", {})
                url = (
                    (link_obj.get("urlEndpoint", {}) or {}).get("url")
                    or _find_first(link_obj, "url")
                )
                if isinstance(url, str) and url.startswith("http"):
                    out_links.append(_unwrap_youtube_redirect(url))
            # Drop YouTube-internal links; keep only real external destinations.
            out_links = [u for u in out_links if "youtube.com" not in u and "youtu.be" not in u]
            if out_links:
                p["links"] = list(dict.fromkeys(out_links))[:25]

        # --- profile image fallback (pageHeaderViewModel.image) ----------------
        if not p["profile_image"] and isinstance(ph, dict):
            img = ph.get("image", {})
            avatar = _find_first(img, "sources") or _find_first(img, "url")
            if isinstance(avatar, list) and avatar:
                last = avatar[-1]
                if isinstance(last, dict) and last.get("url"):
                    p["profile_image"] = last["url"]
            elif isinstance(avatar, str):
                p["profile_image"] = avatar

        # --- legacy fallback: channelAboutFullMetadataRenderer -----------------
        if not p["full_name"] and not p["bio"] and not p["follower_count"]:
            legacy = _find_first(data, "channelAboutFullMetadataRenderer")
            if isinstance(legacy, dict):
                meta = legacy
                if meta.get("title") and not p["full_name"]:
                    p["full_name"] = _render_runs(meta.get("title"))
                desc = meta.get("description")
                if desc and not p["bio"]:
                    p["bio"] = _render_runs(desc) if isinstance(desc, dict) else desc
                sub_text = (meta.get("subscriberCountText") or {}).get("simpleText")
                if sub_text:
                    p["raw"]["subscriber_text"] = sub_text
                    approx = _parse_count(sub_text)
                    if approx is not None:
                        p["follower_count"] = approx
                        p["raw"]["follower_count_source"] = "rounded"
                links = meta.get("primaryLinks", [])
                out_links = []
                for ln in links:
                    runs = (ln.get("title", {}) or {}).get("simpleText")
                    ep = (ln.get("navigationEndpoint", {}) or {}).get("urlEndpoint", {}).get("url")
                    out_links.append(runs if isinstance(runs, str) else (ep or str(ln)))
                if out_links:
                    p["links"] = list(dict.fromkeys(out_links))[:25]
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
