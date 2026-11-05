"""Gate 2 report: QAOA vs brute force on c432 coarsened to 12 nodes (Aer simulator only).

Run: python scripts/gate2_c432.py

The SPEC's check is "QAOA's answer matches brute force". On 12 qubits that is weak by itself:
4000 uniform random shots already have a high chance of including an optimal state. So this
script reports two tiers, and uses the same selection rule for QAOA and for random guessing:

  Tier 1 (SPEC): the best balanced shot equals brute force.
  Tier 2 (evidence): QAOA's shots are optimal and balanced more often than uniform guessing.
      Fixed before running: one-sided binomial test on the shots of all seeds together, p < 0.01.

The tier 2 headline config is tuned λ + normalized H at p=1. λ is tuned with brute force,
which knows the answer. That can't be done on designs too large to brute force, so the
report also shows λ = 2.0, which involves no tuning.

Nothing here contacts IBM hardware. This script writes no files.
"""

from __future__ import annotations

import sys
from pathlib import Path
from statistics import mean

import numpy as np
from scipy.stats import binomtest

from project_q.coarsen import coarsen, multilevel
from project_q.parse_yosys import is_balanced, load_hypergraph, load_netlist, true_cut
from project_q.qubo import build_partition_qubo
from project_q.scorecard import make_record
from project_q.solvers import (
    _bits_from_key,
    brute_force,
    kahypar_solver,
    qaoa,
    select_best_shot,
)

ROOT = Path(__file__).resolve().parents[1]
TARGET = 12
SHOTS = 4000
RESTARTS = 20
SEEDS = range(5)
COARSENING_SEEDS = range(3)  # three instances, so no single lucky one can pass the gate
ALPHA = 0.01


def scan_lambda(hg, grid):
    """For each λ: does the lowest energy mean the best balanced cut? (uses brute force)"""
    rows = []
    for lam in grid:
        qubo = build_partition_qubo(hg, lam=lam)
        bf = brute_force(hg, qubo)
        en = bf.enumeration
        optimal = en.balanced & (en.cuts == bf.best_cut)
        # How many states the optimizer would prefer over the best real answer. 0 is ideal.
        below = int((en.energies < en.energies[optimal].min() - 1e-9).sum())
        rows.append(
            {
                "lam": float(lam),
                "below": below,
                "gap": bf.energy_gap,
                "argmin_optimal": bf.argmin_is_optimal,
                "argmin_cut": true_cut(hg, bf.energy_argmin),
                "argmin_balanced": is_balanced(hg, bf.energy_argmin),
            }
        )
    return rows


def balanced_cuts(counts, hg):
    """(number of balanced shots, their mean true cut)."""
    total, cut_sum = 0, 0
    for key, n in counts.items():
        bits = _bits_from_key(key)
        if is_balanced(hg, bits):
            total += n
            cut_sum += n * true_cut(hg, bits)
    return total, (cut_sum / total if total else float("nan"))


def uniform_control(hg, seed):
    """SHOTS uniform random bit strings, through the same selection rule QAOA uses."""
    rng = np.random.default_rng(seed)
    keys = rng.integers(0, 2, (SHOTS, hg.num_nodes))
    counts = {}
    for row in keys:
        key = "".join(str(b) for b in row)
        counts[key] = counts.get(key, 0) + 1
    bits, _ = select_best_shot(counts, hg)
    return true_cut(hg, bits)


def run_config(hg, qubo, best_cut, uniform_opt, uniform_bal, label, **kwargs):
    runs = [qaoa(hg, qubo, shots=SHOTS, restarts=RESTARTS, seed=s, best_known_cut=best_cut, **kwargs) for s in SEEDS]
    matches = sum(r.cut == best_cut and r.balanced for r in runs)
    total_shots = SHOTS * len(runs)
    opt_hits = round(sum(r.info["p_optimal"] * SHOTS for r in runs))
    bal_hits = round(sum(r.info["feasible_rate"] * SHOTS for r in runs))
    p_opt = binomtest(opt_hits, total_shots, uniform_opt, alternative="greater").pvalue
    p_bal = binomtest(bal_hits, total_shots, uniform_bal, alternative="greater").pvalue
    evidence = p_opt < ALPHA and p_bal < ALPHA
    mean_cut = mean(balanced_cuts(r.info["counts"], hg)[1] for r in runs)
    print(
        f"  {label:<26} match {matches}/{len(runs)}  optimal {opt_hits / total_shots:>7.3%} (test p={p_opt:.1e})  "
        f"balanced {bal_hits / total_shots:>6.1%} (test p={p_bal:.1e})  "
        f"mean balanced-shot cut {mean_cut:5.1f}  {mean(r.runtime_s for r in runs):5.1f} s/run"
        f"  evidence {'YES' if evidence else 'no'}",
        flush=True,
    )
    return {"matches": matches, "evidence": evidence, "runs": runs}


def main() -> None:
    hg = load_hypergraph(load_netlist(ROOT / "benchmarks" / "netlists" / "c432.json"))
    print(f"c432: {hg.num_nodes} nodes, {len(hg.nets)} nets, total weight {hg.total_weight}")
    print(f"Settings: {TARGET} nodes, p=1 and p=2, {RESTARTS} restarts, {SHOTS} shots, seeds {list(SEEDS)}, Aer only\n")

    headline = []
    for c_seed in COARSENING_SEEDS:
        h = coarsen(hg, target=TARGET, seed=c_seed)
        coarse = h.coarsest
        wbar = coarse.total_weight / coarse.num_nodes
        grid = sorted({*np.geomspace(2.0 / wbar**2, 2.0, 7).tolist(), 2.0})
        scan = scan_lambda(coarse, grid)
        tuned = min(scan, key=lambda r: (r["below"], -r["gap"], r["lam"]))
        print(f"=== Coarsening seed {c_seed}: weights {sorted(coarse.weights.values())}, {len(coarse.nets)} nets")
        print(f"  {'λ':>7} {'states below optimum':>21} {'gap':>8} {'lowest-energy state':>32}")
        for r in scan:
            state = f"cut {r['argmin_cut']}, balanced {r['argmin_balanced']}, optimal {r['argmin_optimal']}"
            print(f"  {r['lam']:>7.3f} {r['below']:>21} {r['gap']:>8.2f}   {state}")
        lam = tuned["lam"]
        print(f"  Tuned λ = {lam:.3f} (chosen with brute force, so it knows the answer)")

        qubo_t = build_partition_qubo(coarse, lam=lam)
        qubo_2 = build_partition_qubo(coarse, lam=2.0)
        bf = brute_force(coarse, qubo_t)
        en = bf.enumeration
        uniform_opt = bf.n_optimal / 2**TARGET
        uniform_bal = float(en.balanced.mean())
        chance = 1 - (1 - uniform_opt) ** SHOTS
        control = [uniform_control(coarse, s) for s in SEEDS]
        print(
            f"  Brute force: best cut {bf.best_cut}, {bf.n_optimal} optimal states; QUBO exact: {qubo_t.exact}, "
            f"nets left out: {qubo_t.dropped_nets}"
        )
        print(
            f"  Uniform guessing: optimal {uniform_opt:.3%}, balanced {uniform_bal:.1%}, "
            f"mean balanced cut {float(en.cuts[en.balanced].mean()):.1f}; {SHOTS} shots hit an optimum {chance:.0%} of the time"
        )
        print(f"  Uniform control, 5 seeds: best cuts {control}, match {sum(c == bf.best_cut for c in control)}/5")

        def run(qubo, label, **kwargs):
            return run_config(coarse, qubo, bf.best_cut, uniform_opt, uniform_bal, label, **kwargs)

        run(qubo_2, "λ=2 raw, p=1", p=1)
        run(qubo_2, "λ=2 normalized, p=1", p=1, normalize=True)
        run(qubo_t, "tuned λ raw, p=1", p=1)
        best = run(qubo_t, "tuned λ normalized, p=1 *", p=1, normalize=True)
        run(qubo_t, "tuned λ normalized, p=2", p=2, normalize=True)
        headline.append((c_seed, lam, best, bf.best_cut, coarse))
        print()

    print("=== Gate 2 verdict (config marked * above: tuned λ, normalized, p=1)")
    tier1 = sum(h[2]["matches"] for h in headline)
    tier2 = sum(h[2]["evidence"] for h in headline)
    total = len(headline) * len(SEEDS)
    print(f"Tier 1 (SPEC, best shot = brute force): {tier1}/{total} runs across {len(headline)} instances")
    print(f"Tier 2 (better than uniform guessing): {tier2}/{len(headline)} instances")

    print("\nScorecard for seed 0 of each instance:")
    print(f"{'design':<16} {'solver':<8} {'cut':>4} {'best':>5} {'gap':>7} {'ratio':>6} {'qubits':>6} {'shots':>6} {'time s':>7}")
    for c_seed, lam, best, best_cut, coarse in headline:
        rec = make_record(f"c432-coarse-s{c_seed}", coarse, best["runs"][0], lam, 0.03, best_cut, "brute_force")
        print(
            f"{rec.design:<16} {rec.solver:<8} {rec.cut:>4} {rec.best_known_cut:>5} {rec.gap_percent:>6.1f}% "
            f"{rec.balance_ratio:>6.3f} {rec.qubits:>6} {rec.shots:>6} {rec.runtime_s:>7.2f}"
        )

    print("\nFull c432 through the multilevel pipeline (one first look; Phase 6 does the real comparison):")
    print(f"{'solver':<18} {'cut':>4} {'coarse cut':>10} {'balanced':>8} {'time s':>7}")
    for name in ("random", "sa", "brute", "qaoa"):
        rows = [multilevel(hg, name, target=TARGET, seed=s, lam=2.0) for s in COARSENING_SEEDS]
        for r in rows:
            print(f"{r.solver:<18} {r.cut:>4} {r.info['coarse_cut']:>10} {str(r.balanced):>8} {r.runtime_s:>7.2f}")
        sys.stdout.flush()
    k = kahypar_solver(hg)
    print(f"{k.solver:<18} {k.cut:>4} {'-':>10} {str(k.balanced):>8} {k.runtime_s:>7.2f}")


if __name__ == "__main__":
    main()
