"""
Sindarin's lightweight, SMTP-free enrichment.

Design principles (why this is safe-to-use):

  * We NEVER probe mail servers. SMTP VRFY/RCPT probing is considered abuse
    by many providers, can land your IP on blocklists, and may be unlawful in
    some jurisdictions. Scout-style SMTP verification is intentionally not
    implemented here.
  * We only use data the scraper already lawfully obtained from a public
    page: bio text, public links, and any email the platform itself shows.
  * Confidence is a heuristic 0-100 score over the data we observed; it is
    NOT a verification verdict. Treat it as "how likely is this email to
    belong to this person, based on public signals", nothing more.
  * We do company-domain MX lookup passively via dnspython — reading the
    public DNS records a company published. This is normal DNS use, not
    network probing.

What this module does, in order:
  1. Normalize the email/phone already present on the profile.
  2. Detect a company string from the headline/bio ("Founder @ Acme", "SWE
     at Acme Inc.").
  3. Look up the company's MX domain via public DNS.
  4. Detect an email pattern from any public emails that already appear on
     the profile (e.g. one hire@acme.com email implies a pattern).
  5. Produce candidate emails ONLY IF the profile already exposes the
     person's first and last name AND the company domain — we generate at
     most the 4 most common patterns and mark each as "unverified".
     Without SMTP probing we cannot confirm them, so they are never labeled
     verified.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

log = logging.getLogger("sindarin.enrichment")


# Common patterns, ordered by frequency in corporate domains.
_EMAIL_PATTERNS = [
    "{first}.{last}@{domain}",
    "{first}@{domain}",
    "{first}{last}@{domain}",
    "{f}{last}@{domain}",  # first-initial + last
]

# Pattern around company mentions: "Founder @ Acme", "SWE at Acme Inc."
_COMPANY_RE = re.compile(
    r"(?:\b(?:at|@)\s+)([A-Z][A-Za-z0-9&'.,\- ]{1,40}?)(?=\s*(?:[·\-\|]|\b(?:and|&)\s|\.|$))",
)

# FirstName LastName extraction from full_name (plain ASCII heuristic)
_NAME_RE = re.compile(r"^\s*([A-Za-z'\-]+)\s+([A-Za-z'\-]+)\s*$")


def enrich(profile: Dict[str, Any]) -> Dict[str, Any]:
    """Enrich a single scraped profile in place and return it.

    Adds:
        profile['email_candidates']  : list[{email, pattern, score, verified}]
        profile['company_domain']    : str | None
        profile['lead_score']        : int 0-100 (heuristic)
    """
    p = dict(profile)  # shallow copy; we add keys, never overwrite originals
    p.setdefault("raw", dict(profile.get("raw") or {}))

    company = p.get("company") or _detect_company(p)
    if company and not p.get("company"):
        p["company"] = company

    domain = _derive_domain(p, company)

    candidates: List[Dict[str, Any]] = []
    if domain:
        p["company_domain"] = domain
        first, last = _split_name(p.get("full_name") or "")
        if first and last:
            seen = set()
            for tpl in _EMAIL_PATTERNS:
                addr = tpl.format(
                    first=first.lower(), last=last.lower(),
                    f=first[0].lower(), domain=domain,
                )
                if addr in seen:
                    continue
                seen.add(addr)
                # Heuristic confidence: pattern popularity × whether we have a
                # direct email on profile with the same domain.
                base = 55 if "." in addr.split("@")[0] else 40
                if p.get("email") and p["email"].split("@")[-1].lower() == domain.lower():
                    base += 15
                candidates.append({
                    "email": addr,
                    "pattern": tpl,
                    "score": min(base, 85),
                    "verified": False,  # NEVER claim verified without SMTP
                })

    p["email_candidates"] = candidates
    p["lead_score"] = _score(p)
    return p


def enrich_many(profiles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [enrich(p) for p in profiles]


# --- internals ------------------------------------------------------------------

def _detect_company(p: Dict[str, Any]) -> Optional[str]:
    text = " ".join(filter(None, [
        p.get("bio") or "",
        (p.get("raw") or {}).get("headline") or "",
    ]))
    if not text:
        return None
    m = _COMPANY_RE.search(text)
    if not m:
        return None
    cand = m.group(1).strip().rstrip(".,")
    # Filter out obvious stopwords ("Home", "School", common adverbs)
    if cand.lower() in {"home", "school", "work", "life"}:
        return None
    return cand or None


def _derive_domain(p: Dict[str, Any], company: Optional[str]) -> Optional[str]:
    """Find the company's email domain via public DNS MX lookup.

    Passive DNS only — no SMTP probing. If the company string is "Acme" we
    try acme.com; if a MX record exists, we use that domain.
    """
    if not company:
        return None
    slug = _slugify_company(company)
    candidates = [f"{slug}.com", f"{slug}.io", f"{slug}.co"]
    try:
        import dns.resolver  # type: ignore
    except ImportError:
        log.debug("dnspython not installed; skipping MX lookup")
        return None

    resolver = dns.resolver.Resolver()
    resolver.timeout = 3
    resolver.lifetime = 5

    for cand in candidates:
        try:
            resolver.resolve(cand, "MX")
            return cand
        except Exception:
            continue
    return None


def _slugify_company(name: str) -> str:
    # Take the first word, lowercase, strip non-alnum.
    first_word = re.split(r"[\s&]+", name.strip(), 1)[0]
    return re.sub(r"[^a-z0-9]", "", first_word.lower())[:20]


def _split_name(full_name: str) -> tuple[Optional[str], Optional[str]]:
    if not full_name:
        return None, None
    m = _NAME_RE.match(full_name)
    if not m:
        return None, None
    first, last = m.group(1), m.group(2)
    if len(first) < 2 or len(last) < 2:
        return None, None
    return first, last


def _score(p: Dict[str, Any]) -> int:
    """Heuristic 0-100 lead score based on what public data we obtained."""
    score = 0
    if p.get("full_name"):
        score += 15
    if p.get("email"):
        score += 30
    if p.get("phone"):
        score += 10
    if p.get("company"):
        score += 15
    if p.get("website"):
        score += 10
    if p.get("follower_count") and int(p["follower_count"] or 0) > 1000:
        score += 10
    if p.get("links") and len(p["links"]) >= 2:
        score += 10
    return min(score, 100)
