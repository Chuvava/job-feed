"""Collect new vacancies from all sources and push the matching ones to Telegram.

    python -m jobfeed.main            # normal run
    python -m jobfeed.main --dry-run  # print instead of sending, don't touch state
"""
import argparse
import logging
import os
import sys
from pathlib import Path

import yaml

from jobfeed import filters
from jobfeed.models import Job
from jobfeed.sources import justjoin, linkedin, nofluff
from jobfeed import rank
from jobfeed.digest import Pending, lines as digest_lines
from jobfeed.store import SeenStore
from jobfeed.tracker import Tracker, cross_source_key, legacy_key
from jobfeed.telegram import CARD_BUTTONS, ICONS, Telegram, format_digest, format_job

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

    # Tier decides delivery: A now as a card, everything (A included) goes to the evening digest
    prio_cfg = {**rank.DEFAULTS, **cfg.get("priority", {})}
    tracker = Tracker.load() if fresh else Tracker([])
    pending = Pending(ROOT / "state" / "pending.json")
    ranked = []
    for job in fresh:
        rank.enrich(job)
        pr = rank.priority(job, prio_cfg)
        ranked.append((job, pr, tracker.notes(job)))
    ranked.sort(key=lambda x: x[1].score, reverse=True)
    now_cards = [x for x in ranked if x[1].tier == "A"]
    log.info("tiers: %s", {t: sum(1 for x in ranked if x[1].tier == t) for t in "ABC"})

    if args.dry_run:
        for job, pr, notes in ranked:
            print(f"[{pr.tier} {pr.score}]", format_job(job, [f"🔥 {pr.why}", *notes]), end="\n\n")
        return 0

    tg = Telegram(os.environ["TELEGRAM_TOKEN"], os.environ["TELEGRAM_CHAT_ID"])
    if new_sources:
        indexed = sum(1 for j in collected if j.source in new_sources)
        tg.send(f"✅ Started watching {', '.join(sorted(new_sources))}: indexed {indexed} current offers. "
                f"New ones will arrive here.")
    for job, pr, notes in now_cards[:MAX_MESSAGES_PER_RUN]:
        tg.send(format_job(job, [f"🔥 Приоритет: {pr.why}", *notes]), CARD_BUTTONS)
    for job, pr, notes in ranked:
        pending.add(job, pr, sent_now=pr.tier == "A", notes=notes)
    if pending.due(prio_cfg["digest_hour"]):
        send_digest(tg, pending.take(), prio_cfg["digest_cards"])
    if failed:
        tg.send(f"⚠️ Sources failed this run: {', '.join(failed)}")

    pending.save()
    store.save()
    return 0


def send_digest(tg: Telegram, items: list, cards: int) -> None:
    """Evening summary: A were already sent as cards, B top N as cards, the rest as lines."""
    a = [i for i in items if i[1].tier == "A"]
    b = sorted((i for i in items if i[1].tier == "B"), key=lambda i: i[1].score, reverse=True)
    c = sorted((i for i in items if i[1].tier == "C"), key=lambda i: i[1].score, reverse=True)
    tg.send(f"🗂 <b>Итог дня</b>: новых подходящих {len(items)} · 🔥 сразу {len(a)} · "
            f"🗂 в разборе {len(b)} · ⬇️ низкий приоритет {len(c)}"
            + (f"\nТоп-{min(cards, len(b))} — карточками, остальное — списком." if b else ""))
    if a:
        tg.send("<b>🔥 Уже приходили сегодня:</b>")
        for message in digest_lines(a):
            tg.send(message)
    for job, pr, _, notes in b[:cards]:
        tg.send(format_job(job, [f"🗂 {pr.score} · {pr.why}", *notes]), CARD_BUTTONS)
    if b[cards:]:
        tg.send(f"<b>🗂 Ещё {len(b) - cards}</b> (балл · вакансия · почему) — понравившуюся пришли боту ссылкой:")
        for message in digest_lines(b[cards:]):
            tg.send(message)
    if c:
        tg.send(f"<b>⬇️ Низкий приоритет — {len(c)}</b> (Python/JS без Java/C#, ставка < порога, офис):")
        for message in digest_lines(c):
            tg.send(message)


if __name__ == "__main__":
    sys.exit(main())
