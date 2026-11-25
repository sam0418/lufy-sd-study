"""LUFY's chat loop (lufy/chat.py) as an event-driven session engine.

chat.py blocks on input(); a LINE bot receives one message at a time. This
module replays chat.py's logic step for step, keyed by message instead of by
loop iteration. Constants, cutoffs, prompts and the order of side effects are
unchanged. Differences from chat.py, all identical across conditions:

  * Session state (session no., last bot line, turn counter n) is persisted
    to state.json after every step, so a server restart does not lose a session.
  * generate.py / generate_first_conversation.py draw their 30% "mention the
    time / name" coin once at import. chat.py is a fresh process per session,
    so that is once per session; we redraw it at session start to match.
  * End of session runs SD tagging before forgetting, and in the lufy_sd
    condition uses forget_mechanism_sd_first instead of forget_mechanism_wisely.

All paths are relative to cwd, exactly like LUFY: one process = one condition.
"""
import json
import math
import os
import random
import time

from . import lufy_path  # noqa: F401  (puts ../lufy on sys.path)

import emoji
from llama_index.core import StorageContext, VectorStoreIndex, load_index_from_storage
from llama_index.core.postprocessor import SimilarityPostprocessor

import generate as generate_mod
import generate_first_conversation as gfc_mod
from bmi_utterances import bmi_utterances
from forget_mechanism_wisely import forget_mechanism_wisely
from IPE import IPE
from relevant_memory_current_context import relevant_memory_current_context
from summarize_wisely import summarize_wisely

from .forget_sd_first import forget_mechanism_sd_first
from .sd_tagging import tag_session

W = (0.44776699, -0.2801391, 2.76290708, 1.02800192, -0.01241566)
A = (2.1297349437869455, 2.8709762924617905, 1.5012260479089086, 0.62885264522, 2.2459023043571427)

SESSION1_INTRO = (
    "Welcome {name}! Now you will talk to LUFY, your AI friend! You can talk about anything you want. "
    "If you want to stop, please type 'stop'. Enjoy!\n"
    "First you will be asked to do a short self introduction, an example would be:\n"
    "\"Hey there! I'm Ryuichi, a 25 year old, from Japan, currently studying AI. "
    "I love to exercise, nice to meet you!\"\n\n")
SESSION1_OPENER = ("Hi, I'm LUFY, your AI friend! Very nice to meet you {name}! Could you do a short "
                   "self introduction for me? If you don't mind me asking, could you tell me your age, "
                   "occupation, nationality?")


def _paths(name):
    root = f"./memories/{name}_memories"
    return root, f"{root}/conversation", f"{root}/key_summary.txt", f"{root}/state.json"


def _retrieve(index, query, session, cutoff):
    """chat.py's retrieval + re-scoring block (identical in both places it appears)."""
    w1, w2, w3, w4, w5 = W
    a1, a2, a3, a4, a5 = A
    nodes = index.as_retriever(similarity_top_k=2).retrieve(query)
    nodes = SimilarityPostprocessor(similarity_cutoff=cutoff).postprocess_nodes(nodes)
    scored = []
    for nd in nodes:
        md = nd.metadata
        importance = md["importance"] / 27.5
        perplexity = md["perplexity"] / 250
        arousal = md["arousal"] / 15
        S = (a1 * w1 * importance + a2 * w2 * perplexity + a3 * w3 * arousal
             + a4 * w4 * (md["memory_strength"] - 1) - a5 * w5 * md["unused_count"])
        if S < 0.1:
            S = 0.1
        time_decay = math.exp(-(session - md["last_recalled_session"]) / (0.5 * S))
        scored.append((nd.node_id, nd.score + (time_decay / 10)))
    scored.sort(key=lambda x: x[1], reverse=True)
    return [i for i, _ in scored[:2]]


def _load_index(root):
    return load_index_from_storage(storage_context=StorageContext.from_defaults(persist_dir=root))


class LufySession:
    """One participant's sessions with one bot. Not thread-safe: callers hold a per-user lock."""

    def __init__(self, name, sd_first=False, tag_sd=True):
        self.name = name
        self.sd_first = sd_first
        self.tag_sd = tag_sd or sd_first
        self.root, self.conv_dir, self.summary_path, self.state_path = _paths(name)

    # ---- state -------------------------------------------------------
    def state(self):
        if os.path.exists(self.state_path):
            with open(self.state_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {"active": False}

    def _save(self, st):
        os.makedirs(self.root, exist_ok=True)
        tmp = self.state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False)
        os.replace(tmp, self.state_path)

    @property
    def active(self):
        return self.state().get("active", False)

    # ---- start -------------------------------------------------------
    def start(self):
        """Begin a session; returns the text to send (LUFY's opening line)."""
        coin = random.random()  # redraw per session, as a fresh chat.py process would
        generate_mod.random_number = coin
        gfc_mod.random_number = coin

        if os.path.exists(self.conv_dir):
            session = len(os.listdir(self.conv_dir)) + 1
        else:
            session = 1

        if session == 1:
            index = VectorStoreIndex([])
            index.storage_context.persist(persist_dir=self.root)
            response = SESSION1_OPENER.format(name=self.name)
            shown = SESSION1_INTRO.format(name=self.name) + response
        else:
            index = _load_index(self.root)
            ids = _retrieve(index, f"What is the most important thing that has happened to {self.name}? ",
                            session, cutoff=0.6)
            relevant_memory = ("None" if not ids
                               else relevant_memory_current_context(self.name, ids, session, index))
            with open(self.summary_path, "r", encoding="utf-8") as f:
                key_summary = f.read()
            response = gfc_mod.generate_first_conversation(relevant_memory, key_summary, self.name, session)
            shown = response

        now = time.time()
        self._save({"active": True, "session": session, "response": response, "n": 1,
                    "started_at": now, "last_at": now, "turns": 0, "coin": coin})
        return shown, session

    # ---- one user message --------------------------------------------
    def turn(self, query):
        """Handle one non-empty, non-'stop' user message; returns LUFY's reply."""
        st = self.state()
        session, response, n = st["session"], st["response"], st["n"]
        generate_mod.random_number = st["coin"]

        # save (previous LUFY line, this user line) as one memory
        current_conversation = (f'utterance by LUFY: "{response}"' + "," + f' response by {self.name}: "{query}"')
        current_conversation = emoji.demojize(current_conversation)
        index = _load_index(self.root)
        index.insert(bmi_utterances(current_conversation, session))
        index.storage_context.persist(persist_dir=self.root)
        index = _load_index(self.root)
        os.makedirs(self.conv_dir, exist_ok=True)
        text_file_path = os.path.join(self.conv_dir, f"{session}.txt")
        with open(text_file_path, "a", encoding="utf-8") as f:
            f.write(current_conversation + "\n")

        # chat.py loop body
        n = n + 1
        text = query
        ids = _retrieve(index, query, session, cutoff=0.75)
        relevant_memory = "None" if not ids else relevant_memory_current_context(self.name, ids, session, index)
        if os.path.exists(self.summary_path):
            with open(self.summary_path, "r", encoding="utf-8") as f:
                key_summary = f.read()
        else:
            key_summary = "None"
        if n > 3:
            with open(text_file_path, "r", encoding="utf-8") as f:
                lines = f.read().split("\n")
            text = ",".join(lines[-3:])
        response = generate_mod.generate(relevant_memory, text, key_summary, self.name)
        n = n + 1

        st.update(response=response, n=n, last_at=time.time(), turns=st.get("turns", 0) + 1)
        self._save(st)
        return response, session

    # ---- stop --------------------------------------------------------
    def close(self):
        """Mark the session closed; returns the session number whose post-processing is due (or None)."""
        st = self.state()
        if not st.get("active"):
            return None
        session = st["session"]
        st["active"] = False
        st["closed_at"] = time.time()
        has_text = os.path.exists(os.path.join(self.conv_dir, f"{session}.txt"))
        st["pending_post"] = session if has_text else None
        self._save(st)
        return st["pending_post"]

    def post_session(self, session):
        """chat.py's end-of-session block (slow: GPT-2, RoBERTa, GPT-4o per memory)."""
        IPE(self.name, session)
        summarize_wisely(self.name, session)
        if self.tag_sd:
            tag_session(self.name, session)
        forget = forget_mechanism_sd_first if self.sd_first else forget_mechanism_wisely
        forget(self.name, session + 1, *W)
        st = self.state()
        st["pending_post"] = None
        self._save(st)
