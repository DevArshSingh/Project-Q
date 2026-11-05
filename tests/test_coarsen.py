"""Phase 5: coarsening, FM, multilevel → Gate 2 part 2."""

from pathlib import Path

import pytest

from project_q.coarsen import coarsen, fm_refine, multilevel
from project_q.parse_yosys import Hypergraph, is_balanced, load_hypergraph, load_netlist, true_cut
from project_q.qubo import build_partition_qubo
from project_q.solvers import brute_force, qaoa

ROOT = Path(__file__).resolve().parents[1]
C17 = ROOT / "benchmarks" / "netlists" / "c17.json"
C432 = ROOT / "benchmarks" / "netlists" / "c432.json"


@pytest.fixture(scope="module")
def c17() -> Hypergraph:
    return load_hypergraph(load_netlist(C17))


@pytest.fixture(scope="module")
def c432() -> Hypergraph:
    return load_hypergraph(load_netlist(C432))


def test_coarsen_c432_reaches_target(c432):
    hier = coarsen(c432, target=12)
    assert hier.coarsest.num_nodes <= 12
    assert hier.coarsest.num_nodes >= 2
    assert hier.levels, "c432 should need at least one coarsening step"
    # Weights conserved: total weight of supernodes == original W.
    assert hier.coarsest.total_weight == c432.total_weight


def test_coarsen_preserves_weight_each_level(c432):
    hier = coarsen(c432, target=20)
    w = c432.total_weight
    for level in hier.levels:
        assert level.coarse.total_weight == w
        assert set(level.parent) == set(level.fine.nodes)
        assert set(level.parent.values()) == set(level.coarse.nodes)


def test_fm_improves_or_keeps_cut_on_c17(c17):
    # Start from a balanced but non-optimal 3/3 split (cut 3).
    bits = [0, 1, 0, 1, 0, 1]
    assert is_balanced(c17, bits)
    before = true_cut(c17, bits)
    after_bits = fm_refine(c17, bits)
    assert is_balanced(c17, after_bits)
    assert true_cut(c17, after_bits) <= before


def test_multilevel_brute_on_c17_finds_optimum(c17):
    # c17 is already ≤ target; multilevel should still return cut 2.
    r = multilevel(c17, coarse_solver="brute", target=20, seed=0)
    assert r.balanced and r.cut == 2


def test_gate2_qaoa_matches_brute_on_coarsened_c432():
    """GATE 2 (part 2): QAOA matches brute force on a ~12-node coarsened circuit."""
    c432 = load_hypergraph(load_netlist(C432))
    hier = coarsen(c432, target=12)
    core = hier.coarsest
    assert 2 <= core.num_nodes <= 12

    qubo = build_partition_qubo(core)
    bf = brute_force(core, qubo)
    # Allow a few QAOA restarts; 12 qubits is still small for Aer.
    qa = qaoa(core, qubo, p=1, shots=4000, restarts=8, seed=0)

    assert bf.best.balanced
    assert qa.balanced, f"QAOA unbalanced on {core.num_nodes}-node core; info={qa.info}"
    assert qa.cut == bf.best_cut, (
        f"Gate 2 part 2 FAIL: QAOA cut {qa.cut} != brute {bf.best_cut} "
        f"on {core.num_nodes} supernodes (W={core.total_weight})"
    )
    print(
        f"\nGate 2 part 2: coarsened c432 → {core.num_nodes} nodes, "
        f"brute cut={bf.best_cut}, QAOA cut={qa.cut}, "
        f"feasible={qa.info.get('feasible_rate', qa.info)}"
    )


def test_multilevel_qaoa_returns_balanced_on_c432(c432):
    r = multilevel(c432, coarse_solver="qaoa", target=12, restarts=4, shots=2000, seed=0)
    assert r.balanced
    assert r.info["n_coarse"] <= 12
    assert r.cut == true_cut(c432, r.assignment)
