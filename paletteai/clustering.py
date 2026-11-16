"""Dominant-colour extraction: K-Means in CIELAB with silhouette-based k selection.

Implemented directly on NumPy (no scikit-learn) so the behaviour is fully ours
and easy to explain: k-means++ seeding, a few restarts, a bounded range of k,
and a safe fallback when the silhouette score cannot tell the candidates apart.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

AUTO_K_RANGE = range(3, 9)       # candidates tried in automatic mode
FALLBACK_K = 5                   # used when silhouette is inconclusive
MIN_CONFIDENT_SILHOUETTE = 0.40  # below this no clustering is convincing (uniform noise scores 0.29-0.35)
MERGE_DELTA_E = 5.0              # auto mode merges clusters closer than this (near-identical colours)
MIN_SILHOUETTE_SPREAD = 0.02     # candidates this close are indistinguishable
MANUAL_K_MIN, MANUAL_K_MAX = 2, 10


@dataclass
class ClusterResult:
    centers: np.ndarray            # (k, 3) Lab
    shares: np.ndarray             # (k,) fractions summing to 1, sorted descending
    k: int
    method: str                    # "auto" | "manual"
    silhouette: float | None
    k_scores: dict[int, float] = field(default_factory=dict)
    fallback_used: bool = False
    reason: str = ""


def _sq_dists(X: np.ndarray, C: np.ndarray) -> np.ndarray:
    return ((X[:, None, :] - C[None, :, :]) ** 2).sum(-1)


def _init_plus_plus(X: np.ndarray, k: int, rng: np.random.Generator) -> np.ndarray:
    centers = [X[rng.integers(len(X))]]
    d = ((X - centers[0]) ** 2).sum(1)
    for _ in range(1, k):
        total = d.sum()
        idx = rng.integers(len(X)) if total <= 1e-12 else rng.choice(len(X), p=d / total)
        centers.append(X[idx])
        d = np.minimum(d, ((X - X[idx]) ** 2).sum(1))
    return np.array(centers, dtype=np.float64)


def kmeans(X: np.ndarray, k: int, rng: np.random.Generator, n_init: int = 3, max_iter: int = 40):
    """Lloyd's algorithm. Returns ``(centers, labels, inertia)`` of the best restart."""
    best = None
    for _ in range(n_init):
        C = _init_plus_plus(X, k, rng)
        for _ in range(max_iter):
            d = _sq_dists(X, C)
            labels = d.argmin(1)
            new_C = C.copy()
            for j in range(k):
                members = labels == j
                if members.any():
                    new_C[j] = X[members].mean(0)
                else:  # re-seed an empty cluster at the worst-fitted point
                    new_C[j] = X[d.min(1).argmax()]
            converged = np.allclose(new_C, C, atol=1e-3)
            C = new_C
            if converged:
                break
        d = _sq_dists(X, C)
        labels = d.argmin(1)
        inertia = float(d.min(1).sum())
        if best is None or inertia < best[2]:
            best = (C, labels, inertia)
    return best


def silhouette_score(X: np.ndarray, labels: np.ndarray, rng: np.random.Generator, max_n: int = 1000) -> float:
    """Mean silhouette coefficient, computed on a subsample to stay bounded."""
    if len(X) > max_n:
        idx = rng.choice(len(X), max_n, replace=False)
        X, labels = X[idx], labels[idx]
    ids = np.unique(labels)
    if len(ids) < 2:
        return 0.0
    D = np.sqrt(np.maximum(_sq_dists(X, X), 0.0))
    mean_to = np.empty((len(X), len(ids)))
    own = np.empty(len(X))
    sizes = np.empty(len(ids))
    for col, cid in enumerate(ids):
        members = labels == cid
        sizes[col] = members.sum()
        mean_to[:, col] = D[:, members].mean(1)
    col_of = np.searchsorted(ids, labels)
    rows = np.arange(len(X))
    n_own = sizes[col_of]
    # a(i): mean distance to the *other* members of the own cluster
    own = np.where(n_own > 1, mean_to[rows, col_of] * n_own / np.maximum(n_own - 1, 1), 0.0)
    other = mean_to.copy()
    other[rows, col_of] = np.inf
    b = other.min(1)
    denom = np.maximum(own, b)
    s = np.where((n_own > 1) & (denom > 0), (b - own) / np.where(denom > 0, denom, 1), 0.0)
    return float(s.mean())


def _merge_close(centers: np.ndarray, counts: np.ndarray, threshold: float):
    """Greedily merge the closest pair of centres until all are ``threshold`` apart."""
    centers, counts = centers.copy(), counts.copy()
    while len(centers) > 1:
        d = np.sqrt(_sq_dists(centers, centers))
        np.fill_diagonal(d, np.inf)
        i, j = np.unravel_index(d.argmin(), d.shape)
        if d[i, j] >= threshold:
            break
        total = counts[i] + counts[j]
        centers[i] = (centers[i] * counts[i] + centers[j] * counts[j]) / total
        counts[i] = total
        centers, counts = np.delete(centers, j, axis=0), np.delete(counts, j)
    return centers, counts


def _finish(X, centers, labels, merge: bool = False, **kw) -> ClusterResult:
    counts = np.bincount(labels, minlength=len(centers)).astype(np.float64)
    keep = counts > 0
    centers, counts = centers[keep], counts[keep]
    if merge:
        before = len(centers)
        centers, counts = _merge_close(centers, counts, MERGE_DELTA_E)
        if len(centers) < before:
            kw["reason"] = kw.get("reason", "") + f" Merged {before - len(centers)} near-identical colour(s)."
    order = np.argsort(-counts, kind="stable")
    return ClusterResult(centers=centers[order], shares=counts[order] / counts.sum(), k=len(centers), **kw)


def extract_clusters(X: np.ndarray, requested_k: int | None = None, seed: int = 0) -> ClusterResult:
    """Cluster Lab pixels ``X`` (n, 3) into dominant colours.

    ``requested_k=None`` selects k automatically from ``AUTO_K_RANGE`` by
    silhouette score, falling back to ``FALLBACK_K`` when inconclusive.
    """
    X = np.asarray(X, dtype=np.float64)
    rng = np.random.default_rng(seed)
    distinct = np.unique(np.round(X, 1), axis=0)
    n_distinct = len(distinct)

    # Images with very few distinct colours: the colours *are* the palette.
    if n_distinct <= 2 or (requested_k is None and n_distinct <= AUTO_K_RANGE.start):
        labels = _sq_dists(X, distinct).argmin(1)
        return _finish(
            X, distinct, labels, merge=requested_k is None,
            method="manual" if requested_k else "auto", silhouette=None,
            reason=f"Image contains only {n_distinct} distinct colour(s); used them directly.",
        )

    if requested_k is not None:
        k = max(MANUAL_K_MIN, min(requested_k, MANUAL_K_MAX, n_distinct))
        centers, labels, _ = kmeans(X, k, rng)
        score = silhouette_score(X, labels, rng)
        note = "" if k == requested_k else f" (reduced from {requested_k}: image has only {n_distinct} distinct colours)"
        return _finish(X, centers, labels, method="manual", silhouette=round(score, 4),
                       k_scores={k: round(score, 4)}, reason=f"Colour count set manually to {k}{note}.")

    runs = {}
    for k in AUTO_K_RANGE:
        if k > n_distinct:
            break
        centers, labels, _ = kmeans(X, k, rng)
        runs[k] = (centers, labels, silhouette_score(X, labels, rng))
    scores = {k: round(v[2], 4) for k, v in runs.items()}
    best_k = max(runs, key=lambda k: runs[k][2])
    best = runs[best_k][2]
    spread = best - min(v[2] for v in runs.values())

    fallback = False
    if best < MIN_CONFIDENT_SILHOUETTE or (len(runs) > 1 and spread < MIN_SILHOUETTE_SPREAD):
        fallback = True
        chosen = FALLBACK_K if FALLBACK_K in runs else max(runs)
        why = (
            f"best silhouette {best:.2f} is below {MIN_CONFIDENT_SILHOUETTE}"
            if best < MIN_CONFIDENT_SILHOUETTE
            else f"silhouette scores differ by only {spread:.3f}"
        )
        reason = f"Silhouette was inconclusive ({why}); fell back to {chosen} colours."
    else:
        chosen = best_k
        reason = f"Chose {chosen} colours: highest silhouette score ({best:.2f}) among k = {min(runs)}-{max(runs)}."
    centers, labels, score = runs[chosen]
    return _finish(X, centers, labels, merge=True, method="auto", silhouette=round(score, 4),
                   k_scores=scores, fallback_used=fallback, reason=reason)
