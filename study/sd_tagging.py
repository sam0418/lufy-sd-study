"""Label each new memory as self-disclosure (SD) or not, at the end of a session.

Labels are stored next to the memories in sd_labels.json, keyed by the
sha256 of the memory text (same scheme as LUFY-EVO E035). Not in node
metadata on purpose: lufy/relevant_memory_current_context.py rebuilds a
recalled memory's metadata from a fixed key list and would silently drop
an extra key, while the text stays identical.

Tagging runs after the session, so it never changes reply latency, and it
runs in both conditions (control: logged only) so retention can be compared.
"""
import hashlib
import json
import os
import re

from .sd_detector import detect_self_disclosure


def text_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _labels_path(user_name):
    return f"./memories/{user_name}_memories/sd_labels.json"


def load_labels(user_name):
    path = _labels_path(user_name)
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_sd_hashes(user_name):
    return {h for h, lab in load_labels(user_name).items() if lab.get("sd")}


def user_utterance(text, user_name):
    """Split a stored memory the same way lufy/IPE.py does; return (context, user part)."""
    parts = re.split(rf"(response by {re.escape(user_name)}: )", text)
    if len(parts) < 3:
        return text, text
    return parts[0] + parts[1], parts[2].strip('"')


def tag_session(user_name, session):
    """Detect SD for every memory first mentioned in `session` that has no label yet."""
    with open(f"./memories/{user_name}_memories/docstore.json", "r", encoding="utf-8") as f:
        data = json.load(f)

    labels = load_labels(user_name)
    for entry in data["docstore/data"].values():
        md = entry["__data__"]["metadata"]
        text = entry["__data__"]["text"]
        h = text_hash(text)
        if md.get("first_mentioned_session") != session or h in labels:
            continue
        context, utterance = user_utterance(text, user_name)
        result = detect_self_disclosure(utterance, context)
        labels[h] = {"session": session, "utterance": utterance, **result}

    with open(_labels_path(user_name), "w", encoding="utf-8") as f:
        json.dump(labels, f, ensure_ascii=False, indent=2)
    return labels
