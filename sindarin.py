#!/usr/bin/env python3
"""
Sindarin — safe-to-use social media profile CLI scraper.

Named after the Elvish language of Middle-earth. Intended for lawful, ethical
use only: scrape only public pages, respect each platform's Terms of Service
and robots.txt, rate-limit your requests, and never use it for spam, stalking,
or harassment.

Usage:
    python sindarin.py                # interactive menu
    python sindarin.py --version
    python sindarin.py --help
"""

from __future__ import annotations

import csv
import datetime as _dt
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Load .env if present
env_file = Path(__file__).parent / ".env"
if env_file.exists():
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, MofNCompleteColumn, TimeRemainingColumn
from rich import box
from rich.rule import Rule
from rich.theme import Theme

from app import __version__
from app.scrapers import (
    scrape_github,
    scrape_youtube,
    scrape_instagram,
    scrape_tiktok,
    scrape_twitch,
    scrape_pinterest,
    scrape_linktree,
    scrape_linkedin,
)
from app.enrichment import enrich, enrich_many
from app.scrapers._http import Session as HttpSession, DEFAULT_UA

# --- constants ------------------------------------------------------------------

ACCENT = "#a70947"
ACCENT_DIM = "#6b0530"
ACCENT_LIGHT = "#d64d7a"
ACCENT_MUTED = "#4a0727"

_SINDARIN_LOGO = [
    "    ███████╗███████╗ ██████╗ ██╗   ██╗██╗███████╗",
    "    ██╔════╝██╔════╝██╔═══██╗██║   ██║██║██╔════╝",
    "    ███████╗█████╗  ██║   ██║██║   ██║██║█████╗  ",
    "    ╚════██║██╔══╝  ██║   ██║██║   ██║██║██╔══╝  ",
    "    ███████║███████╗╚██████╔╝╚██████╔╝██║███████╗",
    "    ╚══════╝╚══════╝ ╚═════╝  ╚═════╝ ╚═╝╚══════╝",
]

# Map platform key -> (label, scraper_fn, identifier_prompt)
PLATFORMS = {
    "github": ("GitHub", scrape_github, "GitHub username (e.g. octocat)"),
    "youtube": ("YouTube", scrape_youtube, "YouTube channel handle or URL (e.g. @GoogleDevelopers)"),
    "instagram": ("Instagram", scrape_instagram, "Instagram username (e.g. nasa)"),
    "tiktok": ("TikTok", scrape_tiktok, "TikTok username (e.g. @nasa)"),
    "twitch": ("Twitch", scrape_twitch, "Twitch channel name (e.g. ninja)"),
    "pinterest": ("Pinterest", scrape_pinterest, "Pinterest username (e.g. pinterest)"),
    "linktree": ("Linktree", scrape_linktree, "Linktree username (e.g. linktree)"),
    "linkedin": ("LinkedIn", scrape_linkedin, "LinkedIn profile slug (requires LINKEDIN_COOKIE env)"),
}

# --- rich console -----------------------------------------------------------------

custom_theme = Theme({
    "prompt.choices": ACCENT,
    "prompt.default": "dim",
})
console = Console(theme=custom_theme)


# --- helpers ----------------------------------------------------------------------

def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _print_logo() -> None:
    for line in _SINDARIN_LOGO:
        console.print(f"[{ACCENT}]{line}[/{ACCENT}]")
    console.print()


def _print_header(title: str, subtitle: str = "") -> None:
    console.print()
    console.print(Rule(f"[bold white]{title}[/bold white]  [dim]{subtitle}[/dim]", style=ACCENT_DIM, align="left"))
    console.print()


def _profile_card(p: Dict[str, Any]) -> None:
    """Print a compact profile card to console."""
    lines: List[str] = []

    if p.get("full_name"):
        lines.append(f"[bold white]{p['full_name'][:50]}[/bold white]")

    stats = []
    if p.get("follower_count") is not None:
        stats.append(f"[white]{p['follower_count']:,}[/white] [dim]followers[/dim]")
    if p.get("following_count") is not None:
        stats.append(f"[white]{p['following_count']:,}[/white] [dim]following[/dim]")
    if p.get("post_count") is not None:
        stats.append(f"[white]{p['post_count']:,}[/white] [dim]posts[/dim]")
    if stats:
        lines.append("  ·  ".join(stats))

    if p.get("bio"):
        bio = p["bio"].replace("\n", " ")
        if p.get("email"):
            bio = bio.replace(p["email"], "").strip(" |·-,")
        if p.get("phone"):
            bio = bio.replace(p["phone"], "").strip(" |·-,")
        bio = bio[:100] + ("..." if len(bio) > 100 else "")
        if bio:
            lines.append(f"[dim]{bio}[/dim]")

    for line in lines:
        console.print(f"  {line}")
    console.print()


def _enrichment_summary(enriched: List[Dict[str, Any]]) -> None:
    total_candidates = sum(len(p.get("email_candidates", [])) for p in enriched)
    profiles_with_candidates = sum(1 for p in enriched if p.get("email_candidates"))
    avg_score = sum(p.get("lead_score", 0) for p in enriched) // len(enriched) if enriched else 0

    console.print(Rule("[bold white]Enrichment[/bold white]", style=ACCENT_DIM, align="left"))
    console.print()
    console.print(f"  [green]{total_candidates}[/green] email candidates generated from [green]{profiles_with_candidates}[/green] profiles")
    console.print(f"  [white]Avg lead score:[/white] {avg_score}/100")
    console.print()

    has_any = any(p.get("email_candidates") for p in enriched)
    if not has_any:
        console.print("  [yellow]No email candidates generated.[/yellow]")
        console.print()
        return

    table = Table(show_header=True, box=box.MINIMAL_HEAVY_HEAD, border_style="dim", padding=(0, 1))
    table.add_column("Lead", style="white")
    table.add_column("Candidate Email", style="white")
    table.add_column("Pattern", style="dim")
    table.add_column("Score", style="green")
    table.add_column("Verified", style="yellow")

    for p in enriched:
        name = p.get("full_name") or p.get("username") or "?"
        for c in p.get("email_candidates", []):
            table.add_row(
                name[:25],
                c["email"],
                c["pattern"],
                f"{c['score']}%",
                "No"  # Never claim verified without SMTP
            )
    console.print(table)
    console.print()


def _export_csv(profiles: List[Dict[str, Any]], path: Path) -> None:
    """Write profiles to CSV with all common fields."""
    if not profiles:
        return
    fieldnames = [
        "platform", "source_url", "username", "full_name", "bio",
        "follower_count", "following_count", "post_count",
        "email", "phone", "website", "location", "company",
        "links", "profile_image", "scraped_at", "lead_score",
        "company_domain", "email_candidates",
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for p in profiles:
            row = {k: p.get(k) for k in fieldnames}
            for k in ("links", "email_candidates"):
                if row.get(k):
                    row[k] = json.dumps(row[k], ensure_ascii=False)
            writer.writerow(row)
    console.print(f"[green]✓[/green] Exported {len(profiles)} profiles to [white]{path}[/white]")


def _export_json(profiles: List[Dict[str, Any]], path: Path) -> None:
    """Write profiles to a single JSON array file."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(profiles, f, ensure_ascii=False, indent=2)
    console.print(f"[green]✓[/green] Exported {len(profiles)} profiles to [white]{path}[/white]")


def _export_jsonl(profiles: List[Dict[str, Any]], path: Path) -> None:
    """Write profiles as JSON Lines (one object per line; good for large bulk)."""
    with open(path, "w", encoding="utf-8") as f:
        for p in profiles:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")
    console.print(f"[green]✓[/green] Exported {len(profiles)} profiles to [white]{path}[/white]")


def _prompt_export(profiles: List[Dict[str, Any]], platform: str) -> None:
    """Interactive export: ask format (CSV/JSON/JSONL/Both) and write file(s)."""
    if not profiles:
        return
    if not Confirm.ask("\n[white][+] Export results?[/white]", default=True):
        return

    fmt = Prompt.ask(
        "[white]Format[/white]",
        choices=["csv", "json", "jsonl", "both"],
        default="csv",
        show_choices=True,
    )
    stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")

    if fmt in ("csv", "both"):
        out_path = Prompt.ask(
            "[white]CSV output path[/white]",
            default=f"sindarin_{platform}_{stamp}.csv",
        )
        _export_csv(profiles, Path(out_path))

    if fmt in ("json", "both"):
        out_path = Prompt.ask(
            "[white]JSON output path[/white]",
            default=f"sindarin_{platform}_{stamp}.json",
        )
        _export_json(profiles, Path(out_path))

    if fmt == "jsonl":
        out_path = Prompt.ask(
            "[white]JSONL output path[/white]",
            default=f"sindarin_{platform}_{stamp}.jsonl",
        )
        _export_jsonl(profiles, Path(out_path))


# --- core scraping flow -----------------------------------------------------------


def scrape_single(platform: str, session: Optional[HttpSession] = None) -> Optional[Dict[str, Any]]:
    """Interactive single-profile scrape."""
    label, fn, prompt_text = PLATFORMS[platform]
    is_linkedin = (platform == "linkedin")
    _print_header(label, "Requires LINKEDIN_COOKIE env var" if is_linkedin else "Public profile — no login required")

    ident = Prompt.ask(f"[white]{prompt_text}[/white]")
    if not ident:
        console.print("[yellow]Cancelled.[/yellow]")
        return None

    console.print()
    with console.status(f"[white]Scraping {label}...[/white]", spinner="dots"):
        try:
            profile = fn(ident.strip(), session=session)
        except Exception as e:
            console.print(f"[red]Error:[/red] {e}")
            return None

    _profile_card(profile)

    # Ask about enrichment
    if Confirm.ask("\n[white][+] Enrich with email/domain candidates?[/white]", default=True):
        console.print()
        with console.status("[white]Enriching...[/white]", spinner="dots"):
            enriched = enrich(profile)
        _enrichment_summary([enriched])
        return enriched

    return profile


def scrape_bulk(platform: str, session: Optional[HttpSession] = None) -> List[Dict[str, Any]]:
    """Bulk scrape from a file of usernames (one per line, CSV or TXT)."""
    label, fn, _ = PLATFORMS[platform]
    _print_header(label, "Bulk mode — public profiles only")

    file_path = Prompt.ask("[white]Path to CSV/TXT file (one username per line)[/white]")
    path = Path(file_path).expanduser()
    if not path.exists():
        console.print(f"[red]File not found:[/red] {path}")
        return []

    identifiers: List[str] = []
    if path.suffix.lower() == ".csv":
        import csv as _csv
        with open(path, encoding="utf-8") as f:
            reader = _csv.DictReader(f)
            for row in reader:
                val = next((v for v in row.values() if v and v.strip()), None)
                if val:
                    identifiers.append(val.strip())
    else:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    identifiers.append(line)

    if not identifiers:
        console.print("[yellow]No identifiers found in file.[/yellow]")
        return []

    console.print(f"[white]Found {len(identifiers)} identifiers.[/white]")

    profiles: List[Dict[str, Any]] = []
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        task = progress.add_task(f"[white]Scraping {label}...[/white]", total=len(identifiers))
        for ident in identifiers:
            try:
                p = fn(ident, session=session)
                profiles.append(p)
            except Exception as e:
                console.print(f"[dim]  {ident}: {e}[/dim]")
            progress.advance(task)

    # Ask about enrichment
    if profiles and Confirm.ask("\n[white][+] Enrich all profiles?[/white]", default=True):
        console.print()
        with console.status("[white]Enriching...[/white]", spinner="dots"):
            enriched = enrich_many(profiles)
        _enrichment_summary(enriched)
        return enriched

    return profiles


def choose_platform() -> Optional[str]:
    """Interactive platform selector."""
    console.print()
    table = Table(show_header=False, box=box.SIMPLE, padding=(0, 2))
    table.add_column("Key", style=ACCENT)
    table.add_column("Platform", style="white")
    table.add_column("Notes", style="dim")
    for key, (label, _, prompt) in PLATFORMS.items():
        note = "cookie required" if key == "linkedin" else "public"
        table.add_row(key, label, f"({note})")
    console.print(table)
    console.print()

    choice = Prompt.ask(
        "[white]Platform[/white]",
        choices=list(PLATFORMS.keys()),
        show_choices=False,
    )
    return choice


def main() -> None:
    # CLI flags
    if len(sys.argv) > 1:
        arg = sys.argv[1].lower()
        if arg in ("--version", "-v", "version"):
            print(f"Sindarin v{__version__}")
            sys.exit(0)
        if arg in ("--help", "-h", "help"):
            print(f"Sindarin v{__version__} - Social media profile CLI scraper")
            print()
            print("Usage:")
            print("  python sindarin.py                # interactive menu")
            print("  python sindarin.py --version      # show version")
            print("  python sindarin.py --help         # show this help")
            print()
            print("Environment:")
            print("  GITHUB_TOKEN          GitHub API token (raises rate limit)")
            print("  TWITCH_CLIENT_ID      Twitch Helix API Client ID")
            print("  LINKEDIN_COOKIE       li_at session cookie for LinkedIn")
            print("  HUNTER_API_KEY        (optional) Hunter.io enrichment key")
            print("  SINDARIN_PROXY        Single proxy (http://user:pass@host:port)")
            print("  SINDARIN_PROXY_FILE   Proxy list file (one per line)")
            print("  SINDARIN_FREE_PROXY   Use free proxies (true/false)")
            print("  SINDARIN_DELAY        Per-host delay seconds (default 2.0)")
            print("  SINDARIN_TIMEOUT      Request timeout seconds (default 20)")
            print("  SINDARIN_HONOR_ROBOTS true to respect robots.txt (default false)")
            print("  SINDARIN_USER_AGENT   Override default self-identifying UA")
            print("  (SINDARIN_* vars also accepted under legacy SCOUT_* names)")
            print()
            print("Safety: Only scrapes public pages. No CAPTCHA solving, no login bypass,")
            print("no SMTP probing. Respects robots.txt when SINDARIN_HONOR_ROBOTS=true (opt-in).")
            sys.exit(0)

    _print_logo()
    console.print(Panel(
        f"[bold white]Sindarin v{__version__}[/bold white]\n"
        "[dim]Safe-to-use social media profile scraper[/dim]\n\n"
        "[white]•[/white] Public profiles only — no login bypass\n"
        "[white]•[/white] Self-identifying User-Agent\n"
        "[white]•[/white] Rate-limited, retry with backoff\n"
        "[white]•[/white] Optional proxy rotation\n"
        "[white]•[/white] Lightweight enrichment (no SMTP probing)\n"
        "[white]•[/white] CSV export",
        border_style=ACCENT,
        padding=(1, 2),
    ))

    # Session config — honor documented SINDARIN_* vars, with SCOUT_* as a
    # backwards-compatible fallback.
    def _env(*names: str) -> str:
        for n in names:
            v = os.environ.get(n, "").strip()
            if v:
                return v
        return ""

    delay = float(_env("SINDARIN_DELAY", "SINDARIN_REQUEST_DELAY") or "2.0")
    proxy = _env("SINDARIN_PROXY", "SCOUT_PROXY") or None
    proxy_file = _env("SINDARIN_PROXY_FILE", "SCOUT_PROXY_FILE") or None
    use_free = _env("SINDARIN_FREE_PROXY", "SCOUT_FREE_PROXY").lower() == "true"
    timeout = float(_env("SINDARIN_TIMEOUT", "SCOUT_TIMEOUT") or "20.0")
    ua = _env("SINDARIN_USER_AGENT", "SCOUT_USER_AGENT") or None
    honor_robots = _env("SINDARIN_HONOR_ROBOTS").lower() in ("1", "true", "yes")

    if use_free:
        console.print("[dim]Free proxy mode enabled (unreliable)[/dim]")

    http_session = HttpSession(
        delay=delay,
        timeout=timeout,
        proxy=proxy,
        proxy_file=proxy_file,
        user_agent=ua or DEFAULT_UA,
        honor_robots=honor_robots,
    )

    try:
        while True:
            platform = choose_platform()
            if not platform:
                break

            console.print()
            mode = Prompt.ask(
                "[white]Mode[/white]",
                choices=["single", "bulk"],
                default="single",
                show_choices=True,
            )

            result = None
            if mode == "single":
                result = scrape_single(platform, session=http_session)
            else:
                result = scrape_bulk(platform, session=http_session)

            if result:
                profiles = result if isinstance(result, list) else [result]
                _prompt_export(profiles, platform)

            if not Confirm.ask("\n[white]Scrape another?[/white]", default=True):
                break
            console.print()

    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/yellow]")
    except Exception as e:
        console.print(f"\n[red]Fatal error:[/red] {e}")
        if "--verbose" in sys.argv or "-V" in sys.argv:
            raise
    finally:
        http_session.close()

    console.print("\n[dim]Farewell, traveler.[/dim]")


if __name__ == "__main__":
    main()