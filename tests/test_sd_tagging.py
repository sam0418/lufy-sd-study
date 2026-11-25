"""SD tagging: E033 batching (20 per call, user words only), one retry for missed
verdicts, labels keyed by memory-text hash."""
import json
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from tests import stubs  # noqa: E402

stubs.install()
from study import sd_detector, sd_tagging  # noqa: E402


class FakeClient:
    """Labels a text EDUCATION if it contains 'student'; drops index 0 of the first call."""

    def __init__(self):
        self.calls = []
        self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self.create))

    def create(self, model, messages, **kw):
        prompt = messages[0]["content"]
        texts = [line.split(". ", 1)[1] for line in prompt.split("Texts:\n")[1].split("\n\nReturn")[0].split("\n")]
        self.calls.append(texts)
        results = [{"i": i, "categories": ["EDUCATION"] if "student" in t else []} for i, t in enumerate(texts)]
        if len(self.calls) == 1:
            results = results[1:]
        msg = types.SimpleNamespace(content=json.dumps({"results": results}))
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])


def test_tag_session(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    fake = FakeClient()
    monkeypatch.setattr(sd_detector, "_client", fake)
    idx = stubs.FakeIndex("./memories/Amos_memories")
    for i in range(25):
        said = "I'm a student here" if i % 5 == 0 else f"nice weather {i}"
        idx.insert(stubs.FakeDocument(f'utterance by LUFY: "q{i}", response by Amos: "{said}"',
                                      {"first_mentioned_session": 1}))
    idx.insert(stubs.FakeDocument('utterance by LUFY: "old", response by Amos: "I am a student"',
                                  {"first_mentioned_session": 0}))

    labels = sd_tagging.tag_session("Amos", 1)

    assert [len(c) for c in fake.calls] == [20, 1, 5]          # 20 + retry of the missed one + 5
    assert all("utterance by LUFY" not in t for c in fake.calls for t in c)  # user words only
    assert len(labels) == 25                                     # session 0 memory untouched
    assert sum(v["sd"] for v in labels.values()) == 5
    assert not any("error" in v for v in labels.values())
    assert sd_tagging.load_sd_hashes("Amos") == {h for h, v in labels.items() if v["sd"]}

    sd_tagging.tag_session("Amos", 1)                            # already labelled: no new calls
    assert len(fake.calls) == 3
