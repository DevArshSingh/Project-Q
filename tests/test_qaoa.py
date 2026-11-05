"""Phase 3: QUBO -> Hamiltonian -> QAOA on the Aer simulator (Gate 2, part 1)."""

from itertools import combinations

import numpy as np
import pytest
from c17_reference import C17_JSON, OPTIMAL_A, reference_c17
from qiskit.circuit.library import qaoa_ansatz
from qiskit.quantum_info import Statevector
from qiskit_aer.primitives import EstimatorV2

from project_q.parse_yosys import Hypergraph, is_balanced, load_hypergraph, load_netlist, true_cut
from project_q.qubo import QUBO, build_partition_qubo, qubo_energy
from project_q.solvers import _ansatz_values, brute_force, qaoa, qubo_to_ising, select_best_shot

# Uniform random guessing on c17: 20 of 64 states are balanced and 2 are optimal.
UNIFORM_FEASIBLE = 20 / 64
UNIFORM_OPTIMAL = 2 / 64


def basis_energies(qubo: QUBO) -> list[tuple[list[int], float, float]]:
    """(bits, Hamiltonian expectation + offset, qubo_energy) for every basis state."""
    op, offset = qubo_to_ising(qubo)
    n = qubo.num_vars
    rows = []
    for k in range(1 << n):
        bits = [(k >> i) & 1 for i in range(n)]
        # from_int is little-endian: bit i of k is qubit i, which is variable i.
        h = Statevector.from_int(k, 1 << n).expectation_value(op).real + offset
        rows.append((bits, h, qubo_energy(qubo, bits)))
    return rows


@pytest.fixture(scope="module", params=["reference", "yosys"])
def c17(request) -> Hypergraph:
    return reference_c17() if request.param == "reference" else load_hypergraph(load_netlist(C17_JSON))


@pytest.fixture(scope="module")
def run_c17():
    hg = reference_c17()
    qubo = build_partition_qubo(hg)
    return hg, qubo, qaoa(hg, qubo, best_known_cut=brute_force(hg, qubo).best_cut, seed=0)


# ----------------------------------------------------------------- Hamiltonian


def test_hamiltonian_matches_qubo_on_all_64_c17_states(c17):
    rows = basis_energies(build_partition_qubo(c17))
    assert len(rows) == 64
    for bits, h, e in rows:
        assert h == pytest.approx(e), bits


def test_hamiltonian_matches_asymmetric_qubo_with_linear_terms():
    # c17's Hamiltonian has no Z_i terms, so it can't catch a flipped sign in x = (1−z)/2.
    # Random linear terms and unequal pair terms catch both sign and qubit-order mistakes.
    rng = np.random.default_rng(1)
    qubo = QUBO(nodes=list("abcde"), constant=0.7)
    for i in range(5):
        qubo.add(i, i, float(rng.uniform(-3, 3)))
    for i, j in combinations(range(5), 2):
        qubo.add(i, j, float(rng.uniform(-3, 3)))
    for bits, h, e in basis_energies(qubo):
        assert h == pytest.approx(e), bits


def test_single_linear_term_acts_on_the_right_qubit():
    # E = x_0: the energy is 1 exactly when variable 0 is 1, whatever the other bits are.
    qubo = QUBO(nodes=list("abc"))
    qubo.add(0, 0, 1.0)
    for bits, h, _ in basis_energies(qubo):
        assert h == pytest.approx(bits[0]), bits


def test_empty_qubo_is_rejected():
    with pytest.raises(ValueError, match="no terms"):
        qubo_to_ising(QUBO(nodes=list("ab")))


def test_partition_hamiltonian_is_zz_only(c17):
    op, _ = qubo_to_ising(build_partition_qubo(c17))
    for label in op.paulis.to_labels():
        assert label.count("Z") == 2 and label.count("I") == 4, label


def test_mirror_splits_are_equally_likely():
    # A ZZ-only Hamiltonian can't tell a split from its mirror, so neither can the circuit.
    op, _ = qubo_to_ising(build_partition_qubo(reference_c17()))
    ansatz = qaoa_ansatz(op, reps=1)
    state = Statevector(ansatz.assign_parameters(_ansatz_values(ansatz, [0.4], [0.3])))
    probs = state.probabilities()
    assert probs == pytest.approx(probs[::-1])


@pytest.mark.parametrize("p", [1, 2])
def test_estimator_matches_statevector_with_named_angles(p):
    # Also guards the binding order: γ and β must land on the cost and mixer layers.
    op, _ = qubo_to_ising(build_partition_qubo(reference_c17()))
    ansatz = qaoa_ansatz(op, reps=p)
    values = _ansatz_values(ansatz, [0.4, 0.9][:p], [0.3, 0.1][:p])
    by_name = dict(zip((q.name for q in ansatz.parameters), values))
    assert by_name["γ[0]"] == 0.4 and by_name["β[0]"] == 0.3
    exact = Statevector(ansatz.assign_parameters(values)).expectation_value(op).real
    assert float(EstimatorV2().run([(ansatz, op, values)]).result()[0].data.evs) == pytest.approx(exact)


# ----------------------------------------------------------------- QAOA on c17


def test_qaoa_c17_finds_the_known_optimum(run_c17):
    hg, _, r = run_c17
    assert r.cut == 2 and r.balanced
    assert r.cut == true_cut(hg, r.assignment)
    side_a = {n for n, b in zip(hg.nodes, r.assignment) if b == 0}
    assert side_a in (OPTIMAL_A, set(hg.nodes) - OPTIMAL_A)
    assert r.info["qubits"] == 6 and r.info["shots"] == 4000 and r.info["backend"] == "aer_simulator"
    assert sum(r.info["counts"].values()) == 4000


def test_qaoa_beats_uniform_guessing_on_c17(run_c17):
    # 4000 uniform shots would almost surely include an optimum, so "found cut 2" alone proves
    # little. This is the real check: did the tuned circuit make good answers more likely?
    _, _, r = run_c17
    assert r.info["expected_energy"] < r.info["uniform_energy"]
    assert r.info["feasible_rate"] > UNIFORM_FEASIBLE
    assert r.info["p_optimal"] > UNIFORM_OPTIMAL


def test_qaoa_p1_reaches_the_scanned_minimum_on_c17(run_c17):
    # A grid scan over γ and β puts the true p=1 minimum at ⟨E⟩ ≈ 4.026 on c17. Other local
    # minima sit at about 4.98 and above, so stopping at one of those fails this test.
    assert run_c17[2].info["expected_energy"] == pytest.approx(4.026, abs=0.01)


def test_uniform_energy_is_the_average_over_all_states():
    qubo = build_partition_qubo(reference_c17())
    _, offset = qubo_to_ising(qubo)
    assert offset == pytest.approx(np.mean([e for _, _, e in basis_energies(qubo)]))


def test_same_seed_same_result():
    hg = reference_c17()
    qubo = build_partition_qubo(hg)
    first, second = (qaoa(hg, qubo, restarts=2, shots=200, seed=3) for _ in range(2))
    assert first.assignment == second.assignment
    assert first.info["counts"] == second.info["counts"]
    assert first.info["gammas"] == second.info["gammas"]


def test_qaoa_guards():
    big = Hypergraph(nodes=[f"n{i}" for i in range(25)], nets=[frozenset({"n0", "n1"})])
    with pytest.raises(ValueError, match="24"):
        qaoa(big, build_partition_qubo(big))
    hg = reference_c17()
    with pytest.raises(ValueError, match="p ≥ 1"):
        qaoa(hg, build_partition_qubo(hg), p=0)


# ----------------------------------------------------------------- choosing the answer from shots


def test_most_frequent_shot_is_not_chosen_when_unbalanced():
    hg = reference_c17()  # nodes: g10 g11 g16 g19 g22 g23
    # '000000' has cut 0 and the most shots, but it is lopsided. '101100' reads right to left
    # as g10=0 g11=0 g16=1 g19=1 g22=0 g23=1, the optimal split.
    bits, found = select_best_shot({"000000": 900, "101100": 1}, hg)
    assert found and bits == [0, 0, 1, 1, 0, 1]
    assert true_cut(hg, bits) == 2 and is_balanced(hg, bits)


def test_lowest_cut_beats_frequency_among_balanced_shots():
    hg = reference_c17()
    # Cut 3 split with many shots: A = {g10, g16, g22}, so g11, g19, g23 are 1.
    frequent = [0, 1, 0, 1, 0, 1]
    assert true_cut(hg, frequent) == 3
    key = "".join(str(b) for b in reversed(frequent))
    bits, found = select_best_shot({key: 500, "101100": 2}, hg)
    assert found and true_cut(hg, bits) == 2


def test_no_balanced_shot_is_reported_honestly():
    hg = reference_c17()
    bits, found = select_best_shot({"000000": 50, "000011": 1}, hg)
    assert not found
    assert bits == [1, 1, 0, 0, 0, 0]  # the least lopsided shot, not the most frequent
