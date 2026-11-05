"""Multilevel partitioning: coarsen → solve small core → uncoarsen + FM refine.

Why this exists
---------------
One qubit per gate caps us at ~24 gates on Aer. Real netlists (c432 ≈ 187
gates) are far bigger, so we borrow the industry multilevel trick:

1. **Coarsen (zoom out):** repeatedly merge pairs of gates that share the most
   nets into weighted *supernodes*, until ≤ `target` remain.
2. **Solve:** run a small-core solver (QAOA / SA / random / brute) on the
   coarse hypergraph (≤ 24 variables → qubits).
3. **Uncoarsen + FM (zoom in):** project the split back one level at a time
   and run Fiduccia–Mattheyses: try moving one node across the cut if that
   lowers `true_cut` without breaking balance.

The coarse solver only sees supernodes; FM does the fine polishing. Phase 6's
ablation checks whether QAOA at the core actually helps vs random+FM.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from project_q.parse_yosys import DEFAULT_EPS, Hypergraph, is_balanced, true_cut
from project_q.qubo import DEFAULT_LAMBDA, QUBO, build_partition_qubo
from project_q.solvers import (
    BRUTE_FORCE_MAX_N,
    QAOA_MAX_QUBITS,
    SolverResult,
    _result,
    brute_force,
    qaoa,
    random_balanced,
    simulated_annealing,
)

DEFAULT_TARGET = 20


@dataclass
class CoarsenLevel:
    """One matching step: fine hypergraph → coarse hypergraph + child→parent map."""

    fine: Hypergraph
    coarse: Hypergraph
    parent: dict[str, str]  # fine node name → coarse supernode name


@dataclass
class CoarsenHierarchy:
    """`levels[0]` is the first coarsening of the original; `levels[-1].coarse` is smallest."""

    original: Hypergraph
    levels: list[CoarsenLevel] = field(default_factory=list)

    @property
    def coarsest(self) -> Hypergraph:
        return self.levels[-1].coarse if self.levels else self.original


def _edge_weight(hg: Hypergraph, a: str, b: str) -> float:
    """Heavy-edge score: sum of 1/(|net|-1) over nets containing both a and b."""
    score = 0.0
    for net in hg.nets:
        if a in net and b in net:
            score += 1.0 / (len(net) - 1)
    return score


def heavy_edge_matching(hg: Hypergraph) -> dict[str, str]:
    """Greedy matching: pair unmatched nodes that share the heaviest edge.

    Returns fine_node → pair representative (lexicographically smaller name, or
    the node itself if left unmatched). Roughly halves the node count each call.
    """
    pairs: list[tuple[float, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for net in hg.nets:
        members = sorted(net)
        for i, a in enumerate(members):
            for b in members[i + 1 :]:
                key = (a, b)
                if key in seen:
                    continue
                seen.add(key)
                w = _edge_weight(hg, a, b)
                if w > 0:
                    pairs.append((w, a, b))
    pairs.sort(key=lambda t: (-t[0], t[1], t[2]))

    matched: set[str] = set()
    parent: dict[str, str] = {}
    for _, a, b in pairs:
        if a in matched or b in matched:
            continue
        rep = a if a <= b else b
        parent[a] = rep
        parent[b] = rep
        matched.add(a)
        matched.add(b)
    for n in hg.nodes:
        parent.setdefault(n, n)
    return parent


def _contract(hg: Hypergraph, match: dict[str, str], level_idx: int) -> tuple[Hypergraph, dict[str, str]]:
    """Build coarse hypergraph from a matching. Returns (coarse, fine → supernode)."""
    groups: dict[str, list[str]] = {}
    for n in hg.nodes:
        groups.setdefault(match[n], []).append(n)

    fine_to_super: dict[str, str] = {}
    super_nodes: list[str] = []
    super_weights: dict[str, int] = {}
    for i, members in enumerate(groups.values()):
        name = members[0] if len(members) == 1 else f"S{level_idx}_{i}"
        super_nodes.append(name)
        super_weights[name] = sum(hg.weights[m] for m in members)
        for m in members:
            fine_to_super[m] = name

    coarse_nets: list[frozenset[str]] = []
    coarse_names: list[str] = []
    for net, nname in zip(hg.nets, hg.net_names):
        mapped = frozenset(fine_to_super[n] for n in net)
        if len(mapped) >= 2:
            coarse_nets.append(mapped)
            coarse_names.append(nname)

    coarse = Hypergraph(
        nodes=super_nodes, nets=coarse_nets, net_names=coarse_names, weights=super_weights
    )
    return coarse, fine_to_super


def coarsen(hg: Hypergraph, target: int = DEFAULT_TARGET) -> CoarsenHierarchy:
    """Repeated heavy-edge matching until ≤ `target` supernodes (or no progress)."""
    if target < 2:
        raise ValueError(f"target must be ≥ 2, got {target}")
    hier = CoarsenHierarchy(original=hg)
    current = hg
    level_idx = 0
    while current.num_nodes > target:
        match = heavy_edge_matching(current)
        coarse, fine_to_super = _contract(current, match, level_idx)
        if coarse.num_nodes >= current.num_nodes:
            break
        hier.levels.append(CoarsenLevel(fine=current, coarse=coarse, parent=fine_to_super))
        current = coarse
        level_idx += 1
    return hier


def project_assignment(level: CoarsenLevel, coarse_bits: list[int]) -> list[int]:
    """Lift a coarse 0/1 assignment onto the fine hypergraph (same side as parent)."""
    side = dict(zip(level.coarse.nodes, coarse_bits))
    return [side[level.parent[n]] for n in level.fine.nodes]


def fm_refine(
    hg: Hypergraph,
    assignment: list[int],
    eps: float = DEFAULT_EPS,
    max_passes: int = 10,
) -> list[int]:
    """Fiduccia–Mattheyses: single-node moves that cut `true_cut` without breaking balance.

    One pass locks each node after trying at most one flip. Accepts a move only
    if the new cut is strictly smaller and `is_balanced` still holds.
    """
    bits = [int(b) for b in assignment]
    if len(bits) != hg.num_nodes:
        raise ValueError("assignment length must match hypergraph")

    for _ in range(max_passes):
        improved = False
        locked = [False] * hg.num_nodes
        base_cut = true_cut(hg, bits)
        order = sorted(
            range(hg.num_nodes),
            key=lambda i: -sum(1 for net in hg.nets if hg.nodes[i] in net),
        )
        for i in order:
            if locked[i]:
                continue
            cand = list(bits)
            cand[i] = 1 - cand[i]
            locked[i] = True
            if not is_balanced(hg, cand, eps):
                continue
            new_cut = true_cut(hg, cand)
            if new_cut < base_cut:
                bits = cand
                base_cut = new_cut
                improved = True
        if not improved:
            break
    return bits


def uncoarsen_with_fm(
    hier: CoarsenHierarchy,
    coarse_assignment: list[int],
    eps: float = DEFAULT_EPS,
) -> list[int]:
    """Project coarse → original, running FM at every level."""
    if not hier.levels:
        return fm_refine(hier.original, coarse_assignment, eps=eps)

    bits = list(coarse_assignment)
    for level in reversed(hier.levels):
        bits = project_assignment(level, bits)
        bits = fm_refine(level.fine, bits, eps=eps)
    return bits


def _run_coarse_solver(
    name: str,
    hg: Hypergraph,
    qubo: QUBO,
    eps: float,
    seed: int,
    p: int,
    shots: int,
    restarts: int,
) -> SolverResult:
    if name == "brute":
        return brute_force(hg, qubo, eps=eps).best
    if name == "random":
        return random_balanced(hg, qubo, eps=eps, seed=seed)
    if name == "sa":
        return simulated_annealing(hg, qubo, eps=eps, seed=seed)
    if name == "qaoa":
        return qaoa(hg, qubo, p=p, shots=shots, restarts=restarts, eps=eps, seed=seed)
    raise ValueError(f"unknown coarse solver {name!r}; expected brute|random|sa|qaoa")


def multilevel(
    hg: Hypergraph,
    coarse_solver: str = "qaoa",
    target: int = DEFAULT_TARGET,
    lam: float = DEFAULT_LAMBDA,
    eps: float = DEFAULT_EPS,
    seed: int = 0,
    p: int = 1,
    shots: int = 4000,
    restarts: int = 8,
) -> SolverResult:
    """Full multilevel pipeline. `coarse_solver` ∈ {qaoa, sa, random, brute}."""
    t0 = time.perf_counter()
    if coarse_solver == "qaoa" and target > QAOA_MAX_QUBITS:
        raise ValueError(f"target={target} exceeds QAOA qubit cap {QAOA_MAX_QUBITS}")
    if coarse_solver == "brute" and target > BRUTE_FORCE_MAX_N:
        raise ValueError(f"target={target} exceeds brute-force cap {BRUTE_FORCE_MAX_N}")

    hier = coarsen(hg, target=target) if hg.num_nodes > target else CoarsenHierarchy(original=hg)
    core = hier.coarsest
    qubo = build_partition_qubo(core, lam=lam)
    core_result = _run_coarse_solver(coarse_solver, core, qubo, eps, seed, p, shots, restarts)
    fine_bits = uncoarsen_with_fm(hier, core_result.assignment, eps=eps)

    qubo_orig = build_partition_qubo(hg, lam=lam)
    return _result(
        f"multilevel-{coarse_solver}",
        hg,
        fine_bits,
        qubo_orig,
        eps,
        t0,
        coarse_solver=coarse_solver,
        target=target,
        n_original=hg.num_nodes,
        n_coarse=core.num_nodes,
        n_levels=len(hier.levels),
        coarse_cut=core_result.cut,
        coarse_balanced=core_result.balanced,
        lam=lam,
        seed=seed,
    )
