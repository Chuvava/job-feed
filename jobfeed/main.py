"""Collect new vacancies from all sources and push the matching ones to Telegram.

    python -m jobfeed.main            # normal run
    python -m jobfeed.main --dry-run  # print instead of sending, don't touch state
"""
import argparse
import html
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

import yaml

from jobfeed import filters
from jobfeed.models import Job
from jobfeed.sources import justjoin, linkedin, nofluff
from jobfeed import rank
from jobfeed.digest import TZ, Pending, lines as digest_lines
from jobfeed.store import SeenStore
from jobfeed.tracker import Tracker, cross_source_key, legacy_key
from jobfeed.telegram import CARD_BUTTONS, ICONS, Telegram, format_digest, format_job

ROOT = Path(__file__).resolve().parent.parent
SOURCES = {"justjoin": justjoin.fetch, "nofluff": nofluff.fetch, "linkedin": linkedin.fetch}

log = logging.getLogger("jobfeed")


def load_env(path: Path) -> None:
    """Minimal .env loader for local runs; in CI the values come from secrets."""
    if not path.exists():
        return
    for line in path.read_text("utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def send_backlog(collected: list[Job], cfg: dict, store: SeenStore, dry_run: bool) -> int:
    """Everything matching that is online now, seen or not; marks it all as seen."""
    matches: dict[str, Job] = {}
    fresh_urls: set[str] = set()  # not seen by any earlier run, marked 🆕 in the digest
    for job in collected:
        is_new = job.id not in store and f"src:{job.source}" in store
        store.add(job.id)
        store.add(f"src:{job.source}")
        if filters.passes(job, cfg["filters"]):
            key = cross_source_key(job)
            if is_new and key not in store:
                fresh_urls.add(job.url)
            store.add(key)
            matches.setdefault(key, job)
    by_source = sorted(matches.values(), key=lambda j: list(SOURCES).index(j.source))
    tracker = Tracker.load()
    tracked = {j.url: row["row"] for j in by_source if (row := tracker.find(j))}
    log.info("backlog: %d matching offers", len(by_source))

    if dry_run:
        print("\n".join(format_digest(by_source, fresh_urls, tracked=tracked)))
        return 0

    tg = Telegram(os.environ["TELEGRAM_TOKEN"], os.environ["TELEGRAM_CHAT_ID"])
    tg.send(f"📋 Backlog: {len(by_source)} matching offers currently online, "
            f"🆕 {len(fresh_urls)} of them not sent before")
    for source in SOURCES:
        jobs = [j for j in by_source if j.source == source]
        if jobs:
            tg.send(f"<b>{ICONS[source]} — {len(jobs)}</b>")
            for message in format_digest(jobs, fresh_urls, tracked=tracked):
                tg.send(message)
    store.save()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--only", choices=SOURCES, help="run a single source")
    parser.add_argument("--backlog", action="store_true",
                        help="send every matching offer currently online as a compact digest, not only new ones")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    load_env(ROOT / ".env")
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text("utf-8"))
    store = SeenStore(ROOT / "state" / "seen.json")

    collected: list[Job] = []
    failed: list[str] = []
    for name, fetch in SOURCES.items():
        src_cfg = dict(cfg["sources"].get(name, {}))
        if not src_cfg.get("enabled") or (args.only and args.only != name):
            continue
        if args.backlog:  # look deeper than the regular "what's new" window
            src_cfg.update(src_cfg.get("backlog", {}))
        try:
            jobs = fetch(src_cfg)
            log.info("%s: %d offers", name, len(jobs))
            collected += jobs
        except Exception as e:
            log.error("%s failed: %s", name, e)
            failed.append(name)

    if args.backlog:
        return send_backlog(collected, cfg, store, args.dry_run)

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
        if key in store or legacy_key(job) in store:  # same offer from another board, or a repost
            continue
        store.add(key)
        if job.source not in new_sources:
            fresh.append(job)
    for source in new_sources:
        store.add(f"src:{source}")
    log.info("new matching offers: %d (silently indexed sources: %s)", len(fresh), sorted(new_sources) or "-")

    # Everything queues up; tier A leaves on this run, the rest in batches (config → priority)
    prio_cfg = {**rank.DEFAULTS, **cfg.get("priority", {})}
    tracker = Tracker.load() if fresh else Tracker([])
    pending = Pending(ROOT / "state" / "pending.json")
    for job in fresh:
        if tracker.hidden(job):  # removed with 🗑 earlier (a repost or the same offer elsewhere)
            continue
        rank.enrich(job)
        pr = rank.priority(job, prio_cfg)
        pending.add(job, pr, tracker.notes(job))
    log.info("queued: %d, waiting for a card: %d", len(fresh), pending.waiting())

    if args.dry_run:
        for job, pr, _, notes in sorted(pending._all(), key=lambda i: i[1].score, reverse=True):
            print(f"[{pr.tier} {pr.score}]", format_job(job, [f"{TIER[pr.tier]} {pr.why}", *notes]), end="\n\n")
        return 0

    # State first: a failed Telegram call must never make the next run resend the same cards
    store.save()
    pending.save()
    tg = Telegram(os.environ["TELEGRAM_TOKEN"], os.environ["TELEGRAM_CHAT_ID"])
    if new_sources:
        indexed = sum(1 for j in collected if j.source in new_sources)
        tg.send(f"✅ Started watching {', '.join(sorted(new_sources))}: indexed {indexed} current offers. "
                f"New ones will arrive here.")
    hours = prio_cfg["batch_hours"]
    try:
        instant = pending.take_instant(prio_cfg["instant_tiers"])
        if instant:  # top offers don't wait for a batch slot: being among the first applicants matters
            pending.save()
            for job, pr, _, notes in instant:
                tg.send(format_job(job, card_notes(pr, notes)), CARD_BUTTONS)
        if pending.batch_due(hours):
            batch, left = pending.take_batch(prio_cfg["batch_cards"], hours)
            pending.save()
            low = sum(1 for i in pending.items if i["priority"]["tier"] == "C")
            send_batch(tg, batch, left, low)
        if pending.digest_due(prio_cfg["digest_hour"]):
            items = pending.take_all()
            pending.save()
            send_digest(tg, items)
    except Exception as e:
        log.exception("delivery failed")
        tg.send(f"⚠️ Пачка/итог не отправились: {html.escape(str(e))[:300]}")
    if failed:
        tg.send(f"⚠️ Sources failed this run: {', '.join(failed)}")
    return 0


TIER = {"A": "🔥", "B": "🗂", "C": "⬇️"}


def card_notes(pr: rank.Priority, notes: list[str]) -> list[str]:
    return [f"{TIER[pr.tier]} {pr.score} · {pr.why}", *notes]


def send_batch(tg: Telegram, batch: list, left: int, low: int = 0) -> None:
    for job, pr, _, notes in batch:
        tg.send(format_job(job, card_notes(pr, notes)), CARD_BUTTONS)
    if batch:
        tg.send(f"⏳ Ещё в очереди: {left}" if left else "✅ Это все новые на сейчас")
    else:  # an empty slot still says so, otherwise silence looks like a broken bot
        now = datetime.now(TZ)
        tg.send(f"🕘 {now:%H}:00 — новых подходящих нет"
                + (f" (низкий приоритет: {low}, будут в итоге дня)" if low else ""))


def send_digest(tg: Telegram, items: list) -> None:
    """End of day: stats, then everything that didn't make it into a card, low priority last."""
    shown = [i for i in items if i[2]]
    rest = sorted((i for i in items if not i[2] and i[1].tier != "C"), key=lambda i: i[1].score, reverse=True)
    low = sorted((i for i in items if i[1].tier == "C"), key=lambda i: i[1].score, reverse=True)
    tg.send(f"🗂 <b>Итог дня</b>: новых подходящих {len(items)} · карточками {len(shown)} · "
            f"списком {len(rest)} · ⬇️ низкий приоритет {len(low)}")
    if rest:
        tg.send(f"<b>🗂 Не попали в карточки — {len(rest)}</b> (балл · вакансия · почему); "
                f"понравившуюся пришли боту ссылкой:")
        for message in digest_lines(rest):
            tg.send(message)
    if low:
        tg.send(f"<b>⬇️ Низкий приоритет — {len(low)}</b> (Python/JS без Java/C#, ставка ниже порога, без B2B, офис):")
        for message in digest_lines(low):
            tg.send(message)


if __name__ == "__main__":
    sys.exit(main())
