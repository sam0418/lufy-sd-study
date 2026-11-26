"""The web study: the only public service.

Each participant gets a personal link /p/<token>. The portal decides what
comes next (study.schedule), talks to the two condition engines on
localhost, and stores per-participant records (sessions, QA pairs,
questionnaire) in PORTAL_DATA. Participants see "LUFY A" and "LUFY B" only;
which is which lives in the engines' .env files.

    cd runs/portal && PYTHONPATH=../.. python -m study.portal
"""
import json
import os
import threading
import time
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv
from flask import Flask, abort, jsonify, redirect, render_template, request, url_for

from . import schedule
from .participants import Registry

MAX_TEXT = 2000


class PortalConfig:
    def __init__(self):
        load_dotenv()
        e = os.environ.get
        self.port = int(e("PORT", "8000"))
        self.engines = {"A": e("ENGINE_A_URL", "http://127.0.0.1:8001"),
                        "B": e("ENGINE_B_URL", "http://127.0.0.1:8002")}
        self.participants_file = os.path.abspath(e("PARTICIPANTS_FILE", "../participants.json"))
        self.data_dir = os.path.abspath(e("PORTAL_DATA", "./data"))
        self.questionnaire_file = os.path.abspath(e("QUESTIONNAIRE_FILE", "../questionnaire.json"))
        self.tz = ZoneInfo(e("STUDY_TZ", "Asia/Tokyo"))
        self.sessions_per_bot = int(e("SESSIONS_PER_BOT", "4"))
        self.max_per_bot_per_day = int(e("MAX_PER_BOT_PER_DAY", "1"))
        self.target_min = float(e("SESSION_TARGET_MIN", "30"))


class EngineDown(Exception):
    pass


class EngineClient:
    """HTTP to the two local engines. Returns (status_code, json)."""

    def __init__(self, urls):
        self.urls = urls

    def call(self, bot, method, name, action, body=None):
        try:
            r = requests.request(method, f"{self.urls[bot]}/v1/{name}/{action}", json=body, timeout=120)
            return r.status_code, r.json()
        except (requests.RequestException, ValueError) as e:
            raise EngineDown(str(e))


class Store:
    """One JSON file per participant code; writes are atomic."""

    def __init__(self, data_dir):
        self.dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        self.locks = {}
        self.guard = threading.Lock()

    def lock(self, code):
        with self.guard:
            return self.locks.setdefault(code, threading.Lock())

    def load(self, code):
        path = os.path.join(self.dir, f"{code}.json")
        if not os.path.exists(path):
            return {"sessions": [], "questionnaire": None}
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def save(self, code, record):
        path = os.path.join(self.dir, f"{code}.json")
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
        os.replace(path + ".tmp", path)


def create_app(cfg=None, engines=None, clock=time.time):
    cfg = cfg or PortalConfig()
    engines = engines or EngineClient(cfg.engines)
    registry = Registry(cfg.participants_file)
    store = Store(cfg.data_dir)
    app = Flask(__name__)

    # ---- helpers -----------------------------------------------------
    def participant(token):
        for code, p in registry.codes().items():
            if token and p.get("token") == token:
                return code, p
        abort(404)

    def questionnaire():
        if not os.path.exists(cfg.questionnaire_file):
            return None
        with open(cfg.questionnaire_file, encoding="utf-8") as f:
            q = json.load(f)
        return q if (q.get("items") or q.get("open")) else None

    def reconcile(code, p):
        """An open session the engine has closed (idle timeout, restart) is closed here too."""
        with store.lock(code):
            rec = store.load(code)
            open_s = [s for s in rec["sessions"] if s.get("ended") is None]
            for s in open_s:
                status, st = engines.call(s["bot"], "GET", p["name"], "state")
                if status == 200 and (not st["active"] or st["session"] != s["session"]):
                    if st["session"] == s["session"]:
                        s["turns"] = st.get("turns") or s.get("turns", 0)
                    s["ended"], s["end_reason"] = clock(), "idle"
            if open_s:
                store.save(code, rec)
            return rec

    def step(code, p, rec):
        return schedule.next_step(rec, p["first"], clock(), cfg.tz, cfg.sessions_per_bot,
                                  cfg.max_per_bot_per_day, questionnaire() is not None)

    def open_session(rec):
        return next((s for s in rec["sessions"] if s.get("ended") is None), None)

    # ---- pages -------------------------------------------------------
    @app.get("/")
    def index():
        return render_template("message.html", title="LUFY study",
                               text="Please open the personal link you received from the experimenter.")

    @app.get("/p/<token>")
    def home(token):
        code, p = participant(token)
        try:
            rec = reconcile(code, p)
        except EngineDown:
            return render_template("message.html", title="Please try again later",
                                   text="The chat service is not reachable right now."), 503
        st, detail = step(code, p, rec)
        if st == "resume":
            return redirect(url_for("chat", token=token))
        if st == "qa":
            return redirect(url_for("qa", token=token))
        if st == "questionnaire":
            return redirect(url_for("survey", token=token))
        return render_template("home.html", token=token, name=p["name"], step=st, bot=detail,
                               done=schedule.progress(rec, p["first"]), total=cfg.sessions_per_bot,
                               target=int(cfg.target_min), notice=request.args.get("notice"))

    @app.post("/p/<token>/begin")
    def begin(token):
        code, p = participant(token)
        rec = reconcile(code, p)
        st, bot = step(code, p, rec)
        if st != "chat":
            return redirect(url_for("home", token=token))
        status, out = engines.call(bot, "POST", p["name"], "start", {"code": code})
        if status != 200:
            return redirect(url_for("home", token=token, notice=out.get("error", "error")))
        with store.lock(code):
            rec = store.load(code)
            if not open_session(rec):
                rec["sessions"].append({"bot": bot, "session": out["session"], "started": clock(),
                                        "ended": None, "turns": 0, "end_reason": None, "qa": None})
                store.save(code, rec)
        return redirect(url_for("chat", token=token))

    @app.get("/p/<token>/chat")
    def chat(token):
        code, p = participant(token)
        s = open_session(reconcile(code, p))
        if not s:
            return redirect(url_for("home", token=token))
        return render_template("chat.html", token=token, bot=s["bot"], target=int(cfg.target_min))

    @app.route("/p/<token>/qa", methods=["GET", "POST"])
    def qa(token):
        code, p = participant(token)
        rec = reconcile(code, p)
        st, i = step(code, p, rec)
        if st != "qa":
            return redirect(url_for("home", token=token))
        s = rec["sessions"][i]
        error = None
        if request.method == "POST":
            pairs = [{"q": request.form.get(f"q{k}", "").strip()[:MAX_TEXT],
                      "a": request.form.get(f"a{k}", "").strip()[:MAX_TEXT]} for k in range(3)]
            if all(x["q"] and x["a"] for x in pairs):
                with store.lock(code):
                    rec = store.load(code)
                    rec["sessions"][i]["qa"] = {"pairs": pairs, "at": clock()}
                    store.save(code, rec)
                return redirect(url_for("home", token=token))
            error = "Please fill in all three questions and answers."
        return render_template("qa.html", token=token, bot=s["bot"], error=error, form=request.form)

    @app.route("/p/<token>/questionnaire", methods=["GET", "POST"])
    def survey(token):
        code, p = participant(token)
        rec = reconcile(code, p)
        q = questionnaire()
        if step(code, p, rec)[0] != "questionnaire" or q is None:
            return redirect(url_for("home", token=token))
        bots = ["A", "B"] if q.get("per_bot", True) else [None]
        keys = [(it["id"], b) for it in q.get("items", []) for b in bots]
        error = None
        if request.method == "POST":
            answers = {f"{i}{'_' + b if b else ''}": request.form.get(f"{i}{'_' + b if b else ''}")
                       for i, b in keys}
            opens = {o["id"]: request.form.get(o["id"], "").strip()[:MAX_TEXT] for o in q.get("open", [])}
            if all(answers.values()):
                with store.lock(code):
                    rec = store.load(code)
                    rec["questionnaire"] = {"scales": answers, "open": opens, "at": clock()}
                    store.save(code, rec)
                return redirect(url_for("home", token=token))
            error = "Please answer every item."
        return render_template("questionnaire.html", token=token, q=q, bots=bots, error=error)

    # ---- chat API ----------------------------------------------------
    def api_session(token):
        code, p = participant(token)
        return code, p, open_session(reconcile(code, p))

    @app.get("/api/<token>/chat")
    def api_chat(token):
        code, p, s = api_session(token)
        if not s:
            return jsonify(error="not_active"), 409
        status, st = engines.call(s["bot"], "GET", p["name"], "state")
        if status != 200:
            return jsonify(error=st.get("error", "error")), status
        return jsonify(bot=s["bot"], history=st["history"], elapsed=clock() - s["started"],
                       target_min=cfg.target_min)

    @app.post("/api/<token>/turn")
    def api_turn(token):
        code, p, s = api_session(token)
        if not s:
            return jsonify(error="not_active"), 409
        text = (request.json or {}).get("text", "").strip()[:MAX_TEXT]
        if not text:
            return jsonify(error="empty"), 400
        status, out = engines.call(s["bot"], "POST", p["name"], "turn", {"code": code, "text": text})
        if status != 200:
            return jsonify(error=out.get("error", "error")), status
        with store.lock(code):
            rec = store.load(code)
            for r in rec["sessions"]:
                if r.get("ended") is None:
                    r["turns"] = out["turns"]
            store.save(code, rec)
        return jsonify(text=out["text"], turns=out["turns"])

    @app.post("/api/<token>/stop")
    def api_stop(token):
        code, p, s = api_session(token)
        if not s:
            return jsonify(ok=True)
        status, out = engines.call(s["bot"], "POST", p["name"], "stop", {"code": code})
        with store.lock(code):
            rec = store.load(code)
            for r in rec["sessions"]:
                if r.get("ended") is None:
                    r["ended"], r["end_reason"] = clock(), "user"
                    if status == 200:
                        r["turns"] = out["turns"]
            store.save(code, rec)
        return jsonify(ok=True)

    @app.errorhandler(EngineDown)
    def engine_down(e):
        if request.path.startswith("/api/"):
            return jsonify(error="engine_down"), 503
        return render_template("message.html", title="Please try again later",
                               text="The chat service is not reachable right now."), 503

    @app.errorhandler(404)
    def not_found(e):
        return render_template("message.html", title="Link not found",
                               text="Please check the link you received from the experimenter."), 404

    return app


if __name__ == "__main__":
    c = PortalConfig()
    create_app(c).run(host="127.0.0.1", port=c.port, threaded=True)
