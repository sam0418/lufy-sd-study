"""Light stand-ins for llama_index / openai / emoji so the logic runs without
network, models or API keys. Only what LUFY and study/ actually touch."""
import json
import os
import sys
import types
import uuid


class FakeDocument:
    def __init__(self, text, metadata=None):
        self.text = text
        self.metadata = metadata or {}


def _docstore_path(persist_dir):
    return os.path.join(persist_dir, "docstore.json")


class FakeIndex:
    def __init__(self, persist_dir=None):
        self.persist_dir = persist_dir
        self.storage_context = self
        self.retrieved = []  # what retrieve() returns; tests may set it

    def _load(self):
        p = _docstore_path(self.persist_dir)
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        return {"docstore/data": {}, "docstore/ref_doc_info": {}}

    def _dump(self, data):
        os.makedirs(self.persist_dir, exist_ok=True)
        with open(_docstore_path(self.persist_dir), "w", encoding="utf-8") as f:
            json.dump(data, f)

    def insert(self, doc):
        data = self._load()
        nid, rid = str(uuid.uuid4()), str(uuid.uuid4())
        data["docstore/data"][nid] = {"__data__": {
            "id_": nid, "text": doc.text, "metadata": dict(doc.metadata),
            "relationships": {"1": {"node_id": rid, "metadata": dict(doc.metadata)}}}}
        data["docstore/ref_doc_info"][rid] = {"node_ids": [nid], "metadata": dict(doc.metadata)}
        self._dump(data)

    def delete_ref_doc(self, ref_id, delete_from_docstore=True):
        data = self._load()
        for k, v in list(data["docstore/data"].items()):
            if v["__data__"]["relationships"]["1"]["node_id"] == ref_id:
                del data["docstore/data"][k]
        data["docstore/ref_doc_info"].pop(ref_id, None)
        self._dump(data)
        DELETED.append(ref_id)

    def persist(self, persist_dir=None):
        if persist_dir:
            self.persist_dir = persist_dir
        if not os.path.exists(_docstore_path(self.persist_dir)):
            self._dump(self._load())

    def as_retriever(self, similarity_top_k=2):
        idx = self

        class R:
            def retrieve(self, q):
                return []
        return R()


DELETED = []


def install():
    core = types.ModuleType("llama_index.core")
    core.Document = FakeDocument

    class StorageContext:
        @staticmethod
        def from_defaults(persist_dir=None):
            return FakeIndex(persist_dir)

    class VectorStoreIndex(FakeIndex):
        def __init__(self, docs):
            super().__init__(None)

    def load_index_from_storage(storage_context=None):
        return FakeIndex(storage_context.persist_dir)

    core.StorageContext = StorageContext
    core.VectorStoreIndex = VectorStoreIndex
    core.load_index_from_storage = load_index_from_storage
    post = types.ModuleType("llama_index.core.postprocessor")

    class SimilarityPostprocessor:
        def __init__(self, similarity_cutoff=0):
            pass

        def postprocess_nodes(self, nodes):
            return nodes
    post.SimilarityPostprocessor = SimilarityPostprocessor
    li = types.ModuleType("llama_index")
    li.core = core
    sys.modules.update({"llama_index": li, "llama_index.core": core,
                        "llama_index.core.postprocessor": post})

    em = types.ModuleType("emoji")
    em.demojize = lambda s: s
    sys.modules["emoji"] = em

    oa = types.ModuleType("openai")

    class OpenAI:
        def __init__(self, *a, **k):
            pass
    oa.OpenAI = OpenAI
    sys.modules["openai"] = oa

    # Heavy model modules imported at the top of IPE.py
    for name, attr in (("perplexity", "calculate_perplexity"), ("inference_large", "emotion_prediction")):
        m = types.ModuleType(name)
        setattr(m, attr, lambda *a, **k: None)
        sys.modules[name] = m
