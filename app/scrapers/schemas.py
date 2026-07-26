"""
Canonical profile schema shared by every Sindarin scraper.

A ``Profile`` is a plain dict with the following keys. Fields that don't apply
to a platform are returned as ``None`` so downstream consumers can rely on the
shape. ``platform`` and ``source_url`` are always populated on success.

Keys:
    platform        str   short id, e.g. "github", "youtube"
    source_url      str   canonical URL the data came from
    username        str   handle on the platform
    full_name       str   display name (may be same as username)
    bio             str   free-text bio/about
    follower_count  int   where applicable, else None
    following_count int
    post_count      int
    email           str   email found in bio or public profile page
    phone           str   phone found in bio (tel: links or text)
    website         str   primary external website
    location        str   public location string (e.g. "San Francisco, CA")
    company         str   public company / employer string
    links           list  additional links surfaced on the profile
    profile_image   str   URL of the avatar/banner
    scraped_at      str   ISO-8601 UTC timestamp
    raw             dict  platform-specific extras (use sparingly)
"""

from typing import Any, Dict, List, Optional


REQUIRED_KEYS = (
    "platform",
    "source_url",
    "username",
)

ALL_KEYS = (
    "platform",
    "source_url",
    "username",
    "full_name",
    "bio",
    "follower_count",
    "following_count",
    "post_count",
    "email",
    "phone",
    "website",
    "location",
    "company",
    "links",
    "profile_image",
    "scraped_at",
    "raw",
)


def new_profile(platform: str, source_url: str, username: str) -> Dict[str, Any]:
    """Return a fresh profile dict with all keys present, all optional = None."""
    base: Dict[str, Any] = {
        "platform": platform,
        "source_url": source_url,
        "username": username,
        "full_name": None,
        "bio": None,
        "follower_count": None,
        "following_count": None,
        "post_count": None,
        "email": None,
        "phone": None,
        "website": None,
        "location": None,
        "company": None,
        "links": [],
        "profile_image": None,
        "scraped_at": None,
        "raw": {},
    }
    return base


def validate(profile: Dict[str, Any]) -> None:
    """Raise a ValueError if the profile is missing required keys."""
    missing = [k for k in REQUIRED_KEYS if not profile.get(k)]
    if missing:
        raise ValueError(f"Profile missing required keys: {missing!r}")
    if "links" in profile and not isinstance(profile["links"], list):
        raise ValueError("'links' must be a list")
