"""Experimenter tools.

    python -m study.admin links --participants runs/participants.json --base https://<mac>.ts.net
        give every participant a personal token (kept if present) and print their links
"""
import argparse
import json
import secrets

from .participants import Registry


def links(args):
    reg = Registry(args.participants)
    codes = reg.codes()  # validates names
    changed = False
    for p in codes.values():
        if not p.get("token"):
            p["token"] = secrets.token_urlsafe(16)
            changed = True
        if p.get("first") not in ("A", "B"):
            raise SystemExit(f"{p['name']}: 'first' must be A or B")
    if changed:
        with open(args.participants, "w", encoding="utf-8") as f:
            json.dump(codes, f, ensure_ascii=False, indent=2)
    base = args.base.rstrip("/")
    for code, p in codes.items():
        print(f"{code}\t{p['name']}\tfirst={p['first']}\t{base}/p/{p['token']}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    lk = sub.add_parser("links")
    lk.add_argument("--participants", default="runs/participants.json")
    lk.add_argument("--base", required=True)
    args = ap.parse_args()
    {"links": links}[args.cmd](args)


if __name__ == "__main__":
    main()
