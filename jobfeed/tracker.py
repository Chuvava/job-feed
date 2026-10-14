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

    def __init__(self, rows: list[dict]):
        self.by_url = {norm_url(r["url"]): r for r in rows if str(r.get("url", "")).startswith("http")}
        self.by_key = {f"ct:{company_key(r['company'])}|{title_key(r['role'])}": r for r in rows}

    @classmethod
    def load(cls) -> "Tracker":
        url, secret = os.environ.get("SHEET_URL"), os.environ.get("SHEET_SECRET")
        if not url or not secret:
            return cls([])
        try:
            r = requests.post(url, json={"secret": secret, "action": "list"}, timeout=60)
            rows = r.json().get("rows")
            if rows is None:
                raise ValueError(r.text[:200])
            return cls(rows)
        except Exception as e:  # the feed must keep working without the sheet
            log.warning("tracker unavailable: %s", e)
            return cls([])

    def find(self, job: Job) -> dict | None:
        return self.by_url.get(norm_url(job.url)) or self.by_key.get(cross_source_key(job))

    def __len__(self) -> int:
        return len(self.by_key)
