"""One condition as a local JSON service, for the web portal.

Listens on 127.0.0.1 only: participants never reach it, and its URL is the
only place the bot letter meets the condition (in the run directory's .env).

    cd runs/A && PYTHONPATH=../.. python -m study.engine_server
"""
from flask import Flask, jsonify, request

from .config import Config
from .service import ConditionService, Refused

STATUS = {"unknown": 404, "busy": 409, "saving": 409, "not_active": 409, "error": 502}


def create_app(service):
    app = Flask(__name__)

    @app.errorhandler(Refused)
    def refused(e):
        return jsonify(error=e.key), STATUS.get(e.key, 409)

    @app.get("/health")
    def health():
        return {"bot": service.cfg.bot_key}

    @app.get("/v1/<name>/state")
    def state(name):
        return jsonify(service.state(name))

    @app.post("/v1/<name>/start")
    def start(name):
        return jsonify(service.start(request.json["code"], name))

    @app.post("/v1/<name>/turn")
    def turn(name):
        return jsonify(service.turn(request.json["code"], name, request.json["text"]))

    @app.post("/v1/<name>/stop")
    def stop(name):
        body = request.json
        return jsonify(service.stop(body["code"], name, body.get("reason", "user")))

    return app


if __name__ == "__main__":
    cfg = Config()
    svc = ConditionService(cfg)
    svc.start_background()
    create_app(svc).run(host="127.0.0.1", port=cfg.port, threaded=True)
