import requests

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)


def session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s


def money(value) -> str:
    """15120.0 -> '15 120', None -> '?'"""
    if value is None:
        return "?"
    return f"{round(value):,}".replace(",", " ")
