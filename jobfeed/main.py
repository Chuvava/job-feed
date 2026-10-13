"""Collect new vacancies from all sources and push the matching ones to Telegram.

    python -m jobfeed.main            # normal run
    python -m jobfeed.main --dry-run  # print instead of sending, don't touch state
"""
import argparse
import logging
import os
import re
import sys
from pathlib import Path

import yaml

from jobfeed import filters
from jobfeed.models import Job
from jobfeed.sources import justjoin, linkedin, nofluff
from jobfeed.store import SeenStore
from jobfeed.telegram import Telegram, format_job

ROOT = Path(__file__).resolve().parent.parent
SOURCES = {"justjoin": justjoin.fetch, "nofluff": nofluff.fetch, "linkedin": linkedin.fetch}
MAX_MESSAGES_PER_RUN = 40

log = logging.getLogger("jobfeed")


def load_env(path: Path) -> None:
    """Minimal .env loader for local runs; in CI the values come from secrets."""
    if not path.exists():
        return
    for line in path.read_text("utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def cross_source_key(job: Job) -> str:
    """Same offer posted on several boards -> same key."""
    norm = lambda s: re.sub(r"[^a-z0-9]+", "", s.lower())
    company = re.sub(r"\b(sp z o o|s a|ltd|gmbh|inc|llc)\b", "", job.company.lower())
    return f"ct:{norm(company)}|{norm(job.title)}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--only", choices=SOURCES, help="run a single source")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    load_env(ROOT / ".env")
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text("utf-8"))
    store = SeenStore(ROOT / "state" / "seen.json")

    collected: list[Job] = []
    failed: list[str] = []
    for name, fetch in SOURCES.items():
        src_cfg = cfg["sources"].get(name, {})
        if not src_cfg.get("enabled") or (args.only and args.only != name):
            continue
        try:
            jobs = fetch(src_cfg)
            log.info("%s: %d offers", name, len(jobs))
            collected += jobs
        except Exception as e:
            log.error("%s failed: %s", name, e)
            failed.append(name)

    # A source seen for the first time is indexed silently, otherwise it would dump
    # everything currently online into the chat
    new_sources = {j.source for j in collected if f"src:{j.source}" not in store}
    fresh: list[Job] = []
    for job in collected:
        if job.id in store:
            continue
        store.add(job.id)
        # Filter before the company+title dedupe: the Gdańsk copy of an offer must not
        # hide its Warsaw copy that does match
        if not filters.passes(job, cfg["filters"]):
            continue
        key = cross_source_key(job)
        if key in store:
            continue
        store.add(key)
        if job.source not in new_sources:
            fresh.append(job)
    for source in new_sources:
        store.add(f"src:{source}")
    log.info("new matching offers: %d (silently indexed sources: %s)", len(fresh), sorted(new_sources) or "-")

    if args.dry_run:
        for job in fresh:
            print(format_job(job), end="\n\n")
        return 0

    tg = Telegram(os.environ["TELEGRAM_TOKEN"], os.environ["TELEGRAM_CHAT_ID"])
    if new_sources:
        indexed = sum(1 for j in collected if j.source in new_sources)
        tg.send(f"✅ Started watching {', '.join(sorted(new_sources))}: indexed {indexed} current offers. "
                f"New ones will arrive here.")
    for job in fresh[:MAX_MESSAGES_PER_RUN]:
        tg.send(format_job(job))
    if len(fresh) > MAX_MESSAGES_PER_RUN:
        tg.send(f"…and {len(fresh) - MAX_MESSAGES_PER_RUN} more skipped this run (limit {MAX_MESSAGES_PER_RUN}).")
    if failed:
        tg.send(f"⚠️ Sources failed this run: {', '.join(failed)}")

    store.save()
    return 0


if __name__ == "__main__":
    sys.exit(main())
