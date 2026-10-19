"""Local BM25 RAG over structural code/document chunks; no embedding API."""
import math
import re
from collections import Counter

from index import STRUCTURAL, нарезать


def terms(text):
    # Split snake_case and retain Unicode identifiers and words.
    return re.findall(r"[^\W_]{2,}", text.casefold())


def retrieve(documents, query, k=5):
    chunks = [chunk for document in documents for chunk in нарезать(document, STRUCTURAL)]
    if not chunks or k <= 0:
        return []
    counters = [Counter(terms(c.source + ' ' + c.section + ' ' + c.text)) for c in chunks]
    frequencies = Counter(term for counts in counters for term in counts)
    average_length = sum(sum(counts.values()) for counts in counters) / len(chunks) or 1
    query_terms = set(terms(query))
    scored = []
    for chunk, counts in zip(chunks, counters):
        length = sum(counts.values())
        score = 0.0
        for term in query_terms & counts.keys():
            frequency = counts[term]
            idf = math.log(1 + (len(chunks) - frequencies[term] + .5) / (frequencies[term] + .5))
            score += idf * frequency * 2.2 / (frequency + 1.2 * (.25 + .75 * length / average_length))
        if score > 0:
            scored.append((chunk, score))
    scored.sort(key=lambda item: (-item[1], item[0].source, item[0].n))
    return scored[:k]
