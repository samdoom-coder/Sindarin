# Sindarin

[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)

> **Sindarin** — a safe-to-use CLI scraper for public social media profiles.
> Named after the Elvish language of Middle-earth. Built for lawful, ethical OSINT:
> scrape only public pages, respect each platform's Terms of Service and robots.txt,
> rate-limit your requests, and never use it for spam, stalking, or harassment.

## Install

```bash
git clone https://github.com/yourname/Sindarin.git
cd Sindarin
pip install -r requirements.txt
cp .env.example .env   # optional: add keys/proxies
```

Requires Python 3.9+.

## Usage

```bash
python sindarin.py                # interactive menu
python sindarin.py --verbose      # debug logging
python sindarin.py --version
python sindarin.py --help
```

Run without arguments for the interactive TUI menu (built with `rich`).

## Supported Platforms

| Platform   | Auth Required | What It Scrapes |
|------------|--------------|-----------------|
| GitHub     | None (PAT optional) | profile, bio, repos, email, website |
| YouTube    | None | channel name, description, subscribers, email, links |
| Instagram  | None | profile, bio, followers, email, phone, links |
| TikTok     | None | profile, bio, followers, likes, email |
| Twitch     | Client ID optional | profile, bio, partner/affiliate status, social links |
| Pinterest  | None | profile, bio, followers, pins, website |
| Linktree   | None | all link-in-bio links (Linktree, Stan, Bio.link, Linkr) |
| LinkedIn   | `li_at` cookie | profile, headline, bio, email |

**LinkedIn**: requires the `li_at` session cookie from a logged-in browser. Set `LINKEDIN_COOKIE=your_cookie` in `.env`. Obtain this only in compliance with LinkedIn's ToS and applicable law.

## Safety & Ethics (Why "Safe to Use")

Sindarin is intentionally **conservative**:

- **No login bypass**, no CAPTCHA solving, no session spoofing.
- **No SMTP probing** — we never contact mail servers (no VRFY/RCPT). Email candidates are derived from public bios/links only and are marked **unverified**.
- **Self-identifying User-Agent**: `Sindarin/<version> (+https://github.com/yourname/Sindarin) public-profile-scraper`
- **Per-host rate limiting** (default 2s) + exponential backoff + HTTP 429 `Retry-After` honoring.
- **Optional robots.txt respect**: `SINDARIN_HONOR_ROBOTS=true`
- **Proxy support** (single or rotating file) — opt-in only.
- **Passive DNS only** for company-domain detection during enrichment (no active probing).
- All scrapers surface failures honestly — if a platform shows a login wall, you get an empty profile, not a bypass attempt.

> **Use responsibly.** Scrape only public data you have a lawful basis to process. Respect platform ToS and `robots.txt`. Do not use for spam, recruitment scraping at scale, lead-gen abuse, or any activity that violates privacy laws (GDPR, CCPA, etc.).

## Enrichment (Built-in, Lightweight)

After scraping, Sindarin can enrich profiles:

1. Extract email/phone from bio text.
2. Detect company from headline/bio (`"Founder @ Acme"`, `"SWE at Acme Inc."`).
3. Passive DNS MX lookup for the company domain.
4. Detect email pattern from any public email on the profile.
5. Generate **unverified** candidates (`first.last@domain`, `first@domain`, etc.) with a heuristic 0–100 confidence score.
6. Lead score 0–100 based on data completeness.

No paid APIs required. Optional `HUNTER_API_KEY` adds Hunter.io enrichment.

## Environment Variables

| Variable | Purpose |
|----------|---------|
| `GITHUB_TOKEN` | GitHub PAT for 5,000 req/hr (vs 60 unauthenticated) |
| `TWITCH_CLIENT_ID` | Twitch Helix Client ID |
| `LINKEDIN_COOKIE` | LinkedIn `li_at` cookie |
| `HUNTER_API_KEY` | Optional Hunter.io key for enrichment |
| `SINDARIN_PROXY` | Single proxy URL |
| `SINDARIN_PROXY_FILE` | Path to proxy list (one per line) |
| `SINDARIN_DELAY` | Per-host delay seconds (default 2.0) |
| `SINDARIN_TIMEOUT` | Request timeout seconds (default 20) |
| `SINDARIN_HONOR_ROBOTS` | `true` to respect robots.txt (default false) |
| `SINDARIN_USER_AGENT` | Override default UA |

## Export

Interactive menu → **Export** → CSV / JSON / Both. Files saved with timestamp: `sindarin_YYYYMMDD_HHMMSS.csv`

## Project Structure

```
sindarin.py          # CLI entry point, logo, menus, export
app/
  __init__.py        # version
  enrichment.py      # lightweight enrichment (no SMTP)
  scrapers/
    __init__.py      # exports all scrape_* functions
    _http.py         # safe HTTP session (rate limits, retries, UA)
    schemas.py       # canonical Profile dict schema
    github.py
    youtube.py
    instagram.py
    tiktok.py
    twitch.py
    pinterest.py
    linktree.py
    linkedin.py
```

## License

MIT — see [LICENSE](LICENSE).

---

> "The road goes ever on and on..." — *J.R.R. Tolkien*