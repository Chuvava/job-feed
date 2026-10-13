from jobfeed.models import Job
from jobfeed.sources import money, session

API = "https://justjoin.it/api/candidate-api/offers"
UNITS = {"Hour": "h", "Month": "mo", "Day": "day", "Year": "yr"}


def _salary(employment_types: list[dict]) -> str:
    parts = []
    for e in employment_types:
        if e.get("currencySource") != "original":
            continue  # skip auto-converted USD/EUR/... duplicates
        if e.get("fromPerUnit") is None and e.get("toPerUnit") is None:
            parts.append(e.get("type", ""))
            continue
        unit = UNITS.get(e.get("unit"), e.get("unit") or "")
        parts.append(
            f"{money(e.get('fromPerUnit'))}–{money(e.get('toPerUnit'))} "
            f"{e.get('currency')}/{unit} {e.get('type', '')}".strip()
        )
    return "; ".join(p for p in parts if p)


def fetch(cfg: dict) -> list[Job]:
    s = session()
    jobs: list[Job] = []
    cursor = 0
    for _ in range(cfg.get("max_pages", 5)):
        r = s.get(API, params={
            "categories": cfg.get("category", "testing"),
            "sortBy": "publishedAt",
            "orderBy": "descending",
            "from": cursor,
        }, timeout=30)
        r.raise_for_status()
        body = r.json()
        for o in body.get("data", []):
            jobs.append(Job(
                id=f"justjoin:{o['guid']}",
                source="justjoin",
                title=o.get("title", ""),
                company=o.get("companyName", ""),
                url=f"https://justjoin.it/job-offer/{o['slug']}",
                # multi-city offers list every city here, the filter needs all of them
                location=", ".join(dict.fromkeys(
                    loc["city"] for loc in o.get("locations") or [] if loc.get("city")
                )) or o.get("city", ""),
                workplace=o.get("workplaceType", ""),
                salary=_salary(o.get("employmentTypes") or []),
                seniority=o.get("experienceLevel", ""),
                skills=[sk["name"] for sk in o.get("requiredSkills") or []],
                contracts=sorted({e["type"] for e in o.get("employmentTypes") or [] if e.get("type")}),
            ))
        cursor = (body.get("meta", {}).get("next") or {}).get("cursor")
        if cursor is None:
            break
    return jobs
