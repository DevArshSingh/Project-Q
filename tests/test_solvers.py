"""Phase 2: brute force (Gate 1), balance rule and classical baselines."""

import itertools

import numpy as np
import pytest
from c17_reference import C17_V, OPTIMAL_A, reference_c17

from project_q.parse_yosys import Hypergraph, hypergraph_from_verilog, is_balanced, true_cut
from project_q.qubo import build_partition_qubo, qubo_energy
from project_q.solvers import (
    brute_force,
    enumerate_all,
    kernighan_lin,
    random_balanced,
    simulated_annealing,
)


@pytest.fixture(scope="module", params=["reference", "yosys"])
def c17(request) -> Hypergraph:
    return reference_c17() if request.param == "reference" else hypergraph_from_verilog(C17_V)


# ----------------------------------------------------------------- balance rule


def test_is_balanced_c17_means_exactly_3_3(c17):
    for bits in itertools.product((0, 1), repeat=6):
        assert is_balanced(c17, bits) == (sum(bits) == 3)


def test_is_balanced_odd_and_weighted():
    five = Hypergraph(nodes=list("abcde"), nets=[])
    assert is_balanced(five, [0, 0, 0, 1, 1], eps=0.0)  # 3/2 is the best possible for W=5
    assert not is_balanced(five, [0, 0, 0, 0, 1], eps=0.0)
    heavy = Hypergraph(nodes=list("abc"), nets=[], weights={"a": 4, "b": 2, "c": 2})
    assert is_balanced(heavy, [0, 1, 1])  # 4 | 4
    assert not is_balanced(heavy, [0, 0, 1])  # 6 | 2


# ----------------------------------------------------------------- brute force


def test_vectorized_enumeration_matches_plain_loops(c17):
    q = build_partition_qubo(c17, lam=2.0)
    en = enumerate_all(c17, q, chunk_bits=3)  # small chunks to exercise the chunking
    for k in range(64):
        bits = en.bits(k, 6)
        assert en.cuts[k] == true_cut(c17, bits)
        assert en.balanced[k] == is_balanced(c17, bits)
        assert en.energies[k] == pytest.approx(qubo_energy(q, bits))


def test_gate1_c17(c17):
    """GATE 1: hypergraph, QUBO and brute force agree on c17 at the default λ = 2.0."""
    q = build_partition_qubo(c17)  # default λ
    assert q.lam == 2.0
    bf = brute_force(c17, q)
    assert bf.best_cut == 2 and bf.best.balanced and bf.best.cut == 2
    assert bf.n_optimal == 2  # one split + its mirror image
    assert bf.argmin_is_optimal  # every minimum-energy state is a balanced cut-2 split
    assert bf.min_energy == pytest.approx(2.0)
    assert bf.energy_gap > 0  # the strict gap required by SPEC Gate 1
    assert bf.energy_gap == pytest.approx(1.0)
    assert bf.unbalanced_margin == pytest.approx(2.0)


def test_gate1_reference_optimum_is_research_split():
    hg = reference_c17()
    bf = brute_force(hg, build_partition_qubo(hg))
    side_of = dict(zip(hg.nodes, bf.best.assignment))
    groups = {frozenset(n for n in hg.nodes if side_of[n] == s) for s in (0, 1)}
    assert frozenset(OPTIMAL_A) in groups


def test_lambda_1_has_smaller_unbalanced_margin(c17):
    bf = brute_force(c17, build_partition_qubo(c17, lam=1.0))
    assert bf.energy_gap == pytest.approx(1.0)
    assert bf.unbalanced_margin == pytest.approx(1.0)


def test_brute_force_size_caps():
    def hg_of(n):
        return Hypergraph(nodes=[f"n{i}" for i in range(n)], nets=[])

    big = hg_of(21)
    with pytest.raises(ValueError, match="allow_large"):
        brute_force(big, build_partition_qubo(big))
    huge = hg_of(25)
    with pytest.raises(ValueError, match="hard cap"):
        brute_force(huge, build_partition_qubo(huge), allow_large=True)


# ----------------------------------------------------------------- baselines


def test_random_balanced_is_balanced_and_reproducible(c17):
    q = build_partition_qubo(c17)
    for seed in range(10):
        r = random_balanced(c17, q, seed=seed)
        assert r.balanced and r.cut == true_cut(c17, r.assignment)
    assert random_balanced(c17, q, seed=3).assignment == random_balanced(c17, q, seed=3).assignment


def test_random_balanced_with_many_samples_finds_optimum(c17):
    assert random_balanced(c17, samples=200, seed=0).cut == 2


def test_simulated_annealing_finds_c17_optimum(c17):
    r = simulated_annealing(c17, build_partition_qubo(c17), sweeps=200, restarts=2, seed=0)
    assert r.balanced and r.cut == 2
    assert r.energy == pytest.approx(qubo_energy(build_partition_qubo(c17), r.assignment))


def test_kernighan_lin_balanced_on_c17(c17):
    r = kernighan_lin(c17, build_partition_qubo(c17), seed=0)
    assert r.balanced and r.cut == true_cut(c17, r.assignment)
    assert r.cut >= 2  # can never beat the brute-force optimum


def test_kernighan_lin_best_over_seeds_reaches_optimum(c17):
    assert min(kernighan_lin(c17, seed=s).cut for s in range(10)) == 2


def test_sa_beats_random_floor_on_random_hypergraph():
    rng = np.random.default_rng(1)
    nodes = [f"v{i}" for i in range(16)]
    nets = {frozenset(rng.choice(nodes, size=rng.integers(2, 4), replace=False)) for _ in range(30)}
    hg = Hypergraph(nodes=nodes, nets=sorted(nets, key=sorted))
    q = build_partition_qubo(hg)
    exact = brute_force(hg, q).best_cut
    sa = simulated_annealing(hg, q, seed=0)
    rnd = random_balanced(hg, q, seed=0)
    assert sa.balanced and exact <= sa.cut <= rnd.cut
