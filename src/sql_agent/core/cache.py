from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import asdict, dataclass
from typing import Optional

from sql_agent.config import Settings


def normalize_question(question: str) -> str:
    return " ".join(question.lower().strip().split())


@dataclass
class CachedExample:
    id: int
    question: str
    sql: str


class QueryCache:
    """Exact-match + optional semantic few-shot cache. Only store successful queries."""

    def __init__(self, settings: Settings) -> None:
        self.cfg = settings
        self._lock = threading.Lock()
        self._metadata: list[CachedExample] = []
        self._index = None
        self._embedder = None
        self._next_id = 0
        self._exact: dict[str, str] = {}
        os.makedirs(settings.cache_dir, exist_ok=True)
        self._load()
        if settings.enable_semantic_cache:
            self._init_embedder()

    def _init_embedder(self) -> None:
        try:
            import faiss
            from sentence_transformers import SentenceTransformer

            self._embedder = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
            self._faiss = faiss
            if os.path.exists(self.cfg.faiss_index_path):
                self._index = faiss.read_index(self.cfg.faiss_index_path)
            elif self._metadata:
                vecs = self._embed([e.question for e in self._metadata])
                self._index = faiss.IndexFlatIP(vecs.shape[1])
                self._index.add(vecs)
        except Exception:
            self._embedder = None
            self._index = None

    def _embed(self, texts: list[str]):
        import numpy as np

        vecs = self._embedder.encode(texts, convert_to_numpy=True, normalize_embeddings=True)
        if vecs.ndim == 1:
            vecs = vecs.reshape(1, -1)
        return vecs.astype("float32")

    def _load(self) -> None:
        path = self.cfg.metadata_path
        if not os.path.exists(path):
            return
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        self._metadata = [CachedExample(**item) for item in data]
        self._next_id = max((e.id for e in self._metadata), default=-1) + 1
        for ex in self._metadata:
            self._exact[normalize_question(ex.question)] = ex.sql

    def _persist(self) -> None:
        with open(self.cfg.metadata_path, "w", encoding="utf-8") as f:
            json.dump([asdict(e) for e in self._metadata], f, ensure_ascii=False, indent=2)
        if self._index is not None and getattr(self, "_faiss", None) is not None:
            self._faiss.write_index(self._index, self.cfg.faiss_index_path)

    def exact_sql(self, question: str) -> Optional[str]:
        if not self.cfg.enable_exact_sql_cache:
            return None
        return self._exact.get(normalize_question(question))

    def add_example(self, question: str, sql: str) -> CachedExample:
        with self._lock:
            example = CachedExample(id=self._next_id, question=question, sql=sql)
            self._next_id += 1
            self._metadata.append(example)
            self._exact[normalize_question(question)] = sql
            if self._embedder is not None:
                vec = self._embed([question])
                if self._index is None:
                    self._index = self._faiss.IndexFlatIP(vec.shape[1])
                self._index.add(vec)
            self._persist()
            return example

    def similar_examples(self, question: str, top_k: int | None = None) -> list[tuple[CachedExample, float]]:
        if self._index is None or not self._metadata or self._embedder is None:
            return []
        k = top_k or self.cfg.cache_top_k
        vec = self._embed([question])
        scores, indices = self._index.search(vec, min(k, len(self._metadata)))
        results: list[tuple[CachedExample, float]] = []
        for idx, score in zip(indices[0], scores[0]):
            if idx < 0 or score < self.cfg.similarity_threshold:
                continue
            results.append((self._metadata[int(idx)], float(score)))
        return results


class TTLResultCache:
    def __init__(self, ttl_seconds: int) -> None:
        self.ttl = ttl_seconds
        self._data: dict[str, tuple[float, tuple[list[str], list[tuple]]]] = {}
        self._lock = threading.Lock()

    def _key(self, sql: str) -> str:
        return hashlib.sha256(sql.encode("utf-8")).hexdigest()

    def get(self, sql: str):
        if self.ttl <= 0:
            return None
        key = self._key(sql)
        with self._lock:
            item = self._data.get(key)
            if not item:
                return None
            ts, value = item
            if time.time() - ts > self.ttl:
                self._data.pop(key, None)
                return None
            return value

    def set(self, sql: str, columns: list[str], rows: list[tuple]) -> None:
        if self.ttl <= 0:
            return
        with self._lock:
            self._data[self._key(sql)] = (time.time(), (columns, rows))
