"""Make the untouched LUFY modules importable from any working directory.

LUFY resolves memories as "./memories/<user>_memories", so the server's
working directory decides where a condition's memories live. Each bot
process therefore runs with its own cwd (runs/<bot_key>/), while the code is
imported from ../lufy. IPE.py does sys.path.append("./roberta") relative to
cwd, so lufy/roberta is put on the path up front.
"""
import os
import sys

LUFY_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "lufy"))

for p in (LUFY_DIR, os.path.join(LUFY_DIR, "roberta")):
    if p not in sys.path:
        sys.path.insert(0, p)
