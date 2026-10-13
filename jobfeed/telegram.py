import html
import time

import requests

from jobfeed.models import Job

ICONS = {"justjoin": "🟣 JJ", "nofluff": "🟢 NFJ", "linkedin": "🔵 LinkedIn"}
WORKPLACE = {"remote": "🏠 remote", "hybrid": "🔀 hybrid", "office": "🏢 office"}


def format_job(job: Job) -> str:
    e = html.escape
    lines = [f"<b>{e(job.title)}</b>", f"🏛 {e(job.company)}"]
    where = " · ".join(x for x in [WORKPLACE.get(job.workplace, job.workplace), e(job.location)] if x)
    if where:
        lines.append(where)
    if job.salary:
        lines.append(f"💰 {e(job.salary)}")
    if job.seniority:
        lines.append(f"🎚 {e(job.seniority)}")
    if job.skills:
        lines.append(f"🧰 {e(', '.join(job.skills[:8]))}")
    lines.append(f'{ICONS.get(job.source, job.source)} · <a href="{e(job.url)}">open</a>')
    return "\n".join(lines)


def format_digest(jobs: list[Job], fresh_urls: set[str] = frozenset(), limit: int = 3800) -> list[str]:
    """Compact one-line-per-job list, split into messages under Telegram's 4096-char limit."""
    e = html.escape
    messages, current = [], ""
    # fresh ones first so they don't get lost in a long list
    for job in sorted(jobs, key=lambda j: j.url not in fresh_urls):
        where = " ".join(x for x in [WORKPLACE.get(job.workplace, job.workplace).split(" ")[0], e(job.location[:40])] if x)
        bullet = "🆕" if job.url in fresh_urls else "•"
        line = f'{bullet} <a href="{e(job.url)}">{e(job.title)}</a> — {e(job.company)}'
        line += "".join(f" · {x}" for x in [where, e(job.salary)] if x) + "\n"
        if len(current) + len(line) > limit:
            messages.append(current)
            current = ""
        current += line
    if current:
        messages.append(current)
    return messages


class Telegram:
    def __init__(self, token: str, chat_id: str):
        self.url = f"https://api.telegram.org/bot{token}/sendMessage"
        self.chat_id = chat_id

    def send(self, text: str) -> None:
        for _ in range(3):
            r = requests.post(self.url, json={
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "HTML",
                "link_preview_options": {"is_disabled": True},
            }, timeout=30)
            if r.status_code == 429:  # rate limited, Telegram says how long to wait
                time.sleep(r.json().get("parameters", {}).get("retry_after", 5) + 1)
                continue
            r.raise_for_status()
            time.sleep(1.1)  # stay under ~1 msg/sec per chat
            return
        r.raise_for_status()
