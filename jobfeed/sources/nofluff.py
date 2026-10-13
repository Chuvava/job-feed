from jobfeed.models import Job
from jobfeed.sources import money, session

API = "https://nofluffjobs.com/api/search/posting"


def _salary(sal: dict | None) -> str:
    if not sal:
        return ""
    period = {"Month": "mo", "Hour": "h", "Day": "day", "Year": "yr"}.get(sal.get("period"), "")
    return f"{money(sal.get('from'))}–{money(sal.get('to'))} {sal.get('currency')}/{period} {sal.get('type', '')}".strip()


def _workplace(loc: dict) -> str:
    if loc.get("fullyRemote"):
        return "remote"
    kind = (loc.get("type") or "").lower()
    return "office" if kind == "onsite" else kind


def fetch(cfg: dict) -> list[Job]:
    s = session()
    jobs: list[Job] = []
    page, total_pages = 1, 1
    while page <= total_pages:
        r = s.post(
            API,
            # page only works as a query param, in the body it is ignored
            params={"salaryCurrency": "PLN", "salaryPeriod": "month", "region": "pl", "page": page},
            json={"criteriaSearch": {"category": [cfg.get("category", "testing")]}},
            timeout=30,
        )
        r.raise_for_status()
        body = r.json()
        total_pages = body.get("totalPages", 1)
        for p in body.get("postings", []):
            loc = p.get("location") or {}
            cities = sorted({pl.get("city") for pl in loc.get("places", []) if pl.get("city")})
            jobs.append(Job(
                # one row per city; per-city copies are collapsed later by company+title
                id=f"nofluff:{p['id']}",
                source="nofluff",
                title=p.get("title", ""),
                company=p.get("name", ""),
                url=f"https://nofluffjobs.com/pl/job/{p['url']}",
                location=", ".join(c for c in cities if c.lower() != "remote")[:80],
                workplace=_workplace(loc),
                salary=_salary(p.get("salary")),
                seniority=", ".join(p.get("seniority") or []),
                skills=[t["value"] for t in (p.get("tiles") or {}).get("values", []) if t.get("type") == "requirement"],
                # search shows the b2b salary whenever the offer has one, so a non-b2b type means no B2B at all
                contracts=[p["salary"]["type"]] if (p.get("salary") or {}).get("type") else [],
            ))
        page += 1
    return jobs
