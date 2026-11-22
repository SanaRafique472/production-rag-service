"""BM25 keyword vectors computed locally.

BM25 scores a chunk for a query by summing, over query words, ``IDF(word) x
TF-part(word, chunk)``. We compute the TF part here (with the usual k1/b length
normalisation) and store it as a sparse vector: index = hash of the word,
value = TF part. Qdrant applies the IDF part itself (``Modifier.IDF``), so the
dot product of a query vector (1.0 per query word) with a chunk vector is the
BM25 score. No model download is needed, and it works for any language.
"""

import hashlib
import re
from collections import Counter

_WORD = re.compile(r"\w+", re.UNICODE)
_STOP = frozenset(
    "a an and are as at be by for from has have in is it its of on or that the this to was were will with".split()
)
K1, B, AVG_LEN = 1.2, 0.75, 250.0


def tokenize(text: str) -> list[str]:
    """Lower-case word tokens without very common English stop words."""
    return [w for w in _WORD.findall(text.lower()) if w not in _STOP and len(w) > 1]


def _index(word: str) -> int:
    return int(hashlib.md5(word.encode()).hexdigest()[:8], 16)  # stable 32-bit id


def doc_sparse(text: str) -> tuple[list[int], list[float]]:
    """Sparse BM25 term-frequency vector for a chunk."""
    words = tokenize(text)
    counts = Counter(words)
    norm = K1 * (1 - B + B * len(words) / AVG_LEN)
    merged: dict[int, float] = {}
    for word, tf in counts.items():
        idx = _index(word)
        merged[idx] = merged.get(idx, 0.0) + tf * (K1 + 1) / (tf + norm)
    return list(merged), list(merged.values())


def query_sparse(text: str) -> tuple[list[int], list[float]]:
    """Sparse query vector: weight 1.0 for each distinct query word."""
    idx = sorted({_index(w) for w in tokenize(text)})
    return idx, [1.0] * len(idx)
