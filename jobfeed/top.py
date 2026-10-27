"""Ranked list of what the feed found in a period, for "что вышло за выходные" / "топ с среды".

    python -m jobfeed.top --since 2026-10-24          # offers first seen (or published) since that date
    python -m jobfeed.top --days 3 --json out.json    # the same for the last 3 days, plus a JSON dump

Skips offers already in the tracker or removed with 🗑, keeps one copy per company+title, ranks with
rank.priority and prints tier, score, reason and tracker warnings. ♻️ marks a role this feed has known
for weeks under other links: a repost (often a pipeline / "evergreen" role, not an urgent hire).
Reads state/seen.json (pull first); touches nothing.
"""
import argparse
import json
import logging
import sys
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import yaml

from jobfeed import filters, rank
from jobfeed.main import ROOT, SOURCES, load_env
from jobfeed.tracker import Tracker, cross_source_key, legacy_key

REPOST_DAYS = 21


def first_seen(job, seen: dict) -> str:
    """Earliest time this feed saw the same role under any link ("" when never)."""
    stamps = [seen[k] for k in (cross_source_key(job), legacy_key(job)) if k in seen]
    return min(stamps) if stamps else ""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", help="YYYY-MM-DD (Warsaw); default: --days back")
    parser.add_argument("--days", type=int, default=3)
    parser.add_argument("--light-days", type=int, help="office days/month still counted as almost remote")
    parser.add_argument("--json", type=Path, help="also dump the list here")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    sys.stdout.reconfigure(encoding="utf-8")
    load_env(ROOT / ".env")
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text("utf-8"))
    prio = {**rank.DEFAULTS, **cfg.get("priority", {})}
    if args.light_days is not None:
        prio["light_hybrid_days"] = args.light_days
    since = date.fromisoformat(args.since) if args.since else date.today() - timedelta(days=args.days)
    since_ts = datetime.combine(since, datetime.min.time(), timezone.utc).isoformat()
    seen = json.loads((ROOT / "state" / "seen.json").read_text("utf-8"))

    collected = []
    for name, fetch in SOURCES.items():
        src = dict(cfg["sources"].get(name, {}))
        if not src.get("enabled"):
            continue
        src.update(src.get("backlog", {}))
        if name == "linkedin":
            src["hours_old"] = (date.today() - since).days * 24 + 24
        try:
            collected += fetch(src)
        except Exception as e:
            print(f"! {name} failed: {e}", file=sys.stderr)

    tracker = Tracker.load()
    stats, best = Counter(), {}
    for job in collected:
        if seen.get(job.id, "9") < since_ts and (job.published or "") < since.isoformat():
            continue  # not new in this period
        if not filters.passes(job, cfg["filters"]):
            stats["фильтры"] += 1
        elif job.no_b2b:
            stats["без B2B"] += 1
        elif tracker.find(job) or tracker.hidden(job):
            stats["в трекере / 🗑"] += 1
        else:
            best.setdefault(cross_source_key(job), job)

    rows = []
    for job in best.values():
        if job.source == "linkedin":
            time.sleep(2)  # detail pages answer 429 when hit back to back
        rank.enrich(job)
        pr = rank.priority(job, prio)
        fs = first_seen(job, seen)
        repost = fs and fs < (datetime.now(timezone.utc) - timedelta(days=REPOST_DAYS)).isoformat()
        rows.append({"tier": pr.tier, "score": pr.score, "why": pr.why, "title": job.title, "company": job.company,
                     "url": job.url, "salary": job.salary, "published": job.published, "source": job.source,
                     "repost_since": fs[:10] if repost else "", "notes": tracker.notes(job)})
    rows.sort(key=lambda r: (r["tier"], -r["score"]))

    print(f"С {since:%d.%m}: {len(rows)} новых подходящих; отсеяно {dict(stats)}")
    for r in rows:
        flags = (f" ♻️ с {r['repost_since'][8:10]}.{r['repost_since'][5:7]}" if r["repost_since"] else "")
        print(f"{r['tier']} {r['score']:>2} | {r['title'][:60]} | {r['company'][:28]} | {r['why']}{flags}"
              f" | {r['url']}" + "".join(f"\n      {n}" for n in r["notes"]))
    if args.json:
        args.json.write_text(json.dumps(rows, ensure_ascii=False, indent=1), "utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
