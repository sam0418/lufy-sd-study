"""LINE bot routing with a fake engine: join, start/turn/stop, busy, signature.
Every outgoing message must be a reply (the sender is the only way out)."""
import base64
import hashlib
import hmac
import json
import os
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class FakeSession:
    store = {}

    def __init__(self, name, sd_first=False, tag_sd=True):
        self.name = name
        self.st = FakeSession.store.setdefault(name, {"active": False})
        self.gate = None

    def state(self):
        return dict(self.st)

    def _save(self, st):
        self.st.clear()
        self.st.update(st)

    def start(self):
        import time
        self.st.update(active=True, session=1, turns=0, started_at=time.time(), last_at=time.time())
        return "Hi, I'm LUFY!", 1

    def turn(self, q):
        if FakeSession.gate:
            FakeSession.gate.wait(5)
        self.st["turns"] += 1
        return f"echo:{q}", 1

    def close(self):
        self.st["active"] = False
        self.st["pending_post"] = 1
        return 1

    def post_session(self, s):
        self.st["pending_post"] = None


from study.line_bot import MSG, Bot, create_app  # noqa: E402


class Cfg:
    bot_key, condition, sd_first, tag_sd_in_control = "A", "lufy_sd", True, True
    line_channel_secret, line_channel_token = "secret", "tok"
    session_target_min, idle_close_min, port = 30, 20, 0


def make_bot(tmp_path):
    (tmp_path / "participants.json").write_text(json.dumps({"P01": {"name": "Amos", "first": "A"}}))
    cfg = Cfg()
    cfg.participants_file = str(tmp_path / "participants.json")
    cfg.log_dir = str(tmp_path / "logs")
    sent = []
    bot = Bot(cfg, sender=lambda tok, text: sent.append((tok, text)), session_cls=FakeSession)
    bot.registry.bindings_file = str(tmp_path / "bindings.json")
    bot.loading = lambda uid: None
    FakeSession.store.clear()
    FakeSession.gate = None
    return bot, sent


def ev(text, uid="U1", tok="t"):
    return {"type": "message", "replyToken": tok, "source": {"userId": uid},
            "message": {"type": "text", "text": text}}


def test_full_flow(tmp_path):
    bot, sent = make_bot(tmp_path)
    bot.handle(ev("hello"))
    assert sent[-1][1] == MSG["need_join"]
    bot.handle(ev("join p01"))
    assert "Amos" in sent[-1][1]
    bot.handle(ev("join P01", uid="U2"))
    assert sent[-1][1] == MSG["code_taken"]
    bot.handle(ev("hi"))
    assert sent[-1][1] == MSG["not_active"]
    bot.handle(ev("start"))
    assert sent[-1][1] == "Hi, I'm LUFY!"
    bot.handle(ev("I like cats", tok="t9"))
    assert sent[-1] == ("t9", "echo:I like cats")
    bot.handle(ev("stop"))
    assert "1 messages" in sent[-1][1]
    assert bot.post_q.qsize() == 1
    bot.handle(ev("start"))
    assert sent[-1][1] == MSG["saving"]
    logs = [json.loads(line) for line in open(bot.log_path)]
    assert [e["event"] for e in logs] == ["join", "join", "start", "turn", "stop"]


def test_busy_while_thinking(tmp_path):
    bot, sent = make_bot(tmp_path)
    bot.handle(ev("join P01"))
    bot.handle(ev("start"))
    FakeSession.gate = threading.Event()
    t = threading.Thread(target=bot.handle, args=(ev("first", tok="a"),))
    t.start()
    import time
    time.sleep(0.2)
    bot.handle(ev("second", tok="b"))
    assert sent[-1] == ("b", MSG["busy"])
    FakeSession.gate.set()
    t.join()
    assert sent[-1] == ("a", "echo:first")


def test_signature(tmp_path):
    bot, _ = make_bot(tmp_path)
    app = create_app(bot).test_client()
    body = json.dumps({"events": []}).encode()
    good = base64.b64encode(hmac.new(b"secret", body, hashlib.sha256).digest()).decode()
    assert app.post("/callback", data=body, headers={"X-Line-Signature": good}).status_code == 200
    assert app.post("/callback", data=body, headers={"X-Line-Signature": "bad"}).status_code == 400
