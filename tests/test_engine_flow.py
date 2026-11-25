"""The engine must feed generate() exactly what chat.py would, turn by turn."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from tests import stubs  # noqa: E402

stubs.install()
from study import engine  # noqa: E402


def test_session1_flow(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    calls = []

    def fake_generate(related_memory, context, key_summary, user_name):
        calls.append(context)
        return f"bot{len(calls)}"

    monkeypatch.setattr(engine.generate_mod, "generate", fake_generate)
    s = engine.LufySession("Amos")
    shown, session = s.start()
    assert session == 1 and "self introduction" in shown and s.active

    r1, _ = s.turn("I'm Amos from Malaysia")
    r2, _ = s.turn("I study robotics")
    r3, _ = s.turn("I like cats")

    # chat.py: first loop iteration has n = 2, so generate() gets the raw query
    assert calls[0] == "I'm Amos from Malaysia"
    # from the second iteration n >= 4 > 3: the last 3 lines of the file (the last one is "")
    lines = open("memories/Amos_memories/conversation/1.txt", encoding="utf-8").read().split("\n")
    assert calls[1] == ",".join([lines[0], lines[1], ""])
    assert calls[2] == ",".join(lines[-3:])
    assert lines[0].startswith('utterance by LUFY: "Hi, I\'m LUFY') and 'response by Amos: "I\'m Amos' in lines[0]
    assert 'utterance by LUFY: "bot1", response by Amos: "I study robotics"' == lines[1]
    assert s.state()["turns"] == 3

    assert s.close() == 1 and not s.active
    assert s.state()["pending_post"] == 1


def test_stop_without_messages_skips_post(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    s = engine.LufySession("Ellen")
    s.start()
    assert s.close() is None
