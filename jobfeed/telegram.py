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
