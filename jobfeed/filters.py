from jobfeed.models import Job


def _has_any(text: str, needles: list[str]) -> bool:
    text = text.lower()
    return any(n.lower() in text for n in needles)


def passes(job: Job, f: dict) -> bool:
    if not _has_any(job.title, f.get("title_include", [])):
        return False
    if _has_any(job.title, f.get("title_exclude", [])):
        return False
    if job.seniority and _has_any(job.seniority, f.get("seniority_exclude", [])):
        # "Mid, Senior" must survive while "Junior" alone must not
        levels = [s.strip().lower() for s in job.seniority.split(",")]
        if all(_has_any(lvl, f.get("seniority_exclude", [])) for lvl in levels):
            return False
    allowed = f.get("workplace")
    if allowed and job.workplace and job.workplace not in allowed:
        return False
    return True
