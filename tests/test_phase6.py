"""Phase 6: the λ rule, CLI routing, the results table, the ablation verdict, and the plots."""

import json
import math
import os
import subprocess
import sys
from pathlib import Path

import pytest
from c17_reference import reference_c17

from project_q import cli, experiments as ex
from project_q.coarsen import coarsen, multilevel
from project_q.parse_yosys import Hypergraph, is_balanced, true_cut
from project_q.plots import cut_axis_top, plot_ablation, plot_lambda_sweep, plot_partition, verdict_headline
from project_q.qubo import build_partition_qubo, suggest_lambda
from project_q.solvers import brute_force

ROOT = Path(__file__).resolve().parents[1]
NETLISTS = ROOT / "benchmarks" / "netlists"


@pytest.fixture(scope="module")
def c432():
    return ex.load_benchmark("c432")


# ----------------------------------------------------------------- the λ rule


def test_rule_gives_the_gate1_value_on_c17_and_gate1_holds():
    hg = reference_c17()
    lam = suggest_lambda(hg)
    assert lam == pytest.approx(2.0)
    bf = brute_force(hg, build_partition_qubo(hg, lam=lam))
    assert bf.best_cut == 2 and bf.argmin_is_optimal and bf.energy_gap == pytest.approx(1.0)
    assert suggest_lambda(ex.load_benchmark("c17")) == pytest.approx(2.0)  # the Yosys version too


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_rule_on_the_instances_used_to_choose_it(c432, seed):
    # These 3 helped choose the formula, so this guards against regressions and isn't evidence.
    # The evidence is the held-out group in results/lambda_rule.csv.
    coarse = coarsen(c432, target=12, seed=seed).coarsest
    assert brute_force(coarse, build_partition_qubo(coarse, lam=suggest_lambda(coarse))).argmin_is_optimal


def _chain(n: int, weights=None) -> Hypergraph:
    nodes = [f"g{i}" for i in range(n)]
    nets = [frozenset(pair) for pair in zip(nodes, nodes[1:])]
    hg = Hypergraph(nodes=nodes, nets=nets, net_names=[f"n{i}" for i in range(len(nets))])
    if weights:
        hg.weights = dict(zip(nodes, weights))
    return hg


@pytest.mark.parametrize(
    ("n", "eps", "delta_out", "delta_min"),
    [
        (6, 0.0, 1.0, 0.0),  # W even: only 3/3 is legal
        (5, 0.0, 1.5, 0.5),  # W odd: 3/2 is legal
        (100, 0.03, 2.0, 0.0),  # ⌈100/2⌉·1.03 = 51.5, so the heaviest legal side is 51 (δ = 1)
    ],
)
def test_rule_arithmetic_and_it_stays_finite(n, eps, delta_out, delta_min):
    # A chain of n gates has n−1 two-node nets, so 2(n−1) pins over n nodes.
    expected = (2 * (n - 1) / n) / (delta_out**2 - delta_min**2)
    lam = suggest_lambda(_chain(n), eps)
    assert math.isfinite(lam) and lam > 0 and lam == pytest.approx(expected)


def test_rule_ignores_nets_the_qubo_leaves_out():
    nodes = [f"g{i}" for i in range(10)]
    big = frozenset(nodes)  # 10 nodes: above the QUBO's limit of 8
    hg = Hypergraph(nodes=nodes, nets=[big], net_names=["big"])
    # No visible nets: λ is still positive, using the floor of 1 net per node.
    assert suggest_lambda(hg, 0.0) == pytest.approx(1.0)


def test_rule_uses_no_solver():
    # A rule that relied on brute force couldn't scale, so qubo.py must not depend on solvers.
    source = (ROOT / "project_q" / "qubo.py").read_text(encoding="utf-8")
    assert "project_q.solvers" not in source and "brute_force" not in source


def test_multilevel_defaults_to_the_rule_on_the_coarse_circuit(c432):
    result = multilevel(c432, "sa", target=12, seed=1)
    coarse = coarsen(c432, target=12, seed=1).coarsest
    assert result.info["lam"] == pytest.approx(suggest_lambda(coarse))
    assert result.info["lam_source"] == "rule"
    explicit = multilevel(c432, "sa", target=12, seed=1, lam=2.0)
    assert explicit.info["lam"] == 2.0 and explicit.info["lam_source"] == "explicit"


# ----------------------------------------------------------------- CLI routing


FAST = ["--target", "6", "--restarts", "2", "--shots", "200"]


def _record(folder: Path) -> dict:
    [path] = list(folder.glob("*.json"))
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("solver", ["qaoa", "brute", "random", "sa"])
def test_large_design_routes_to_multilevel(solver, c432, tmp_path, capsys):
    assert cli.main(["partition", str(NETLISTS / "c432.json"), "--solver", solver, "--results-dir", str(tmp_path), *FAST]) == 0
    out = capsys.readouterr().out
    assert "using multilevel" in out
    data = _record(tmp_path)
    assert data["solver"] == f"multilevel-{solver}"
    assert data["cut"] == true_cut(c432, data["assignment"])
    # Exact at the coarse level is not exact: nothing may call this a proven optimum.
    assert data["best_known_cut"] is None and data["best_known_source"] != "brute_force"
    assert "best known unknown" in out
    if solver == "qaoa":
        assert "at the coarse level (6 qubits)" in out
        assert data["qubits"] == 6 and data["backend"] == "aer_simulator"


@pytest.mark.parametrize("solver", ["kl", "kahypar"])
def test_large_design_keeps_flat_solvers_flat(solver, tmp_path, capsys):
    assert cli.main(["partition", str(NETLISTS / "c432.json"), "--solver", solver, "--results-dir", str(tmp_path)]) == 0
    assert "using multilevel" not in capsys.readouterr().out
    assert not _record(tmp_path)["solver"].startswith("multilevel")


def test_small_design_stays_flat_with_the_c17_lambda(tmp_path, capsys):
    assert cli.main(["partition", str(NETLISTS / "c17.json"), "--solver", "sa", "--results-dir", str(tmp_path)]) == 0
    assert "using multilevel" not in capsys.readouterr().out
    data = _record(tmp_path)
    assert data["solver"] == "sa" and data["lambda"] == pytest.approx(2.0) and data["cut"] == 2


def test_explicit_lambda_reaches_multilevel(tmp_path):
    args = ["partition", str(NETLISTS / "c432.json"), "--solver", "sa", "--lam", "0.5", "--results-dir", str(tmp_path), *FAST]
    assert cli.main(args) == 0
    assert _record(tmp_path)["lambda"] == 0.5


# ----------------------------------------------------------------- results table


def test_job_list_matches_the_plan():
    jobs = ex.summary_jobs()
    assert len(jobs) == len(set(jobs))
    assert sum(j.design == "c17" and j.solver == "brute" for j in jobs) == 1
    for design in ("c432", "c880"):
        for solver in ("random", "sa", "brute", "qaoa"):
            assert sum(j.design == design and j.solver == solver and j.target == 12 for j in jobs) == 10
            assert sum(j.design == design and j.solver == solver and j.target == 20 for j in jobs) == 3
    assert all(j.target is None for j in jobs if j.solver in ("kl", "kahypar"))
    assert max(j.target or 0 for j in jobs) <= 24  # CLAUDE.md qubit limit


def test_same_seed_means_same_coarse_circuit_for_every_solver(c432):
    a = coarsen(c432, target=12, seed=4).coarsest
    b = multilevel(c432, "random", target=12, seed=4).info
    c = multilevel(c432, "brute", target=12, seed=4).info
    assert b["levels"][0]["nodes"] == c["levels"][0]["nodes"] == a.num_nodes
    assert b["weight_cap"] == c["weight_cap"]
    # Brute force's coarse cut must be the true optimum of that same coarse circuit.
    assert c["coarse_cut"] == brute_force(a, build_partition_qubo(a)).best_cut


@pytest.mark.parametrize(
    "job",
    [
        ex.Job("c17", "brute", None),
        ex.Job("c17", "kl", 1),
        ex.Job("c432", "kahypar", 1),
        ex.Job("c432", "sa", 2, 12),
        ex.Job("c432", "brute", 3, 12),
    ],
)
def test_row_cut_recomputes_from_its_assignment_and_repeats(job):
    hg = ex.load_benchmark(job.design)
    row, again = ex.run_job(job, hg), ex.run_job(job, hg)
    assert row["status"] == "ok"
    bits = [int(c) for c in row["assignment"]]
    assert row["cut"] == true_cut(hg, bits) and row["balanced"] == is_balanced(hg, bits)
    row.pop("runtime_s"), again.pop("runtime_s")
    assert row == again


def test_qaoa_row_records_its_settings():
    row = ex.run_job(ex.Job("c17", "qaoa", 1), ex.load_benchmark("c17"), shots=200, restarts=2)
    assert (row["seed"], row["shots"], row["restarts"], row["p"], row["qubits"]) == (1, 200, 2, 1, 6)
    assert row["backend"] == "aer_simulator" and row["normalize"] is False
    assert row["lambda"] == pytest.approx(2.0) and row["lambda_source"] == "rule"
    ml = ex.run_job(ex.Job("c432", "qaoa", 1, 6), ex.load_benchmark("c432"), shots=200, restarts=2)
    assert ml["normalize"] is True and ml["coarsen_seed"] == 1 and ml["qubits"] == 6 and ml["coarse_cut"] != ""


def test_a_solver_failure_becomes_a_visible_row(monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("could not draw a balanced split")

    monkeypatch.setattr(ex, "multilevel", boom)
    row = ex.run_job(ex.Job("c432", "random", 0, 12), ex.load_benchmark("c432"))
    assert row["status"] == "error" and "balanced split" in row["error"] and row["cut"] == ""


CLASSICAL_RUN = """
from project_q import experiments as ex
for job in [ex.Job('c432', 'kl', 1), ex.Job('c432', 'random', 1), ex.Job('c432', 'sa', 1, 12), ex.Job('c432', 'brute', 1, 12)]:
    row = ex.run_job(job, ex.load_benchmark(job.design))
    print(row['solver'], row['cut'], row['assignment'])
"""


def test_classical_rows_do_not_depend_on_python_hash_order():
    outputs = set()
    for hash_seed in ("1", "2"):
        env = os.environ | {"PYTHONHASHSEED": hash_seed}
        outputs.add(subprocess.run([sys.executable, "-c", CLASSICAL_RUN], capture_output=True, text=True, check=True, env=env).stdout)
    assert len(outputs) == 1


def _str_row(**fields) -> dict:
    return {k: str(v) for k, v in fields.items()}


def test_best_known_is_proven_only_on_c17():
    rows = [
        _str_row(design="c17", solver="brute", status="ok", balanced=True, cut=2),
        _str_row(design="c17", solver="random", status="ok", balanced=True, cut=3),
        _str_row(design="c432", solver="multilevel-brute", status="ok", balanced=True, cut=11),
        _str_row(design="c432", solver="kahypar", status="ok", balanced=True, cut=12),
        _str_row(design="c432", solver="multilevel-sa", status="ok", balanced=False, cut=5),
    ]
    ex.fill_best_known(rows)
    assert (rows[1]["best_known_cut"], rows[1]["best_known_source"], rows[1]["gap_percent"]) == (2, "brute_force", 50.0)
    assert rows[2]["best_known_source"] == "best_of_this_run" and rows[3]["best_known_cut"] == 11
    # An unbalanced cut of 5 can't become the best, and gets no gap.
    assert rows[4]["best_known_cut"] == 11 and rows[4]["gap_percent"] == ""


# ----------------------------------------------------------------- ablation verdict


def _ablation_rows(random_cuts, qaoa_cuts, design="c432", target=12, unbalanced=()):
    rows = []
    for solver, cuts in (("multilevel-random", random_cuts), ("multilevel-qaoa", qaoa_cuts)):
        for seed, cut in enumerate(cuts):
            balanced = not (solver == "multilevel-qaoa" and seed in unbalanced)
            rows.append(_str_row(
                design=design, target=target, solver=solver, seed=seed, status="ok", cut=cut, balanced=balanced,
                coarse_cut=cut + 5, mode="multilevel",
            ))  # fmt: skip
    return rows


def test_verdict_says_matches_when_they_tie():
    v = ex.ablation_verdict(_ablation_rows([12] * 10, [12] * 10), "c432", 12)
    assert v.sentence.startswith("Random+FM matches QAOA+FM.") and v.ties == 10 and v.p_value == 1.0


def test_verdict_says_matches_when_random_is_better():
    v = ex.ablation_verdict(_ablation_rows([11] * 10, [13] * 10), "c432", 12)
    assert v.sentence.startswith("Random+FM matches QAOA+FM.") and v.random_better == 10


def test_verdict_says_matches_when_qaoa_is_ahead_but_not_significantly():
    v = ex.ablation_verdict(_ablation_rows([13, 13, 13, 13, 12, 12, 12, 12, 12, 12], [12, 12, 12, 12, 13, 12, 12, 12, 12, 12]), "c432", 12)
    assert v.qaoa_mean < v.random_mean and v.p_value >= 0.05
    assert v.sentence.startswith("Random+FM matches QAOA+FM.")


def test_verdict_says_beat_only_when_lower_and_significant():
    v = ex.ablation_verdict(_ablation_rows([14] * 10, [12] * 10), "c432", 12)
    assert v.sentence.startswith("QAOA+FM beat random+FM.") and v.p_value < 0.05


def test_verdict_with_few_seeds_is_descriptive_only():
    v = ex.ablation_verdict(_ablation_rows([14] * 3, [12] * 3), "c432", 12)
    assert v.sentence.startswith("Descriptive only") and "beat" not in v.sentence


def test_verdict_leaves_out_unbalanced_runs_and_counts_them():
    v = ex.ablation_verdict(_ablation_rows([14] * 10, [1] + [14] * 9, unbalanced={0}), "c432", 12)
    assert v.seeds == 9 and v.excluded == 1 and v.ties == 9


# ----------------------------------------------------------------- plots


def test_plots_write_png_files(tmp_path):
    rows = _ablation_rows([14] * 10, [12] * 10) + _ablation_rows([14] * 3, [12] * 3, target=20)
    rows += [_str_row(design="c432", solver="kahypar", status="ok", cut=11, target="", mode="flat", balanced=True)]
    assert plot_ablation(rows, tmp_path / "a.png").stat().st_size > 1000

    sweep = [
        _str_row(
            instance="x", **{"lambda": lam}, rule_lambda=0.7, best_known_cut=12, mean_balanced_shot_cut=25,
            qubo_min_cut=12, feasible_rate=0.2, mean_shot_balance_ratio=1.2, qubo_min_balance_ratio=1.01,
        )
        for lam in ex.LAMBDA_GRID
    ]  # fmt: skip
    assert plot_lambda_sweep(sweep, tmp_path / "s.png").stat().st_size > 1000


def test_verdict_headline_keeps_decimal_numbers():
    sentence = ex.ablation_verdict(_ablation_rows([14] * 3, [12] * 3), "c432", 12).sentence
    assert verdict_headline(sentence) == "Descriptive only, 3 seeds can't reach p < 0.05"


def test_cut_axis_shows_every_run_at_every_target():
    # The first draft shared an axis that stopped at the top row's range and hid 38 and 80.
    rows = _ablation_rows([14] * 10, [12] * 10) + _ablation_rows([70] * 3, [12] * 3, target=20)
    assert cut_axis_top(rows, "c432") > 75  # 70 + 5 is the largest coarse cut


def test_partition_picture_agrees_with_true_cut(tmp_path):
    hg = reference_c17()
    best = brute_force(hg, build_partition_qubo(hg, lam=2.0)).best
    assert plot_partition(hg, best.assignment, tmp_path / "p.png").stat().st_size > 1000


# ----------------------------------------------------------------- wording


def test_phase6_files_avoid_the_banned_phrase():
    phrase = "quantum " + "advantage"
    paths = [ROOT / "project_q" / n for n in ("experiments.py", "plots.py", "cli.py", "qubo.py")]
    paths += [ROOT / "scripts" / "phase6.py"]
    for path in paths:
        assert phrase not in path.read_text(encoding="utf-8").lower(), path
    defense = (ROOT / "docs" / "DEFENSE.md").read_text(encoding="utf-8")
    qa = (ROOT / "docs" / "QA_PREP.md").read_text(encoding="utf-8")
    for text in (defense, qa):
        if "## Phase 6" in text:
            assert phrase not in text.split("## Phase 6", 1)[1].lower()
