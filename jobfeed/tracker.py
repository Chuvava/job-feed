"""Offer identity across boards and against the tracker sheet.

Marks, never hides: a card for an offer that is already in the tracker still arrives,
it just says which row it is, so nothing gets lost to a false match.
"""
import logging
import os
import re

import requests

from jobfeed.filters import _norm
from jobfeed.models import Job

log = logging.getLogger(__name__)

# Applied after punctuation is turned into spaces: "Sp. z o.o." -> "sp z o o"
LEGAL = re.compile(r"\b(sp z o o|sp k|s a|ltd|gmbh|inc|llc|polska|poland|group|agencja pracy.*)\b")
# Gender tags that the same offer carries on one board and not on another: (K/M), m/f/d, f/m/x...
GENDER = re.compile(r"\b(k ?m ?x?|m ?k|m ?f ?[dnx]?|f ?m ?[dnx]?)\s*$")


def _words(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9#+]+", " ", _norm(text))).strip()


def company_key(company: str) -> str:
    # tracker rows look like "ALTEN Polska — клиент: банк" or "Nordea (через emagine)"
    company = re.split(r" — | - |\(", company)[0]
    return re.sub(r"\W+", "", LEGAL.sub("", _words(company)))


def title_key(title: str) -> str:
    return re.sub(r"\W+", "", GENDER.sub("", _words(title)))


def cross_source_key(job: Job) -> str:
    """Same offer posted on several boards -> same key."""
    return f"ct:{company_key(job.company)}|{title_key(job.title)}"


def legacy_key(job: Job) -> str:
    """Key format used before 14.10.2026; still checked so reposts of old offers don't come back."""
    norm = lambda s: re.sub(r"[^a-z0-9]+", "", s.lower())
    company = re.sub(r"\b(sp z o o|s a|ltd|gmbh|inc|llc)\b", "", job.company.lower())
    return f"ct:{norm(company)}|{norm(job.title)}"


def norm_url(url: str) -> str:
    return re.sub(r"[?#].*$", "", str(url).strip()).rstrip("/").lower()


class Tracker:
    """Rows of the "2026 New" tab, read through the Apps Script web app (action "list")."""

    def __init__(self, rows: list[dict], hidden: list[dict] = ()):
        self.hidden_urls = {norm_url(h["url"]) for h in hidden if h.get("url")}
        self.hidden_keys = {f"ct:{company_key(h['company'])}|{title_key(h['role'])}" for h in hidden if h.get("role")}
        self.by_url = {norm_url(r["url"]): r for r in rows if str(r.get("url", "")).startswith("http")}
        self.by_key = {f"ct:{company_key(r['company'])}|{title_key(r['role'])}": r for r in rows}
        self.by_company: dict[str, list[dict]] = {}
        for r in rows:
            if company_key(r["company"]):
                self.by_company.setdefault(company_key(r["company"]), []).append(r)

    @classmethod
    def load(cls) -> "Tracker":
        url, secret = os.environ.get("SHEET_URL"), os.environ.get("SHEET_SECRET")
        if not url or not secret:
            return cls([])
        for attempt in range(3):  # Apps Script sometimes hangs on a cold start
            try:
                r = requests.post(url, json={"secret": secret, "action": "list"}, timeout=30)
                rows = r.json().get("rows")
                if rows is None:
                    raise ValueError(r.text[:200])
                hidden = r.json().get("hidden") or []
                log.info("tracker: %d rows, %d hidden", len(rows), len(hidden))
                return cls(rows, hidden)
            except Exception as e:  # the feed must keep working without the sheet
                log.warning("tracker unavailable (attempt %d): %s", attempt + 1, e)
        return cls([])

    def find(self, job: Job) -> dict | None:
        return self.by_url.get(norm_url(job.url)) or self.by_key.get(cross_source_key(job))

    def hidden(self, job: Job) -> bool:
        """Removed with 🗑 in the bot (same URL, or the same company+title from another board)."""
        return norm_url(job.url) in self.hidden_urls or cross_source_key(job) in self.hidden_keys

    def notes(self, job: Job) -> list[str]:
        """📌 this very offer is in the tracker; ⚠️ other rows of the same company (don't send two CVs)."""
        own = self.find(job)
        out = [f"📌 Уже в трекере · строка {own['row']} · {own['stage']}"] if own else []
        for r in self.by_company.get(company_key(job.company), []):
            if r is not own:
                cv = f" · CV {r['cv']}" if r.get("cv") else ""
                out.append(f"⚠️ {r['company']}: «{r['role']}» · {r['stage']}{cv} · строка {r['row']}")
        return out[:3]

    def __len__(self) -> int:
        return len(self.by_key)
