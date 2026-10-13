"""LinkedIn via JobSpy: uses LinkedIn's public guest search, no account involved."""
import logging
import math
import time

from jobfeed.models import Job
from jobfeed.sources import money

log = logging.getLogger(__name__)


def _val(row, key):
    v = row.get(key)
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    return v


def fetch(cfg: dict) -> list[Job]:
    from jobspy import scrape_jobs  # heavy import, only when LinkedIn is enabled

    jobs: dict[str, Job] = {}
    for i, term in enumerate(cfg.get("search_terms", [])):
        if i:
            time.sleep(cfg.get("delay_seconds", 15))  # back-to-back queries get HTTP 429
        try:
            df = scrape_jobs(
                site_name=["linkedin"],
                search_term=term,
                location=cfg.get("location", "Poland"),
                hours_old=cfg.get("hours_old", 3),
                results_wanted=cfg.get("results_per_term", 40),
            )
        except Exception as e:  # one blocked query must not kill the whole run
            log.warning("LinkedIn search %r failed: %s", term, e)
            continue
        for row in df.to_dict("records"):
            job_id = f"linkedin:{_val(row, 'id') or _val(row, 'job_url')}"
            salary = ""
            if _val(row, "min_amount") or _val(row, "max_amount"):
                salary = (f"{money(_val(row, 'min_amount'))}–{money(_val(row, 'max_amount'))} "
                          f"{_val(row, 'currency') or ''}/{_val(row, 'interval') or ''}")
            jobs[job_id] = Job(
                id=job_id,
                source="linkedin",
                title=_val(row, "title") or "",
                company=_val(row, "company") or "",
                url=_val(row, "job_url") or "",
                location=_val(row, "location") or "",
                workplace="remote" if _val(row, "is_remote") else "",
                salary=salary,
                seniority=_val(row, "job_level") or "",
                published=str(_val(row, "date_posted") or "")[:10],
            )
    return list(jobs.values())
