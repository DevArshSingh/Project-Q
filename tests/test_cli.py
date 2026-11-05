"""Phase 4: scorecard records, the CLI, and the c432/c880 benchmarks."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from c17_reference import C17_JSON, C17_V, reference_c17

from project_q import cli
from project_q.parse_yosys import hypergraph_from_verilog, is_balanced, load_hypergraph, load_netlist, true_cut
from project_q.scorecard import balance_ratio, gap_percent, make_record, save_record
from project_q.solvers import SolverResult, kahypar_solver

ROOT = Path(__file__).resolve().parents[1]
BENCHMARKS = ROOT / "benchmarks"
SPEC_FIELDS = {
    "design", "solver", "k", "cut", "best_known_cut", "gap_percent", "balance_ratio", "qubits", "shots",
    "runtime_s", "backend", "lambda", "p", "seed",
}  # fmt: skip
FAST = ["--restarts", "3", "--shots", "500"]


def reported_name(solver: str) -> str:
    """KaHyPar is optional: without it, the same command honestly reports `kl-fallback`."""
    if solver != "kahypar":
        return solver
    try:
        import kahypar  # noqa: F401
    except ImportError:
        return "kl-fallback"
    return solver


def records(folder: Path) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("*.json"))]


# ----------------------------------------------------------------- scorecard


@pytest.mark.parametrize(
    ("cut", "best", "balanced", "expected"),
    [
        (2, 2, True, 0.0),
        (3, 2, True, 50.0),
        (1, 2, False, None),  # breaking the balance rule must not look like beating the best
        (2, None, True, None),
        (0, 0, True, 0.0),
        (1, 0, True, None),
    ],
)
def test_gap_percent(cut, best, balanced, expected):
    assert gap_percent(cut, best, balanced) == expected


def test_balance_ratio_agrees_with_the_balance_rule():
    hg = reference_c17()
    for k in range(64):
        bits = [(k >> i) & 1 for i in range(6)]
        assert (balance_ratio(hg, bits) <= 1.03) == is_balanced(hg, bits, 0.03)


def test_record_recomputes_the_cut_instead_of_trusting_the_solver(tmp_path):
    hg = reference_c17()
    bits = [0, 0, 1, 1, 0, 1]
    lying = SolverResult("kl", bits, cut=999, balanced=True)
    record = make_record("c17", hg, lying, 2.0, 0.03, 2, "brute_force")
    assert record.cut == 2 and record.gap_percent == 0.0
    assert record.lam is None  # KL ignores λ

    data = json.loads(save_record(record, tmp_path).read_text(encoding="utf-8"))
    assert SPEC_FIELDS <= data.keys()
    assert "lam" not in data


def test_each_save_writes_a_new_file(tmp_path):
    hg = reference_c17()
    record = make_record("c17", hg, SolverResult("kl", [0, 0, 1, 1, 0, 1], 2, True), 2.0, 0.03)
    assert save_record(record, tmp_path) != save_record(record, tmp_path)


# ----------------------------------------------------------------- KaHyPar


def test_kahypar_finds_the_c17_optimum():
    pytest.importorskip("kahypar")
    r = kahypar_solver(reference_c17())
    assert r.solver == "kahypar" and r.cut == 2 and r.balanced


def test_kahypar_reports_a_fallback_when_not_installed(monkeypatch):
    monkeypatch.setitem(sys.modules, "kahypar", None)  # makes `import kahypar` raise ImportError
    r = kahypar_solver(reference_c17())
    assert r.solver == "kl-fallback" and r.info["requested"] == "kahypar"


# ----------------------------------------------------------------- CLI: partition


@pytest.mark.parametrize("solver", cli.SOLVERS)
def test_partition_every_solver_on_c17(solver, tmp_path, capsys):
    assert cli.main(["partition", str(C17_V), "--solver", solver, "--results-dir", str(tmp_path), *FAST]) == 0
    assert f"solver {reported_name(solver)}" in capsys.readouterr().out
    [data] = records(tmp_path)
    hg = hypergraph_from_verilog(C17_V)
    assert SPEC_FIELDS <= data.keys()
    assert data["design"] == "c17" and data["k"] == 2 and data["best_known_cut"] == 2
    assert data["best_known_source"] == "brute_force"
    # The record's numbers must follow from its assignment, checked again here.
    assert data["cut"] == true_cut(hg, data["assignment"])
    assert data["balanced"] == is_balanced(hg, data["assignment"], 0.03)


def test_partition_qaoa_record_fields(tmp_path):
    cli.main(["partition", str(C17_V), "--p", "1", "--seed", "4", "--results-dir", str(tmp_path), *FAST])
    [data] = records(tmp_path)
    assert data["solver"] == "qaoa" and data["backend"] == "aer_simulator"
    assert (data["qubits"], data["shots"], data["p"], data["seed"], data["lambda"]) == (6, 500, 1, 4, 2.0)
    assert 0 <= data["info"]["feasible_rate"] <= 1 and 0 <= data["info"]["p_optimal"] <= 1


def test_module_entry_point_runs(tmp_path):
    out = subprocess.run(
        [sys.executable, "-m", "project_q.cli", "partition", "benchmarks/c17.v", "--solver", "kl",
         "--results-dir", str(tmp_path)],
        capture_output=True, text=True, check=True, cwd=ROOT,
    )  # fmt: skip
    assert "cut 2" in out.stdout


def test_lambda_reaches_the_solver(tmp_path):
    cli.main(["partition", str(C17_V), "--solver", "sa", "--lam", "3.5", "--results-dir", str(tmp_path)])
    assert records(tmp_path)[0]["lambda"] == 3.5


# ----------------------------------------------------------------- CLI: inputs and errors


def test_json_netlist_input(tmp_path):
    assert cli.main(["partition", str(C17_JSON), "--solver", "kl", "--results-dir", str(tmp_path)]) == 0
    assert records(tmp_path)[0]["cut"] == 2


def test_without_yosys_uses_the_committed_netlist(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli.shutil, "which", lambda _: None)
    assert cli.main(["partition", str(C17_V), "--solver", "kl", "--results-dir", str(tmp_path)]) == 0
    assert "Yosys not installed" in capsys.readouterr().err


def test_without_yosys_or_netlist_fails_clearly(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli.shutil, "which", lambda _: None)
    design = tmp_path / "new.v"
    design.write_text("module new(); endmodule\n")
    assert cli.main(["partition", str(design), "--results-dir", str(tmp_path)]) == 2
    assert "Yosys is not installed" in capsys.readouterr().err


def test_missing_file_fails_clearly(tmp_path, capsys):
    assert cli.main(["partition", str(tmp_path / "nope.v")]) == 2
    assert "design not found" in capsys.readouterr().err


def _chain_netlist(n: int):
    """A chain of n unit gates, as a hypergraph, for sizes between the committed benchmarks."""
    from project_q.parse_yosys import Hypergraph

    nodes = [f"g{i}" for i in range(n)]
    nets = [frozenset(pair) for pair in zip(nodes, nodes[1:])]
    return Hypergraph(nodes=nodes, nets=nets, net_names=[f"n{i}" for i in range(len(nets))])


def test_brute_between_its_limit_and_multilevel_is_refused_clearly(tmp_path, capsys, monkeypatch):
    # 21-24 nodes: too big for routine brute force, but small enough that nothing gets coarsened.
    monkeypatch.setattr(cli, "load_design", lambda _: ("chain22", _chain_netlist(22)))
    assert cli.main(["partition", "ignored.v", "--solver", "brute", "--results-dir", str(tmp_path)]) == 2
    assert "20" in capsys.readouterr().err
    assert not records(tmp_path)


# ----------------------------------------------------------------- CLI: benchmark


def test_benchmark_c17_prints_every_solver_and_writes_json(tmp_path, capsys):
    assert cli.main(["benchmark", str(C17_V), "--results-dir", str(tmp_path), *FAST]) == 0
    out = capsys.readouterr().out
    for solver in cli.SOLVERS:
        assert reported_name(solver) in out
    assert "Best known cut: 2 (brute_force)" in out and "skipped" not in out

    by_solver = {r["solver"]: r for r in records(tmp_path)}
    assert set(by_solver) == {reported_name(solver) for solver in cli.SOLVERS}
    assert by_solver["brute"]["cut"] == 2 and by_solver["brute"]["gap_percent"] == 0.0
    assert by_solver["qaoa"]["backend"] == "aer_simulator" and by_solver["kl"]["backend"] == "classical"


def test_benchmark_c432_uses_multilevel_and_never_claims_a_proven_best(tmp_path, capsys):
    design = str(BENCHMARKS / "netlists" / "c432.json")
    assert cli.main(["benchmark", design, "--target", "6", "--results-dir", str(tmp_path), *FAST]) == 0
    out = capsys.readouterr().out
    assert "skipped" not in out
    assert "best_of_this_run" in out and "isn't proven optimal" in out
    by_solver = {r["solver"]: r for r in records(tmp_path)}
    assert set(by_solver) == {
        "multilevel-brute", "multilevel-random", "multilevel-sa", "multilevel-qaoa", "kl", reported_name("kahypar"),
    }  # fmt: skip
    assert {r["best_known_source"] for r in by_solver.values()} == {"best_of_this_run"}
    assert by_solver["multilevel-qaoa"]["qubits"] == 6


# ----------------------------------------------------------------- c432 / c880


@pytest.mark.parametrize(("name", "nodes", "nets"), [("c432", 187, 200), ("c880", 480, 501)])
def test_committed_netlists_parse(name, nodes, nets):
    hg = load_hypergraph(load_netlist(BENCHMARKS / "netlists" / f"{name}.json"))
    assert (hg.num_nodes, len(hg.nets)) == (nodes, nets)


@pytest.mark.parametrize("name", ["c432", "c880"])
def test_committed_netlists_match_a_live_yosys_run(name):
    live = hypergraph_from_verilog(BENCHMARKS / f"{name}.v")
    saved = load_hypergraph(load_netlist(BENCHMARKS / "netlists" / f"{name}.json"))
    assert live.nodes == saved.nodes and set(live.nets) == set(saved.nets)


@pytest.mark.parametrize("name", ["c432", "c880"])
def test_sources_are_recorded(name):
    assert f"{name}.v" in (BENCHMARKS / "SOURCES.md").read_text(encoding="utf-8")
