import unicodedata

from jobfeed.models import Job


def _norm(text: str) -> str:
    """'Łódź' -> 'lodz', 'Kraków' -> 'krakow'"""
    text = text.lower().replace("ł", "l")
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def _has_any(text: str, needles: list[str]) -> bool:
    text = _norm(text)
    return any(_norm(n) in text for n in needles)


def _location_ok(job: Job, rules: dict) -> bool:
    """rules: {workplace: "any" | [cities]}; a workplace type missing from rules is rejected."""
    def allowed(kind: str) -> bool:
        cities = rules.get(kind)
        return cities == "any" or (isinstance(cities, list) and _has_any(job.location, cities))

    if job.workplace:
        return allowed(job.workplace)
    # Workplace unknown (LinkedIn often doesn't say): accept if any rule would match.
    # A bare country ("Poland") usually means remote.
    no_city = _norm(job.location).strip() in ("", "poland", "polska")
    return allowed("hybrid") or allowed("office") or (no_city and rules.get("remote") == "any")


def passes(job: Job, f: dict) -> bool:
    if not _has_any(job.title, f.get("title_include", [])):
        return False
    if _has_any(job.title, f.get("title_exclude", [])):
        return False
    if job.seniority and _has_any(job.seniority, f.get("seniority_exclude", [])):
        # "Mid, Senior" must survive while "Junior" alone must not
        levels = [s.strip() for s in job.seniority.split(",")]
        if all(_has_any(lvl, f.get("seniority_exclude", [])) for lvl in levels):
            return False
    if "workplace" in f and not _location_ok(job, f["workplace"]):
        return False
    return True
