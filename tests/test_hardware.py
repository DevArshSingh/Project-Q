"""Phase 8: the hardware script and its analysis, tested with no IBM contact at all.

Every socket connection is blocked in this file, and --submit runs against a stub service and a
stub sampler, so these tests can't reach IBM even by mistake.
"""

import importlib.util
import json
import socket
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from c17_reference import reference_c17
from qiskit.circuit import QuantumCircuit
from qiskit.quantum_info import Statevector
from qiskit_ibm_runtime.fake_provider import FakeFez

from project_q import hardware as hw
from project_q.experiments import load_benchmark
from project_q.parse_yosys import is_balanced, true_cut
from project_q.qubo import build_partition_qubo
from project_q.solvers import _ansatz_values, _bits_from_key, qaoa, qubo_to_ising

ROOT = Path(__file__).resolve().parents[1]
SENTINEL = "SENTINEL-TOKEN-do-not-print-0123456789"

_spec = importlib.util.spec_from_file_location("hardware_c17", ROOT / "scripts" / "hardware_c17.py")
script = importlib.util.module_from_spec(_spec)
sys.modules["hardware_c17"] = script
_spec.loader.exec_module(script)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("a test tried to open a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)


@pytest.fixture(scope="module")
def problem():
    return hw.load_problem()


def key_of(bits: list[int]) -> str:
    """Inverse of _bits_from_key: qubit 0 is the rightmost character."""
    return "".join(str(b) for b in reversed(bits))


@pytest.fixture(scope="module")
def keys(problem):
    """Example c17 shots of each kind, found by checking all 64 states with true_cut and is_balanced."""
    hg = problem[0]
    states = [[(k >> i) & 1 for i in range(6)] for k in range(64)]
    optimal = [s for s in states if is_balanced(hg, s) and true_cut(hg, s) == 2]
    worse = next(s for s in states if is_balanced(hg, s) and true_cut(hg, s) == 3)
    return SimpleNamespace(
        optimal=key_of(optimal[0]),
        mirror=key_of([1 - b for b in optimal[0]]),
        worse=key_of(worse),
        one_side="000000",  # cut 0, but every gate on one side, so it is not a valid answer
        lopsided="000001",
        all_optimal=[key_of(s) for s in optimal],
    )


# ----------------------------------------------------------------- angles and circuit


def test_angles_match_what_the_optimizer_finds_on_aer():
    hg = reference_c17()
    result = qaoa(hg, build_partition_qubo(hg), seed=0, best_known_cut=2)
    assert round(result.info["gammas"][0], 4) == hw.GAMMA
    assert round(result.info["betas"][0], 4) == hw.BETA


@pytest.mark.parametrize("hg", [reference_c17(), load_benchmark("c17")], ids=["reference", "yosys"])
def test_energy_at_the_fixed_angles_is_the_known_minimum_on_both_c17_versions(hg):
    qubo = build_partition_qubo(hg)
    operator, offset = qubo_to_ising(qubo)
    circuit, values = hw.build_circuit(qubo)
    circuit.remove_final_measurements()
    energy = Statevector(circuit.assign_parameters(values)).expectation_value(operator).real + offset
    assert energy == pytest.approx(4.026, abs=1e-3)


def test_values_bind_gamma_and_beta_to_the_right_parameters(problem):
    circuit, values = hw.build_circuit(build_partition_qubo(problem[0]))
    assert dict(zip((p.name for p in circuit.parameters), values)) == {"β[0]": hw.BETA, "γ[0]": hw.GAMMA}


def test_preflight_gives_the_expected_guessing_rates(problem):
    assert problem[2]["feasible_rate"] == pytest.approx(20 / 64)
    assert problem[2]["optimal_split_probability"] == pytest.approx(2 / 64)


def test_preflight_refuses_if_the_problem_changed(monkeypatch):
    monkeypatch.setattr(hw, "EXPECTED_BEST_CUT", 3)
    with pytest.raises(RuntimeError, match="pre-flight"):
        hw.load_problem()


# ----------------------------------------------------------------- transpile choice


def test_transpile_logs_ten_seeds_and_keeps_the_fewest_two_qubit_gates(problem):
    circuit, _ = hw.build_circuit(build_partition_qubo(problem[0]))
    best, log = hw.transpile_best(circuit, FakeFez())
    assert [row["seed"] for row in log["all_seeds"]] == list(range(10))
    fewest = min(row["two_qubit_gates"] for row in log["all_seeds"])
    assert log["chosen"]["two_qubit_gates"] == fewest
    assert hw.circuit_metrics(best) == log["chosen"]
    chosen_row = next(row for row in log["all_seeds"] if row["seed"] == log["chosen_seed"])
    assert {k: chosen_row[k] for k in log["chosen"]} == log["chosen"]
    for row in log["all_seeds"]:
        assert set(row) == {"seed", "depth", "two_qubit_gates", "two_qubit_depth"}


def _circuit_of(two_qubit_gates: int, extra_depth: int) -> QuantumCircuit:
    circuit = QuantumCircuit(2)
    for _ in range(two_qubit_gates):
        circuit.cz(0, 1)
    for _ in range(extra_depth):
        circuit.x(0)
    return circuit


def test_ties_go_to_lower_depth_then_lower_seed(monkeypatch):
    by_seed = {0: (5, 0), 1: (3, 9), 2: (3, 2), 3: (3, 2), 4: (4, 0)}

    def fake_manager(optimization_level, backend, seed_transpiler):
        assert optimization_level == 3 and backend == "backend"
        return SimpleNamespace(run=lambda circuit: _circuit_of(*by_seed[seed_transpiler]))

    monkeypatch.setattr(hw, "generate_preset_pass_manager", fake_manager)
    _, log = hw.transpile_best(None, "backend", seeds=tuple(by_seed))
    assert log["chosen_seed"] == 2


def test_chosen_circuit_without_noise_matches_aer_so_layout_and_bit_order_survive(problem):
    # Transpiling moves qubits around. If keys came back in another order, every score would be wrong.
    circuit, values = hw.build_circuit(build_partition_qubo(problem[0]))
    best, _ = hw.transpile_best(circuit, FakeFez(), seeds=(0, 1))
    shots = 20000
    original = hw.aer_baseline(circuit, values, shots=shots, seed=0)
    transpiled = hw.aer_baseline(best, values, shots=shots, seed=1)
    distance = sum(abs(original.get(k, 0) - transpiled.get(k, 0)) for k in set(original) | set(transpiled)) / (2 * shots)
    assert distance < 0.05


def test_chosen_circuit_uses_only_the_backends_gates(problem):
    backend = FakeFez()
    circuit, _ = hw.build_circuit(build_partition_qubo(problem[0]))
    best, _ = hw.transpile_best(circuit, backend, seeds=(0,))
    allowed = set(backend.target.operation_names) | {"barrier", "measure"}
    assert set(best.count_ops()) <= allowed


def test_size_limits():
    fine = {"depth": hw.MAX_DEPTH, "two_qubit_gates": hw.MAX_TWO_QUBIT_GATES}
    assert hw.too_large(fine) is None
    assert "two-qubit" in hw.too_large(fine | {"two_qubit_gates": hw.MAX_TWO_QUBIT_GATES + 1})
    assert "depth" in hw.too_large(fine | {"depth": hw.MAX_DEPTH + 1})


# ----------------------------------------------------------------- analysis, on simulated counts


def test_rates_on_hand_counted_shots(problem, keys):
    hg = problem[0]
    counts = {keys.optimal: 100, keys.mirror: 50, keys.worse: 250, keys.one_side: 300, keys.lopsided: 300}
    stats = hw.shot_stats(hg, counts, 2)
    assert stats["shots"] == 1000
    assert stats["feasible_rate"] == pytest.approx(0.4)  # 100 + 50 + 250
    assert stats["optimal_split_probability"] == pytest.approx(0.15)  # the split and its mirror
    assert stats["best_balanced_cut"] == 2
    assert stats["feasible_rate_stderr"] == pytest.approx((0.4 * 0.6 / 1000) ** 0.5)


def test_unbalanced_cut_zero_never_counts_as_optimal(problem, keys):
    stats = hw.shot_stats(problem[0], {keys.one_side: 10, keys.worse: 10}, 2)
    assert stats["optimal_split_probability"] == 0
    assert stats["best_balanced_cut"] == 3


def test_no_balanced_shots_gives_null_not_a_fake_cut(problem, keys):
    stats = hw.shot_stats(problem[0], {keys.one_side: 7, keys.lopsided: 3}, 2)
    assert stats["feasible_rate"] == 0 and stats["best_balanced_cut"] is None


def test_key_order_matters(problem):
    # Qiskit's rightmost character is qubit 0. Find a balanced key whose reversal has another
    # cut, so that reading keys the wrong way round would change the score.
    hg = problem[0]
    for k in range(64):
        key = format(k, "06b")
        bits = _bits_from_key(key)
        if is_balanced(hg, bits) and true_cut(hg, bits) != true_cut(hg, bits[::-1]):
            assert hw.shot_stats(hg, {key: 1}, 2)["best_balanced_cut"] == true_cut(hg, bits)
            return
    pytest.fail("every balanced state has the same cut as its reversal, so this test checks nothing")


def test_uniform_shots_score_the_guessing_rates(problem):
    hg, _, uniform = problem
    counts = {format(k, "06b"): 10 for k in range(64)}
    stats = hw.shot_stats(hg, counts, 2)
    assert stats["feasible_rate"] == pytest.approx(uniform["feasible_rate"])
    assert stats["optimal_split_probability"] == pytest.approx(uniform["optimal_split_probability"])


@pytest.mark.parametrize("bad", [{}, {"000": 5}])
def test_bad_counts_are_refused(problem, bad):
    with pytest.raises(ValueError):
        hw.shot_stats(problem[0], bad, 2)


def _report(problem, keys, kind="hardware", job_id="job-123"):
    hg, _, uniform = problem
    counts = {keys.optimal: 30, keys.worse: 50, keys.one_side: 20}
    aer = {keys.optimal: 60, keys.mirror: 20, keys.lopsided: 20}
    return hw.analyze_counts(hg, counts, aer, 2, uniform, kind=kind, job_id=job_id, backend="ibm_test",
                             transpile={"chosen_seed": 7})  # fmt: skip


def test_report_has_every_required_field(problem, keys):
    report = _report(problem, keys)
    assert report["job_id"] == "job-123"
    assert report["counts"] == {keys.optimal: 30, keys.worse: 50, keys.one_side: 20}
    assert report["measured"]["feasible_rate"] == pytest.approx(0.8)
    assert report["measured"]["optimal_split_probability"] == pytest.approx(0.3)
    assert report["aer"]["optimal_split_probability"] == pytest.approx(0.8)
    assert report["best_cut_vs_aer"] == {"measured": 2, "aer": 2}
    assert report["uniform_guessing"]["feasible_rate"] == pytest.approx(20 / 64)
    assert sorted(report["optimal_states"]) == sorted(keys.all_optimal)
    assert report["transpile"]["chosen_seed"] == 7
    assert "proves little" in report["best_cut_note"]


def test_report_and_plot_are_written(problem, keys, tmp_path):
    report = _report(problem, keys)
    json_path, png_path = tmp_path / "r" / "out.json", tmp_path / "p" / "out.png"
    hw.write_report(report, json_path, png_path)
    assert json.loads(json_path.read_text(encoding="utf-8")) == report
    assert png_path.stat().st_size > 0


def test_results_are_never_overwritten(problem, keys, tmp_path):
    report = _report(problem, keys)
    json_path = tmp_path / "out.json"
    hw.write_report(report, json_path, tmp_path / "out.png")
    with pytest.raises(FileExistsError):
        hw.write_report(report, json_path, tmp_path / "out.png")


@pytest.mark.parametrize("target", ["json", "png"])
def test_dry_run_data_cannot_take_the_real_paths(problem, keys, tmp_path, target):
    report = _report(problem, keys, kind="dry_run", job_id=None)
    paths = (hw.RESULT_JSON, tmp_path / "x.png") if target == "json" else (tmp_path / "x.json", hw.RESULT_PNG)
    with pytest.raises(ValueError, match="dry run"):
        hw.write_report(report, *paths)
    assert not hw.RESULT_JSON.exists() and not hw.RESULT_PNG.exists()


def test_unknown_kind_is_refused(problem, keys):
    with pytest.raises(ValueError):
        _report(problem, keys, kind="maybe")


# ----------------------------------------------------------------- the script, with stubs


class StubBackend(FakeFez):
    def status(self):
        return SimpleNamespace(pending_jobs=3)


class StubJob:
    def __init__(self, world):
        self.world = world

    def job_id(self):
        return "stub-job-1"

    def result(self):
        if self.world.result_error:
            raise self.world.result_error
        counts = {format(k, "06b"): 62 for k in range(64)} | {"000000": 32}
        return [SimpleNamespace(data=SimpleNamespace(meas=SimpleNamespace(get_counts=lambda: counts)))]

    def in_final_state(self):
        return self.world.job_finished

    def status(self):
        return "QUEUED"

    def usage(self):
        return 3.0


class StubSampler:
    """Stands in for SamplerV2 and records how it was called."""

    world = None

    def __init__(self, mode):
        self.mode = mode
        self.options = SimpleNamespace(max_execution_time=None)

    def run(self, pubs, shots=None):
        world = StubSampler.world
        world.events.append("run")
        world.runs.append({"mode": self.mode, "pubs": pubs, "shots": shots, "limit": self.options.max_execution_time})
        world.marker_at_run = script.JOB_FILE.exists()
        if world.run_error:
            raise world.run_error
        return StubJob(world)


class StubService:
    def __init__(self, world):
        self.world = world

    def backends(self, **filters):
        self.world.filters = filters
        return [self.world.backend]

    def backend(self, name):
        self.world.named = name
        return self.world.backend

    def job(self, job_id):
        self.world.fetched.append(job_id)
        return StubJob(self.world)


@pytest.fixture
def world(monkeypatch, tmp_path):
    w = SimpleNamespace(
        tmp=tmp_path, events=[], runs=[], connects=[], fetched=[], filters=None, named=None, marker_at_run=None,
        backend=StubBackend(), connect_error=None, run_error=None, result_error=None, job_finished=True, answers=[],
    )  # fmt: skip
    StubSampler.world = w

    def stub_connect(token, instance):
        w.connects.append(instance)
        if w.connect_error:
            raise w.connect_error
        return StubService(w)

    def stub_input(prompt=""):
        w.events.append("prompt")
        print(prompt)
        if not w.answers:
            raise EOFError
        return w.answers.pop(0)

    monkeypatch.setattr(script, "connect", stub_connect)
    monkeypatch.setattr(script, "SamplerV2", StubSampler)
    monkeypatch.setattr("builtins.input", stub_input)
    monkeypatch.setattr(hw, "TRANSPILE_SEEDS", (0, 1))
    monkeypatch.setattr(script, "JOB_FILE", tmp_path / "results" / "job.json")
    monkeypatch.setattr(script, "RESULT_JSON", tmp_path / "results" / "hardware.json")
    monkeypatch.setattr(script, "RESULT_PNG", tmp_path / "plots" / "hardware.png")
    monkeypatch.setattr(script, "DRY_RUN_JSON", tmp_path / "dry" / "hardware.json")
    monkeypatch.setattr(script, "DRY_RUN_PNG", tmp_path / "dry" / "hardware.png")
    monkeypatch.delenv(script.TOKEN_ENV, raising=False)
    return w


def files_contain(root: Path, text: str) -> list[Path]:
    return [p for p in root.rglob("*") if p.is_file() and text.encode() in p.read_bytes()]


def test_only_the_named_env_variable_supplies_the_token(world, monkeypatch):
    assert script.read_token() is None
    monkeypatch.setenv("IBM_TOKEN", SENTINEL)
    assert script.read_token() is None
    monkeypatch.setenv(script.TOKEN_ENV, SENTINEL)
    assert script.read_token() == SENTINEL


def test_real_connect_uses_the_env_token_explicitly(monkeypatch):
    seen = {}

    class Recorder:
        def __init__(self, **kwargs):
            seen.update(kwargs)

    monkeypatch.setattr("qiskit_ibm_runtime.QiskitRuntimeService", Recorder)
    script.connect(SENTINEL, None)
    assert seen == {"channel": "ibm_quantum_platform", "token": SENTINEL, "instance": None}


# Dry run


def test_dry_run_is_the_default_needs_no_token_and_never_connects(world, monkeypatch, capsys):
    monkeypatch.setattr(script, "SamplerV2", _real_sampler())
    assert script.main([]) == 0
    assert world.connects == [] and world.fetched == []
    assert "prompt" not in world.events
    report = json.loads(script.DRY_RUN_JSON.read_text(encoding="utf-8"))
    assert report["kind"] == "dry_run" and report["job_id"] is None and report["shots"] == 4000
    assert script.DRY_RUN_PNG.exists()
    assert not script.RESULT_JSON.exists() and not script.JOB_FILE.exists()
    out = capsys.readouterr().out
    assert "DRY RUN" in out and "not hardware results" in out


def test_dry_run_ignores_a_token_in_the_environment(world, monkeypatch, capsys):
    monkeypatch.setattr(script, "SamplerV2", _real_sampler())
    monkeypatch.setenv(script.TOKEN_ENV, SENTINEL)
    monkeypatch.setattr(script, "read_token", lambda: pytest.fail("a dry run must not read the token"))
    assert script.main([]) == 0
    captured = capsys.readouterr()
    assert SENTINEL not in captured.out + captured.err
    assert files_contain(world.tmp, SENTINEL) == []


def test_dry_run_can_repeat_and_gives_the_same_numbers(world, monkeypatch):
    monkeypatch.setattr(script, "SamplerV2", _real_sampler())
    assert script.main([]) == 0
    first = json.loads(script.DRY_RUN_JSON.read_text(encoding="utf-8"))
    assert script.main([]) == 0
    assert json.loads(script.DRY_RUN_JSON.read_text(encoding="utf-8")) == first


def _real_sampler():
    from qiskit_ibm_runtime import SamplerV2

    return SamplerV2


# Submit gate


def test_submit_without_token_does_not_connect(world, capsys):
    assert script.main(["--submit"]) == 2
    assert world.connects == [] and world.runs == []
    assert "is not set" in capsys.readouterr().out


@pytest.mark.parametrize("answer", ["no", "", "YES", "Yes", " yes", "yes ", "y"])
def test_anything_but_exact_yes_does_not_submit(world, monkeypatch, answer):
    monkeypatch.setenv(script.TOKEN_ENV, SENTINEL)
    world.answers = [answer]
    assert script.main(["--submit"]) == 1
    assert world.runs == [] and not script.JOB_FILE.exists()


def test_closed_input_does_not_submit(world, monkeypatch):
    monkeypatch.setenv(script.TOKEN_ENV, SENTINEL)
    assert script.main(["--submit"]) == 1
    assert world.runs == [] and not script.JOB_FILE.exists()


def test_yes_runs_one_job_mode_sampler_with_4000_shots(world, monkeypatch):
    monkeypatch.setenv(script.TOKEN_ENV, SENTINEL)
    world.answers = ["yes"]
    assert script.main(["--submit"]) == 0
    assert len(world.runs) == 1
    run = world.runs[0]
    assert run["mode"] is world.backend  # Job mode: the backend itself, not a Session
    assert run["shots"] == 4000 and run["limit"] == hw.MAX_EXECUTION_TIME_S
    assert len(run["pubs"]) == 1
    circuit, values = run["pubs"][0]
    assert list(values) == list(_ansatz_values(circuit, [hw.GAMMA], [hw.BETA]))  # the fixed angles, unchanged
    assert world.filters == {"simulator": False, "operational": True, "min_num_qubits": 6}

    report = json.loads(script.RESULT_JSON.read_text(encoding="utf-8"))
    assert report["kind"] == "hardware" and report["job_id"] == "stub-job-1" and report["usage_s"] == 3.0
    assert report["angles"]["gamma"] == hw.GAMMA and report["angles"]["beta"] == hw.BETA
    assert len(report["transpile"]["all_seeds"]) == 2  # the fixture limits this test to 2 seeds
    assert report["aer"]["shots"] == 4000  # baseline computed in the same run
    assert script.RESULT_PNG.exists()
    marker = json.loads(script.JOB_FILE.read_text(encoding="utf-8"))
    assert marker["job_id"] == "stub-job-1" and marker["status"] == "done"


def test_metrics_print_before_the_prompt(world, monkeypatch, capsys):
    monkeypatch.setenv(script.TOKEN_ENV, SENTINEL)
    world.answers = ["no"]
    script.main(["--submit"])
    out = capsys.readouterr().out
    prompt = out.index("Type yes to submit")
    for text in ("Circuit depth:", "2-qubit gates:", "Shots:           4000", "Estimated time:"):
        assert out.index(text) < prompt


def test_marker_is_on_disk_before_the_job_is_sent(world, monkeypatch):
    monkeypatch.setenv(script.TOKEN_ENV, SENTINEL)
    world.answers = ["yes"]
    script.main(["--submit"])
    assert world.marker_at_run is True


def test_named_backend_is_used(world, monkeypatch):
    monkeypatch.setenv(script.TOKEN_ENV, SENTINEL)
    world.answers = ["no"]
    script.main(["--submit", "--backend", "ibm_chosen"])
    assert world.named == "ibm_chosen"


# One run only


def test_second_submit_is_refused_before_connecting(world, monkeypatch, capsys):
    monkeypatch.setenv(script.TOKEN_ENV, SENTINEL)
    world.answers = ["yes", "yes"]
    assert script.main(["--submit"]) == 0
    world.connects.clear()
    assert script.main(["--submit"]) == 2
    assert world.connects == [] and len(world.runs) == 1
    assert "one hardware run" in capsys.readouterr().out


def test_failed_send_still_blocks_a_second_submit(world, monkeypatch):
    monkeypatch.setenv(script.TOKEN_ENV, SENTINEL)
    world.answers = ["yes"]
    world.run_error = ConnectionError("dropped")
    assert script.main(["--submit"]) == 1
    marker = json.loads(script.JOB_FILE.read_text(encoding="utf-8"))
    assert marker["status"] == "submit_failed" and marker["job_id"] is None and "dropped" in marker["error"]
    world.answers = ["yes"]
    assert script.main(["--submit"]) == 2
    assert len(world.runs) == 1  # no retry


def test_failed_job_is_recorded_not_retried(world, monkeypatch):
    monkeypatch.setenv(script.TOKEN_ENV, SENTINEL)
    world.answers = ["yes"]
    world.result_error = RuntimeError("job failed on the backend")
    assert script.main(["--submit"]) == 1
    assert len(world.runs) == 1 and not script.RESULT_JSON.exists()
    marker = json.loads(script.JOB_FILE.read_text(encoding="utf-8"))
    assert marker["job_id"] == "stub-job-1" and "job failed" in marker["error"]


def test_marker_appearing_late_is_not_overwritten(world, monkeypatch):
    # Another terminal could write the marker while this one waits at the prompt.
    monkeypatch.setenv(script.TOKEN_ENV, SENTINEL)
    world.answers = ["yes"]
    real_input = __builtins__["input"] if isinstance(__builtins__, dict) else __builtins__.input

    def input_then_other_terminal(prompt=""):
        answer = real_input(prompt)
        script.JOB_FILE.parent.mkdir(parents=True, exist_ok=True)
        script.JOB_FILE.write_text('{"job_id": "other"}', encoding="utf-8")
        return answer

    monkeypatch.setattr("builtins.input", input_then_other_terminal)
    assert script.main(["--submit"]) == 1
    assert world.runs == []
    assert json.loads(script.JOB_FILE.read_text(encoding="utf-8")) == {"job_id": "other"}


# Depth stop


def test_depth_stop_fires_before_the_prompt(world, monkeypatch, capsys):
    monkeypatch.setenv(script.TOKEN_ENV, SENTINEL)
    monkeypatch.setattr(hw, "MAX_TWO_QUBIT_GATES", 1)
    world.answers = ["yes"]
    assert script.main(["--submit"]) == 3
    assert "prompt" not in world.events and world.runs == [] and not script.JOB_FILE.exists()
    assert "Nothing was sent" in capsys.readouterr().out


# Fetch


def _write_marker(job_id="stub-job-1"):
    script.JOB_FILE.parent.mkdir(parents=True, exist_ok=True)
    marker = {"status": "submitted", "job_id": job_id, "backend": "ibm_test", "transpile": {"chosen_seed": 7}}
    script.JOB_FILE.write_text(json.dumps(marker), encoding="utf-8")


def test_fetch_collects_the_recorded_job_and_never_runs_a_sampler(world, monkeypatch):
    monkeypatch.setenv(script.TOKEN_ENV, SENTINEL)
    _write_marker()
    assert script.main(["--fetch"]) == 0
    assert world.fetched == ["stub-job-1"] and world.runs == []
    assert "prompt" not in world.events
    report = json.loads(script.RESULT_JSON.read_text(encoding="utf-8"))
    assert report["job_id"] == "stub-job-1" and report["aer"]["shots"] == 4000


def test_fetch_of_an_unfinished_job_writes_nothing(world, monkeypatch, capsys):
    monkeypatch.setenv(script.TOKEN_ENV, SENTINEL)
    world.job_finished = False
    _write_marker()
    assert script.main(["--fetch"]) == 0
    assert not script.RESULT_JSON.exists()
    assert "not finished" in capsys.readouterr().out


def test_fetch_without_a_job_does_not_connect(world, monkeypatch):
    monkeypatch.setenv(script.TOKEN_ENV, SENTINEL)
    assert script.main(["--fetch"]) == 2
    _write_marker(job_id=None)
    assert script.main(["--fetch"]) == 2
    assert world.connects == []


def test_submit_and_fetch_cannot_be_combined(world):
    with pytest.raises(SystemExit):
        script.main(["--submit", "--fetch"])


# Token


def test_token_never_appears_even_when_a_connection_error_quotes_it(world, monkeypatch, capsys):
    monkeypatch.setenv(script.TOKEN_ENV, SENTINEL)
    world.connect_error = RuntimeError(f"401 for token {SENTINEL}")
    assert script.main(["--submit"]) == 1
    captured = capsys.readouterr()
    assert SENTINEL not in captured.out + captured.err
    assert "[token removed]" in captured.out
    assert files_contain(world.tmp, SENTINEL) == []


def test_token_never_reaches_output_or_files_on_a_full_run(world, monkeypatch, capsys):
    monkeypatch.setenv(script.TOKEN_ENV, SENTINEL)
    world.answers = ["yes"]
    assert script.main(["--submit"]) == 0
    captured = capsys.readouterr()
    assert SENTINEL not in captured.out + captured.err
    assert files_contain(world.tmp, SENTINEL) == []


def test_token_is_scrubbed_from_a_saved_error(world, monkeypatch, capsys):
    monkeypatch.setenv(script.TOKEN_ENV, SENTINEL)
    world.answers = ["yes"]
    world.run_error = RuntimeError(f"bad request with {SENTINEL}")
    assert script.main(["--submit"]) == 1
    captured = capsys.readouterr()
    assert SENTINEL not in captured.out + captured.err
    assert files_contain(world.tmp, SENTINEL) == []


# ----------------------------------------------------------------- nothing real was produced


def test_any_saved_hardware_result_is_labelled_as_hardware():
    # These files appear only after a real run. If one is present, it must say it is hardware.
    if hw.RESULT_JSON.exists():
        report = json.loads(hw.RESULT_JSON.read_text(encoding="utf-8"))
        assert report["kind"] == "hardware" and report["job_id"]
