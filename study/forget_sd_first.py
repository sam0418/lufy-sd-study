"""SD-first forgetting: the only experimental manipulation in this study.

This is lufy/forget_mechanism_wisely.py with exactly one change: when
ranking memories to keep, self-disclosure memories come first, and within
each group the original survival probability decides. How many memories are
kept, and every probability, are identical to the original. This matches the
"sd-first" arm used offline in LUFY-EVO evaluation/_forget.py (E035).

tests/test_forget_equivalence.py checks that with no SD labels this keeps
exactly the same memories as the original function.
"""
import json
import math

from llama_index.core import StorageContext, load_index_from_storage

from .sd_tagging import load_sd_hashes, text_hash

A1, A2, A3, A4, A5 = (2.1297349437869455, 2.8709762924617905, 1.5012260479089086,
                      0.62885264522, 2.2459023043571427)


def select_keep(entries, session, w, sd_hashes):
    """Return (ids_to_keep, n_memories). Pure; mirrors the original math line by line."""
    w1, w2, w3, w4, w5 = w
    total_probability = 0
    id_prob_sd = []
    for entry in entries:
        md = entry["__data__"]["metadata"]
        node_id = entry["__data__"]["relationships"]["1"]["node_id"]
        time_elapsed = session - md["last_recalled_session"]
        importance = md["importance"] / 27.5
        perplexity = md["perplexity"] / 400
        arousal = md["arousal"] / 15
        S = (A1 * w1 * importance + A2 * w2 * perplexity + A3 * w3 * arousal
             + A4 * w4 * (md["memory_strength"] - 1) - A5 * w5 * md["unused_count"])
        if S < 0.1:
            S = 0.1
        probability = math.exp(-time_elapsed / (0.33 * S))
        total_probability += probability
        is_sd = text_hash(entry["__data__"]["text"]) in sd_hashes
        id_prob_sd.append((node_id, probability, is_sd))

    n = len(id_prob_sd)
    average_probability = total_probability / n
    if 0.1 < average_probability < 0.15:
        pass
    elif average_probability < 0.1:
        average_probability = 0.1
    else:
        average_probability = 0.15
    n_keep = math.ceil((average_probability * n) + session - 2)

    # The manipulation: SD memories rank above non-SD ones. Python's sort is
    # stable, so with no SD labels this is the original descending-probability order.
    id_prob_sd.sort(key=lambda x: (x[2], x[1]), reverse=True)
    return [i for i, _, _ in id_prob_sd[:n_keep]], n


def forget_mechanism_sd_first(user_name, session, w1, w2, w3, w4, w5):
    """Drop-in replacement for forget_mechanism_wisely (same signature, same side effects)."""
    persist = f"./memories/{user_name}_memories"
    with open(f"{persist}/docstore.json", "r", encoding="utf-8") as f:
        data = json.load(f)

    entries = list(data["docstore/data"].values())
    sd_hashes = load_sd_hashes(user_name)
    keep, n_before = select_keep(entries, session, (w1, w2, w3, w4, w5), sd_hashes)
    keep = set(keep)

    deleted = 0
    for entry in entries:
        node_id = entry["__data__"]["relationships"]["1"]["node_id"]
        if node_id not in keep:
            deleted += 1
            storage_context = StorageContext.from_defaults(persist_dir=persist)
            index = load_index_from_storage(storage_context=storage_context)
            index.delete_ref_doc(f"{node_id}", delete_from_docstore=True)
            index.storage_context.persist(persist_dir=persist)

    with open(f"{persist}/docstore.json", "r", encoding="utf-8") as f:
        n_after = len(json.load(f)["docstore/data"])

    n_sd_kept = sum(1 for e in entries
                    if e["__data__"]["relationships"]["1"]["node_id"] in keep
                    and text_hash(e["__data__"]["text"]) in sd_hashes)
    with open(f"{persist}/LUFY_count.txt", "a", encoding="utf-8") as f:
        f.write(f"Current session: {session-1}\n")
        f.write(f"Number of memories before deletion: {n_before}\n")
        f.write(f"Number of memories deleted: {deleted}\n")
        f.write(f"Number of memories after deletion: {n_after}\n")
        f.write(f"SD memories kept (sd-first): {n_sd_kept}\n")
