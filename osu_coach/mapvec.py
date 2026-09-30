"""Map embeddings learned from players' top plays (skip-gram with negative sampling, as word2vec).

Each player's top plays are a "sentence" and each map+mods a "word": two maps in the same
player's top plays are each other's context. Maps played by the same kind of players end up
close together, without reading them. Top plays also group players by level, so difficulty is
expected to be the strongest direction; the kind of map comes after it.
"""

import json
from collections import Counter
from dataclasses import dataclass

import numpy as np

from .locate import CACHE_DIR

MAPVEC_PATH = CACHE_DIR / "mapvec.npz"
MIN_PLAYERS = 5        # a map+mods needs this many players' top plays to get a vector
DIM = 32
NEGATIVES = 5
CONTEXTS = 10          # context maps sampled per map per epoch
EPOCHS = 15
LEARNING_RATE = 0.05
BATCH = 4096


@dataclass
class MapVectors:
    items: list[tuple[int, str]]      # (beatmap id, mods)
    vectors: np.ndarray               # len(items) x DIM, unit length

    def index(self) -> dict[tuple[int, str], int]:
        return {it: i for i, it in enumerate(self.items)}

    def neighbours(self, i: int, k: int = 10) -> list[tuple[int, float]]:
        sims = self.vectors @ self.vectors[i]
        order = np.argsort(-sims)
        return [(int(j), float(sims[j])) for j in order[1:k + 1]]

    def save(self, path=MAPVEC_PATH):
        np.savez_compressed(path, vectors=self.vectors,
                            items=np.array([f"{b}:{m}" for b, m in self.items]))

    @classmethod
    def load(cls, path=MAPVEC_PATH) -> "MapVectors":
        d = np.load(path)
        items = [(int(s.split(":")[0]), s.split(":")[1]) for s in d["items"]]
        return cls(items, d["vectors"])


def sentences(profiles: dict, min_players: int = MIN_PLAYERS) -> tuple[list[tuple[int, str]], list[np.ndarray]]:
    counts = Counter((r["beatmap"], r["mods"]) for rows in profiles["plays"].values() for r in rows)
    items = sorted(it for it, n in counts.items() if n >= min_players)
    index = {it: i for i, it in enumerate(items)}
    sents = []
    for rows in profiles["plays"].values():
        ids = sorted({index[(r["beatmap"], r["mods"])] for r in rows if (r["beatmap"], r["mods"]) in index})
        if len(ids) >= 2:
            sents.append(np.array(ids))
    return items, sents


def train(items: list, sents: list[np.ndarray], dim: int = DIM, epochs: int = EPOCHS, seed: int = 0,
          progress=None) -> MapVectors:
    """Skip-gram with negative sampling, in plain numpy (minibatch SGD)."""
    rng = np.random.default_rng(seed)
    n = len(items)
    w_in = (rng.random((n, dim)) - 0.5) / dim
    w_out = np.zeros((n, dim))
    freq = np.bincount(np.concatenate(sents), minlength=n).astype(float) ** 0.75
    noise = freq / freq.sum()

    def sigmoid(x):
        return 1 / (1 + np.exp(-np.clip(x, -30, 30)))

    for epoch in range(epochs):
        # (target, context) pairs: for every map in a player's top plays, CONTEXTS others from the same player
        centre, context = [], []
        for s in sents:
            reps = min(CONTEXTS, len(s) - 1)
            for _ in range(reps):
                perm = rng.permutation(len(s))
                centre.append(s)
                context.append(s[np.roll(perm, 1)][np.argsort(perm)])  # a different item of the same player
        centre, context = np.concatenate(centre), np.concatenate(context)
        order = rng.permutation(len(centre))
        centre, context = centre[order], context[order]
        lr = LEARNING_RATE * (1 - epoch / epochs) + 1e-4
        loss = 0.0
        for b in range(0, len(centre), BATCH):
            c, o = centre[b:b + BATCH], context[b:b + BATCH]
            neg = rng.choice(n, size=(len(c), NEGATIVES), p=noise)
            vc = w_in[c]                                   # B x D
            pos = sigmoid((vc * w_out[o]).sum(1))          # B
            negs = sigmoid(np.einsum("bd,bkd->bk", vc, w_out[neg]))  # B x K
            loss += float(-np.log(pos + 1e-9).sum() - np.log(1 - negs + 1e-9).sum())
            g_pos = (pos - 1)[:, None]                     # d loss / d score
            g_neg = negs[..., None]
            grad_c = g_pos * w_out[o] + (g_neg * w_out[neg]).sum(1)
            np.add.at(w_out, o, -lr * g_pos * vc)
            np.add.at(w_out, neg, -lr * g_neg * vc[:, None, :])
            np.add.at(w_in, c, -lr * grad_c)
        if progress:
            progress(epoch + 1, epochs, loss / len(centre))
    vectors = w_in / np.linalg.norm(w_in, axis=1, keepdims=True)
    return MapVectors(list(items), vectors)


def load_profiles() -> dict:
    return json.loads((CACHE_DIR / "profiles.json").read_text(encoding="utf-8"))
