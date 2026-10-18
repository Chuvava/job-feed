"""Priority of an offer: a tier that decides when it is delivered, and a score that orders it.

    A  🔥 right away: Java/C# stack + remote or a light hybrid + rate ≥ min_rate (or not stated)
    B  🗂 evening digest, ordered by score
    C  ⬇️ evening digest, bottom: only Python/JS stack, low rate, no B2B, or office/heavy hybrid outside Łódź

Thresholds live in config.yaml → priority. Weights follow cv-match/profile.md.
"""
import logging
import re
from dataclasses import dataclass
from datetime import date

from jobfeed import lookup
from jobfeed.filters import _norm
from jobfeed.models import Job

log = logging.getLogger(__name__)

DEFAULTS = {"min_rate": 120, "low_rate": 100, "light_hybrid_days": 4,
            "batch_hours": [9, 12, 15, 18, 21], "batch_cards": 3, "digest_hour": 21}

# Matched as whole words against title + skills (+ description when skills are unknown)
STACK = {
    r"java(?!script)": 3, r"c#|\.net|csharp": 3, r"selenium|selenide": 2, r"playwright": 2,
    r"rest ?assured": 2, r"api": 1, r"typescript": 1, r"sql|mysql|oracle": 1, r"testng|junit|nunit": 1,
    r"cucumber|bdd|gherkin": 1, r"jenkins|gitlab|azure|teamcity|ci/cd": 1, r"docker|kubernetes|k8s": 1,
    r"appium|mobile": 1, r"postman|swagger|openapi": 1, r"kafka": 1, r"sdet|automation|aqa": 2,
}
PENALTY = {r"python|pytest": -4, r"cypress": -1, r"ranorex|tosca|uft": -2, r"k6|gatling|locust": -1,
           r"lead|manager|head of|principal": -1, r"mid|regular": -1}
CORE = r"java(?!script)|c#|\.net|csharp"
OTHER_LANG = r"python|pytest|javascript|js|node\.?js|php|ruby|golang"

CURRENCY = {"pln": 1, "eur": 4.25, "usd": 3.7, "chf": 4.5, "gbp": 4.9}
HOURS = {"h": 1, "hour": 1, "hourly": 1, "day": 8, "daily": 8, "mo": 168, "month": 168, "monthly": 168,
         "yr": 2016, "year": 2016, "yearly": 2016}


def _hits(patterns, text: str) -> list:
    return [(p, w) for p, w in patterns.items() if re.search(rf"(?<![a-z]){p}(?![a-z])", text)]


def _has(pattern: str, text: str) -> bool:
    return bool(re.search(rf"(?<![a-z])({pattern})(?![a-z])", text))


def rate_per_hour(salary: str) -> float | None:
    """'18 480–25 200 PLN/mo b2b; …' -> 150.0 (top of the B2B range, PLN per hour)."""
    parts = [p for p in salary.split(";") if p.strip()]
    b2b = [p for p in parts if "b2b" in p.lower()]
    best = None
    for part in b2b or parts:
        m = re.search(r"([\d\s ?.,]+)[–-]([\d\s ?.,]+)\s*([A-Za-z]{3})\s*/\s*([A-Za-z]+)", part)
        if not m:
            continue
        nums = [float(re.sub(r"[^\d.]", "", x.replace(",", "."))) for x in m.group(1, 2)
                if re.sub(r"[^\d]", "", x)]
        unit = HOURS.get(m.group(4).lower())
        if not nums or not unit:
            continue
        value = max(nums) * CURRENCY.get(m.group(3).lower(), 1)
        if unit == 168 and value < 1000:      # boards sometimes label an hourly rate as monthly
            unit = 1
        elif unit == 1 and value > 2000:      # …and the other way round
            unit = 168
        rate = value / unit
        best = rate if best is None else max(best, rate)
    return best


_WEEK = r"(week|tydzien|tygodniu|tyg)"
_MONTH = r"(month|miesiac|miesiacu|mies|msc)"
_OFFICE = r"(office|biur|onsite|on-site|on site|stacjonar|hybrid|hybryd|siedzib|w firmie)"


def office_days_per_month(text: str) -> float | None:
    """Office days per month from the vacancy text, None when it doesn't say."""
    t = _norm(text)
    if re.search(rf"(once|raz) (a |per |w |na )?{_WEEK}", t):
        return 4.3
    if re.search(rf"(once|raz) (a |per |w |na )?{_MONTH}", t):
        return 1
    for m in re.finditer(r"(\d{1,2})(?:\s*[-–]\s*(\d{1,2}))?\s*(x|times|days?|dni|dzien|razy?|visits?|wizyt\w*)?"
                         rf"([^0-9.\n]{{0,30}}?)(?:per|a|w|na|/|in|each|every)?\s*(?:the\s*)?({_WEEK}|{_MONTH})\b", t):
        if not m.group(3) and not re.search(r"(office|biur|onsite|visit|wizyt)", m.group(4)):
            continue  # a bare number before "week/month" is something else (sprints, years…)
        if re.search(r"(year|lat|rok|hour|godzin)", m.group(4)):
            continue
        before = t[max(0, m.start() - 80):m.start()]
        n = float(m.group(2) or m.group(1))   # "2-3 days" -> the worse case
        days = n * 4.3 if re.match(_WEEK, m.group(5)) else n
        if re.search(r"(remote|zdaln)", before) and not re.search(_OFFICE, before):
            days = max(0.0, 21.5 - days) if re.match(_MONTH, m.group(5)) else (5 - n) * 4.3
        elif not re.search(_OFFICE, t[max(0, m.start() - 120):m.end() + 40]):
            continue
        return days
    return None


@dataclass
class Priority:
    tier: str            # A | B | C
    score: int
    rate: float | None   # PLN/h
    office_days: float | None
    why: str             # short human reason for the card


def enrich(job: Job) -> None:
    """Fetch the detail page when the list data can't decide the tier: LinkedIn has no skills
    or workplace, hybrid offers hide the office frequency in the text."""
    if job.source != "linkedin" and job.workplace != "hybrid":
        return
    try:
        d = lookup.lookup(job.url)
    except Exception as e:
        log.warning("detail of %s failed: %s", job.url, e)
        return
    job.description = d.get("description") or ""
    if not job.skills:
        job.skills = d.get("skills_required") or []
    if not job.salary and d.get("salary"):
        job.salary = d["salary"]
    m = re.match(r"hybrid \((\d) days/week remote\)", d.get("workplace") or "")
    if m:  # NoFluff states remote days per week
        job.description = f"office {5 - int(m.group(1))} days per week\n" + job.description


def priority(job: Job, cfg: dict | None = None, today: date | None = None) -> Priority:
    cfg = {**DEFAULTS, **(cfg or {})}
    today = today or date.today()
    head = _norm(" ".join([job.title, *job.skills]))
    stack_text = head if job.skills else _norm(job.title + " " + job.description[:3000])
    loc = _norm(job.location)

    score = sum(w for _, w in _hits(STACK, stack_text) + _hits(PENALTY, head))
    core = _has(CORE, stack_text)
    other_only = _has(OTHER_LANG, stack_text) and not core

    rate = rate_per_hour(job.salary)
    office = office_days_per_month(job.description) if job.workplace == "hybrid" else None
    remote = job.workplace == "remote" or (not job.workplace and "remote" in _norm(job.title + job.location))
    light = remote or (office is not None and office <= cfg["light_hybrid_days"])
    heavy = "lodz" not in loc and (job.workplace == "office" or (office is not None and office > 12))

    if remote:
        score += 3
    elif light:
        score += 2
    elif "lodz" in loc:
        score += 2
    elif "warsz" in loc or "warsaw" in loc:
        score += 1
    if rate is None:
        score += 1
    elif rate >= 150:
        score += 4
    elif rate >= cfg["min_rate"]:
        score += 3
    elif rate < cfg["low_rate"]:
        score -= 3
    if "senior" in _norm(job.seniority + " " + job.title):
        score += 1
    if job.published:
        age = (today - date.fromisoformat(job.published)).days
        score += 2 if age <= 3 else 1 if age <= 7 else 0

    low = other_only or (rate is not None and rate < cfg["low_rate"]) or heavy or job.no_b2b
    rate_ok = rate is None or rate >= cfg["min_rate"]
    tier = "A" if core and light and rate_ok and not low else "C" if low else "B"

    why = []
    why.append("remote" if remote else f"офис ~{office:.0f} дн/мес" if office is not None else job.workplace or "формат ?")
    why.append(f"~{rate:.0f} PLN/ч" if rate else "ставка не указана")
    lang = [n for n, p in (("Java", r"java(?!script)"), ("C#", r"c#|\.net|csharp"), ("Python", r"python|pytest"),
                           ("JS", r"javascript|js|node\.?js"), ("TS", r"typescript")) if _has(p, stack_text)]
    why.append("/".join(lang) or "язык ?")
    if job.no_b2b:
        why.append("без B2B")
    return Priority(tier, score, rate, office, " · ".join(why))
