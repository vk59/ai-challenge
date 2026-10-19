"""Small product-only RAG corpus: shared vector index or explicit lexical demo."""

import hashlib
import json
import math
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = Path(os.environ.get("AI_ADVENT_SUPPORT_CACHE", str(HERE.parents[1] / "memory/day33-support")))
os.environ.setdefault("AI_ADVENT_MEMORY_DIR", str(CACHE))
sys.path.insert(0, str(HERE.parents[1] / "shared"))

from index import Document, Index, STRUCTURAL, нарезать
from embeddings import бэкенд, модель_бэкенда, размерность_бэкенда


def terms(text):
    return set(re.findall(r"[a-zа-яё0-9_]{3,}", text.lower()))


class Knowledge:
    def __init__(self, mode="cloud", docs=None, cache=None):
        self.mode = mode
        self.cache = Path(cache or CACHE)
        self.documents = []
        for path in sorted(Path(docs or HERE / "docs").glob("*.md")):
            text = path.read_text(encoding="utf-8")
            self.documents.append(Document("docs/" + path.name, path.stem, "markdown", text))
        if not self.documents:
            raise ValueError("Product documentation is empty")
        self.chunks = [chunk for doc in self.documents for chunk in нарезать(doc, STRUCTURAL)]
        self.index = None

    def _index(self):
        if self.index is not None:
            return self.index
        self.cache.mkdir(parents=True, exist_ok=True)
        backend = бэкенд()
        identity = json.dumps({"backend": backend, "model": модель_бэкенда(backend),
                               "dimensions": размерность_бэкенда(backend),
                               "strategy": STRUCTURAL}, sort_keys=True)
        tag = hashlib.sha256(identity.encode()).hexdigest()[:16]
        fingerprint = hashlib.sha256("\n".join(
            doc.path + "\n" + doc.text for doc in self.documents).encode()).hexdigest()
        marker = self.cache / f"corpus-{tag}.sha256"
        index = Index(self.cache / f"support-{tag}.sqlite")
        if (not marker.exists() or marker.read_text() != fingerprint
                or not index.stats(STRUCTURAL)["chunks"]):
            index.rebuild(self.documents, STRUCTURAL)
            marker.write_text(fingerprint, encoding="utf-8")
        self.index = index
        return index

    def search(self, query):
        query_terms = terms(query)
        codes = set(re.findall(r"\b[A-Z]+(?:_[A-Z]+)+\b", query))
        if self.mode == "demo":
            count = len(self.chunks)
            frequencies = {term: sum(term in terms(c.text) for c in self.chunks)
                           for term in query_terms}
            scored = []
            for chunk in self.chunks:
                common = query_terms & terms(chunk.text)
                score = sum(math.log(1 + count / (1 + frequencies[t])) for t in common)
                if any(code in chunk.text for code in codes):
                    score += 20
                if score > 0:
                    scored.append((chunk, score))
        else:
            scored = [(c, score) for c, score in self._index().search(query, STRUCTURAL, k=5)
                      if score >= 0.30]
            # Exact CRM diagnostic codes outrank a semantically similar generic FAQ.
            scored = [(c, s + (1 if any(code in c.text for code in codes) else 0))
                      for c, s in scored]
        scored.sort(key=lambda item: item[1], reverse=True)
        return [{"source": c.source, "section": c.section, "text": c.text,
                 "score": round(score, 3)} for c, score in scored[:3]]
