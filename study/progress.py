"""Progress toward the target (default 10 participants x 300 turns per bot).

    python -m study.progress [--logs logs] [--target 300]
"""
import argparse
import glob
import json
import os
from collections import defaultdict


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--logs", default="logs")
    ap.add_argument("--target", type=int, default=300)
    args = ap.parse_args()

    turns = defaultdict(int)
    sessions = defaultdict(set)
    errors = defaultdict(int)
    bots = set()
    for path in glob.glob(os.path.join(args.logs, "bot_*.jsonl")):
        with open(path, encoding="utf-8") as f:
            for line in f:
                e = json.loads(line)
                key = (e.get("code"), e["bot"])
                bots.add(e["bot"])
                if e["event"] == "turn":
                    turns[key] += 1
                    sessions[key].add(e["session"])
                elif e["event"] in ("turn_error", "post_error", "reply_failed"):
                    errors[key] += 1

    codes = sorted({c for c, _ in turns} | {c for c, _ in errors if c})
    bots = sorted(bots)
    print(f"{'code':6}" + "".join(f"{'bot ' + b + ' turns/sessions':>24}" for b in bots) + f"{'errors':>8}")
    total = 0
    for c in codes:
        row = f"{c:6}"
        for b in bots:
            t = turns[(c, b)]
            total += t
            row += f"{t:>12}/{args.target} ({len(sessions[(c, b)])} s)".rjust(24)
        row += f"{sum(errors[(c, b)] for b in bots):>8}"
        print(row)
    goal = args.target * 10 * max(len(bots), 1)
    print(f"\ntotal turns: {total} / {goal} ({100 * total / goal:.0f}%)")


if __name__ == "__main__":
    main()
