"""One-off sorting of everything currently online: fresh, matching, B2B offers ranked by stack fit.

    python -m jobfeed.triage --dry-run                 # print the ranking
    python -m jobfeed.triage --days 14 --cards 25      # top 25 as cards with buttons, the rest as a digest

Offers already in the tracker (by URL or company+title, read via SHEET_URL) are skipped.

Does not touch state/seen.json: the hourly feed keeps working as before.
"""
import argparse
import html
import logging
import os
import sys
from datetime import date, timedelta

import yaml

from jobfeed import filters, rank
from jobfeed.main import ROOT, SOURCES, load_env
from jobfeed.models import Job
from jobfeed.telegram import CARD_BUTTONS, Telegram, format_job
from jobfeed.tracker import Tracker, cross_source_key

log = logging.getLogger("jobfeed.triage")

def score(job: Job, today: date, prio_cfg: dict) -> tuple[int, str]:
    pr = rank.priority(job, prio_cfg, today)
    return pr.score, pr.why


def card(job: Job, pts: int) -> str:
    """Feed card plus a 📅/🔥 line; it goes before the source line, which the bot expects to be last."""
    lines = format_job(job).split("\n")
    when = date.fromisoformat(job.published).strftime("%d.%m") if job.published else "дата ?"
    lines.insert(len(lines) - 1, f"📅 {when} · 🔥 {pts}")
    return "\n".join(lines)


def digest(ranked: list[tuple[int, Job]], limit: int = 3800) -> list[str]:
    e = html.escape
    messages, current = [], ""
    for pts, job in ranked:
        when = date.fromisoformat(job.published).strftime("%d.%m") if job.published else "?"
        where = job.workplace or job.location[:30]
        line = (f'{pts:>2} · {when} · <a href="{e(job.url)}">{e(job.title)}</a> — {e(job.company)}'
                f'{" · " + e(where) if where else ""}{" · " + e(job.salary) if job.salary else ""}\n')
        if len(current) + len(line) > limit:
            messages.append(current)
            current = ""
        current += line
    return messages + ([current] if current else [])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=14, help="drop offers published earlier than this")
    parser.add_argument("--cards", type=int, default=25, help="how many top offers to send as cards with buttons")
    parser.add_argument("--only", choices=SOURCES)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    load_env(ROOT / ".env")
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text("utf-8"))
    today = date.today()
    since = today - timedelta(days=args.days)
    tracker = Tracker.load()
    log.info("tracker rows: %d", len(tracker))

    collected: list[Job] = []
    for name, fetch in SOURCES.items():
        src_cfg = dict(cfg["sources"].get(name, {}))
        if not src_cfg.get("enabled") or (args.only and args.only != name):
            continue
        src_cfg.update(src_cfg.get("backlog", {}))
        if name == "linkedin":
            src_cfg["hours_old"] = args.days * 24
        try:
            collected += fetch(src_cfg)
        except Exception as e:
            log.error("%s failed: %s", name, e)

    stats = {"online": len(collected), "filters": 0, "no_b2b": 0, "old": 0, "tracked": 0}
    best: dict[str, tuple[int, Job]] = {}
    for job in collected:
        if not filters.passes(job, cfg["filters"]):
            stats["filters"] += 1; continue
        if job.no_b2b:
            stats["no_b2b"] += 1; continue
        if job.published and date.fromisoformat(job.published) < since:
            stats["old"] += 1; continue
        if tracker.find(job):
            stats["tracked"] += 1; continue
        pts, _ = score(job, today, {**rank.DEFAULTS, **cfg.get("priority", {})})
        key = cross_source_key(job)
        if key not in best or pts > best[key][0]:  # same offer on two boards: keep the richer copy
            best[key] = (pts, job)
    ranked = sorted(best.values(), key=lambda x: (x[0], x[1].published or ""), reverse=True)
    log.info("stats: %s, left: %d", stats, len(ranked))

    top, rest = ranked[:args.cards], ranked[args.cards:]
    head = (f"🗂 <b>Разбор за {args.days} дней</b>: {len(ranked)} подходящих B2B-вакансий "
            f"(отсеяно: фильтры {stats['filters']}, без B2B {stats['no_b2b']}, старше {args.days} дн. {stats['old']}, "
            f"уже в трекере {stats['tracked']}).\nТоп-{len(top)} — карточками с кнопками, по убыванию 🔥, "
            f"остальные — списком ниже.")
    if args.dry_run:
        print(head)
        for pts, job in top:
            print(card(job, pts), end="\n\n")
        print("\n".join(digest(rest)))
        return 0

    tg = Telegram(os.environ["TELEGRAM_TOKEN"], os.environ["TELEGRAM_CHAT_ID"])
    tg.send(head)
    for pts, job in top:
        tg.send(card(job, pts), CARD_BUTTONS)
    if rest:
        tg.send(f"<b>Остальные {len(rest)}</b> (🔥 · дата · вакансия) — понравившуюся пришли боту ссылкой:")
        for message in digest(rest):
            tg.send(message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
