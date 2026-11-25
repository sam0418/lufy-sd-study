"""LINE webhook for one bot (= one condition). Replies only; never pushes.

Every message LUFY sends is a reply to a user message, using that message's
reply token, so nothing counts against the LINE message quota. Consequences:
the bot never speaks first (a session starts when the participant sends
"start"), and nothing is sent when a session auto-closes.

Run from the bot's run directory, e.g.:
    cd runs/A && python -m study.line_bot      (with ../../ on PYTHONPATH)
"""
import base64
import hashlib
import hmac
import json
import os
import queue
import threading
import time
import traceback

import requests
from flask import Flask, abort, request

from .config import Config
from .participants import JOIN_RE, Registry

API = "https://api.line.me/v2/bot"

MSG = {
    "need_join": "Hi! Please send: join <your participant code>  (e.g. join P01)\n"
                 "請傳送：join 你的受試者代號（例如 join P01）",
    "joined": "Thanks, {name}! You're registered.\nSend \"start\" to begin a chat, and \"stop\" to end it.\n"
              "傳「start」開始聊天，傳「stop」結束。",
    "already": "You're already registered, {name}. Send \"start\" to begin.",
    "unknown_code": "That code wasn't found. Please check it with the experimenter.",
    "code_taken": "That code is already linked to another LINE account. Please contact the experimenter.",
    "already_other": "This LINE account is already linked to another code. Please contact the experimenter.",
    "not_active": "No chat is running. Send \"start\" to begin a session.",
    "already_active": "We're already chatting! Send \"stop\" when you want to end this session.",
    "busy": "(Still thinking about your last message, please wait for my reply.)",
    "saving": "(I'm still saving our last chat. Please try \"start\" again in a minute or two.)",
    "stopped": "Thanks for chatting! This session is saved. ({turns} messages)\nSee you next time!",
    "error": "(Sorry, something went wrong on my side. Please send that message again.)",
    "text_only": "(I can only read text messages.)",
    "time_up": "\n\n(⏰ {minutes} minutes reached. Send \"stop\" whenever you'd like to end this session.)",
}

START_WORDS = {"start", "開始", "スタート"}
STOP_WORDS = {"stop", "結束", "ストップ"}


class Bot:
    def __init__(self, cfg=None, sender=None, session_cls=None):
        self.cfg = cfg or Config()
        if session_cls is None:
            from .engine import LufySession as session_cls  # heavy imports; tests pass a fake
        self.session_cls = session_cls
        self.registry = Registry(self.cfg.participants_file)
        self.locks = {}
        self.locks_guard = threading.Lock()
        self.post_q = queue.Queue()
        self.send = sender or self._line_reply
        os.makedirs(self.cfg.log_dir, exist_ok=True)
        self.log_path = os.path.join(self.cfg.log_dir, f"bot_{self.cfg.bot_key}.jsonl")

    # ---- plumbing ----------------------------------------------------
    def log(self, **kw):
        kw.update(ts=time.time(), bot=self.cfg.bot_key, condition=self.cfg.condition)
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(kw, ensure_ascii=False) + "\n")

    def _headers(self):
        return {"Authorization": f"Bearer {self.cfg.line_channel_token}", "Content-Type": "application/json"}

    def _line_reply(self, reply_token, text):
        r = requests.post(f"{API}/message/reply", headers=self._headers(), timeout=10,
                          json={"replyToken": reply_token, "messages": [{"type": "text", "text": text[:5000]}]})
        if r.status_code != 200:
            raise RuntimeError(f"reply failed {r.status_code}: {r.text}")

    def loading(self, user_id):
        """Typing indicator while LUFY thinks. Not a message; does not use quota."""
        try:
            requests.post(f"{API}/chat/loading/start", headers=self._headers(), timeout=5,
                          json={"chatId": user_id, "loadingSeconds": 60})
        except requests.RequestException:
            pass

    def verify(self, body, signature):
        mac = hmac.new(self.cfg.line_channel_secret.encode(), body, hashlib.sha256).digest()
        return hmac.compare_digest(base64.b64encode(mac).decode(), signature or "")

    def lock_for(self, user_id):
        with self.locks_guard:
            return self.locks.setdefault(user_id, threading.Lock())

    def session_for(self, participant):
        return self.session_cls(participant["name"], sd_first=self.cfg.sd_first,
                           tag_sd=self.cfg.tag_sd_in_control)

    def reply(self, token, key_or_text, **fmt):
        text = MSG[key_or_text].format(**fmt) if key_or_text in MSG else key_or_text
        try:
            self.send(token, text)
            return True
        except Exception as e:  # expired token, network, ...
            self.log(event="reply_failed", error=str(e))
            return False

    # ---- event handling ----------------------------------------------
    def handle(self, event):
        if event.get("type") != "message":
            return
        user_id = event["source"].get("userId")
        token = event["replyToken"]
        msg = event["message"]
        if msg.get("type") != "text":
            self.reply(token, "text_only")
            return
        text = msg["text"].strip()

        code, participant = self.registry.lookup(user_id)
        if participant is None:
            m = JOIN_RE.match(text)
            if not m:
                self.reply(token, "need_join")
                return
            ok, key = self.registry.join(user_id, m.group(1))
            self.log(event="join", user=user_id, code=m.group(1).upper(), result=key)
            name = self.registry.codes().get(m.group(1).upper(), {}).get("name", "")
            self.reply(token, key, name=name)
            return

        lock = self.lock_for(user_id)
        if not lock.acquire(blocking=False):
            self.reply(token, "busy")
            return
        try:
            self._handle_participant(code, participant, user_id, token, text)
        finally:
            lock.release()

    def _handle_participant(self, code, participant, user_id, token, text):
        sess = self.session_for(participant)
        st = sess.state()
        word = text.lower()

        if word in START_WORDS:
            if st.get("active"):
                self.reply(token, "already_active")
                return
            if st.get("pending_post"):
                self.reply(token, "saving")
                return
            self.loading(user_id)
            t0 = time.time()
            shown, session = sess.start()
            self.log(event="start", code=code, session=session, bot_text=shown, latency=time.time() - t0)
            self.reply(token, shown)
            return

        if word in STOP_WORDS:
            if not st.get("active"):
                self.reply(token, "not_active")
                return
            pending = sess.close()
            self.log(event="stop", code=code, session=st["session"], turns=st.get("turns", 0), reason="user")
            if pending:
                self.post_q.put((code, participant, pending))
            self.reply(token, "stopped", turns=st.get("turns", 0))
            return

        if not st.get("active"):
            self.reply(token, "not_active")
            return

        self.loading(user_id)
        t0 = time.time()
        try:
            response, session = sess.turn(text)
        except Exception:
            self.log(event="turn_error", code=code, session=st.get("session"), user_text=text,
                     error=traceback.format_exc())
            self.reply(token, "error")
            return
        latency = time.time() - t0
        st2 = sess.state()
        out = response
        elapsed_min = (time.time() - st2["started_at"]) / 60
        if elapsed_min >= self.cfg.session_target_min and not st2.get("time_up_shown"):
            out += MSG["time_up"].format(minutes=int(self.cfg.session_target_min))
            st2["time_up_shown"] = True
            sess._save(st2)
        sent = self.reply(token, out)
        self.log(event="turn", code=code, session=session, turn=st2["turns"], user_text=text,
                 bot_text=response, latency=latency, delivered=sent)

    # ---- background --------------------------------------------------
    def post_worker(self):
        """End-of-session processing, one at a time (it loads GPT-2 and RoBERTa-large)."""
        while True:
            # No per-user lock needed: while pending_post is set, start() is refused and
            # turn()/close() need an active session, so nothing else touches these memories.
            code, participant, session = self.post_q.get()
            t0 = time.time()
            try:
                self.session_for(participant).post_session(session)
                self.log(event="post_done", code=code, session=session, seconds=time.time() - t0)
            except Exception:
                self.log(event="post_error", code=code, session=session, error=traceback.format_exc())

    def sweeper(self):
        """Close sessions idle longer than IDLE_CLOSE_MIN (silently: replies only, no push)."""
        while True:
            time.sleep(60)
            try:
                for user_id, (code, p) in self.registry.bound_users().items():
                    lock = self.lock_for(user_id)
                    if not lock.acquire(blocking=False):
                        continue
                    try:
                        sess = self.session_for(p)
                        st = sess.state()
                        if st.get("active") and time.time() - st["last_at"] > self.cfg.idle_close_min * 60:
                            pending = sess.close()
                            self.log(event="stop", code=code, session=st["session"],
                                     turns=st.get("turns", 0), reason="idle")
                            if pending:
                                self.post_q.put((code, p, pending))
                    finally:
                        lock.release()
            except Exception:
                self.log(event="sweeper_error", error=traceback.format_exc())

    def recover(self):
        """Re-queue post-processing that a restart interrupted."""
        for user_id, (code, p) in self.registry.bound_users().items():
            pending = self.session_for(p).state().get("pending_post")
            if pending:
                self.post_q.put((code, p, pending))

    def start_background(self):
        self.recover()
        threading.Thread(target=self.post_worker, daemon=True).start()
        threading.Thread(target=self.sweeper, daemon=True).start()


def create_app(bot=None):
    bot = bot or Bot()
    app = Flask(__name__)

    @app.post("/callback")
    def callback():
        body = request.get_data()
        if not bot.verify(body, request.headers.get("X-Line-Signature")):
            abort(400)
        for event in json.loads(body).get("events", []):
            # answer the webhook at once; the reply goes out from the thread
            threading.Thread(target=bot.handle, args=(event,), daemon=True).start()
        return "OK"

    @app.get("/health")
    def health():
        return {"bot": bot.cfg.bot_key, "condition": bot.cfg.condition}

    app.bot = bot
    return app


if __name__ == "__main__":
    b = Bot()
    b.start_background()
    create_app(b).run(host="0.0.0.0", port=b.cfg.port, threaded=True)
