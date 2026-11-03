"""Solvers for balanced 2-way partitioning.

Every solver returns a `SolverResult` whose `cut` comes from `true_cut` and whose
`balanced` comes from `is_balanced` -- never from the QUBO energy -- so all
solvers are scored by the same classical ruler (CLAUDE.md rule).

Assignments are lists of 0/1 in `hg.nodes` order (0 = side A, 1 = side B).
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from itertools import combinations

import networkx as nx
import numpy as np

from project_q.parse_yosys import DEFAULT_EPS, Hypergraph, balance_bound, is_balanced, true_cut
from project_q.qubo import QUBO, qubo_energies, qubo_energy

BRUTE_FORCE_MAX_N = 20  # routine cap: 2^20 ≈ 1M assignments, well under a second
BRUTE_FORCE_HARD_CAP = 24  # CLAUDE.md: never above 24 qubits / variables


@dataclass
class SolverResult:
    solver: str
    assignment: list[int]
    cut: int  # true hyperedge cut, recomputed classically
    balanced: bool  # hard balance rule, recomputed classically
    energy: float | None = None  # QUBO energy of `assignment`, if a QUBO was given
    runtime_s: float = 0.0
    info: dict = field(default_factory=dict)


def _result(solver, hg, assignment, qubo, eps, t0, **info) -> SolverResult:
    assignment = [int(b) for b in assignment]
    return SolverResult(
        solver=solver,
        assignment=assignment,
        cut=true_cut(hg, assignment),
        balanced=is_balanced(hg, assignment, eps),
        energy=qubo_energy(qubo, assignment) if qubo is not None else None,
        runtime_s=time.perf_counter() - t0,
        info=info,
    )


# --------------------------------------------------------------------------- brute force


@dataclass
class Enumeration:
    """Every assignment of n variables. Row k has bit i = (k >> i) & 1 for node i."""

    cuts: np.ndarray  # true cut per assignment
    balanced: np.ndarray  # hard balance rule per assignment
    energies: np.ndarray  # QUBO energy per assignment

    def bits(self, k: int, n: int) -> list[int]:
        return [(int(k) >> i) & 1 for i in range(n)]


def _check_size(n: int, max_n: int, allow_large: bool) -> None:
    if n > BRUTE_FORCE_HARD_CAP:
        raise ValueError(f"brute force refuses n={n} > {BRUTE_FORCE_HARD_CAP} (hard cap)")
    if n > max_n and not allow_large:
        raise ValueError(f"brute force on n={n} > max_n={max_n}; pass allow_large=True (≤ {BRUTE_FORCE_HARD_CAP})")


def enumerate_all(
    hg: Hypergraph,
    qubo: QUBO,
    eps: float = DEFAULT_EPS,
    max_n: int = BRUTE_FORCE_MAX_N,
    allow_large: bool = False,
    chunk_bits: int = 16,
) -> Enumeration:
    """True cut, balance and QUBO energy of all 2^n assignments, vectorized with numpy.

    Assignments are generated in chunks of 2^chunk_bits rows so the 0/1 matrix
    stays small (2^16 × 24 bytes ≈ 1.5 MB) even when n is 24.
    """
    n = hg.num_nodes
    if qubo.nodes != hg.nodes:
        raise ValueError("QUBO variable order does not match hypergraph node order")
    _check_size(n, max_n, allow_large)

    index = {node: i for i, node in enumerate(hg.nodes)}
    net_idx = [np.array(sorted(index[v] for v in net)) for net in hg.nets]
    w = np.array([hg.weights[v] for v in hg.nodes])
    bound = balance_bound(hg, eps)
    shifts = np.arange(n)

    total = 1 << n
    cuts = np.zeros(total, dtype=np.int32)
    balanced = np.zeros(total, dtype=bool)
    energies = np.zeros(total)
    step = 1 << min(chunk_bits, n)
    for start in range(0, total, step):
        k = np.arange(start, start + step, dtype=np.int64)
        x = ((k[:, None] >> shifts) & 1).astype(np.int8)  # shape (step, n)
        chunk_cut = np.zeros(step, dtype=np.int32)
        for idx in net_idx:
            # A net is cut iff some but not all of its nodes are on side B.
            on_b = x[:, idx].sum(axis=1)
            chunk_cut += (on_b > 0) & (on_b < len(idx))
        w_b = x @ w
        cuts[start : start + step] = chunk_cut
        balanced[start : start + step] = np.maximum(w_b, w.sum() - w_b) <= bound
        energies[start : start + step] = qubo_energies(qubo, x)
    return Enumeration(cuts=cuts, balanced=balanced, energies=energies)


@dataclass
class BruteForceResult:
    best: SolverResult  # a best balanced assignment by true cut
    best_cut: int
    n_optimal: int  # number of balanced assignments achieving best_cut (counts both labelings)
    energy_argmin: list[int]  # lowest-QUBO-energy assignment
    min_energy: float
    argmin_is_optimal: bool  # every minimum-energy assignment is a balanced optimum
    energy_gap: float  # min E over non-optimal − max E over optimal (> 0 ⇔ optima strictly lowest)
    unbalanced_margin: float  # min E over unbalanced − max E over optimal
    enumeration: Enumeration


def brute_force(
    hg: Hypergraph,
    qubo: QUBO,
    eps: float = DEFAULT_EPS,
    max_n: int = BRUTE_FORCE_MAX_N,
    allow_large: bool = False,
) -> BruteForceResult:
    """Exact answer by checking every assignment. The ground truth for small problems.

    Energy gap: how far the best non-optimal assignment sits above the optima.
    A positive gap means the QUBO's lowest energy states are exactly the
    balanced minimum-cut splits, i.e. the QUBO encodes the right problem.
    """
    t0 = time.perf_counter()
    en = enumerate_all(hg, qubo, eps, max_n=max_n, allow_large=allow_large)
    n = hg.num_nodes
    if not en.balanced.any():
        raise ValueError("no balanced assignment exists for this eps")

    best_cut = int(en.cuts[en.balanced].min())
    optimal = en.balanced & (en.cuts == best_cut)
    k_best = int(np.flatnonzero(optimal)[0])
    k_emin = int(np.argmin(en.energies))
    min_e = float(en.energies[k_emin])
    max_opt_e = float(en.energies[optimal].max())
    non_opt = en.energies[~optimal]
    unbal = en.energies[~en.balanced]
    tol = 1e-9
    best = _result("brute", hg, en.bits(k_best, n), qubo, eps, t0)
    return BruteForceResult(
        best=best,
        best_cut=best_cut,
        n_optimal=int(optimal.sum()),
        energy_argmin=en.bits(k_emin, n),
        min_energy=min_e,
        argmin_is_optimal=bool(optimal[en.energies <= min_e + tol].all()),
        energy_gap=float(non_opt.min() - max_opt_e) if non_opt.size else float("inf"),
        unbalanced_margin=float(unbal.min() - max_opt_e) if unbal.size else float("inf"),
        enumeration=en,
    )


# --------------------------------------------------------------------------- random


def random_balanced(
    hg: Hypergraph, qubo: QUBO | None = None, eps: float = DEFAULT_EPS, samples: int = 1, seed: int = 0
) -> SolverResult:
    """The floor every real solver must beat: random balanced split(s), keep the best cut.

    Shuffle the nodes, then drop each one onto the currently lighter side. With
    unit weights this gives a uniformly random half/half split.
    """
    t0 = time.perf_counter()
    rng = random.Random(seed)
    best = None
    for _ in range(samples):
        for _attempt in range(100):
            order = list(range(hg.num_nodes))
            rng.shuffle(order)
            bits = [0] * hg.num_nodes
            w = [0, 0]
            for i in order:
                if w[0] == w[1]:
                    side = rng.randint(0, 1)
                else:
                    side = 0 if w[0] < w[1] else 1
                bits[i] = side
                w[side] += hg.weights[hg.nodes[i]]
            if is_balanced(hg, bits, eps):
                break
        else:
            raise RuntimeError("could not draw a balanced split; node weights too uneven for eps")
        c = true_cut(hg, bits)
        if best is None or c < best[0]:
            best = (c, bits)
    return _result("random", hg, best[1], qubo, eps, t0, samples=samples, seed=seed)


# --------------------------------------------------------------------------- simulated annealing


def simulated_annealing(
    hg: Hypergraph,
    qubo: QUBO,
    eps: float = DEFAULT_EPS,
    sweeps: int = 500,
    restarts: int = 4,
    t_end: float = 0.05,
    seed: int = 0,
) -> SolverResult:
    """Classical annealing on the QUBO energy: the natural classical rival to QAOA.

    Single-bit-flip Metropolis moves with a geometric temperature schedule from a
    hot start (accepts almost anything) down to `t_end` (accepts almost only
    improvements). Like QAOA, the answer is the lowest-energy *balanced* state
    visited; its cut is then recomputed classically.
    """
    t0 = time.perf_counter()
    rng = np.random.default_rng(seed)
    q = qubo.to_matrix()
    diag = np.diag(q).copy()
    off = q + q.T
    np.fill_diagonal(off, 0.0)  # symmetric pair couplings: E = Σ diag·x + ½ xᵀ·off·x
    n = qubo.num_vars
    w = np.array([hg.weights[v] for v in hg.nodes])
    total_w, bound = w.sum(), balance_bound(hg, eps)
    # Hot enough that the largest possible single-flip change is usually accepted.
    t_start = max(1.0, float(np.abs(diag).max() + np.abs(off).sum(axis=1).max()))
    temps = np.geomspace(t_start, t_end, sweeps)

    best_bal: tuple[float, np.ndarray] | None = None
    best_any: tuple[float, np.ndarray] | None = None
    for _ in range(restarts):
        x = rng.integers(0, 2, n).astype(float)
        field_ = off @ x  # local field h_i = Σ_j off_ij x_j
        energy = qubo_energy(qubo, x.astype(int).tolist())
        w_b = float(w @ x)
        for t in temps:
            for i in rng.permutation(n):
                # Flipping x_i changes E by (1 − 2x_i)·(diag_i + h_i).
                delta = (1 - 2 * x[i]) * (diag[i] + field_[i])
                if delta <= 0 or rng.random() < np.exp(-delta / t):
                    step = 1 - 2 * x[i]
                    x[i] += step
                    field_ += off[:, i] * step
                    w_b += w[i] * step
                    energy += delta
                    if best_any is None or energy < best_any[0] - 1e-12:
                        best_any = (energy, x.copy())
                    if max(w_b, total_w - w_b) <= bound and (best_bal is None or energy < best_bal[0] - 1e-12):
                        best_bal = (energy, x.copy())
    chosen = best_bal if best_bal is not None else best_any
    return _result(
        "sa",
        hg,
        chosen[1].astype(int).tolist(),
        qubo,
        eps,
        t0,
        found_balanced=best_bal is not None,
        lowest_energy_seen=float(best_any[0]),
        sweeps=sweeps,
        restarts=restarts,
        seed=seed,
    )


# --------------------------------------------------------------------------- Kernighan-Lin


def clique_expansion(hg: Hypergraph) -> nx.Graph:
    """Hypergraph -> ordinary weighted graph: each net becomes all its node pairs, weight 1/(n−1).

    Same pair weighting as the QUBO, so a 2- or 3-node net contributes exactly 1
    to the graph cut when it is cut.
    """
    g = nx.Graph()
    g.add_nodes_from(hg.nodes)
    for net in hg.nets:
        wgt = 1.0 / (len(net) - 1)
        for a, b in combinations(sorted(net), 2):
            prev = g.get_edge_data(a, b, {"weight": 0.0})["weight"]
            g.add_edge(a, b, weight=prev + wgt)
    return g


def kernighan_lin(
    hg: Hypergraph, qubo: QUBO | None = None, eps: float = DEFAULT_EPS, seed: int = 0, max_iter: int = 10
) -> SolverResult:
    """Classic graph-bisection heuristic (networkx) on the clique-expanded graph.

    Caveat: networkx KL balances node *counts*, not node weights, so on weighted
    supernodes its split may fail `is_balanced`; the result reports that honestly.
    """
    t0 = time.perf_counter()
    side_a, _ = nx.algorithms.community.kernighan_lin_bisection(
        clique_expansion(hg), max_iter=max_iter, weight="weight", seed=seed
    )
    bits = [0 if v in side_a else 1 for v in hg.nodes]
    return _result("kl", hg, bits, qubo, eps, t0, seed=seed, max_iter=max_iter)
