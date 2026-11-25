"""Runtime configuration, read from environment variables (.env supported).

One process serves one LINE channel = one experimental condition.
"""
import os

from dotenv import load_dotenv

load_dotenv()

CONDITIONS = ("lufy", "lufy_sd")


def _bool(name, default):
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes")


class Config:
    def __init__(self):
        # Which bot this process is (e.g. "A" or "B"); participants only ever see the bot name.
        self.bot_key = os.environ.get("BOT_KEY", "A")
        self.condition = os.environ.get("CONDITION", "lufy")
        if self.condition not in CONDITIONS:
            raise ValueError(f"CONDITION must be one of {CONDITIONS}, got {self.condition!r}")

        self.line_channel_secret = os.environ.get("LINE_CHANNEL_SECRET", "")
        self.line_channel_token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")

        # Shared across both bots so one participant has one id.
        self.participants_file = os.path.abspath(
            os.environ.get("PARTICIPANTS_FILE", "../participants.json"))
        self.log_dir = os.path.abspath(os.environ.get("LOG_DIR", "../logs"))

        # Session hygiene. LUFY sessions were 30 minutes in the paper.
        self.session_target_min = float(os.environ.get("SESSION_TARGET_MIN", "30"))
        self.idle_close_min = float(os.environ.get("IDLE_CLOSE_MIN", "20"))

        # Tag SD in the control condition too (logged only, never used for
        # forgetting) so SD retention can be compared across conditions.
        self.tag_sd_in_control = _bool("TAG_SD_IN_CONTROL", True)

        self.port = int(os.environ.get("PORT", "8000"))

    @property
    def sd_first(self):
        return self.condition == "lufy_sd"
