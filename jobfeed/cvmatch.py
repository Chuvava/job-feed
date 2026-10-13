"""ATS-style literal keyword check of a CV against vacancy keywords.

    python -m jobfeed.cvmatch --cv a.pdf --cv b.pdf --must "Java;REST Assured|RestAssured" --nice "Docker"

A keyword is "Name|alias|alias"; it counts as present when any spelling occurs in the CV text
as a whole word (case-insensitive). ATS filters are this literal, so "Azure" does not
satisfy "Azure DevOps". Score = (2*must hits + nice hits) / (2*musts + nices).
"""
import argparse
import json
import re
import sys
from pathlib import Path

from pypdf import PdfReader


def cv_text(path: Path) -> str:
    text = "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
    return re.sub(r"\s+", " ", text)  # pypdf splits words into lines, flatten it


def present(text: str, keyword: str) -> str | None:
    """Returns the spelling found in the CV, or None."""
    for spelling in keyword.split("|"):
        spelling = spelling.strip()
        if spelling and re.search(rf"(?<![\w#+.]){re.escape(spelling)}(?![\w#+])", text, re.IGNORECASE):
            return spelling
    return None


def check(text: str, must: list[str], nice: list[str]) -> dict:
    result = {"must": {}, "nice": {}}
    for group, keywords in (("must", must), ("nice", nice)):
        for kw in keywords:
            result[group][kw.split("|")[0].strip()] = present(text, kw)
    hits_must = sum(v is not None for v in result["must"].values())
    hits_nice = sum(v is not None for v in result["nice"].values())
    total = 2 * len(must) + len(nice)
    result["score"] = round(100 * (2 * hits_must + hits_nice) / total) if total else None
    return result


def split(arg: str | None) -> list[str]:
    return [k.strip() for k in (arg or "").split(";") if k.strip()]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--cv", action="append", required=True, type=Path)
    p.add_argument("--must", help="';'-separated, aliases with '|'")
    p.add_argument("--nice", help="';'-separated, aliases with '|'")
    args = p.parse_args()
    out = {cv.name: check(cv_text(cv), split(args.must), split(args.nice)) for cv in args.cv}
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
