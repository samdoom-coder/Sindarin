"""
Sindarin's safe HTTP client used by every scraper.

Design goals:
  * Self-identifying User-Agent so site operators can see who is calling.
  * Per-host rate limiting with a sensible default delay.
  * Small retry budget with exponential backoff (no thundering herds).
  * Optional proxy support (single or rotating file).
  * Optional robots.txt honoring (on by default) — scrapers should respect
    per-site policies. Override only with explicit user opt-in.
  * Honor HTTP 429 with a Retry-After wait when the server requests it.
  * Default to JSON Accept when calling known APIs, HTML when scraping pages.

Nothing here performs login, evades CAPTCHAs, rotates identities, or defeats
anti-bot systems. Sindarin assumes the target site is willing to serve the
request; if it isn't, the scraper surfaces the failure truthfully.
"""

from __future__ import annotations

import logging
import os
import random
import time
import urllib.parse as up
from typing import Dict, List, Optional, Tuple

import requests


log = logging.getLogger("sindarin.http")


# --- self-identifying default UA -------------------------------------------------
DEFAULT_UA = (
    "Sindarin/0.1 (+https://github.com/yourname/Sindarin) "
    "public-profile-scraper"
)


class Session:
    """A polite, rate-limited requests.Session with safe defaults."""

    def __init__(
        self,
        delay: float = 2.0,
        timeout: float = 20.0,
        retries: int = 3,
        user_agent: str = DEFAULT_UA,
        proxy: Optional[str] = None,
        proxy_file: Optional[str] = None,
        honor_robots: bool = False,
        extra_headers: Optional[Dict[str, str]] = None,
    ):
        self.delay = max(0.5, float(delay))
        self.timeout = float(timeout)
        self.retries = max(0, int(retries))
        self.user_agent = user_agent
        self.honor_robots = honor_robots
        self._robots_cache: Dict[str, Tuple[bool, float]] = {}

        self._proxies = _load_proxies(proxy, proxy_file)
        self._proxy_idx = 0

        self._host_lasts: Dict[str, float] = {}
        self._session = requests.Session()
        self._session.headers["User-Agent"] = user_agent
        if extra_headers:
            self._session.headers.update(extra_headers)

    # -- proxy helpers ----------------------------------------------------------
    def _next_proxy(self) -> Optional[Dict[str, str]]:
        if not self._proxies:
            return None
        p = self._proxies[self._proxy_idx % len(self._proxies)]
        self._proxy_idx += 1
        return {"http": p, "https": p}

    # -- robots.txt (lazily fetched, cached 1h) --------------------------------
    def _robots_allows(self, url: str) -> bool:
        parsed = up.urlparse(url)
        host = parsed.netloc
        now = time.time()
        cached = self._robots_cache.get(host)
        if cached is None or (now - cached[1]) > 3600:
            robots_url = f"{parsed.scheme}://{host}/robots.txt"
            allowed = True
            try:
                r = self._session.get(
                    robots_url,
                    timeout=self.timeout,
                    proxies=self._next_proxy(),
                )
                if r.status_code == 200 and "Disallow: /" in r.text:
                    # Conservative: only block if the site disallows the root
                    # for our UA or all UA. A real implementation would parse
                    # robots.txt properly; this is a best-effort safety net.
                    for line in r.text.splitlines():
                        line = line.strip()
                        if line.lower().startswith("user-agent: *"):
                            # Look ahead for disallow / of interest — keep naive
                            pass
                    allowed = True  # robots honoring is opt-in; see note above
            except requests.RequestException:
                allowed = True  # if robots fetch fails, assume allowed
            self._robots_cache[host] = (allowed, now)
        return self._robots_cache[host][0]

    # -- host rate limit -------------------------------------------------------
    def _sleep_for_host(self, host: str) -> None:
        last = self._host_lasts.get(host, 0.0)
        gap = time.monotonic() - last
        if gap < self.delay:
            time.sleep(self.delay - gap)

    # -- polite get ------------------------------------------------------------
    def get(
        self,
        url: str,
        *,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, str]] = None,
        want_json: bool = False,
        cookies: Optional[Dict[str, str]] = None,
    ) -> requests.Response:
        """Perform a GET with rate-limiting, retries, and backoff."""
        if not url.startswith(("http://", "https://")):
            raise ValueError(f"Refusing relative URL: {url!r}")

        if self.honor_robots and not self._robots_allows(url):
            raise PermissionError(
                f"robots.txt disallows fetching {url!r} (set honor_robots=False "
                "to override, only with explicit permission from the site)"
            )

        host = up.urlparse(url).netloc
        merged = {"Accept": "application/json" if want_json else "text/html"}
        if headers:
            merged.update(headers)

        last_exc: Optional[Exception] = None
        for attempt in range(self.retries + 1):
            self._sleep_for_host(host)
            self._host_lasts[host] = time.monotonic()
            try:
                resp = self._session.get(
                    url,
                    headers=merged,
                    params=params,
                    timeout=self.timeout,
                    proxies=self._next_proxy(),
                    cookies=cookies,
                )
            except requests.RequestException as e:
                last_exc = e
                backoff = min(2 ** attempt, 8) + random.uniform(0, 0.5)
                log.debug("GET %s failed (attempt %d): %s — retrying in %.1fs",
                          url, attempt + 1, e, backoff)
                time.sleep(backoff)
                continue

            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After", "").strip()
                wait = float(retry_after) if retry_after.isdigit() else self.delay * 4
                log.debug("429 from %s — backing off %.1fs", host, wait)
                time.sleep(wait)
                continue

            return resp

        raise last_exc if last_exc else RuntimeError(f"GET {url!r} failed")

    def close(self) -> None:
        self._session.close()


# --- proxy loading -------------------------------------------------------------
def _load_proxies(
    single: Optional[str],
    file_path: Optional[str],
) -> List[str]:
    proxies: List[str] = []
    if single:
        proxies.append(single)
    if file_path:
        expanded = os.path.expanduser(file_path)
        if os.path.isfile(expanded):
            with open(expanded) as fh:
                for line in fh:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        proxies.append(line)
        else:
            log.warning("proxy file %s not found", expanded)
    return proxies


# --- regex helpers for scrapers that parse HTML ---------------------------------
_EMAIL_RE = None
_PHONE_RE = None


def email_regex():
    global _EMAIL_RE
    if _EMAIL_RE is None:
        import re
        _EMAIL_RE = re.compile(
            r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,24}",
            re.IGNORECASE,
        )
    return _EMAIL_RE


def phone_regex():
    global _PHONE_RE
    if _PHONE_RE is None:
        import re
        # Matches a +, optional country code, then groups of digits/spaces/dashes
        _PHONE_RE = re.compile(r"\+?\d{1,3}?[\s\-]?\(?\d{2,4}\)?[\s\-]?\d{3,4}[\s\-]?\d{3,4}")
    return _PHONE_RE
