"""Phase 6 runner: results table, ablation, λ rule check, λ sweep, plots (Aer simulator only).

    python scripts/phase6.py estimate             measure a few runs and print the runtime estimate
    python scripts/phase6.py summary --confirmed  results/summary.csv
    python scripts/phase6.py rule --confirmed     results/lambda_rule.csv
    python scripts/phase6.py sweep --confirmed    results/lambda_sweep.csv
    python scripts/phase6.py plots                plots/*.png and the verdict, from the CSVs
    python scripts/phase6.py all --confirmed      all of the above, in that order

Long steps refuse to start without --confirmed, so the estimate comes first. Finished rows are
saved one at a time, and a rerun skips them, so an interrupted run loses nothing. Runs are
serial, so `runtime_s` means something. Nothing here contacts IBM hardware.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from project_q import experiments as ex
from project_q.plots import plot_ablation, plot_lambda_sweep, plot_partition
from project_q.qubo import build_partition_qubo, suggest_lambda
from project_q.solvers import brute_force

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
PLOTS = ROOT / "plots"
SUMMARY = RESULTS / "summary.csv"
SWEEP = RESULTS / "lambda_sweep.csv"
RULE = RESULTS / "lambda_rule.csv"
VERDICT = RESULTS / "ablation_verdict.txt"


def _minutes(seconds: float) -> str:
    return f"{seconds / 60:5.1f} min"


def estimate() -> None:
    """Time real samples, then scale them by how many runs each step has."""
    print("Timing samples (this takes about a minute)...", flush=True)
    designs = {name: ex.load_benchmark(name) for name in ("c17", "c432", "c880")}
    jobs = ex.summary_jobs()

    def sample(job: ex.Job, **kwargs) -> float:
        start = time.perf_counter()
        ex.run_job(job, designs[job.design], **kwargs)
        return time.perf_counter() - start

    classical = [j for j in jobs if not j.uses_qaoa]
    t0 = time.perf_counter()
    for job in classical:
        ex.run_job(job, designs[job.design])
    classical_s = time.perf_counter() - t0

    c17_s = sample(ex.Job("c17", "qaoa", 0))
    t12_s = sample(ex.Job("c432", "qaoa", 0, 12))
    # 20 qubits is timed on 2 of the 20 restarts and scaled up, to keep the estimate itself short.
    t20_s = sample(ex.Job("c432", "qaoa", 0, 20), restarts=2) * 10
    n_c17 = sum(j.uses_qaoa and j.design == "c17" for j in jobs)
    n_t12 = sum(j.uses_qaoa and j.target == 12 for j in jobs)
    n_t20 = sum(j.uses_qaoa and j.target == 20 for j in jobs)

    t0 = time.perf_counter()
    instances = ex.rule_instances()
    ex.rule_row(*next(i for i in instances if i[2] == 20))
    rule_t20_s = time.perf_counter() - t0
    n_rule_t20 = sum(i[2] == 20 for i in instances)
    rule_s = rule_t20_s * n_rule_t20 + 0.5 * (len(instances) - n_rule_t20)

    n_sweep = len(ex.LAMBDA_GRID) * len(ex.SWEEP_SEEDS)
    steps = [
        (f"Classical rows ({len(classical)} runs, measured)", classical_s),
        (f"λ rule check ({len(instances)} instances, {n_rule_t20} at 20 nodes)", rule_s),
        (f"c17 QAOA ({n_c17} runs at {c17_s:.1f} s)", n_c17 * c17_s),
        (f"QAOA at 12 qubits ({n_t12} runs at {t12_s:.1f} s)", n_t12 * t12_s),
        (f"QAOA at 20 qubits ({n_t20} runs at about {t20_s:.0f} s)", n_t20 * t20_s),
        (f"λ sweep c17 ({n_sweep} runs)", n_sweep * c17_s),
        (f"λ sweep coarse c432 ({n_sweep} runs)", n_sweep * t12_s),
    ]
    print(f"\n{'Step':<58}{'Estimate':>10}")
    for label, seconds in steps:
        print(f"{label:<58}{_minutes(seconds):>10}")
    total = sum(s for _, s in steps)
    print(f"{'Total':<58}{_minutes(total):>10}")
    print("\nTimes vary with λ and seed: COBYLA stops when it converges, so allow up to 1.5 times this.")
    print("Nothing was saved. Start the real run with: python scripts/phase6.py all --confirmed")


def _done(path: Path, columns: tuple[str, ...]) -> set[tuple[str, ...]]:
    return {tuple(row[c] for c in columns) for row in ex.read_rows(path)}


def run_summary() -> None:
    designs = {name: ex.load_benchmark(name) for name in ("c17", "c432", "c880")}
    done = {(r["design"], r["solver"], r["mode"], r["seed"], r["target"]) for r in ex.read_rows(SUMMARY)}
    jobs = ex.summary_jobs()
    for i, job in enumerate(jobs, 1):
        if ex.job_key(job) in done:
            continue
        row = ex.run_job(job, designs[job.design])
        ex.append_row(SUMMARY, row, ex.SUMMARY_COLUMNS)
        print(
            f"[{i}/{len(jobs)}] {job.design} {row['solver']} seed={job.seed} target={job.target}: "
            f"{row['status']} cut={row['cut']} balanced={row['balanced']} {float(row['runtime_s'] or 0):.1f} s",
            flush=True,
        )
    ex.write_rows(SUMMARY, ex.fill_best_known(ex.read_rows(SUMMARY)), ex.SUMMARY_COLUMNS)
    print(f"wrote {SUMMARY}")


def run_rule() -> None:
    done = {(r["group"], r["design"], r["target"], r["coarsen_seed"]) for r in ex.read_rows(RULE)}
    for group, design, target, seed in ex.rule_instances():
        key = (group, design, "" if target is None else str(target), "" if seed is None else str(seed))
        if key in done:
            continue
        row = ex.rule_row(group, design, target, seed)
        ex.append_row(RULE, row, ex.RULE_COLUMNS)
        print(f"{group} {design} target={target} seed={seed}: λ={row['rule_lambda']:.3f} "
              f"rule optimal {row['rule_min_is_optimal']}, λ=2 optimal {row['fixed2_min_is_optimal']}", flush=True)  # fmt: skip
    report_rule()


def report_rule() -> None:
    rows = ex.read_rows(RULE)
    for group in ("known", "held_out"):
        picked = [r for r in rows if r["group"] == group]
        rule = sum(r["rule_min_is_optimal"] == "True" for r in picked)
        fixed = sum(r["fixed2_min_is_optimal"] == "True" for r in picked)
        print(f"{group}: lowest-energy state is optimal on {rule}/{len(picked)} with the rule, {fixed}/{len(picked)} with λ = 2.0")


def run_sweep() -> None:
    done = {(r["instance"], float(r["lambda"]), int(r["seed"])) for r in ex.read_rows(SWEEP)}
    for design, instance, hg, c_seed, target, normalize in ex.sweep_instances():
        for lam in ex.LAMBDA_GRID:
            for seed in ex.SWEEP_SEEDS:
                if (instance, lam, seed) in done:
                    continue
                row = ex.sweep_row(design, instance, hg, c_seed, target, normalize, lam, seed)
                ex.append_row(SWEEP, row, ex.SWEEP_COLUMNS)
                print(f"{instance} λ={lam} seed={seed}: best cut {row['best_shot_cut']}, balanced shots "
                      f"{row['feasible_rate']:.1%}, {row['runtime_s']:.1f} s", flush=True)  # fmt: skip
    print(f"wrote {SWEEP}")


def run_plots() -> None:
    rows = ex.read_rows(SUMMARY)
    print(plot_ablation(rows, PLOTS / "ablation.png"))
    print(plot_lambda_sweep(ex.read_rows(SWEEP), PLOTS / "lambda_sweep.png"))
    hg = ex.load_benchmark("c17")
    best = brute_force(hg, build_partition_qubo(hg, lam=suggest_lambda(hg))).best
    print(plot_partition(hg, best.assignment, PLOTS / "partition_c17.png"))

    sentences = []
    for design in ("c432", "c880"):
        for target in (12, 20):
            verdict = ex.ablation_verdict(rows, design, target)
            note = f" Left out {verdict.excluded} seeds with an unbalanced final split." if verdict.excluded else ""
            sentences.append(verdict.sentence + note)
    VERDICT.write_text("\n".join(sentences) + "\n", encoding="utf-8")
    print("\n".join(sentences))
    report_rule()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("step", choices=["estimate", "summary", "rule", "sweep", "plots", "all"])
    parser.add_argument("--confirmed", action="store_true", help="Needed for long steps: you have seen the estimate.")
    args = parser.parse_args()
    if args.step in {"summary", "rule", "sweep", "all"} and not args.confirmed:
        print(f"'{args.step}' can take a long time. Run 'estimate' first, then add --confirmed.", file=sys.stderr)
        return 2
    if args.step == "estimate":
        estimate()
    if args.step in {"summary", "all"}:
        run_summary()
    if args.step in {"rule", "all"}:
        run_rule()
    if args.step in {"sweep", "all"}:
        run_sweep()
    if args.step in {"plots", "all"}:
        run_plots()
    return 0


if __name__ == "__main__":
    sys.exit(main())
