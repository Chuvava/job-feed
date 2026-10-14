"""Evening digest: every new offer of the day that wasn't worth a card right away, ranked.

New offers are collected into state/pending.json by each hourly run; the first run at or after
priority.digest_hour (Warsaw time) sends them and empties the file. Nothing is dropped:
top N go as cards with buttons, the rest as one line each, low priority at the bottom.
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


class Pending:
    def __init__(self, path: Path):
        self.path = path
        data = json.loads(path.read_text("utf-8")) if path.exists() else {}
        self.last_digest: str = data.get("last_digest", "")
        self.items: list[dict] = data.get("items", [])

    def add(self, job: Job, pr: Priority, sent_now: bool, notes: list[str]) -> None:
        d = asdict(job)
        d.pop("description", None)  # can be long and is not needed later
        self.items.append({"job": d, "priority": asdict(pr), "sent_now": sent_now, "notes": notes})

    def due(self, hour: int, now: datetime | None = None) -> bool:
        now = now or datetime.now(TZ)
        return now.hour >= hour and self.last_digest != now.date().isoformat()

    def take(self) -> list[tuple[Job, Priority, bool, list[str]]]:
        out = [(Job(**i["job"]), Priority(**i["priority"]), i["sent_now"], i["notes"]) for i in self.items]
        self.items = []
        self.last_digest = datetime.now(TZ).date().isoformat()
        return out

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"last_digest": self.last_digest, "items": self.items},
                                        ensure_ascii=False, indent=0), "utf-8")


def lines(items: list[tuple[Job, Priority, bool, list[str]]], limit: int = 3800) -> list[str]:
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
