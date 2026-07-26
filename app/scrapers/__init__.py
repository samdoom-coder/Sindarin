"""
Sindarin scrapers package.

Each module exposes a single function, ``scrape(identifier, **opts)``,
returning a ``SindarinProfile`` (a TypedDict-like dict) with a consistent
schema across platforms. See ``schemas.py`` for the canonical fields.

Sindarin ships only public, non-login scrapers by default. ``linkedin`` is
cookie-gated because LinkedIn requires an authenticated session for profile
pages — that cookie is the user's responsibility and must be obtained in
compliance with LinkedIn's Terms of Service.

Supported platforms:
    - GitHub      (public REST API + HTML fallback)
    - YouTube     (public about-page HTML)
    - Instagram   (public profile HTML)
    - TikTok      (public profile HTML)
    - Twitch      (public helix API; Client ID optional)
    - Pinterest   (public profile HTML)
    - Linktree    (public profile HTML; also Stan / Bio.link / Linkr)
    - LinkedIn    (requires ``LINKEDIN_COOKIE`` env var)
"""

from .github import scrape as scrape_github
from .youtube import scrape as scrape_youtube
from .instagram import scrape as scrape_instagram
from .tiktok import scrape as scrape_tiktok
from .twitch import scrape as scrape_twitch
from .pinterest import scrape as scrape_pinterest
from .linktree import scrape as scrape_linktree
from .linkedin import scrape as scrape_linkedin

__all__ = [
    "scrape_github",
    "scrape_youtube",
    "scrape_instagram",
    "scrape_tiktok",
    "scrape_twitch",
    "scrape_pinterest",
    "scrape_linktree",
    "scrape_linkedin",
]
