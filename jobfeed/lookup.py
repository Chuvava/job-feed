"""Vacancy URL -> structured JSON (used by the job-tracker skill and later by the bot).

    python -m jobfeed.lookup <url>
"""
import json
import re
import sys
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from jobfeed.sources import money, session
from jobfeed.sources.justjoin import _salary as jj_salary
from jobfeed.sources.nofluff import _workplace as nfj_workplace

DESCRIPTION_LIMIT = 6000
BROWSER_HEADERS = {
    # LinkedIn serves a JS bot wall unless the request looks like a real browser
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "text/html",
    "Accept-Language": "en-US,en;q=0.9",
}


def _text(html: str | None) -> str:
    if not html:
        return ""
    text = BeautifulSoup(html, "html.parser").get_text("\n")
    text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    return text[:DESCRIPTION_LIMIT]


def justjoin(url: str) -> dict:
    slug = urlparse(url).path.rstrip("/").split("/")[-1]
    o = session().get(f"https://justjoin.it/api/candidate-api/offers/{slug}", timeout=30).json()
    return {
        "source": "justjoin",
        "url": f"https://justjoin.it/job-offer/{slug}",
        "title": o.get("title"),
        "company": o.get("companyName"),
        "location": ", ".join(dict.fromkeys(l["city"] for l in o.get("locations") or [] if l.get("city"))),
        "workplace": o.get("workplaceType"),
        "salary": jj_salary(o.get("employmentTypes") or []),
        "seniority": o.get("experienceLevel"),
        "skills_required": [s["name"] for s in o.get("requiredSkills") or []],
        "skills_nice": [s["name"] for s in o.get("niceToHaveSkills") or []],
        "languages": [f"{l['code']} {l.get('level', '')}".strip() for l in o.get("languages") or []],
        "apply_url": o.get("applyUrl"),
        "published": o.get("publishedAt"),
        "description": _text(o.get("body")),
    }


def _nfj_remote_days(days) -> str:
    """The detail endpoint gives remote days per week instead of a type."""
    if not isinstance(days, int):
        return ""
    return {0: "office", 5: "remote"}.get(days, f"hybrid ({days} days/week remote)")


def nofluff(url: str) -> dict:
    posting_id = urlparse(url).path.rstrip("/").split("/")[-1]
    p = session().get(f"https://nofluffjobs.com/api/posting/{posting_id}", timeout=30).json()
    sal = (p.get("essentials") or {}).get("originalSalary") or {}
    salary = "; ".join(
        f"{money(t['range'][0])}–{money(t['range'][1])} {sal.get('currency')}/{t.get('period', '').lower()} {kind}"
        for kind, t in (sal.get("types") or {}).items() if t.get("range")
    )
    loc = p.get("location") or {}
    req = p.get("requirements") or {}
    specs = p.get("specs") or {}
    description = "\n\n".join(filter(None, [
        _text((p.get("details") or {}).get("description")),
        "Daily tasks:\n- " + "\n- ".join(specs["dailyTasks"]) if specs.get("dailyTasks") else "",
        _text(req.get("description")),
    ]))
    return {
        "source": "nofluff",
        "url": f"https://nofluffjobs.com/pl/job/{posting_id}",
        "title": p.get("title"),
        "company": (p.get("company") or {}).get("name"),
        "location": ", ".join(sorted({pl["city"] for pl in loc.get("places", []) if pl.get("city")})),
        "workplace": nfj_workplace(loc) or _nfj_remote_days(loc.get("remote")),
        "salary": salary,
        "seniority": ", ".join((p.get("basics") or {}).get("seniority") or []),
        "skills_required": [m["value"] for m in req.get("musts") or []],
        "skills_nice": [m["value"] for m in req.get("nices") or []],
        "languages": [l.get("code") for l in (p.get("recruitment") or {}).get("languages") or []],
        "contract_start": ((p.get("essentials") or {}).get("contract") or {}).get("start"),
        "description": description[:DESCRIPTION_LIMIT],
    }


def linkedin(url: str) -> dict:
    m = re.search(r"(\d{8,})", url)
    if not m:
        raise ValueError("no LinkedIn job id in the URL")
    job_id = m.group(1)
    r = session().get(f"https://www.linkedin.com/jobs/view/{job_id}", headers=BROWSER_HEADERS, timeout=30)
    soup = BeautifulSoup(r.text, "html.parser")
    pick = lambda css: (el.get_text(" ", strip=True) if (el := soup.select_one(css)) else "")
    if not pick(".topcard__title"):
        raise RuntimeError(f"LinkedIn returned no job card (HTTP {r.status_code}), probably a bot wall")
    criteria = {
        pick_h.get_text(strip=True): pick_v.get_text(strip=True)
        for item in soup.select(".description__job-criteria-item")
        if (pick_h := item.select_one("h3")) and (pick_v := item.select_one("span"))
    }
    return {
        "source": "linkedin",
        "url": f"https://www.linkedin.com/jobs/view/{job_id}",
        "title": pick(".topcard__title"),
        "company": pick(".topcard__org-name-link") or pick(".topcard__flavor"),
        "location": pick(".topcard__flavor--bullet"),
        "salary": pick(".compensation__salary"),
        "seniority": criteria.get("Seniority level", ""),
        "employment_type": criteria.get("Employment type", ""),
        "description": _text(str(soup.select_one(".show-more-less-html__markup") or "")),
    }


def generic(url: str) -> dict:
    r = session().get(url, headers=BROWSER_HEADERS, timeout=30)
    soup = BeautifulSoup(r.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
        tag.decompose()
    return {
        "source": urlparse(url).netloc,
        "url": url,
        "title": soup.title.get_text(strip=True) if soup.title else "",
        "description": _text(str(soup.body or soup)),
        "note": "unknown site: fields not extracted, read them from description",
    }


def lookup(url: str) -> dict:
    host = urlparse(url).netloc.lower()
    if "justjoin.it" in host:
        return justjoin(url)
    if "nofluffjobs.com" in host:
        return nofluff(url)
    if "linkedin.com" in host:
        return linkedin(url)
    return generic(url)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python -m jobfeed.lookup <url>")
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(lookup(sys.argv[1]), ensure_ascii=False, indent=1))
