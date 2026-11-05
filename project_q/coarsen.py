"""Multilevel partitioning: shrink the circuit, solve it small, grow it back and polish.

EDA background for the team:
- c432 has 187 gates but we can afford about 20 qubits, so QAOA can't see the whole circuit.
- Coarsening merges tightly connected gates into weighted "supernodes" until few remain.
  Gates that share many wires would probably end up on the same side anyway, so merging
  them keeps most good splits available. A supernode's weight is how many gates it holds.
- A solver (QAOA, annealing, ...) splits the small circuit. Each supernode is then opened
  back up, and Fiduccia-Mattheyses (FM) refinement moves single nodes to fix the mistakes
  that merging made. This is the same overall idea hMETIS and KaHyPar use.

The key fact that keeps this honest: a net's cut status can't change when its nodes are
merged, so a coarse split has exactly the same cut as the fine split it stands for.
Nets that end up entirely inside one supernode can never be cut, so they are removed.
Duplicate nets are kept as separate entries, because each one is a real wire.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field

from project_q.parse_yosys import DEFAULT_EPS, Hypergraph, balance_bound, is_balanced, true_cut
from project_q.qubo import DEFAULT_LAMBDA, build_partition_qubo
from project_q.solvers import SolverResult, _result, brute_force, qaoa, random_balanced, simulated_annealing

DEFAULT_TARGET = 20  # BUILD_PLAN: coarsen until at most this many supernodes remain
# A supernode may weigh this many times the average at the target size. Without a limit, one
# huge supernode could make a balanced split impossible. 1.5 leaves room for strongly
# connected groups to merge. It is our choice, not a value from the literature.
WEIGHT_CAP_FACTOR = 1.5
COARSE_SOLVERS = ("qaoa", "sa", "random", "brute")


# --------------------------------------------------------------------------- coarsening


@dataclass
class Coarsening:
    """levels[0] is the input and levels[-1] the smallest; maps[i] sends levels[i] nodes to levels[i+1]."""

    levels: list[Hypergraph]
    maps: list[dict[str, str]]
    weight_cap: int
    cap_relaxed: bool = False  # True if merging stalled and the cap had to rise
    forced_merges: int = 0  # merges of unconnected nodes, made only after a stall

    @property
    def coarsest(self) -> Hypergraph:
        return self.levels[-1]


def _contract(hg: Hypergraph, parent: dict[str, str], coarse_nodes: list[str]) -> Hypergraph:
    """Build the next level: a supernode's weight is the sum of its members' weights."""
    weights = dict.fromkeys(coarse_nodes, 0)
    for node in hg.nodes:
        weights[parent[node]] += hg.weights[node]
    nets, names = [], []
    for net, name in zip(hg.nets, hg.net_names):
        coarse_net = frozenset(parent[n] for n in net)
        if len(coarse_net) >= 2:  # a net inside one supernode can never be cut
            nets.append(coarse_net)
            names.append(name)
    return Hypergraph(nodes=coarse_nodes, nets=nets, net_names=names, weights=weights)


def _matching_pass(
    hg: Hypergraph, target: int, cap: int, rng: random.Random
) -> list[tuple[str, str]]:
    """One round of heavy-edge matching. Returns pairs of nodes to merge (each node at most once).

    A pair's score is Σ 1/(|net|−1) over the nets it shares, the same pair weight the QUBO
    uses. A 2-node net says "these two belong together" far more strongly than a 10-node net.
    Visiting nodes in random order avoids always favouring the same part of the circuit.
    """
    nets_of: dict[str, list[frozenset[str]]] = {n: [] for n in hg.nodes}
    for net in hg.nets:
        for n in sorted(net):  # sorted: frozenset order changes between Python runs
            nets_of[n].append(net)

    order = list(hg.nodes)
    rng.shuffle(order)
    matched: set[str] = set()
    pairs: list[tuple[str, str]] = []
    remaining = hg.num_nodes
    for node in order:
        if remaining <= target:  # stop exactly at the target instead of overshooting it
            break
        if node in matched:
            continue
        scores: dict[str, float] = {}
        for net in nets_of[node]:
            for other in net:
                if other != node and other not in matched:
                    scores[other] = scores.get(other, 0.0) + 1.0 / (len(net) - 1)
        allowed = [o for o in scores if hg.weights[node] + hg.weights[o] <= cap]
        if not allowed:
            continue
        # Highest score wins. Ties go to the lighter partner, which keeps weights even,
        # then to the name, so the choice doesn't depend on dictionary order.
        partner = min(allowed, key=lambda o: (-scores[o], hg.weights[o], o))
        matched |= {node, partner}
        pairs.append((node, partner))
        remaining -= 1
    return pairs


def coarsen(hg: Hypergraph, target: int = DEFAULT_TARGET, seed: int = 0, weight_cap: int | None = None) -> Coarsening:
    """Repeat heavy-edge matching until at most `target` supernodes remain."""
    if target < 2:
        raise ValueError(f"a 2-way split needs at least 2 nodes, got target={target}")
    rng = random.Random(seed)
    if weight_cap is None:
        # ⌈1.5·W/target⌉, but never below the heaviest node, or that node could not exist.
        weight_cap = -(-3 * hg.total_weight // (2 * target))
        weight_cap = max(weight_cap, max(hg.weights.values(), default=1))
    result = Coarsening(levels=[hg], maps=[], weight_cap=weight_cap)

    current = hg
    while current.num_nodes > target:
        pairs = _matching_pass(current, target, result.weight_cap, rng)
        if not pairs:
            # Stall: every connected pair is over the cap. Merge the two lightest nodes, and
            # raise the cap only as far as that needs. Weight is what keeps a balanced split
            # possible, so we protect it before connectivity. Both events are recorded.
            a, b = sorted(current.nodes, key=lambda n: (current.weights[n], n))[:2]
            pairs = [(a, b)]
            result.forced_merges += 1
            needed = current.weights[a] + current.weights[b]
            if needed > result.weight_cap:
                result.weight_cap = needed
                result.cap_relaxed = True

        level = len(result.levels)
        partner = {a: b for pair in pairs for a, b in (pair, pair[::-1])}
        parent: dict[str, str] = {}
        coarse_nodes: list[str] = []
        for node in current.nodes:  # keeps the node order stable, so runs are repeatable
            if node in parent:
                continue
            label = f"L{level}n{len(coarse_nodes)}"
            coarse_nodes.append(label)
            parent[node] = label
            if node in partner:
                parent[partner[node]] = label
        result.maps.append(parent)
        current = _contract(current, parent, coarse_nodes)
        result.levels.append(current)
    return result


def project(assignment: list[int], coarse: Hypergraph, fine: Hypergraph, parent: dict[str, str]) -> list[int]:
    """Open supernodes back up: every member takes its supernode's side. The cut doesn't change."""
    side = dict(zip(coarse.nodes, assignment))
    return [side[parent[n]] for n in fine.nodes]


# --------------------------------------------------------------------------- FM refinement


class _FMState:
    """A split that can change one node at a time, with the cut tracked incrementally.

    pins[net][s] counts how many of the net's nodes are on side s. Moving node v needs only
    those counts: a net where v is alone on its side stops being cut (+1), and a net with
    nothing on the other side starts being cut (−1). Tests check this against `true_cut`.
    """

    def __init__(self, hg: Hypergraph, assignment: list[int]):
        index = {n: i for i, n in enumerate(hg.nodes)}
        self.side = [int(b) for b in assignment]
        self.weight = [hg.weights[n] for n in hg.nodes]
        self.nets = [sorted(index[n] for n in net) for net in hg.nets]
        self.nets_of: list[list[int]] = [[] for _ in hg.nodes]
        self.pins = []
        for e, net in enumerate(self.nets):
            on_b = sum(self.side[v] for v in net)
            self.pins.append([len(net) - on_b, on_b])
            for v in net:
                self.nets_of[v].append(e)
        self.side_weight = [0, 0]
        for v, s in enumerate(self.side):
            self.side_weight[s] += self.weight[v]
        self.cut = sum(1 for a, b in self.pins if a and b)

    def gain(self, v: int) -> int:
        """How much the cut drops if v changes side (negative means it rises)."""
        src, dst = self.side[v], 1 - self.side[v]
        total = 0
        for e in self.nets_of[v]:
            if self.pins[e][src] == 1:
                total += 1
            if self.pins[e][dst] == 0:
                total -= 1
        return total

    def move(self, v: int) -> None:
        src, dst = self.side[v], 1 - self.side[v]
        self.cut -= self.gain(v)
        for e in self.nets_of[v]:
            self.pins[e][src] -= 1
            self.pins[e][dst] += 1
        self.side[v] = dst
        self.side_weight[src] -= self.weight[v]
        self.side_weight[dst] += self.weight[v]

    def heavier_after(self, v: int) -> int:
        """The heavier side's weight if v moved."""
        w = self.weight[v]
        moved = [self.side_weight[0], self.side_weight[1]]
        moved[self.side[v]] -= w
        moved[1 - self.side[v]] += w
        return max(moved)


def _violation(heavier: float, bound: float) -> float:
    """How far the heavier side is over the balance limit (0 when balanced)."""
    return max(0.0, heavier - bound)


def _fm_pass(state: _FMState, bound: float) -> int:
    """One FM pass: move every node at most once, then keep the best split seen. Returns moves kept.

    The move is often worse in the short term. FM takes the best available move even if it
    raises the cut, because that can open up better moves later (this is how it climbs out
    of local minima). Afterwards it rolls back to the best point in the sequence.
    A split that breaks the balance rule always ranks below one that doesn't, so the same
    pass also repairs an unbalanced start.
    """
    n = len(state.side)
    locked = [False] * n
    gains = [state.gain(v) for v in range(n)]
    moves: list[int] = []
    best_key = (_violation(max(state.side_weight), bound), state.cut)
    best_len = 0

    for _ in range(n):
        current = _violation(max(state.side_weight), bound)
        chosen, chosen_key = None, None
        for v in range(n):
            if locked[v]:
                continue
            after = state.heavier_after(v)
            if current == 0:
                legal = after <= bound  # already balanced: stay balanced
            else:
                legal = _violation(after, bound) < current  # unbalanced: must get closer
            if legal:
                # Biggest gain first, then the more even split, then the lowest index.
                key = (-gains[v], after, v)
                if chosen_key is None or key < chosen_key:
                    chosen, chosen_key = v, key
        if chosen is None:
            break
        state.move(chosen)
        locked[chosen] = True
        moves.append(chosen)
        # Only nodes sharing a net with the moved node can have a new gain.
        for e in state.nets_of[chosen]:
            for u in state.nets[e]:
                if not locked[u]:
                    gains[u] = state.gain(u)
        key = (_violation(max(state.side_weight), bound), state.cut)
        if key < best_key:
            best_key, best_len = key, len(moves)

    for v in reversed(moves[best_len:]):  # undo everything after the best point
        state.move(v)
    return best_len


def fm_refine(
    hg: Hypergraph, assignment: list[int], eps: float = DEFAULT_EPS, max_passes: int = 10
) -> tuple[list[int], int]:
    """Fiduccia-Mattheyses single-node moves. Returns (refined assignment, passes that changed it).

    The balance limit comes from `balance_bound`, the same limit `is_balanced` applies. We
    track side weights incrementally, because calling `is_balanced` for every candidate on
    every move would be far too slow on c880. The final result is re-scored with the real
    `true_cut` and `is_balanced` by the caller.
    """
    if len(assignment) != hg.num_nodes:
        raise ValueError(f"assignment has {len(assignment)} entries, hypergraph has {hg.num_nodes} nodes")
    state = _FMState(hg, assignment)
    bound = balance_bound(hg, eps)
    improving_passes = 0
    for _ in range(max_passes):
        if _fm_pass(state, bound) == 0:  # no improvement anywhere in this pass, so we're done
            break
        improving_passes += 1
    if state.cut != true_cut(hg, state.side):  # the incremental count must match the ground truth
        raise RuntimeError(f"FM cut {state.cut} disagrees with true_cut {true_cut(hg, state.side)}")
    return state.side, improving_passes


# --------------------------------------------------------------------------- the pipeline


def _solve_coarse(
    coarse: Hypergraph, name: str, lam: float, eps: float, seed: int, p: int, shots: int, restarts: int, normalize: bool
) -> SolverResult:
    qubo = build_partition_qubo(coarse, lam=lam)
    if name == "qaoa":
        # Brute force here is only for reporting how often QAOA's shots hit the optimum.
        best = brute_force(coarse, qubo, eps=eps, allow_large=True).best_cut
        return qaoa(
            coarse, qubo, p=p, shots=shots, restarts=restarts, eps=eps, seed=seed, best_known_cut=best,
            normalize=normalize,
        )  # fmt: skip
    if name == "sa":
        return simulated_annealing(coarse, qubo, eps=eps, seed=seed)
    if name == "random":
        return random_balanced(coarse, qubo, eps=eps, seed=seed)
    return brute_force(coarse, qubo, eps=eps).best


def multilevel(
    hg: Hypergraph,
    coarse_solver: str = "qaoa",
    target: int = DEFAULT_TARGET,
    lam: float = DEFAULT_LAMBDA,
    eps: float = DEFAULT_EPS,
    seed: int = 0,
    p: int = 1,
    shots: int = 4000,
    restarts: int = 20,
    normalize: bool = True,
    max_passes: int = 10,
) -> SolverResult:
    """Coarsen to `target` nodes, solve with `coarse_solver`, then project + FM back to the full circuit.

    The record keeps the cut before and after FM at each level, because a good final cut may
    be FM's work and not the coarse solver's. That is what the Phase 6 ablation measures.
    Only the coarse solver is ever quantum, and only on the Aer simulator.
    """
    if coarse_solver not in COARSE_SOLVERS:
        raise ValueError(f"unknown coarse solver {coarse_solver!r}; choose from {COARSE_SOLVERS}")
    t0 = time.perf_counter()
    hierarchy = coarsen(hg, target=target, seed=seed)
    coarse = hierarchy.coarsest
    solved = _solve_coarse(coarse, coarse_solver, lam, eps, seed, p, shots, restarts, normalize)

    bits = solved.assignment
    trace = []
    # Polish the coarsest split, then alternate: open one level up, polish, and so on.
    for level in range(len(hierarchy.levels) - 1, -1, -1):
        current = hierarchy.levels[level]
        if level < len(hierarchy.levels) - 1:
            bits = project(bits, hierarchy.levels[level + 1], current, hierarchy.maps[level])
        before = true_cut(current, bits)
        bits, passes = fm_refine(current, bits, eps=eps, max_passes=max_passes)
        trace.append(
            {
                "nodes": current.num_nodes,
                "cut_before_fm": before,
                "cut_after_fm": true_cut(current, bits),
                "balanced": is_balanced(current, bits, eps),
                "fm_passes": passes,
            }
        )

    extra = {}
    if coarse_solver == "qaoa":
        keep = ("p", "shots", "qubits", "backend", "feasible_rate", "p_optimal", "gammas", "betas")
        extra = {k: solved.info[k] for k in keep}
    return _result(
        f"multilevel-{coarse_solver}",
        hg,
        bits,
        None,  # QUBO energy isn't the score, and this QUBO belongs to the coarse level
        eps,
        t0,
        seed=seed,
        coarse_solver=coarse_solver,
        target=target,
        coarse_nodes=coarse.num_nodes,
        coarse_cut=solved.cut,
        coarse_balanced=solved.balanced,
        weight_cap=hierarchy.weight_cap,
        cap_relaxed=hierarchy.cap_relaxed,
        forced_merges=hierarchy.forced_merges,
        levels=trace,
        **extra,
    )
