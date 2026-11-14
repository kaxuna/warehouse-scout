"""Text embedders behind one interface.

An embedder turns a text into a list of numbers (a vector) such that texts
with similar meaning get vectors pointing in similar directions. Search is
then "find the stored vectors closest to the question's vector".

Two implementations:
    FastEmbedEmbedder  a small neural model that runs locally; no API key
    HashEmbedder       no model at all: counts words. Used in unit tests, and
                       as the "is the neural model worth it?" baseline in evals

A hosted embedder (Voyage, for example) would be a third class with the same
three members.
"""

import hashlib
import math
import re
from collections.abc import Sequence
from typing import Protocol

Vector = list[float]


class Embedder(Protocol):
    name: str  # stored with every vector, so vectors of different models never mix
    dimensions: int

    def embed(self, texts: Sequence[str]) -> list[Vector]:
        """One vector per text, each of length `dimensions` and of unit length."""
        ...


class FastEmbedEmbedder:
    """BAAI/bge-small-en-v1.5 through fastembed (ONNX runtime, CPU)."""

    dimensions = 384

    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5") -> None:
        # Imported here, not at the top of the file: loading the library and
        # the model takes seconds, and code that never embeds should not pay.
        from fastembed import TextEmbedding

        self.name = f"fastembed:{model_name}"
        self._model = TextEmbedding(model_name)

    def embed(self, texts: Sequence[str]) -> list[Vector]:
        return [vector.tolist() for vector in self._model.embed(list(texts))]


class HashEmbedder:
    """Bag of words: each word adds 1 to a slot chosen by hashing the word.

    Two texts are close when they share words. No notion of meaning: "price"
    and "cost" are unrelated to it. Deterministic and instant.
    """

    name = "hash-bag-of-words"

    def __init__(self, dimensions: int = 256) -> None:
        self.dimensions = dimensions

    def embed(self, texts: Sequence[str]) -> list[Vector]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> Vector:
        vector = [0.0] * self.dimensions
        for word in re.findall(r"[a-z0-9]+", text.lower()):
            # hashlib, not hash(): Python's built-in hash of a string changes
            # between runs, and stored vectors must stay comparable.
            digest = hashlib.md5(word.encode(), usedforsecurity=False).digest()
            vector[int.from_bytes(digest[:4]) % self.dimensions] += 1.0
        length = math.sqrt(sum(value * value for value in vector))
        return [value / length for value in vector] if length else vector


def make_embedder(kind: str) -> Embedder:
    match kind:
        case "fastembed":
            return FastEmbedEmbedder()
        case "hash":
            return HashEmbedder()
        case _:
            raise ValueError(f"Unknown embedder {kind!r}; expected 'fastembed' or 'hash'")
