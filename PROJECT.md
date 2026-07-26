# Sindarin — Project Documentation

> **For future upgrades and maintenance.** This file captures architecture, conventions, and upgrade paths.

---

## Project Overview

**Sindarin** is a safe-to-use CLI scraper for public social media profiles. Named after the Elvish language of Middle-earth.

- **Language**: Python 3.9+
- **License**: MIT
- **Safety posture**: No login bypass, no CAPTCHA solving, no SMTP probing, self-identifying UA, rate-limited, robots.txt opt-in.
- **Scope**: 8 platforms (GitHub, YouTube, Instagram, TikTok, Twitch, Pinterest, Linktree, LinkedIn).
- **Entry point**: `sindarin.py`

---

## Directory Structure

```
Sindarin/
├── sindarin.py              # CLI entry, menus, export, logo
├── requirements.txt         # requests, rich, dnspython
├── .env.example             # env template (LinkedIn cookie, Twitch client_id, proxies, delays)
├── .gitignore
├── LICENSE                  # MIT + ethical-use notice
├── README.md                # User-facing docs
├── PROJECT.md               # This file (internal)
└── app/
    ├── __init__.py          # version = "0.1.0"
    ├── enrichment.py        # lightweight enrichment (NO SMTP)
    └── scrapers/
        ├── __init__.py      # exports scrape_* functions
        ├── schemas.py       # canonical Profile dict + new_profile()/validate()
        ├── _http.py         # Session: rate-limit, retry, UA, proxy, robots.txt
        ├── github.py        # GitHub REST API (optional PAT)
        ├── youtube.py       # About page HTML + ytInitialData JSON
        ├── instagram.py     # Public profile HTML + _sharedData JSON
        ├── tiktok.py        # SIGI_STATE / UNIVERSAL_DATA JSON
        ├── twitch.py        # Helix API (opt Client-ID) + HTML fallback
        ├── pinterest.py     # HTML + _/_profile_root/ resource endpoint
        ├── linktree.py      # __NEXT_DATA__ JSON (Linktree, Stan, Bio.link, Linkr)
        └── linkedin.py      # li_at cookie required; CDATA JSON parsing
```

---

## Core Conventions

### 1. Profile Schema (app/scrapers/schemas.py)

Every scraper returns a dict with **identical keys** (canonical `Profile`):

```python
REQUIRED_KEYS = ("platform", "source_url", "username")
ALL_KEYS = (
    "platform", "source_url", "username",
    "full_name", "bio",
    "follower_count", "following_count", "post_count",
    "email", "phone", "website", "location", "company",
    "links", "profile_image", "scraped_at", "raw"
)
```

- Missing optional fields → `None` (not omitted).
- `links` → always a list (empty if none).
- `raw` → platform-specific extras dict (e.g., `{"verified": true}`).
- Always call `validate(profile)` before returning.

### 2. Scraper Signature

```python
def scrape(identifier: str, session: Optional[HttpSession] = None) -> Dict[str, Any]:
```

- `identifier`: username, handle, slug, or URL (scraper normalizes).
- `session`: shared `HttpSession` for connection reuse and rate-limit coordination. If `None`, scraper creates & closes its own.

### 3. HTTP Session (app/scrapers/_http.py)

`Session` is the **single** HTTP primitive. All scrapers use it.

```python
Session(
    delay=2.0,              # min seconds between requests to same host
    timeout=20.0,
    retries=3,
    user_agent=DEFAULT_UA,  # self-identifying
    proxy=None,             # single "http://user:pass@host:port"
    proxy_file=None,        # path to file, one proxy per line
    honor_robots=False,     # opt-in robots.txt respect
)
```

- Per-host token bucket via `_sleep_for_host()`.
- Exponential backoff + jitter on network errors.
- Honors HTTP 429 `Retry-After`.
- Proxy rotation via `_next_proxy()`.

**Environment overrides** (read in `sindarin.py`):
- `SINDARIN_DELAY`, `SINDARIN_TIMEOUT`, `SINDARIN_PROXY`, `SINDARIN_PROXY_FILE`, `SINDARIN_HONOR_ROBOTS`, `SINDARIN_USER_AGENT`.

### 4. Enrichment (app/enrichment.py)

**Design rule**: *Never probe mail servers.* No SMTP, no VRFY/RCPT.

What it does:
1. Normalize existing email/phone from bio.
2. Detect company string from headline/bio (`"SWE @ Acme"` → `"Acme"`).
3. Passive DNS MX lookup for `acme.com`, `acme.io`, `acme.co` (dnspython).
4. If public email exists on profile → infer pattern (`first.last@`, `first@`, `firstlast@`, `flast@`).
5. Generate **candidates only**, marked `verified: false`.
6. Heuristic `lead_score` 0–100 from data completeness.

Output added to profile:
```python
profile["company_domain"]      # e.g. "github.com"
profile["email_candidates"]    # [{"email", "pattern", "score", "verified": false}, ...]
profile["lead_score"]          # int 0-100
```

Optional: `HUNTER_API_KEY` for Hunter.io enrichment (not implemented yet; placeholder in env template).

---

## Platform Details

| Platform | Auth | Method | Key Selectors |
|----------|------|--------|---------------|
| GitHub | PAT optional (`GITHUB_TOKEN`) | REST `/users/{login}` | `name`, `bio`, `followers`, `email`, `blog`, `location`, `company` |
| YouTube | None | HTML About + `ytInitialData` | `channelAboutFullMetadataRenderer`, `subscriberCountText` |
| Instagram | None | HTML + `window._sharedData` | `graphql.user` (biography, edge_followed_by, etc.) |
| TikTok | None | HTML + `SIGI_STATE` / `__UNIVERSAL_DATA_FOR_REHYDRATION__` | `UserModule.users`, `stats` |
| Twitch | Client-ID optional (`TWITCH_CLIENT_ID`) | Helix `/users` + HTML fallback | `display_name`, `description`, `broadcaster_type` |
| Pinterest | None | HTML (`og:`, `ld+json`) + `/_/_profile_root/{user}/` | `full_name`, `about`, `follower_count` |
| Linktree | None | HTML + `__NEXT_DATA__` | `props.pageProps.account.links[]` |
| LinkedIn | **LinkedIn requires `LINKEDIN_COOKIE=li_at`**; scraper refuses to run without it. Parses CDATA JSON in `<code>` blocks for `included[]` Person entries.

---

## CLI Flow (sindarin.py)

```
main()
 ├─ parse args (--version, --help, --verbose)
 ├─ load .env if exists
 ├─ build HttpSession from env
 ├─ print logo (ASCII "Sindarin" in ACCENT color)
 ├─ interactive menu loop:
 │   ├─ list 8 platforms + Bulk (9) + Exit (0)
 │   ├─ single scrape:
 │   │   ├─ prompt identifier
 │   │   ├─ scrape → profile_card()
 │   │   ├─ optional enrich → enrichment_summary()
 │   │   └─ optional export
 │   └─ bulk scrape:
 │       ├─ pick platform
 │       ├─ load CSV/TXT (one identifier/line)
 │       ├─ progress bar → list[profile]
 │       ├─ optional enrich_all
 │       └─ export (CSV / JSON / both)
 └─ cleanup session
```

**Colors**: `ACCENT="#a70947"`, `ACCENT_DIM="#6b0530"`.

**Logo** (in `sindarin.py`):
```
    ███████╗███████╗ ██████╗ ██╗   ██╗██╗███████╗
    ██╔════╝██╔════╝██╔═══██╗██║   ██║██║██╔════╝
    ███████╗█████╗  ██║   ██║██║   ██║██║█████╗
    ╚════██║██╔══╝  ██║   ██║██║   ██║██║██╔══╝
    ███████║███████╗╚██████╔╝╚██████╔╝██║███████╗
    ╚══════╝╚══════╝ ╚═════╝  ╚═════╝ ╚═╝╚══════╝
```

---

## Adding a New Platform

1. **Create** `app/scrapers/newplatform.py` with `scrape(identifier, session=None)`.
2. **Follow schema**: return `new_profile("newplatform", url, username)` + fill fields + `validate()`.
3. **Use `Session`** for all HTTP; respect `delay`, `timeout`, `retries`.
4. **No login bypass**. If auth required → require env var, raise `RuntimeError` if missing.
5. **Register** in `app/scrapers/__init__.py`:
   ```python
   from .newplatform import scrape as scrape_newplatform
   __all__.append("scrape_newplatform")
   ```
6. **Add entry** to `PLATFORMS` dict in `sindarin.py`:
   ```python
   "newplatform": ("NewPlatform", scrape_newplatform, "Prompt text"),
   ```
7. **Test**: `python -c "from app.scrapers import scrape_newplatform; print(scrape_newplatform('testuser'))"`

---

## Upgrading Dependencies

| Package | Purpose | Upgrade Notes |
|---------|---------|---------------|
| `requests` | HTTP | Pin major; test retry/429 logic. |
| `rich` | CLI TUI | Check `Table`, `Progress`, `Prompt` API changes. |
| `dnspython` | MX lookup | `dns.resolver.Resolver` API stable. |

Run:
```bash
pip install --upgrade -r requirements.txt
python -c "from app.scrapers import *; print('imports ok')"
python sindarin.py --version
```

---

## Safety Checklist (Before Any Release)

- [ ] No new SMTP/probing code in enrichment.
- [ ] All scrapers use `Session` (no raw `requests.get`).
- [ ] Default UA remains self-identifying.
- [ ] Rate limiting (`delay`) enforced per host.
- [ ] LinkedIn still requires `LINKEDIN_COOKIE` and refuses without it.
- [ ] `honor_robots` defaults to `False` (opt-in only).
- [ ] No CAPTCHA-solving, no headless browser, no session spoofing.
- [ ] `LICENSE` ethical-use notice intact.
- [ ] `README.md` safety section matches reality.

---

## Known Limitations / Future Work

- **Instagram**: frequent login walls; scraper returns empty profile rather than bypass.
- **YouTube**: subscriber count often rounded string; `follower_count` stays `None` unless integer parseable.
- **Twitch**: without `TWITCH_CLIENT_ID`, HTML fallback is fragile.
- **LinkedIn**: cookie expires; user must refresh manually.
- **Enrichment**: no Hunter.io integration yet (env var reserved).
- **Bulk**: no resume/continue on failure; re-run whole file.
- **Export**: no SQLite/DB option; CSV/JSON only.
- **Logging**: `--verbose` only; no structured log file.

---

## Version History

| Version | Date | Notes |
|---------|------|-------|
| 0.1.0 | 2026-07-26 | Initial: 8 scrapers, enrichment, CLI, logo, safety defaults. |

---

## Quick Commands

```bash
# Install / update deps
pip install -r requirements.txt

# Run CLI
python sindarin.py
python sindarin.py --verbose
python sindarin.py --help

# Smoke test all scrapers
python -c "
from app.scrapers import *
for name, fn in [('github', scrape_github), ('youtube', scrape_youtube), ('tiktok', scrape_tiktok)]:
    try:
        r = fn('test' if name!='youtube' else '@test')
        print(f'{name}: {r[\"platform\"]} ok')
    except Exception as e:
        print(f'{name}: {e}')
"

# Verify enrichment
python -c "
from app.enrichment import enrich
p = enrich({'platform':'github','username':'octocat','full_name':'The Octocat','company':'@github','bio':'','follower_count':100,'website':'https://github.blog','links':[],'raw':{}})
print(p['lead_score'], p['company_domain'], len(p['email_candidates']))
"
```

---

> *Maintainer: keep this file current. When architecture changes, update here first.*