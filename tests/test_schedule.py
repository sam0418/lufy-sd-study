import os
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from study.schedule import next_step  # noqa: E402

TZ = ZoneInfo("Asia/Tokyo")
DAY = 86400
T0 = 1795600800  # 2026-11-26 10:00 JST


def sess(bot, started, turns=5, ended=True, qa=True):
    return {"bot": bot, "session": 1, "started": started, "ended": started + 1800 if ended else None,
            "turns": turns, "qa": {"pairs": []} if qa else None}


def test_alternates_first_bot_first_and_one_per_bot_per_day():
    rec = {"sessions": [], "questionnaire": None}
    assert next_step(rec, "B", T0, TZ) == ("chat", "B")
    rec["sessions"].append(sess("B", T0))
    assert next_step(rec, "B", T0 + 3600, TZ) == ("chat", "A")      # same day, other bot
    rec["sessions"].append(sess("A", T0 + 3600))
    assert next_step(rec, "B", T0 + 7200, TZ) == ("wait", "B")      # B already done today
    assert next_step(rec, "B", T0 + DAY, TZ) == ("chat", "B")       # next day


def test_open_session_resumes_and_qa_comes_first():
    rec = {"sessions": [sess("A", T0, ended=False, qa=False)], "questionnaire": None}
    assert next_step(rec, "A", T0, TZ) == ("resume", "A")
    rec["sessions"][0]["ended"] = T0 + 60
    assert next_step(rec, "A", T0, TZ) == ("qa", 0)


def test_empty_session_does_not_count_or_need_qa():
    rec = {"sessions": [sess("A", T0, turns=0, qa=False)], "questionnaire": None}
    assert next_step(rec, "A", T0 + 60, TZ) == ("chat", "A")


def test_finish_with_and_without_questionnaire():
    rec = {"sessions": [sess(b, T0 + d * DAY) for d in range(4) for b in "AB"], "questionnaire": None}
    assert next_step(rec, "A", T0 + 5 * DAY, TZ, has_questionnaire=True) == ("questionnaire", None)
    assert next_step(rec, "A", T0 + 5 * DAY, TZ) == ("done", None)
    rec["questionnaire"] = {"scales": {}}
    assert next_step(rec, "A", T0 + 5 * DAY, TZ, has_questionnaire=True) == ("done", None)
