"""What a participant should do next. Pure: no I/O, so the rules are testable.

Rules (defaults; set in the portal's .env):
  * Each bot gets SESSIONS_PER_BOT sessions (4).
  * The bots alternate, the participant's `first` bot first: A, B, A, B, ...
  * At most MAX_PER_BOT_PER_DAY sessions per bot per local day (1), so 4
    sessions take at least 4 days, as in the LUFY study.
  * After every session, the 3 question-answer pairs come before anything else.
  * A session ended with no messages does not count and needs no QA.
  * After the last session: the questionnaire (if one is configured), then done.

A record is the portal's file for one participant:
  {"sessions": [{"bot", "session", "started", "ended", "turns", "end_reason", "qa"}],
   "questionnaire": null | {...}}
"""
from datetime import datetime


def local_date(ts, tz):
    return datetime.fromtimestamp(ts, tz).date()


def counted(record):
    """Sessions that count: ended with at least one message, or still open."""
    return [s for s in record["sessions"] if s.get("ended") is None or s.get("turns", 0) > 0]


def next_step(record, first, now, tz, sessions_per_bot=4, max_per_bot_per_day=1, has_questionnaire=False):
    """Return (step, detail):
    ("resume", bot) · ("qa", index into record["sessions"]) · ("chat", bot) ·
    ("wait", bot) — that bot's turn, but today's limit is used · ("questionnaire", None) · ("done", None)
    """
    sessions = record["sessions"]
    for s in sessions:
        if s.get("ended") is None:
            return "resume", s["bot"]
    for i, s in enumerate(sessions):
        if s.get("turns", 0) > 0 and s.get("qa") is None:
            return "qa", i

    second = "B" if first == "A" else "A"
    done = {b: sum(1 for s in counted(record) if s["bot"] == b) for b in (first, second)}
    if done[first] >= sessions_per_bot and done[second] >= sessions_per_bot:
        if has_questionnaire and record.get("questionnaire") is None:
            return "questionnaire", None
        return "done", None

    bot = first if done[first] == done[second] else second
    today = local_date(now, tz)
    today_count = sum(1 for s in counted(record)
                      if s["bot"] == bot and local_date(s["started"], tz) == today)
    if today_count >= max_per_bot_per_day:
        return "wait", bot
    return "chat", bot


def progress(record, first, sessions_per_bot=4):
    second = "B" if first == "A" else "A"
    return {b: sum(1 for s in counted(record) if s["bot"] == b and s.get("ended") is not None)
            for b in (first, second)}
