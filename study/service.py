"""One condition's chat service, independent of the front end.

Wraps LufySession with what a multi-user front end needs: a lock per
participant, end-of-session processing in one background worker, closing
sessions left idle, recovery after a restart, and the turn log that
study.progress reads. Used by study.engine_server (the web study).
"""
import json
import os
import queue
import re
import threading
import time
import traceback

from .participants import Registry


class Refused(Exception):
    """The request is valid but not now: busy, saving, not_active, unknown."""

    def __init__(self, key):
        super().__init__(key)
        self.key = key


class ConditionService:
    def __init__(self, cfg, session_cls=None):
        self.cfg = cfg
        if session_cls is None:
            from .engine import LufySession as session_cls  # heavy imports; tests pass a fake
        self.session_cls = session_cls
        self.registry = Registry(cfg.participants_file)
        self.locks = {}
        self.locks_guard = threading.Lock()
        self.post_q = queue.Queue()
        os.makedirs(cfg.log_dir, exist_ok=True)
        self.log_path = os.path.join(cfg.log_dir, f"bot_{cfg.bot_key}.jsonl")

    # ---- plumbing ----------------------------------------------------
    def log(self, **kw):
        kw.update(ts=time.time(), bot=self.cfg.bot_key, condition=self.cfg.condition)
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(kw, ensure_ascii=False) + "\n")

    def participants(self):
        """code -> name, from participants.json (re-read so new codes need no restart)."""
        return {code: p["name"] for code, p in self.registry.codes().items()}

    def _session(self, name):
        if name not in self.participants().values():
            raise Refused("unknown")
        return self.session_cls(name, sd_first=self.cfg.sd_first, tag_sd=self.cfg.tag_sd_in_control)

    def _lock(self, name):
        with self.locks_guard:
            return self.locks.setdefault(name, threading.Lock())

    # ---- reads -------------------------------------------------------
    def state(self, name):
        sess = self._session(name)
        st = sess.state()
        out = {k: st.get(k) for k in ("active", "session", "turns", "started_at", "last_at", "pending_post")}
        out["active"] = bool(out["active"])
        out["history"] = self.history(sess, name) if st.get("active") else []
        return out

    @staticmethod
    def history(sess, name):
        """The open session as [{"role": "bot"|"user", "text": ...}], for redrawing a reloaded page."""
        st = sess.state()
        path = os.path.join(sess.conv_dir, f"{st['session']}.txt")
        msgs = []
        pat = re.compile(r'^utterance by LUFY: "(.*)", response by ' + re.escape(name) + r': "(.*)"$', re.S)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                for line in f.read().split("\n"):
                    m = pat.match(line)
                    if m:
                        msgs += [{"role": "bot", "text": m.group(1)}, {"role": "user", "text": m.group(2)}]
        msgs.append({"role": "bot", "text": st["response"]})
        return msgs

    # ---- writes ------------------------------------------------------
    def start(self, code, name):
        sess = self._session(name)
        lock = self._lock(name)
        if not lock.acquire(blocking=False):
            raise Refused("busy")
        try:
            st = sess.state()
            if st.get("active"):
                return {"session": st["session"], "text": st["response"], "resumed": True}
            if st.get("pending_post"):
                raise Refused("saving")
            t0 = time.time()
            shown, session = sess.start()
            self.log(event="start", code=code, session=session, bot_text=shown, latency=time.time() - t0)
            return {"session": session, "text": shown, "resumed": False}
        finally:
            lock.release()

    def turn(self, code, name, text):
        sess = self._session(name)
        lock = self._lock(name)
        if not lock.acquire(blocking=False):
            raise Refused("busy")
        try:
            st = sess.state()
            if not st.get("active"):
                raise Refused("not_active")
            t0 = time.time()
            try:
                response, session = sess.turn(text)
            except Exception:
                self.log(event="turn_error", code=code, session=st.get("session"), user_text=text,
                         error=traceback.format_exc())
                raise Refused("error")
            st2 = sess.state()
            self.log(event="turn", code=code, session=session, turn=st2["turns"], user_text=text,
                     bot_text=response, latency=time.time() - t0)
            return {"session": session, "text": response, "turns": st2["turns"]}
        finally:
            lock.release()

    def stop(self, code, name, reason="user"):
        sess = self._session(name)
        with self._lock(name):
            st = sess.state()
            if not st.get("active"):
                raise Refused("not_active")
            pending = sess.close()
            self.log(event="stop", code=code, session=st["session"], turns=st.get("turns", 0), reason=reason)
            if pending:
                self.post_q.put((code, name, pending))
            return {"session": st["session"], "turns": st.get("turns", 0)}

    # ---- background --------------------------------------------------
    def post_worker(self):
        """End-of-session processing, one at a time (loads GPT-2 and RoBERTa-large).
        No lock: while pending_post is set, start() refuses and turn/stop need an active session."""
        while True:
            code, name, session = self.post_q.get()
            t0 = time.time()
            try:
                self._session(name).post_session(session)
                self.log(event="post_done", code=code, session=session, seconds=time.time() - t0)
            except Exception:
                self.log(event="post_error", code=code, session=session, error=traceback.format_exc())

    def sweep_once(self):
        for code, name in self.participants().items():
            lock = self._lock(name)
            if not lock.acquire(blocking=False):
                continue
            try:
                sess = self._session(name)
                st = sess.state()
                if st.get("active") and time.time() - st["last_at"] > self.cfg.idle_close_min * 60:
                    pending = sess.close()
                    self.log(event="stop", code=code, session=st["session"], turns=st.get("turns", 0),
                             reason="idle")
                    if pending:
                        self.post_q.put((code, name, pending))
            finally:
                lock.release()

    def sweeper(self):
        while True:
            time.sleep(60)
            try:
                self.sweep_once()
            except Exception:
                self.log(event="sweeper_error", error=traceback.format_exc())

    def recover(self):
        for code, name in self.participants().items():
            pending = self._session(name).state().get("pending_post")
            if pending:
                self.post_q.put((code, name, pending))

    def start_background(self):
        self.recover()
        threading.Thread(target=self.post_worker, daemon=True).start()
        threading.Thread(target=self.sweeper, daemon=True).start()
