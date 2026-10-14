import html
import time

import requests

from jobfeed.models import Job

ICONS = {"justjoin": "🟣 JJ", "nofluff": "🟢 NFJ", "linkedin": "🔵 LinkedIn"}
WORKPLACE = {"remote": "🏠 remote", "hybrid": "🔀 hybrid", "office": "🏢 office"}
# Handled by the Cloudflare Worker in bot/ (it reads the job back from the card text)
CARD_BUTTONS = {"inline_keyboard": [[
    {"text": "⭐ В трекер", "callback_data": "save"},
    {"text": "🎯", "callback_data": "target"},
    {"text": "✅ Подался", "callback_data": "applied"},
    {"text": "🗑", "callback_data": "hide"},
]]}


def format_job(job: Job, notes: list[str] = ()) -> str:
    """notes go right before the source line: the bot expects title first and source last."""
    e = html.escape
    lines = [f"<b>{e(job.title)}</b>", f"🏛 {e(job.company)}"]
    if job.no_b2b:
        lines.insert(0, f"⚠️ без B2B ({e(', '.join(job.contracts))})")
    where = " · ".join(x for x in [WORKPLACE.get(job.workplace, job.workplace), e(job.location)] if x)
    if where:
        lines.append(where)
    if job.salary:
        lines.append(f"💰 {e(job.salary)}")
    if job.seniority:
        lines.append(f"🎚 {e(job.seniority)}")
    if job.skills:
        lines.append(f"🧰 {e(', '.join(job.skills[:8]))}")
    lines += [e(n) for n in notes]
    lines.append(f'{ICONS.get(job.source, job.source)} · <a href="{e(job.url)}">open</a>')
    return "\n".join(lines)


def format_digest(jobs: list[Job], fresh_urls: set[str] = frozenset(), limit: int = 3800,
                  tracked: dict[str, int] = {}) -> list[str]:
    """Compact one-line-per-job list, split into messages under Telegram's 4096-char limit."""
    e = html.escape
    messages, current = [], ""
    # fresh ones first so they don't get lost in a long list
    for job in sorted(jobs, key=lambda j: j.url not in fresh_urls):
        where = " ".join(x for x in [WORKPLACE.get(job.workplace, job.workplace).split(" ")[0], e(job.location[:40])] if x)
        bullet = "🆕" if job.url in fresh_urls else "•"
        line = f'{bullet} <a href="{e(job.url)}">{e(job.title)}</a> — {e(job.company)}'
        if job.no_b2b:
            line += " ⚠️без B2B"
        if job.url in tracked:
            line += f" 📌стр.{tracked[job.url]}"
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

    def send(self, text: str, buttons: dict | None = None) -> None:
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
        }
        if buttons:
            payload["reply_markup"] = buttons
        for _ in range(3):
            r = requests.post(self.url, json=payload, timeout=30)
            if r.status_code == 429:  # rate limited, Telegram says how long to wait
                time.sleep(r.json().get("parameters", {}).get("retry_after", 5) + 1)
                continue
            r.raise_for_status()
            time.sleep(1.1)  # stay under ~1 msg/sec per chat
            return
        r.raise_for_status()
