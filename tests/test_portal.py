"""Portal + engine service end to end, in-process, with a fake LUFY session."""
import json
import os
import sys
import time
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from study import portal  # noqa: E402
from study.engine_server import create_app as engine_app  # noqa: E402
from study.service import ConditionService  # noqa: E402

T0 = 1795600800  # 2026-11-26 10:00 JST


class FakeSession:
    stores = {}

    def __init__(self, name, sd_first=False, tag_sd=True):
        self.st = FakeSession.stores.setdefault((sd_first, name), {"active": False})
        self.conv_dir = "/nonexistent"
        self.posted = []

    def state(self):
        return dict(self.st)

    def start(self):
        n = self.st.get("session", 0) + 1
        self.st.update(active=True, session=n, turns=0, response=f"hello {n}",
                       started_at=time.time(), last_at=time.time(), pending_post=None)
        return f"hello {n}", n

    def turn(self, q):
        self.st["turns"] += 1
        self.st["response"] = f"echo:{q}"
        self.st["last_at"] = time.time()
        return f"echo:{q}", self.st["session"]

    def close(self):
        self.st["active"] = False
        self.st["pending_post"] = self.st["session"] if self.st["turns"] else None
        return self.st["pending_post"]

    def post_session(self, s):
        self.st["pending_post"] = None


class Cfg:
    def __init__(self, tmp, key, sd):
        self.bot_key, self.condition, self.sd_first, self.tag_sd_in_control = key, "x", sd, True
        self.participants_file = str(tmp / "participants.json")
        self.log_dir = str(tmp / "logs")
        self.idle_close_min = 20


class InProcessEngines:
    """EngineClient over Flask test clients instead of HTTP."""

    def __init__(self, apps):
        self.clients = {k: a.test_client() for k, a in apps.items()}

    def call(self, bot, method, name, action, body=None):
        r = self.clients[bot].open(f"/v1/{name}/{action}", method=method, json=body)
        return r.status_code, r.get_json()


def setup(tmp_path, questionnaire=None):
    (tmp_path / "participants.json").write_text(json.dumps(
        {"P01": {"name": "Amos", "first": "B", "token": "tok1"}}))
    if questionnaire:
        (tmp_path / "q.json").write_text(json.dumps(questionnaire))
    FakeSession.stores.clear()
    services = {"A": ConditionService(Cfg(tmp_path, "A", False), FakeSession),
                "B": ConditionService(Cfg(tmp_path, "B", True), FakeSession)}
    cfg = portal.PortalConfig()
    cfg.participants_file = str(tmp_path / "participants.json")
    cfg.data_dir = str(tmp_path / "data")
    cfg.questionnaire_file = str(tmp_path / "q.json")
    cfg.tz = ZoneInfo("Asia/Tokyo")
    now = [T0]
    app = portal.create_app(cfg, InProcessEngines({k: engine_app(s) for k, s in services.items()}),
                            clock=lambda: now[0])
    return app.test_client(), services, now, tmp_path


def drain(services):
    """Run queued end-of-session processing now (the worker thread is not started in tests)."""
    for svc in services.values():
        while not svc.post_q.empty():
            code, name, session = svc.post_q.get_nowait()
            svc._session(name).post_session(session)


def chat_once(c, bot_expected):
    r = c.get("/p/tok1")
    assert f"Start chatting with LUFY {bot_expected}" in r.get_data(as_text=True)
    r = c.post("/p/tok1/begin")
    assert r.headers["Location"].endswith("/p/tok1/chat")
    d = c.get("/api/tok1/chat").get_json()
    assert d["bot"] == bot_expected and d["history"][-1]["role"] == "bot"
    assert c.post("/api/tok1/turn", json={"text": "I'm a student"}).get_json()["text"] == "echo:I'm a student"
    assert c.post("/api/tok1/stop", json={}).status_code == 200
    r = c.get("/p/tok1")
    assert r.headers["Location"].endswith("/p/tok1/qa")
    form = {f"{x}{k}": "x" for x in "qa" for k in range(3)}
    assert c.post("/p/tok1/qa", data={**form, "a2": ""}).status_code == 200      # incomplete: stays
    assert c.post("/p/tok1/qa", data=form).status_code == 302


def test_full_day_flow(tmp_path):
    c, services, now, tmp = setup(tmp_path)
    assert c.get("/p/wrong").status_code == 404
    chat_once(c, "B")            # first = B
    chat_once(c, "A")            # same day, then A
    assert "That's all for today" in c.get("/p/tok1").get_data(as_text=True)
    rec = json.loads((tmp / "data" / "P01.json").read_text())
    assert [(s["bot"], s["turns"], s["end_reason"]) for s in rec["sessions"]] == [("B", 1, "user"), ("A", 1, "user")]
    assert len(rec["sessions"][0]["qa"]["pairs"]) == 3
    now[0] += 86400
    drain(services)
    chat_once(c, "B")            # next day


def test_idle_close_is_reconciled(tmp_path):
    c, services, now, tmp = setup(tmp_path)
    c.post("/p/tok1/begin")
    c.post("/api/tok1/turn", json={"text": "hi"})
    FakeSession.stores[(True, "Amos")]["last_at"] -= 21 * 60
    services["B"].sweep_once()
    assert c.get("/p/tok1").headers["Location"].endswith("/p/tok1/qa")
    rec = json.loads((tmp / "data" / "P01.json").read_text())
    assert rec["sessions"][0]["end_reason"] == "idle" and rec["sessions"][0]["turns"] == 1


def test_questionnaire_after_last_session(tmp_path):
    q = {"items": [{"id": "enjoy", "text": "placeholder", "scale": 7, "low": "1", "high": "7"}], "per_bot": True}
    c, services, now, tmp = setup(tmp_path, q)
    rec = {"sessions": [{"bot": b, "session": 1, "started": T0 - (d + 1) * 86400, "ended": T0 - d * 86400,
                         "turns": 3, "end_reason": "user", "qa": {"pairs": []}}
                        for d in range(4) for b in "AB"], "questionnaire": None}
    (tmp / "data").mkdir(exist_ok=True)
    (tmp / "data" / "P01.json").write_text(json.dumps(rec))
    assert c.get("/p/tok1").headers["Location"].endswith("/p/tok1/questionnaire")
    assert c.post("/p/tok1/questionnaire", data={"enjoy_A": "5"}).status_code == 200   # B missing
    assert c.post("/p/tok1/questionnaire", data={"enjoy_A": "5", "enjoy_B": "6"}).status_code == 302
    assert "finished the study" in c.get("/p/tok1").get_data(as_text=True)


def test_saving_blocks_next_session_of_same_bot(tmp_path):
    c, services, now, tmp = setup(tmp_path)
    FakeSession.stores[(True, "Amos")] = {"active": False, "session": 1, "turns": 3, "pending_post": 1}
    r = c.post("/p/tok1/begin")
    assert "notice=saving" in r.headers["Location"]
