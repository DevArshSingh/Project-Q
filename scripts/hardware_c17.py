"""Phase 8: one IBM hardware run of c17 (6 qubits, p=1, fixed angles found on Aer).

    python scripts/hardware_c17.py                  dry run: local fake backend, no token, no network
    python scripts/hardware_c17.py --submit         connect, print the estimate, ask, then run one job
    python scripts/hardware_c17.py --fetch          collect the recorded job's result; never submits

--submit and --fetch read the token only from the QISKIT_IBM_TOKEN environment variable.

There is deliberately one hardware job for this project (CLAUDE.md, BUILD_PLAN Phase 8). A marker
file is written before the job is sent, and --submit refuses while it exists. No flag overrides
that. Report whatever comes back, even if noise dominates, and do not re-run for a better result.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path

from qiskit_ibm_runtime import SamplerV2
from qiskit_ibm_runtime.fake_provider import FakeFez

from project_q import hardware as hw

ROOT = Path(__file__).resolve().parents[1]
JOB_FILE = ROOT / "results" / "hardware_c17_job.json"
RESULT_JSON = hw.RESULT_JSON
RESULT_PNG = hw.RESULT_PNG
DRY_RUN_JSON = hw.DRY_RUN_JSON
DRY_RUN_PNG = hw.DRY_RUN_PNG
TOKEN_ENV = "QISKIT_IBM_TOKEN"
CHANNEL = "ibm_quantum_platform"
MIN_QUBITS = 6
DRY_RUN_NOISE_SEED = 1

# SamplerV2 is deprecated since qiskit-ibm-runtime 0.50 but still supported in the installed 0.51.
# The team chose it knowingly, so the warning shouldn't clutter the confirmation screen.
warnings.filterwarnings("ignore", message="The SamplerV2 class is deprecated")


# --------------------------------------------------------------------------- token and connection


def read_token() -> str | None:
    """The token, from this one environment variable only. Never print or save the result."""
    return os.environ.get(TOKEN_ENV) or None


def scrub(text: object, token: str | None) -> str:
    """A library error could quote the token, so remove it from anything we print or save."""
    text = str(text)
    return text.replace(token, "[token removed]") if token else text


def connect(token: str, instance: str | None):
    # Passing the token explicitly means a saved account on this machine is not used.
    from qiskit_ibm_runtime import QiskitRuntimeService

    return QiskitRuntimeService(channel=CHANNEL, token=token, instance=instance)


def choose_backend(service, name: str | None):
    """Print the available real backends, least busy first, and return the requested or first one."""
    backends = service.backends(simulator=False, operational=True, min_num_qubits=MIN_QUBITS)
    ranked = sorted(((b.status().pending_jobs, b.name, b) for b in backends), key=lambda row: row[:2])
    print("Available backends, least busy first:")
    for pending, backend_name, _ in ranked:
        print(f"  {backend_name:<20} {pending:>5} jobs waiting")
    if name is not None:
        return service.backend(name)
    if not ranked:
        raise RuntimeError("no operational backend is available")
    return ranked[0][2]


# --------------------------------------------------------------------------- shared steps


def prepare(backend):
    """Pre-flight check, circuit, transpile comparison and Aer baseline. Uses no QPU time."""
    hg, qubo, uniform = hw.load_problem()
    print(f"Pre-flight: brute force on c17 gives cut {hw.EXPECTED_BEST_CUT} at λ = {qubo.lam}")
    circuit, values = hw.build_circuit(qubo)
    isa_circuit, transpile = hw.transpile_best(circuit, backend)
    aer_counts = hw.aer_baseline(circuit, values)
    return {
        "hg": hg,
        "uniform": uniform,
        "values": values,
        "isa_circuit": isa_circuit,
        "transpile": transpile,
        "aer_counts": aer_counts,
    }


def print_estimate(plan: dict, backend) -> None:
    print(f"\nBackend: {backend.name}")
    print(f"Angles (fixed, found on Aer): γ = {hw.GAMMA}, β = {hw.BETA}")
    print("Transpile comparison (level 3):")
    print(f"  {'seed':>4} {'depth':>6} {'2q gates':>9} {'2q depth':>9}")
    for row in plan["transpile"]["all_seeds"]:
        print(f"  {row['seed']:>4} {row['depth']:>6} {row['two_qubit_gates']:>9} {row['two_qubit_depth']:>9}")
    chosen = plan["transpile"]["chosen"]
    print(f"Chosen: seed {plan['transpile']['chosen_seed']}")
    print(f"  Circuit depth:   {chosen['depth']}")
    print(f"  2-qubit gates:   {chosen['two_qubit_gates']}")
    print(f"  2-qubit depth:   {chosen['two_qubit_depth']}")
    print(f"  Shots:           {hw.SHOTS}")
    seconds = hw.estimate_shot_seconds(plan["isa_circuit"], plan["values"], backend)
    print(f"  Estimated time:  about {seconds:.1f} s on the qubits. Billed usage adds overhead.")
    print(f"  Job time limit:  {hw.MAX_EXECUTION_TIME_S} s")


def print_report(report: dict, json_path: Path, png_path: Path) -> None:
    measured, aer, uniform = report["measured"], report["aer"], report["uniform_guessing"]
    label = "Hardware" if report["kind"] == "hardware" else "Dry run"
    print(f"\n{'':<10} {'balanced':>9} {'optimal':>8} {'best cut':>9}")
    for name, row in ((label, measured), ("Aer", aer)):
        print(
            f"{name:<10} {row['feasible_rate']:>9.2%} {row['optimal_split_probability']:>8.2%} "
            f"{str(row['best_balanced_cut']):>9}"
        )
    print(f"{'Guessing':<10} {uniform['feasible_rate']:>9.2%} {uniform['optimal_split_probability']:>8.2%} {'':>9}")
    print(report["best_cut_note"])
    print(f"Wrote {json_path}\nWrote {png_path}")


def save_marker(marker: dict) -> None:
    JOB_FILE.write_text(json.dumps(marker, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def counts_from(result) -> dict[str, int]:
    return result[0].data.meas.get_counts()


# --------------------------------------------------------------------------- dry run


def dry_run() -> int:
    """Same steps as a real run on a fake backend, a local copy of a chip's layout and error rates.

    Its noise model is simple, so real hardware may well do worse. These are not hardware numbers.
    """
    backend = FakeFez()
    print("DRY RUN: local simulation only. No token, nothing sent to IBM.")
    plan = prepare(backend)
    print_estimate(plan, backend)
    reason = hw.too_large(plan["transpile"]["chosen"])
    if reason:
        print(f"\nA real run would stop here: {reason}.")
    sampler = SamplerV2(mode=backend)
    # Must not be 0: the runtime's local mode tests this value for truth, so it silently drops a
    # seed of 0 and the numbers change on every run (seen in qiskit-ibm-runtime 0.51.0).
    sampler.options.simulator.seed_simulator = DRY_RUN_NOISE_SEED
    counts = counts_from(sampler.run([(plan["isa_circuit"], plan["values"])], shots=hw.SHOTS).result())
    report = hw.analyze_counts(
        plan["hg"], counts, plan["aer_counts"], hw.EXPECTED_BEST_CUT, plan["uniform"],
        kind="dry_run", job_id=None, backend=f"{backend.name} (local noise model)", transpile=plan["transpile"],
    )  # fmt: skip
    # A second dry run replaces the first. Only real results are protected from overwriting.
    DRY_RUN_JSON.unlink(missing_ok=True)
    hw.write_report(report, DRY_RUN_JSON, DRY_RUN_PNG)
    print_report(report, DRY_RUN_JSON, DRY_RUN_PNG)
    print("These are simulated numbers, not hardware results.")
    return 0


# --------------------------------------------------------------------------- the real run


def already_used() -> Path | None:
    return next((path for path in (JOB_FILE, RESULT_JSON) if path.exists()), None)


def finish(job, marker: dict, plan_uniform: dict, hg, aer_counts: dict[str, int]) -> int:
    """Wait for the job and write the report. A failed job is recorded, never retried."""
    result = job.result()  # blocks through the queue
    usage = None
    try:
        usage = job.usage()
    except Exception:  # usage is a nice extra; losing it must not lose the counts
        pass
    report = hw.analyze_counts(
        hg, counts_from(result), aer_counts, hw.EXPECTED_BEST_CUT, plan_uniform,
        kind="hardware", job_id=marker["job_id"], backend=marker["backend"], transpile=marker["transpile"],
        usage_s=usage,
    )  # fmt: skip
    hw.write_report(report, RESULT_JSON, RESULT_PNG)
    marker["status"] = "done"
    save_marker(marker)
    print_report(report, RESULT_JSON, RESULT_PNG)
    return 0


def submit(args) -> int:
    used = already_used()
    if used is not None:
        print(f"Refusing to submit: {used} exists, and this project gets one hardware run.")
        print("Use --fetch to collect a recorded job. Do not re-run to get a better result.")
        return 2
    token = read_token()
    if token is None:
        print(f"Refusing to submit: {TOKEN_ENV} is not set.")
        return 2

    marker = None
    try:
        service = connect(token, args.instance)
        backend = choose_backend(service, args.backend)
        plan = prepare(backend)
        print_estimate(plan, backend)

        reason = hw.too_large(plan["transpile"]["chosen"])
        if reason:
            print(f"\nStopping before submitting: {reason}. Nothing was sent.")
            return 3

        print(f"\nThis sends ONE real job to {backend.name} and uses your QPU budget.")
        try:
            answer = input("Type yes to submit: ")
        except EOFError:
            answer = ""
        if answer != "yes":
            print("Not submitted.")
            return 1

        attempt = {
            "status": "submit_attempted",
            "job_id": None,
            "backend": backend.name,
            "submitted_at": datetime.now(timezone.utc).isoformat(),
            "transpile": plan["transpile"],
        }
        # Written before the job is sent: if the connection drops mid-send the job may still
        # exist, so a later --submit has to stop rather than spend budget on a second one.
        # Mode "x" fails if the file appeared since the check at the top, and `marker` stays
        # None in that case, so the failure handler can't overwrite the earlier record.
        JOB_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(JOB_FILE, "x", encoding="utf-8") as f:
            json.dump(attempt, f)
        marker = attempt

        # mode=backend is Job mode: one standalone job, no Session. No twirling or dynamical
        # decoupling is requested, so the shots are raw.
        sampler = SamplerV2(mode=backend)
        sampler.options.max_execution_time = hw.MAX_EXECUTION_TIME_S
        job = sampler.run([(plan["isa_circuit"], plan["values"])], shots=hw.SHOTS)
        marker["job_id"] = job.job_id()
        marker["status"] = "submitted"
        save_marker(marker)
        print(f"Submitted job {marker['job_id']}. Waiting for it; the queue can take a while.")
        print("If you stop waiting, the job keeps going. Collect it later with --fetch.")
        return finish(job, marker, plan["uniform"], plan["hg"], plan["aer_counts"])
    except KeyboardInterrupt:
        print("\nStopped waiting. Any submitted job keeps going. Collect it with --fetch, and do not resubmit.")
        return 130
    except Exception as exc:
        return _report_failure(exc, marker, token)


def fetch(args) -> int:
    """Collect the recorded job. There is no sampler in this path, so it cannot submit anything."""
    if RESULT_JSON.exists():
        print(f"{RESULT_JSON} already exists, so there is nothing to fetch.")
        return 0
    if not JOB_FILE.exists():
        print(f"No job recorded: {JOB_FILE} doesn't exist.")
        return 2
    marker = json.loads(JOB_FILE.read_text(encoding="utf-8"))
    if not marker.get("job_id"):
        print("A submit was attempted but no job ID was recorded. Check your jobs on the IBM dashboard.")
        return 2
    token = read_token()
    if token is None:
        print(f"Can't fetch: {TOKEN_ENV} is not set.")
        return 2
    try:
        job = connect(token, args.instance).job(marker["job_id"])
        if not job.in_final_state():
            print(f"Job {marker['job_id']} is not finished yet: {job.status()}")
            return 0
        hg, qubo, uniform = hw.load_problem()
        circuit, values = hw.build_circuit(qubo)
        return finish(job, marker, uniform, hg, hw.aer_baseline(circuit, values))
    except KeyboardInterrupt:
        print("\nStopped waiting. The job keeps going.")
        return 130
    except Exception as exc:
        return _report_failure(exc, marker, token)


def _report_failure(exc: Exception, marker: dict | None, token: str) -> int:
    """Print the error without the token or a traceback, and keep the failure on record."""
    message = scrub(f"{type(exc).__name__}: {exc}", token)
    print(f"Failed: {message}")
    if marker is not None:
        marker["error"] = message
        if marker.get("job_id") is None:
            marker["status"] = "submit_failed"
        save_marker(marker)
        print(f"Recorded in {JOB_FILE}. This run is not retried automatically.")
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Phase 8: one IBM hardware run of c17. Dry run by default.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--submit", action="store_true", help="really submit one job, after you type yes")
    mode.add_argument("--fetch", action="store_true", help="collect the recorded job's result; never submits")
    parser.add_argument("--backend", help="backend name (default: the least busy one)")
    parser.add_argument("--instance", help="IBM Cloud instance CRN (optional)")
    args = parser.parse_args(argv)
    if args.submit:
        return submit(args)
    if args.fetch:
        return fetch(args)
    return dry_run()


if __name__ == "__main__":
    sys.exit(main())
