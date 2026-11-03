"""Phase 2: QUBO formula checks against the classical true cut (brute force over all 64 c17 states)."""

import itertools

import numpy as np
import pytest
from c17_reference import C17_V, reference_c17

from project_q.parse_yosys import Hypergraph, hypergraph_from_verilog, true_cut
from project_q.qubo import build_partition_qubo, qubo_energies, qubo_energy

ALL_6 = list(itertools.product((0, 1), repeat=6))


@pytest.fixture(scope="module", params=["reference", "yosys"])
def c17(request) -> Hypergraph:
    return reference_c17() if request.param == "reference" else hypergraph_from_verilog(C17_V)


def test_cut_terms_equal_true_cut_on_all_64_states(c17):
    """The formula check: with λ = 0 the QUBO is only cut terms, and c17 has only
    2- and 3-node nets (exact formulas), so energy must equal the true cut everywhere."""
    q = build_partition_qubo(c17, lam=0.0)
    assert q.exact and q.dropped_nets == 0
    for bits in ALL_6:
        assert qubo_energy(q, bits) == pytest.approx(true_cut(c17, bits))


def test_balance_term_expansion(c17):
    """Energy(λ) − Energy(0) must equal λ(Σ w_i x_i − W/2)² for every state."""
    lam = 2.0
    q_cut, q_full = build_partition_qubo(c17, lam=0.0), build_partition_qubo(c17, lam=lam)
    for bits in ALL_6:
        penalty = lam * (sum(bits) - 3) ** 2
        assert qubo_energy(q_full, bits) - qubo_energy(q_cut, bits) == pytest.approx(penalty)


def test_balance_term_uses_weights():
    hg = Hypergraph(nodes=["a", "b", "c"], nets=[frozenset({"a", "b"})], weights={"a": 2, "b": 1, "c": 1})
    q = build_partition_qubo(hg, lam=1.5)
    for bits in itertools.product((0, 1), repeat=3):
        wx = 2 * bits[0] + bits[1] + bits[2]
        expected = true_cut(hg, bits) + 1.5 * (wx - 2) ** 2
        assert qubo_energy(q, bits) == pytest.approx(expected)


def test_vectorized_energies_match_loop(c17):
    q = build_partition_qubo(c17, lam=2.0)
    x = np.array(ALL_6)
    np.testing.assert_allclose(qubo_energies(q, x), [qubo_energy(q, b) for b in ALL_6])


def test_large_nets_use_pair_approximation_and_huge_nets_are_dropped():
    nodes = [f"n{i}" for i in range(10)]
    four = frozenset(nodes[:4])
    hg = Hypergraph(nodes=nodes, nets=[four, frozenset(nodes)])  # 4-net + 10-net
    q = build_partition_qubo(hg, lam=0.0)
    assert not q.exact and q.dropped_nets == 1
    # 1|3 split of the 4-net is scored exactly (1); 2|2 is over-scored (4/3).
    one_three = [1, 0, 0, 0] + [0] * 6
    two_two = [1, 1, 0, 0] + [0] * 6
    assert qubo_energy(q, one_three) == pytest.approx(1.0)
    assert qubo_energy(q, two_two) == pytest.approx(4 / 3)
