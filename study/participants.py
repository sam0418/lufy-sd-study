"""Participant codes (shared, read-only) and LINE-user bindings (per bot).

participants.json (written by the experimenter, shared by both bots):

    {"P01": {"name": "Amos", "first": "A"},
     "P02": {"name": "Ellen", "first": "B"}}

`name` is what LUFY calls the participant and is also LUFY's memory folder
key and a regex fragment inside lufy/IPE.py, so it must be letters only.
`first` is which bot the participant talks to first each day (counterbalancing).

A participant links their LINE account by sending "join P01" to each bot.
LINE user ids differ between providers, so bindings are stored per bot in
the bot's own run directory (bindings.json).
"""
import json
import os
import re
import threading

NAME_RE = re.compile(r"^[A-Za-z]{1,20}$")
JOIN_RE = re.compile(r"^\s*join\s+(\S+)\s*$", re.I)

_lock = threading.Lock()


class Registry:
    def __init__(self, participants_file, bindings_file="./bindings.json"):
        self.participants_file = participants_file
        self.bindings_file = bindings_file

    def codes(self):
        with open(self.participants_file, "r", encoding="utf-8") as f:
            codes = json.load(f)
        for code, p in codes.items():
            if not NAME_RE.match(p.get("name", "")):
                raise ValueError(f"{code}: name must be letters only, got {p.get('name')!r}")
        return codes

    def _bindings(self):
        if not os.path.exists(self.bindings_file):
            return {}
        with open(self.bindings_file, "r", encoding="utf-8") as f:
            return json.load(f)

    def lookup(self, line_user_id):
        """Return (code, participant dict) or (None, None)."""
        code = self._bindings().get(line_user_id)
        if code is None:
            return None, None
        return code, self.codes().get(code)

    def bound_users(self):
        codes = self.codes()
        return {uid: (c, codes[c]) for uid, c in self._bindings().items() if c in codes}

    def join(self, line_user_id, code):
        """Bind; returns (ok, message_key)."""
        code = code.upper()
        with _lock:
            codes = self.codes()
            if code not in codes:
                return False, "unknown_code"
            b = self._bindings()
            if b.get(line_user_id) == code:
                return True, "already"
            if code in b.values():
                return False, "code_taken"
            if line_user_id in b:
                return False, "already_other"
            b[line_user_id] = code
            tmp = self.bindings_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(b, f, indent=2)
            os.replace(tmp, self.bindings_file)
            return True, "joined"
