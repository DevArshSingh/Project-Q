"""Hypergraph -> QUBO for balanced 2-way partitioning.

Quantum/optimization background for the team:
- One binary variable per node: x_i = 0 puts node i on side A, x_i = 1 on side B.
  Each variable becomes one qubit later (Phase 3).
- A QUBO (Quadratic Unconstrained Binary Optimization) is an energy
      E(x) = Σ_i Q_ii x_i + Σ_{i<j} Q_ij x_i x_j + constant
  with only linear and pairwise terms. Lower energy = better split. QAOA and
  simulated annealing both search for low-energy x.
- A QUBO can't express hard constraints, so "keep the sides balanced" becomes
  a penalty λ(Σ w_i x_i − W/2)² that is 0 at a perfect split and grows
  quadratically with imbalance.

Cut terms. For a pair {i, j}, (x_i + x_j − 2 x_i x_j) is 1 when they differ and
0 when equal. A net with n nodes gets every pair weighted 1/(n−1):
- n = 2: exactly the pair term (SPEC formula `xi+xj-2xixj`).
- n = 3: (1/2)·Σ_pairs = xi+xj+xk − xixj − xjxk − xixk (SPEC formula). A 1|2
  split cuts 2 of the 3 pairs -> 2·(1/2) = 1. Exact.
- n = 4..8: approximation. A 1|(n−1) split scores exactly 1, but a more even
  split scores more than 1 (2|2 on a 4-net scores 4/3), so these nets are
  over-penalized. That is why the true cut is always recomputed classically.
- n > 8: dropped from the QUBO (still counted by `true_cut`).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from itertools import combinations

import numpy as np

from project_q.parse_yosys import DEFAULT_EPS, Hypergraph, balance_bound

DEFAULT_LAMBDA = 2.0  # the value Gate 1 and the Phase 8 angles use; suggest_lambda gives 2.0 on c17
MAX_QUBO_NET_SIZE = 8


@dataclass
class QUBO:
    """E(x) = Σ_{(i,j)} terms[(i,j)]·x_i·x_j + constant, with i ≤ j.

    (i, i) entries are linear terms (x_i² = x_i for binary x).
    Variable i corresponds to `nodes[i]`.
    """

    nodes: list[str]
    terms: dict[tuple[int, int], float] = field(default_factory=dict)
    constant: float = 0.0
    lam: float = DEFAULT_LAMBDA
    exact: bool = True  # False if any net used the pair approximation
    dropped_nets: int = 0  # nets with more than MAX_QUBO_NET_SIZE nodes

    @property
    def num_vars(self) -> int:
        return len(self.nodes)

    def add(self, i: int, j: int, coeff: float) -> None:
        if coeff == 0:
            return
        key = (i, j) if i <= j else (j, i)
        self.terms[key] = self.terms.get(key, 0.0) + coeff

    def to_matrix(self) -> np.ndarray:
        """Upper-triangular matrix Q with E(x) = xᵀQx + constant."""
        q = np.zeros((self.num_vars, self.num_vars))
        for (i, j), c in self.terms.items():
            q[i, j] += c
        return q


def suggest_lambda(hg: Hypergraph, eps: float = DEFAULT_EPS) -> float:
    """A balance weight λ computed from the circuit alone, with no brute force.

    Rule: λ = d̄ / (δ_out² − δ_min²)
      d̄      average number of nets per node, counting only nets the QUBO can see (≤ 8 nodes)
      δ_min  smallest possible imbalance |w(A) − W/2|: 0 if W is even, 0.5 if W is odd
      δ_out  the first imbalance the hard balance rule rejects

    Why: the penalty is λδ², so going from the most even split to the first illegal one
    costs λ(δ_out² − δ_min²). The rule sets that cost equal to the wires on a typical node.
    Much lower and illegal splits win, because they save more cut than the penalty costs.
    Much higher and the penalty swamps the cut among legal splits: a fixed λ = 2.0 charges
    a c432 supernode split at δ = 2.5 about 12 more than one at δ = 0.5, although the hard
    rule accepts both, which is how it picked cut 19 over the optimum of 12.

    This is a rule of thumb, not a guarantee, so the true cut is still always recomputed.
    See results/lambda_rule.csv for how it did on instances not used to choose it.
    On c17: d̄ = 2, δ_min = 0, δ_out = 1, so λ = 2.0, the value Gate 1 already uses.
    """
    total = hg.total_weight
    # Side weights are whole numbers, so round the limit down to the heaviest legal side.
    allowed = math.floor(balance_bound(hg, eps) + 1e-9) - total / 2
    delta_out = allowed + 1
    delta_min = 0.5 if total % 2 else 0.0
    visible_pins = sum(len(net) for net in hg.nets if len(net) <= MAX_QUBO_NET_SIZE)
    # max(..., 1): with no visible nets there is nothing to trade off, but λ must stay positive.
    mean_nets_per_node = max(visible_pins / hg.num_nodes, 1.0)
    return mean_nets_per_node / (delta_out**2 - delta_min**2)


def build_partition_qubo(hg: Hypergraph, lam: float = DEFAULT_LAMBDA) -> QUBO:
    """Cut terms for every net (see module docstring) + λ·(Σ w_i x_i − W/2)²."""
    index = {n: i for i, n in enumerate(hg.nodes)}
    q = QUBO(nodes=list(hg.nodes), lam=lam)

    for net in hg.nets:
        size = len(net)
        if size > MAX_QUBO_NET_SIZE:
            q.dropped_nets += 1
            continue
        if size > 3:
            q.exact = False
        weight = 1.0 / (size - 1)
        for a, b in combinations(sorted(index[n] for n in net), 2):
            # weight · (x_a + x_b − 2 x_a x_b)
            q.add(a, a, weight)
            q.add(b, b, weight)
            q.add(a, b, -2.0 * weight)

    # Balance: λ(Σ w_i x_i − W/2)². Expanding with x_i² = x_i:
    #   = λ[ Σ_i (w_i² − W·w_i) x_i + Σ_{i<j} 2 w_i w_j x_i x_j + W²/4 ]
    if lam:
        w = [hg.weights[n] for n in hg.nodes]
        total = sum(w)
        for i, wi in enumerate(w):
            q.add(i, i, lam * (wi * wi - total * wi))
        for i, j in combinations(range(len(w)), 2):
            q.add(i, j, lam * 2.0 * w[i] * w[j])
        q.constant += lam * total * total / 4.0
    return q


def qubo_energy(qubo: QUBO, bits: Sequence[int]) -> float:
    """Energy of one assignment (bits in `qubo.nodes` order). Plain loop, used as reference."""
    if len(bits) != qubo.num_vars:
        raise ValueError(f"expected {qubo.num_vars} bits, got {len(bits)}")
    return qubo.constant + sum(c * bits[i] * bits[j] for (i, j), c in qubo.terms.items())


def qubo_energies(qubo: QUBO, x: np.ndarray) -> np.ndarray:
    """Energies for many assignments at once; `x` has shape (m, n) with 0/1 entries."""
    q = qubo.to_matrix()
    x = np.asarray(x, dtype=float)
    # row-wise xᵀQx without forming an m×m matrix
    return np.einsum("mi,ij,mj->m", x, q, x) + qubo.constant
