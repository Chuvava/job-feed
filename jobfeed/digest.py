"""Batched delivery: new offers queue up in state/pending.json and go out a few at a time.

Every hourly run adds its new offers to the queue. At each batch hour (Warsaw time) the best
unshown offers go out as cards; the last batch of the day is followed by the summary that lists
everything else, low priority at the bottom, and empties the queue. Nothing is sent twice and
nothing is dropped. Offers found after the last batch wait for the morning one.
"""
import html
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from jobfeed.models import Job
from jobfeed.rank import Priority

TZ = ZoneInfo("Europe/Warsaw")
Item = tuple[Job, Priority, bool, list[str]]   # job, priority, already shown as a card, tracker notes


class Pending:
    def __init__(self, path: Path):
        self.path = path
        data = json.loads(path.read_text("utf-8")) if path.exists() else {}
        self.last_digest: str = data.get("last_digest", "")
        self.last_batch: str = data.get("last_batch", "")
        self.items: list[dict] = data.get("items", [])

    def add(self, job: Job, pr: Priority, notes: list[str]) -> None:
        d = asdict(job)
        d.pop("description", None)  # can be long and is not needed later
        self.items.append({"job": d, "priority": asdict(pr), "shown": False, "notes": notes})

    def _all(self) -> list[Item]:
        return [(Job(**i["job"]), Priority(**i["priority"]), i["shown"], i["notes"]) for i in self.items]

    def batch_due(self, hours: list[int], now: datetime | None = None) -> bool:
        """True once per batch slot; a failed or skipped run is caught up by the next one."""
        now = now or datetime.now(TZ)
        past = [h for h in hours if h <= now.hour]
        return bool(past) and self.last_batch != f"{now.date()} {max(past)}"

    def take_batch(self, n: int, hours: list[int], now: datetime | None = None) -> tuple[list[Item], int]:
        """Best n unshown offers (low priority never goes as a card) and how many still wait."""
        now = now or datetime.now(TZ)
        self.last_batch = f"{now.date()} {max(h for h in hours if h <= now.hour)}"
        order = sorted((i for i in self.items if not i["shown"] and i["priority"]["tier"] != "C"),
                       key=lambda i: i["priority"]["score"], reverse=True)
        for i in order[:n]:
            i["shown"] = True
        picked = [(Job(**i["job"]), Priority(**i["priority"]), True, i["notes"]) for i in order[:n]]
        return picked, len(order) - len(picked)

    def take_instant(self, tiers: list[str]) -> list[Item]:
        """Unshown offers of these tiers, marked shown: they go out on the run that found them."""
        picked = [i for i in self.items if not i["shown"] and i["priority"]["tier"] in tiers]
        for i in picked:
            i["shown"] = True
        return [(Job(**i["job"]), Priority(**i["priority"]), True, i["notes"]) for i in picked]

    def digest_due(self, hour: int, now: datetime | None = None) -> bool:
        now = now or datetime.now(TZ)
        return now.hour >= hour and self.last_digest != now.date().isoformat()

    def take_all(self, now: datetime | None = None) -> list[Item]:
        out = self._all()
        self.items = []
        self.last_digest = (now or datetime.now(TZ)).date().isoformat()
        return out

    def waiting(self) -> int:
        return sum(1 for i in self.items if not i["shown"] and i["priority"]["tier"] != "C")

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"last_digest": self.last_digest, "last_batch": self.last_batch,
                                         "items": self.items}, ensure_ascii=False, indent=0), "utf-8")


def lines(items: list[Item], limit: int = 3800) -> list[str]:
    e = html.escape
    messages, current = [], ""
    for job, pr, _, notes in items:
        flag = " 📌" if any(n.startswith(("📌", "⚠️")) for n in notes) else ""
        line = (f'{pr.score:>2} · <a href="{e(job.url)}">{e(job.title)}</a> — {e(job.company)}'
                f' · {e(pr.why)}{flag}\n')
        if len(current) + len(line) > limit:
            messages.append(current)
            current = ""
        current += line
    return messages + ([current] if current else [])
