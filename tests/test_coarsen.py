"""Phase 5: coarsening, FM refinement, the multilevel pipeline, and QAOA's normalize option."""

import random
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from c17_reference import reference_c17
from qiskit.circuit.library import qaoa_ansatz
from qiskit.quantum_info import Statevector

from project_q.coarsen import _FMState, coarsen, fm_refine, multilevel, project
from project_q.parse_yosys import Hypergraph, is_balanced, load_hypergraph, load_netlist, side_weights, true_cut
from project_q.qubo import build_partition_qubo, qubo_energy
from project_q.scorecard import make_record
from project_q.solvers import _ansatz_values, brute_force, enumerate_all, qaoa, qubo_to_ising

ROOT = Path(__file__).resolve().parents[1]
NETLISTS = ROOT / "benchmarks" / "netlists"


@pytest.fixture(scope="module")
def c432() -> Hypergraph:
    return load_hypergraph(load_netlist(NETLISTS / "c432.json"))


@pytest.fixture(scope="module")
def c880() -> Hypergraph:
    return load_hypergraph(load_netlist(NETLISTS / "c880.json"))


def random_bits(n: int, seed: int) -> list[int]:
    rng = random.Random(seed)
    return [rng.randint(0, 1) for _ in range(n)]


def hand_cut(hg: Hypergraph, bits: list[int]) -> int:
    """A second, separately written cut count, so the tests don't only trust `true_cut`."""
    index = {n: i for i, n in enumerate(hg.nodes)}
    total = 0
    for net in hg.nets:
        sides = [bits[index[n]] for n in net]
        total += 0 < sum(sides) < len(sides)
    return total


# ----------------------------------------------------------------- coarsening


@pytest.mark.parametrize("target", [12, 20])
@pytest.mark.parametrize("seed", [0, 1, 2])
def test_coarsening_reaches_target_and_keeps_weight_under_the_cap(c432, target, seed):
    result = coarsen(c432, target=target, seed=seed)
    assert result.coarsest.num_nodes == target
    assert not result.cap_relaxed
    assert max(result.coarsest.weights.values()) <= result.weight_cap
    for level in result.levels:
        assert level.total_weight == c432.total_weight


def test_coarsening_merges_a_node_with_the_neighbour_it_shares_most_nets_with():
    # x shares 2 nets with y and 1 with z, so x must merge with y, whichever node goes first.
    hg = Hypergraph(
        nodes=["x", "y", "z", "u"],
        nets=[frozenset("xy"), frozenset("xy"), frozenset("xz"), frozenset("zu")],
    )
    for seed in range(10):
        result = coarsen(hg, target=2, seed=seed, weight_cap=2)
        first = result.maps[0]
        assert first["x"] == first["y"]


def test_each_level_only_contracts_pairs(c432):
    # Matching merges at most 2 nodes into one, so no level can more than halve the node count.
    result = coarsen(c432, target=12)
    for fine, coarse, parent in zip(result.levels, result.levels[1:], result.maps):
        members = {}
        for node in fine.nodes:
            members[parent[node]] = members.get(parent[node], 0) + 1
        assert set(members) == set(coarse.nodes)
        assert max(members.values()) <= 2


@pytest.mark.parametrize("design", ["c432", "c880"])
def test_every_level_of_a_split_has_the_same_cut_and_side_weights(design, request):
    # The core promise of coarsening: a coarse split costs exactly what its fine split costs.
    hg = request.getfixturevalue(design)
    result = coarsen(hg, target=12)
    for seed in range(20):
        bits = random_bits(12, seed)
        expected_cut = hand_cut(result.coarsest, bits)
        expected_weights = side_weights(result.coarsest, bits)
        for i in range(len(result.levels) - 1, 0, -1):
            bits = project(bits, result.levels[i], result.levels[i - 1], result.maps[i - 1])
            assert true_cut(result.levels[i - 1], bits) == expected_cut
            assert hand_cut(result.levels[i - 1], bits) == expected_cut
            assert side_weights(result.levels[i - 1], bits) == expected_weights


def test_coarsening_is_repeatable_between_python_runs():
    # Python changes frozenset order between runs, which must not change our answer.
    script = (
        "from project_q.coarsen import coarsen\n"
        "from project_q.parse_yosys import load_hypergraph, load_netlist\n"
        f"hg = load_hypergraph(load_netlist({str(NETLISTS / 'c432.json')!r}))\n"
        "c = coarsen(hg, target=12, seed=0)\n"
        "print(sorted(c.coarsest.weights.items()), sorted(sorted(n) for n in c.coarsest.nets))\n"
    )
    outputs = {
        subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, check=True,
            env={"PYTHONHASHSEED": hash_seed, "PATH": str(Path(sys.executable).parent)},
        ).stdout
        for hash_seed in ("1", "2", "3")
    }  # fmt: skip
    assert len(outputs) == 1


def test_small_design_is_left_alone():
    hg = reference_c17()
    result = coarsen(hg, target=6)
    assert result.levels == [hg] and result.maps == []


def test_target_below_two_is_rejected():
    with pytest.raises(ValueError, match="at least 2"):
        coarsen(reference_c17(), target=1)


def test_unconnected_nodes_still_reach_the_target():
    hg = Hypergraph(nodes=list("abcdef"), nets=[])
    result = coarsen(hg, target=3)
    assert result.coarsest.num_nodes == 3
    assert result.forced_merges > 0
    assert result.coarsest.total_weight == 6


def test_a_raised_cap_is_recorded():
    hg = Hypergraph(nodes=list("abc"), nets=[frozenset("ab"), frozenset("bc")], weights={"a": 5, "b": 5, "c": 5})
    result = coarsen(hg, target=2)  # the default cap of 12 fits; a cap of 5 can't fit any merge
    assert not result.cap_relaxed
    result = coarsen(hg, target=2, weight_cap=5)
    assert result.cap_relaxed and result.weight_cap == 10
    assert result.coarsest.num_nodes == 2


# ----------------------------------------------------------------- FM refinement


@pytest.mark.parametrize("design", ["c432", "c880"])
def test_fm_gain_and_incremental_cut_match_true_cut_on_every_move(design, request):
    hg = request.getfixturevalue(design)
    bits = random_bits(hg.num_nodes, 0)
    state = _FMState(hg, bits)
    rng = random.Random(1)
    assert state.cut == true_cut(hg, state.side)
    for _ in range(300):
        v = rng.randrange(hg.num_nodes)
        before = true_cut(hg, state.side)
        gain = state.gain(v)
        state.move(v)
        assert true_cut(hg, state.side) == before - gain
        assert state.cut == true_cut(hg, state.side)
        assert tuple(state.side_weight) == side_weights(hg, state.side)


def test_fm_gain_matches_true_cut_on_weighted_supernodes(c432):
    coarse = coarsen(c432, target=12).coarsest
    state = _FMState(coarse, random_bits(12, 3))
    for v in range(12):
        before = true_cut(coarse, state.side)
        gain = state.gain(v)
        state.move(v)
        assert true_cut(coarse, state.side) == before - gain
        assert tuple(state.side_weight) == side_weights(coarse, state.side)


def test_fm_improves_a_balanced_start_and_keeps_it_balanced(c432):
    for seed in range(5):
        bits = [0] * 94 + [1] * 93
        random.Random(seed).shuffle(bits)
        assert is_balanced(c432, bits)
        refined, passes = fm_refine(c432, bits)
        assert is_balanced(c432, refined)
        assert true_cut(c432, refined) < true_cut(c432, bits)
        assert passes >= 1


def test_fm_repairs_an_unbalanced_start(c432):
    assert not is_balanced(c432, [0] * 187)
    refined, _ = fm_refine(c432, [0] * 187)
    assert is_balanced(c432, refined)


def test_fm_takes_a_short_term_loss_to_escape_a_local_minimum():
    # u and v belong with the b's, but 3 nets tie them together. Moving either one alone
    # raises the cut, so a plain "improving moves only" search would stay stuck here.
    b_ring = [frozenset(p) for p in (("b1", "b2"), ("b2", "b3"), ("b1", "b3"))] * 2
    a_ring = [frozenset(p) for p in (("a1", "a2"), ("a2", "a3"), ("a1", "a3"))] * 2
    nets = a_ring + b_ring + [frozenset("uv")] * 3 + [frozenset(("u", "b1"))] * 2 + [frozenset(("v", "b2"))] * 2
    hg = Hypergraph(nodes=["a1", "a2", "a3", "u", "v", "b1", "b2", "b3"], nets=nets)
    bits = [0, 0, 0, 0, 0, 1, 1, 1]
    eps = 0.3  # bound ⌈8/2⌉·1.3 = 5.2, so 5/3 and 3/5 are both balanced
    state = _FMState(hg, bits)
    assert all(state.gain(v) <= 0 for v in range(8))
    assert true_cut(hg, bits) == 4

    refined, _ = fm_refine(hg, bits, eps=eps)
    assert true_cut(hg, refined) == 0
    assert is_balanced(hg, refined, eps)


def test_fm_rolls_back_when_nothing_improves():
    # At the c17 optimum, no sequence of legal moves helps, so the split must come back unchanged.
    hg = reference_c17()
    bits = [0 if n in {"g10", "g11", "g22"} else 1 for n in hg.nodes]
    assert fm_refine(hg, bits, eps=0.34)[0] == bits  # bound 4.02 lets moves happen
    assert fm_refine(hg, bits)[0] == bits  # bound 3.09 blocks all single moves


def test_fm_rejects_a_wrong_length_assignment():
    with pytest.raises(ValueError, match="entries"):
        fm_refine(reference_c17(), [0, 1])


# ----------------------------------------------------------------- QUBO on weighted supernodes


def test_balance_term_matches_its_formula_on_every_coarse_state(c432):
    # CLAUDE.md asks every QUBO to be checked by brute force. Same QUBO code, but c17 has
    # unit weights only, so this checks the weighted case on all 4096 real coarse states.
    coarse = coarsen(c432, target=12).coarsest
    lam = 2.0
    with_balance = enumerate_all(coarse, build_partition_qubo(coarse, lam=lam))
    cut_only = enumerate_all(coarse, build_partition_qubo(coarse, lam=0))
    w = np.array([coarse.weights[n] for n in coarse.nodes])
    x = (np.arange(1 << 12)[:, None] >> np.arange(12)) & 1
    expected = lam * (x @ w - coarse.total_weight / 2) ** 2
    assert np.allclose(with_balance.energies - cut_only.energies, expected)
    # Vectorized cuts and balance agree with the plain-Python ground truth on a sample.
    for k in random.Random(0).sample(range(1 << 12), 200):
        bits = x[k].tolist()
        assert with_balance.cuts[k] == true_cut(coarse, bits) == hand_cut(coarse, bits)
        assert with_balance.balanced[k] == is_balanced(coarse, bits)


def test_coarse_qubo_cut_terms_equal_the_true_cut_when_nets_are_exact():
    # Merging c17 down to 4 nodes leaves nets of at most 3 nodes, so the cut terms are exact.
    coarse = coarsen(reference_c17(), target=4).coarsest
    qubo = build_partition_qubo(coarse, lam=0)
    assert qubo.exact
    for k in range(1 << coarse.num_nodes):
        bits = [(k >> i) & 1 for i in range(coarse.num_nodes)]
        assert qubo_energy(qubo, bits) == pytest.approx(true_cut(coarse, bits))


def test_c17_gate1_still_holds():
    # CLAUDE.md: QUBO checks stay anchored to brute force on c17 (expected cut 2, gap 1).
    hg = reference_c17()
    result = brute_force(hg, build_partition_qubo(hg))
    assert result.best_cut == 2
    assert result.argmin_is_optimal
    assert result.energy_gap == pytest.approx(1.0)


# ----------------------------------------------------------------- multilevel


@pytest.mark.parametrize("coarse_solver", ["sa", "random", "brute"])
@pytest.mark.parametrize("design", ["c432", "c880"])
def test_multilevel_classical_solvers_give_balanced_splits(design, coarse_solver, request):
    hg = request.getfixturevalue(design)
    for seed in range(3):
        result = multilevel(hg, coarse_solver, target=12, seed=seed)
        assert result.solver == f"multilevel-{coarse_solver}"
        assert len(result.assignment) == hg.num_nodes
        assert result.balanced and is_balanced(hg, result.assignment)
        assert result.cut == true_cut(hg, result.assignment) == hand_cut(hg, result.assignment)
        # The first entry is the coarse level and the last is the full circuit.
        levels = result.info["levels"]
        assert levels[0]["nodes"] == 12 and levels[-1]["nodes"] == hg.num_nodes
        assert levels[-1]["cut_after_fm"] == result.cut


def test_multilevel_cut_follows_the_level_trace(c432):
    # Projecting never changes the cut, and FM never makes a balanced split worse.
    levels = multilevel(c432, "brute", target=12).info["levels"]
    for coarse, fine in zip(levels, levels[1:]):
        assert fine["cut_before_fm"] == coarse["cut_after_fm"]
    for level in levels:
        if level["balanced"]:
            assert level["cut_after_fm"] <= level["cut_before_fm"]


def test_multilevel_on_c17_needs_no_coarsening_and_finds_the_optimum():
    hg = reference_c17()
    for coarse_solver in ("brute", "sa"):
        result = multilevel(hg, coarse_solver, target=6)
        assert result.cut == 2 and result.balanced
        assert result.info["coarse_nodes"] == 6


def test_multilevel_brute_uses_the_proven_coarse_optimum(c432):
    coarse = coarsen(c432, target=12).coarsest
    expected = brute_force(coarse, build_partition_qubo(coarse)).best_cut
    assert multilevel(c432, "brute", target=12).info["coarse_cut"] == expected


def test_multilevel_with_qaoa_on_the_simulator(c432):
    # Few restarts and shots to keep it quick: this checks the wiring, not QAOA's quality.
    result = multilevel(c432, "qaoa", target=8, restarts=2, shots=300, seed=0, lam=2.0)
    assert result.solver == "multilevel-qaoa"
    assert result.balanced and result.cut == true_cut(c432, result.assignment)
    assert result.info["qubits"] == 8 and result.info["backend"] == "aer_simulator"
    record = make_record("c432", c432, result, 2.0, 0.03)
    assert record.lam == 2.0 and record.qubits == 8 and record.shots == 300 and record.p == 1
    assert record.cut == result.cut


def test_unknown_coarse_solver_is_rejected(c432):
    with pytest.raises(ValueError, match="unknown coarse solver"):
        multilevel(c432, "magic")


# ----------------------------------------------------------------- QAOA normalize and Gate 2


def test_normalized_qaoa_reports_energy_on_the_qubo_scale(c432):
    coarse = coarsen(c432, target=6).coarsest
    qubo = build_partition_qubo(coarse)
    result = qaoa(coarse, qubo, restarts=2, shots=200, normalize=True)
    op, offset = qubo_to_ising(qubo)
    scale = float(np.abs(op.coeffs).max())
    assert result.info["hamiltonian_scale"] == pytest.approx(scale) and scale > 100

    # Exact check: same angles and the normalized circuit, measured with the unscaled H.
    ansatz = qaoa_ansatz(op / scale, reps=1)
    values = _ansatz_values(ansatz, result.info["gammas"], result.info["betas"])
    state = Statevector(ansatz.assign_parameters(values))
    assert state.expectation_value(op).real + offset == pytest.approx(result.info["expected_energy"])


def test_dividing_h_by_a_number_keeps_every_state_in_the_same_energy_order(c432):
    coarse = coarsen(c432, target=6).coarsest
    qubo = build_partition_qubo(coarse)
    op, offset = qubo_to_ising(qubo)
    scale = float(np.abs(op.coeffs).max())
    for k in range(64):
        bits = [(k >> i) & 1 for i in range(6)]
        value = Statevector.from_int(k, 64).expectation_value(op / scale).real
        assert value * scale + offset == pytest.approx(qubo_energy(qubo, bits))


def test_qaoa_is_unchanged_on_c17_unless_normalize_is_requested():
    hg = reference_c17()
    qubo = build_partition_qubo(hg)
    plain = qaoa(hg, qubo, restarts=2, shots=200, seed=0)
    explicit = qaoa(hg, qubo, restarts=2, shots=200, seed=0, normalize=False)
    assert plain.info["hamiltonian_scale"] == 1.0
    assert plain.info["gammas"] == explicit.info["gammas"]
    assert plain.info["counts"] == explicit.info["counts"]


def test_gate2_qaoa_matches_brute_force_on_12_coarse_c432_nodes(c432):
    # The SPEC's Gate 2 check. On its own it is weak: 4000 uniform random shots would often
    # pass too, so scripts/gate2_c432.py also compares QAOA's shot rates with random guessing.
    coarse = coarsen(c432, target=12, seed=0).coarsest
    qubo = build_partition_qubo(coarse)
    best = brute_force(coarse, qubo).best_cut
    result = qaoa(coarse, qubo, restarts=5, seed=0, best_known_cut=best, normalize=True)
    assert result.info["qubits"] == 12
    assert result.balanced
    assert result.cut == best
