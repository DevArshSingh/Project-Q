"""Phase 8: the pieces of the single IBM hardware run on c17 that need no IBM account.

This module builds the circuit, picks a transpiled version, computes the Aer baseline and
scores measured counts. It never talks to IBM. The only file that does is
scripts/hardware_c17.py, and only with --submit or --fetch.

Why the angles are fixed: hardware time is scarce and noisy, so we do not optimize on it.
The optimizer already ran on the noise-free simulator, and hardware only samples the result.

Why the score is recomputed here: a shot is just a bit string, so every rate below comes
from `true_cut` and `is_balanced`, never from the QUBO energy (CLAUDE.md rule).
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from qiskit.circuit import QuantumCircuit
from qiskit.circuit.library import qaoa_ansatz
from qiskit.transpiler import generate_preset_pass_manager
from qiskit_aer.primitives import SamplerV2 as AerSampler

from project_q.experiments import load_benchmark
from project_q.parse_yosys import Hypergraph, is_balanced, true_cut
from project_q.qubo import DEFAULT_LAMBDA, QUBO, build_partition_qubo
from project_q.solvers import _ansatz_values, _bits_from_key, brute_force, qubo_to_ising, select_best_shot

ROOT = Path(__file__).resolve().parents[1]
RESULT_JSON = ROOT / "results" / "hardware_c17.json"
RESULT_PNG = ROOT / "plots" / "hardware_vs_aer.png"
DRY_RUN_JSON = ROOT / "results" / "dryrun" / "hardware_c17.json"
DRY_RUN_PNG = ROOT / "plots" / "dryrun" / "hardware_vs_aer.png"

# Best p=1 angles on Aer: qaoa() on c17, seed 0, λ = 2.0, as printed by scripts/phase3_c17.py.
# tests/test_hardware.py checks that these still match what qaoa() finds.
GAMMA = 0.2286
BETA = -0.2621
EXPECTED_BEST_CUT = 2  # SPEC: c17's best balanced cut

SHOTS = 4000
AER_SEED = 0
TRANSPILE_SEEDS = tuple(range(10))
# BUILD_PLAN step 3 says to stop if depth looks large. Fixed before any real run, at about
# twice what the fake backends need (50-57 two-qubit gates, depth 125-169).
MAX_TWO_QUBIT_GATES = 100
MAX_DEPTH = 300
MAX_EXECUTION_TIME_S = 120  # IBM cancels the job past this much system time; protects the ~10 min budget
DEFAULT_REP_DELAY_S = 250e-6  # wait between shots so qubits can reset, if the backend doesn't report it


def load_problem() -> tuple[Hypergraph, QUBO, dict]:
    """c17 from the committed Yosys netlist, its QUBO, and uniform-guess rates from brute force.

    It also re-checks the problem before anything runs: if brute force doesn't give cut 2 at
    λ = 2.0, the angles were tuned for a different problem, so we refuse to continue.
    """
    hg = load_benchmark("c17")
    qubo = build_partition_qubo(hg, lam=DEFAULT_LAMBDA)
    bf = brute_force(hg, qubo)
    if bf.best_cut != EXPECTED_BEST_CUT or not bf.argmin_is_optimal:
        raise RuntimeError(f"pre-flight failed: brute force gives cut {bf.best_cut} on c17, expected {EXPECTED_BEST_CUT}")
    en = bf.enumeration
    uniform = {
        # What shots would score if the circuit were pure noise: every state equally likely.
        "feasible_rate": float(en.balanced.mean()),
        "optimal_split_probability": bf.n_optimal / en.balanced.size,
    }
    return hg, qubo, uniform


def build_circuit(qubo: QUBO) -> tuple[QuantumCircuit, list[float]]:
    """The p=1 QAOA circuit with measurements, plus its angles in parameter order.

    measure_all stores qubit i in classical bit i. Transpiling moves qubit i onto some physical
    qubit but keeps that link, so counts keys still read as `hg.nodes` order.
    """
    operator, _ = qubo_to_ising(qubo)
    circuit = qaoa_ansatz(operator, reps=1)
    circuit.measure_all()
    return circuit, _ansatz_values(circuit, [GAMMA], [BETA])


def two_qubit_count(circuit: QuantumCircuit) -> int:
    return sum(1 for inst in circuit.data if inst.operation.num_qubits == 2)


def circuit_metrics(circuit: QuantumCircuit) -> dict:
    """Two-qubit gates are the noisiest step on the hardware, so they are what we minimize."""
    return {
        "depth": circuit.depth(),
        "two_qubit_gates": two_qubit_count(circuit),
        "two_qubit_depth": circuit.depth(lambda inst: inst.operation.num_qubits == 2),
    }


def transpile_best(circuit: QuantumCircuit, backend, seeds=None) -> tuple[QuantumCircuit, dict]:
    """Transpile once per seed and keep the circuit with the fewest two-qubit gates.

    Transpiling translates the circuit to the chip's gates and wiring, and its layout search is
    random, so seeds give different circuits. Comparing them uses no QPU time. It looks only at
    circuit size, never at any measured result, so it can't favour a good-looking answer.
    Ties go to the lower depth, then the lower seed, so the choice is repeatable.
    """
    seeds = TRANSPILE_SEEDS if seeds is None else seeds
    runs = []
    for seed in seeds:
        manager = generate_preset_pass_manager(optimization_level=3, backend=backend, seed_transpiler=seed)
        candidate = manager.run(circuit)
        runs.append((seed, candidate, circuit_metrics(candidate)))
    seed, best, metrics = min(runs, key=lambda r: (r[2]["two_qubit_gates"], r[2]["depth"], r[0]))
    log = {
        "optimization_level": 3,
        "selection": "fewest two-qubit gates, then lowest depth, then lowest seed",
        "chosen_seed": seed,
        "chosen": metrics,
        "all_seeds": [{"seed": s, **m} for s, _, m in runs],
    }
    return best, log


def too_large(metrics: dict) -> str | None:
    """Why this circuit should not run, or None if it's within the limits fixed above."""
    if metrics["two_qubit_gates"] > MAX_TWO_QUBIT_GATES:
        return f"{metrics['two_qubit_gates']} two-qubit gates is above the limit of {MAX_TWO_QUBIT_GATES}"
    if metrics["depth"] > MAX_DEPTH:
        return f"depth {metrics['depth']} is above the limit of {MAX_DEPTH}"
    return None


def estimate_shot_seconds(circuit: QuantumCircuit, values: list[float], backend, shots: int = SHOTS) -> float:
    """Rough time on the qubits: shots × (reset delay + one circuit). Billed usage adds overhead."""
    configuration = backend.configuration() if hasattr(backend, "configuration") else None
    rep_delay = getattr(configuration, "default_rep_delay", None) or DEFAULT_REP_DELAY_S
    duration = circuit.assign_parameters(values).estimate_duration(backend.target)
    return shots * (rep_delay + duration)


def aer_baseline(circuit: QuantumCircuit, values: list[float], shots: int = SHOTS, seed: int = AER_SEED) -> dict[str, int]:
    """Noise-free counts for the same circuit and angles: what hardware would give without noise."""
    return AerSampler(seed=seed).run([(circuit, values)], shots=shots).result()[0].data.meas.get_counts()


# --------------------------------------------------------------------------- scoring


def shot_stats(hg: Hypergraph, counts: dict[str, int], best_cut: int) -> dict:
    """Balanced rate, optimal rate and best balanced cut of a set of shots."""
    shots = sum(counts.values())
    if shots <= 0:
        raise ValueError("no shots to analyze")
    feasible = optimal = 0
    for key, n in counts.items():
        if len(key) != hg.num_nodes:
            raise ValueError(f"counts key {key!r} does not have {hg.num_nodes} bits")
        bits = _bits_from_key(key)
        if is_balanced(hg, bits):
            feasible += n
            # The mirror image is the same split, so both labelings count as optimal.
            if true_cut(hg, bits) == best_cut:
                optimal += n
    bits, found_balanced = select_best_shot(counts, hg)

    def stderr(rate: float) -> float:  # how much a rate moves by chance alone at this many shots
        return math.sqrt(rate * (1 - rate) / shots)

    return {
        "shots": shots,
        "feasible_rate": feasible / shots,
        "feasible_rate_stderr": stderr(feasible / shots),
        "optimal_split_probability": optimal / shots,
        "optimal_split_probability_stderr": stderr(optimal / shots),
        "best_balanced_cut": true_cut(hg, bits) if found_balanced else None,
    }


def analyze_counts(
    hg: Hypergraph,
    counts: dict[str, int],
    aer_counts: dict[str, int],
    best_cut: int,
    uniform: dict,
    *,
    kind: str,
    job_id: str | None,
    backend: str,
    transpile: dict | None = None,
    usage_s: float | None = None,
) -> dict:
    """Everything the report says about one set of shots, next to the Aer baseline and guessing."""
    if kind not in ("hardware", "dry_run"):
        raise ValueError(f"kind must be 'hardware' or 'dry_run', got {kind!r}")
    measured, aer = shot_stats(hg, counts, best_cut), shot_stats(hg, aer_counts, best_cut)

    n = hg.num_nodes
    states = [format(k, f"0{n}b") for k in range(1 << n)]
    optimal_states = [
        s for s in states if is_balanced(hg, _bits_from_key(s)) and true_cut(hg, _bits_from_key(s)) == best_cut
    ]
    chance = 1 - (1 - uniform["optimal_split_probability"]) ** measured["shots"]
    return {
        "kind": kind,
        "design": "c17",
        "job_id": job_id,
        "backend": backend,
        "qubit_order": list(hg.nodes),  # the rightmost character of a key is the first of these
        "angles": {"gamma": GAMMA, "beta": BETA, "p": 1, "lambda": DEFAULT_LAMBDA},
        "shots": measured["shots"],
        "error_mitigation": "none (raw shots)",
        "transpile": transpile,
        "usage_s": usage_s,
        "proven_best_cut": best_cut,
        "measured": measured,
        "aer": aer,
        "uniform_guessing": uniform,
        "best_cut_vs_aer": {"measured": measured["best_balanced_cut"], "aer": aer["best_balanced_cut"]},
        "best_cut_note": (
            f"Cut {best_cut} proves little: {measured['shots']} uniform random shots would include an "
            f"optimal split with probability {chance:.6f}. The rates are the real measure."
        ),
        "optimal_states": optimal_states,
        "counts": dict(sorted(counts.items())),
        "aer_counts": dict(sorted(aer_counts.items())),
    }


def write_report(report: dict, json_path: str | Path, png_path: str | Path) -> None:
    """Save the chart, then the JSON. A dry run can't take the real paths, and results aren't overwritten.

    The chart comes first so that a plotting failure leaves no JSON, and --fetch can try again.
    """
    from project_q.plots import plot_hardware_vs_aer  # plots imports matplotlib, only needed here

    json_path, png_path = Path(json_path), Path(png_path)
    real = {RESULT_JSON.resolve(), RESULT_PNG.resolve()}
    if report["kind"] != "hardware" and {json_path.resolve(), png_path.resolve()} & real:
        raise ValueError("a dry run must not write to the real hardware result paths")
    if json_path.exists():
        raise FileExistsError(f"{json_path} already exists; results are never overwritten")
    plot_hardware_vs_aer(report, png_path)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    with open(json_path, "x", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
        f.write("\n")
