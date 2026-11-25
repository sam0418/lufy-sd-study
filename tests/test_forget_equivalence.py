"""SD-first must equal the original forgetting when no memory is SD, and must
keep the same number of memories when some are."""
import json
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from tests import stubs  # noqa: E402

stubs.install()
from study import lufy_path  # noqa: E402,F401
from forget_mechanism_wisely import forget_mechanism_wisely  # noqa: E402
from study.engine import W  # noqa: E402
from study.forget_sd_first import forget_mechanism_sd_first  # noqa: E402
from study.sd_tagging import text_hash  # noqa: E402

USER = "Amos"


def make_store(tmp_path, n=60, sessions=3, seed=0):
    rng = random.Random(seed)
    idx = stubs.FakeIndex(str(tmp_path / "memories" / f"{USER}_memories"))
    texts = []
    for i in range(n):
        s = rng.randint(1, sessions)
        text = f'utterance by LUFY: "q{i}", response by {USER}: "a{i}"'
        texts.append(text)
        idx.insert(stubs.FakeDocument(text, {
            "filetype": "conversation", "memory_strength": rng.randint(1, 3),
            "importance": rng.uniform(1, 10), "last_recalled_session": s,
            "first_mentioned_session": s, "valence": 0, "arousal": rng.uniform(1, 5),
            "perplexity": rng.uniform(10, 160), "unused_count": rng.randint(0, 2)}))
    with open(f"{idx.persist_dir}/conversation.marker", "w"):
        pass
    return idx, texts


def kept_texts(idx):
    with open(f"{idx.persist_dir}/docstore.json") as f:
        return sorted(v["__data__"]["text"] for v in json.load(f)["docstore/data"].values())


def run(fn, tmp_path, seed, sd=()):
    os.makedirs(tmp_path, exist_ok=True)
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        idx, texts = make_store(tmp_path, seed=seed)
        if sd:
            labels = {text_hash(texts[i]): {"sd": True} for i in sd}
            with open(f"{idx.persist_dir}/sd_labels.json", "w") as f:
                json.dump(labels, f)
        fn(USER, 4, *W)
        return kept_texts(idx), texts
    finally:
        os.chdir(cwd)


def test_no_sd_labels_matches_original(tmp_path):
    for seed in range(5):
        a, _ = run(forget_mechanism_wisely, tmp_path / f"orig{seed}", seed)
        b, _ = run(forget_mechanism_sd_first, tmp_path / f"sd{seed}", seed)
        assert a == b and len(a) > 0


def test_sd_memories_kept_first_same_count(tmp_path):
    sd_idx = list(range(0, 60, 7))  # 9 SD memories
    a, _ = run(forget_mechanism_wisely, tmp_path / "orig", 1)
    b, texts = run(forget_mechanism_sd_first, tmp_path / "sd", 1, sd=sd_idx)
    assert len(a) == len(b)  # retention budget unchanged
    sd_texts = {texts[i] for i in sd_idx}
    # every slot goes to SD first: all SD kept, or all slots are SD
    assert len(sd_texts & set(b)) == min(len(b), len(sd_texts))
    assert len(sd_texts & set(b)) > len(sd_texts & set(a))
