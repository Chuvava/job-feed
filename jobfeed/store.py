"""Seen-jobs state, kept as a JSON file that the GitHub workflow commits back."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

KEEP_DAYS = 180  # long-running NoFluff offers stay listed for months


class SeenStore:
    def __init__(self, path: Path):
        self.path = path
        self.seen: dict[str, str] = json.loads(path.read_text("utf-8")) if path.exists() else {}

    def __contains__(self, job_id: str) -> bool:
        return job_id in self.seen

    def add(self, job_id: str) -> None:
        self.seen.setdefault(job_id, datetime.now(timezone.utc).isoformat(timespec="seconds"))

    def save(self) -> None:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)).isoformat()
        # "src:*" markers say a source was already indexed, they must never expire
        self.seen = {k: v for k, v in self.seen.items() if v >= cutoff or k.startswith("src:")}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.seen, indent=0, sort_keys=True), "utf-8")
