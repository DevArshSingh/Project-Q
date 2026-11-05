"""Phase 6 experiments: every number in results/*.csv comes from a function in this file.

The rules that keep the numbers honest:
- Each CSV row is one real run with its seeds written down, so anyone can repeat it.
- The cut comes from `make_record`, which recomputes it with `true_cut`. The split itself is
  saved in the `assignment` column, so a reader can check the cut without trusting us.
- The ablation verdict's wording is decided by code fixed before the runs, not by us afterwards.

QAOA runs on the Aer simulator only; nothing here contacts IBM hardware.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from statistics import mean

from scipy.stats import binomtest

from project_q.coarsen import COARSE_SOLVERS, coarsen, multilevel
from project_q.parse_yosys import DEFAULT_EPS, Hypergraph, is_balanced, load_hypergraph, load_netlist, true_cut
from project_q.qubo import DEFAULT_LAMBDA, build_partition_qubo, suggest_lambda
from project_q.scorecard import balance_ratio, make_record
from project_q.solvers import (
    _bits_from_key,
    brute_force,
    kahypar_solver,
    kernighan_lin,
    qaoa,
    random_balanced,
    simulated_annealing,
)

ROOT = Path(__file__).resolve().parents[1]
SHOTS = 4000
RESTARTS = 20
P = 1
LAMBDA_GRID = (0.1, 0.3, 1.0, 2.0, 3.0, 10.0)  # BUILD_PLAN Phase 6
SWEEP_SEEDS = range(5)
SWEEP_COARSEN_SEED = 1  # c432 seed 1: chosen openly, because λ = 2.0 is known to fail there
SWEEP_TARGET = 12
ALPHA = 0.05  # the ablation's significance level, fixed before running
MIN_SEEDS_FOR_TEST = 6  # with fewer pairs a two-sided sign test can never reach p < 0.05

SUMMARY_COLUMNS = [
    "design", "solver", "mode", "seed", "coarsen_seed", "target", "lambda", "lambda_source", "p", "shots",
    "restarts", "normalize", "backend", "qubits", "status", "cut", "best_known_cut", "best_known_source",
    "gap_percent", "balanced", "balance_ratio", "coarse_cut", "coarse_balanced", "feasible_rate", "p_optimal",
    "runtime_s", "error", "assignment",
]  # fmt: skip
SUMMARY_KEY = ("design", "solver", "mode", "seed", "target")


def load_benchmark(name: str) -> Hypergraph:
    """The committed netlist, so results don't depend on having Yosys."""
    return load_hypergraph(load_netlist(ROOT / "benchmarks" / "netlists" / f"{name}.json"))


# --------------------------------------------------------------------------- results table


@dataclass(frozen=True)
class Job:
    """One planned run. `target=None` means flat: the solver sees the whole circuit."""

    design: str
    solver: str
    seed: int | None
    target: int | None = None

    @property
    def mode(self) -> str:
        return "flat" if self.target is None else "multilevel"

    @property
    def uses_qaoa(self) -> bool:
        return self.solver == "qaoa"


def summary_jobs() -> list[Job]:
    """Every run in results/summary.csv, cheapest first, so a problem shows up early."""
    jobs = [Job("c17", "brute", None)]
    jobs += [Job("c17", s, seed) for s in ("random", "sa", "kl", "kahypar") for seed in range(5)]
    for design in ("c432", "c880"):
        # Same seed means same coarse circuit for every solver, so the runs can be compared in pairs.
        jobs += [Job(design, s, seed) for s in ("kl", "kahypar", "random", "sa") for seed in range(10)]
        jobs += [Job(design, s, seed, 12) for s in ("random", "sa", "brute") for seed in range(10)]
        jobs += [Job(design, s, seed, 20) for s in ("random", "sa", "brute") for seed in range(3)]
    jobs += [Job("c17", "qaoa", seed) for seed in range(5)]
    jobs += [Job(d, "qaoa", seed, 12) for d in ("c432", "c880") for seed in range(10)]
    jobs += [Job(d, "qaoa", seed, 20) for d in ("c432", "c880") for seed in range(3)]
    return jobs


def _solve(job: Job, hg: Hypergraph, eps: float, shots: int, restarts: int):
    """Returns (result, λ given to the record, where λ came from)."""
    if job.target is not None:
        if job.solver not in COARSE_SOLVERS:
            raise ValueError(f"{job.solver} is not a coarse solver")
        # λ = None: multilevel applies the rule on the coarse circuit and records the value.
        result = multilevel(
            hg, job.solver, target=job.target, lam=None, eps=eps, seed=job.seed, p=P, shots=shots, restarts=restarts,
        )  # fmt: skip
        return result, None, result.info["lam_source"]

    seed = 0 if job.seed is None else job.seed
    if job.solver == "kl":
        return kernighan_lin(hg, None, eps, seed=seed), None, ""
    if job.solver == "kahypar":
        return kahypar_solver(hg, None, eps, seed=seed), None, ""
    if job.solver == "random":
        return random_balanced(hg, None, eps, seed=seed), None, ""

    lam = suggest_lambda(hg, eps)
    qubo = build_partition_qubo(hg, lam=lam)
    if job.solver == "sa":
        return simulated_annealing(hg, qubo, eps, seed=seed), lam, "rule"
    if job.solver == "brute":
        return brute_force(hg, qubo, eps).best, None, ""
    if job.solver == "qaoa":
        best = brute_force(hg, qubo, eps).best_cut  # only to report how many shots were optimal
        return qaoa(hg, qubo, p=P, shots=shots, restarts=restarts, eps=eps, seed=seed, best_known_cut=best), lam, "rule"
    raise ValueError(f"unknown solver {job.solver!r}")


def run_job(job: Job, hg: Hypergraph, eps: float = DEFAULT_EPS, shots: int = SHOTS, restarts: int = RESTARTS) -> dict:
    """Run one job and return its CSV row. A solver failure becomes a visible row, not a lost run."""
    row = dict.fromkeys(SUMMARY_COLUMNS, "")
    row |= {
        "design": job.design, "mode": job.mode, "seed": "" if job.seed is None else job.seed,
        "coarsen_seed": job.seed if job.target is not None else "", "target": "" if job.target is None else job.target,
    }  # fmt: skip
    row["solver"] = f"multilevel-{job.solver}" if job.target is not None else job.solver
    try:
        result, lam, lam_source = _solve(job, hg, eps, shots, restarts)
    except (RuntimeError, ValueError) as error:
        return row | {"status": "error", "error": str(error)}

    record = make_record(job.design, hg, result, lam, eps)
    info = record.info
    row |= {
        "solver": record.solver,  # KaHyPar reports `kl-fallback` if it isn't installed
        "lambda": "" if record.lam is None else record.lam,
        "lambda_source": lam_source if record.lam is not None else "",
        "p": "" if record.p is None else record.p,
        "shots": "" if record.shots is None else record.shots,
        "restarts": restarts if job.uses_qaoa else "",
        # Multilevel QAOA divides H by its largest coefficient; flat c17 doesn't, as in Phase 3.
        "normalize": (job.target is not None) if job.uses_qaoa else "",
        "backend": record.backend,
        "qubits": "" if record.qubits is None else record.qubits,
        "status": "ok",
        "cut": record.cut,
        "balanced": record.balanced,
        "balance_ratio": record.balance_ratio,
        "coarse_cut": info.get("coarse_cut", ""),
        "coarse_balanced": info.get("coarse_balanced", ""),
        "feasible_rate": info.get("feasible_rate", ""),
        "p_optimal": "" if info.get("p_optimal") is None else info["p_optimal"],
        "runtime_s": record.runtime_s,
        "assignment": "".join(str(b) for b in record.assignment),
    }
    return row


def read_rows(path: str | Path) -> list[dict]:
    path = Path(path)
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def append_row(path: str | Path, row: dict, columns: list[str]) -> None:
    """Add one row and flush it to disk now, so a crash can't lose finished runs."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not path.is_file()
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        if is_new:
            writer.writeheader()
        writer.writerow(row)


def write_rows(path: str | Path, rows: list[dict], columns: list[str]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def job_key(job: Job) -> tuple[str, ...]:
    """The identity a job's row has in the CSV, used to skip finished runs when resuming."""
    name = f"multilevel-{job.solver}" if job.target is not None else job.solver
    return (job.design, name, job.mode, "" if job.seed is None else str(job.seed), "" if job.target is None else str(job.target))


def fill_best_known(rows: list[dict]) -> list[dict]:
    """Add best-known cuts and gaps once all runs are in.

    c17's best is proven by brute force. On c432 and c880 nothing is proven: `multilevel-brute`
    is exact only at the coarse level, so the best is simply the lowest balanced cut in the file.
    """
    designs = {r["design"] for r in rows}
    for design in designs:
        ok = [r for r in rows if r["design"] == design and r["status"] == "ok" and r["balanced"] == "True"]
        exact = [int(r["cut"]) for r in ok if r["solver"] == "brute"]
        if exact:
            best, source = exact[0], "brute_force"
        elif ok:
            best, source = min(int(r["cut"]) for r in ok), "best_of_this_run"
        else:
            continue
        for row in ok:
            row["best_known_cut"], row["best_known_source"] = best, source
            row["gap_percent"] = 100.0 * (int(row["cut"]) - best) / best if best else ""
        for row in rows:
            if row["design"] == design and row not in ok and row["status"] == "ok":
                # An unbalanced split gets no gap: breaking the rule could look like beating the best.
                row["best_known_cut"], row["best_known_source"], row["gap_percent"] = best, source, ""
    return rows


# --------------------------------------------------------------------------- ablation verdict


@dataclass
class Verdict:
    design: str
    target: int
    seeds: int
    random_mean: float
    qaoa_mean: float
    qaoa_better: int  # seeds where QAOA+FM's cut is lower than random+FM's
    ties: int
    random_better: int
    p_value: float
    excluded: int  # seeds dropped because either final split broke the balance rule
    sentence: str


def ablation_verdict(rows: list[dict], design: str, target: int) -> Verdict:
    """Compare random+FM with QAOA+FM seed by seed. The wording rule was fixed before any run.

    Sign test: ignore ties and ask whether QAOA wins more seeds than a fair coin would. It
    needs no assumptions about the shape of the cuts. It says "beat" only if QAOA's mean is
    lower AND p < 0.05; anything else is stated as "matches". There is no correction for
    testing several designs and sizes, which makes a "beat" easier to reach, not harder.
    """
    picked = [r for r in rows if r["design"] == design and str(r["target"]) == str(target) and r["status"] == "ok"]
    by_solver: dict[str, dict[str, dict]] = {"multilevel-random": {}, "multilevel-qaoa": {}}
    for row in picked:
        if row["solver"] in by_solver:
            by_solver[row["solver"]][str(row["seed"])] = row
    seeds = sorted(set(by_solver["multilevel-random"]) & set(by_solver["multilevel-qaoa"]), key=int)
    usable = [
        s for s in seeds
        if str(by_solver["multilevel-random"][s]["balanced"]) == "True"
        and str(by_solver["multilevel-qaoa"][s]["balanced"]) == "True"
    ]  # fmt: skip
    random_cuts = [int(by_solver["multilevel-random"][s]["cut"]) for s in usable]
    qaoa_cuts = [int(by_solver["multilevel-qaoa"][s]["cut"]) for s in usable]
    if not usable:
        raise ValueError(f"no comparable balanced runs for {design} at target {target}")

    qaoa_better = sum(q < r for q, r in zip(qaoa_cuts, random_cuts))
    random_better = sum(r < q for q, r in zip(qaoa_cuts, random_cuts))
    ties = len(usable) - qaoa_better - random_better
    decided = qaoa_better + random_better
    p_value = float(binomtest(qaoa_better, decided, 0.5).pvalue) if decided else 1.0
    r_mean, q_mean = mean(random_cuts), mean(qaoa_cuts)

    numbers = (
        f"{design}, {target} supernodes, {len(usable)} seeds: mean final cut random+FM {r_mean:.1f}, QAOA+FM {q_mean:.1f}; "
        f"QAOA lower on {qaoa_better}, tied on {ties}, random lower on {random_better}; sign test p = {p_value:.3f}."
    )
    if len(usable) < MIN_SEEDS_FOR_TEST:
        sentence = f"Descriptive only, {len(usable)} seeds can't reach p < {ALPHA}. {numbers}"
    elif q_mean < r_mean and p_value < ALPHA:
        sentence = f"QAOA+FM beat random+FM. {numbers}"
    else:
        sentence = f"Random+FM matches QAOA+FM. {numbers}"
    return Verdict(
        design, target, len(usable), r_mean, q_mean, qaoa_better, ties, random_better, p_value,
        len(seeds) - len(usable), sentence,
    )  # fmt: skip


# --------------------------------------------------------------------------- λ sweep

SWEEP_COLUMNS = [
    "design", "instance", "lambda", "rule_lambda", "seed", "coarsen_seed", "target", "p", "shots", "restarts",
    "normalize", "backend", "qubits", "best_known_cut", "best_shot_cut", "best_shot_balanced", "feasible_rate",
    "p_optimal", "mean_balanced_shot_cut", "mean_shot_balance_ratio", "qubo_min_cut", "qubo_min_balanced",
    "qubo_min_balance_ratio", "qubo_min_is_optimal", "energy_gap", "runtime_s",
]  # fmt: skip
SWEEP_KEY = ("instance", "lambda", "seed")


def sweep_instances() -> list[tuple[str, str, Hypergraph, int | None, int | None, bool]]:
    """(design, instance label, hypergraph, coarsening seed, target, normalize)."""
    c17 = load_benchmark("c17")
    coarse = coarsen(load_benchmark("c432"), target=SWEEP_TARGET, seed=SWEEP_COARSEN_SEED).coarsest
    return [
        ("c17", "c17", c17, None, None, False),
        ("c432", f"c432-coarse-{SWEEP_TARGET}-s{SWEEP_COARSEN_SEED}", coarse, SWEEP_COARSEN_SEED, SWEEP_TARGET, True),
    ]


def sweep_row(
    design: str, instance: str, hg: Hypergraph, coarsen_seed, target, normalize: bool, lam: float, seed: int,
    eps: float = DEFAULT_EPS, shots: int = SHOTS, restarts: int = RESTARTS,
) -> dict:  # fmt: skip
    """One QAOA run at one λ, plus what brute force says about that λ's QUBO (scoring only)."""
    qubo = build_partition_qubo(hg, lam=lam)
    bf = brute_force(hg, qubo, eps=eps, allow_large=True)
    result = qaoa(
        hg, qubo, p=P, shots=shots, restarts=restarts, eps=eps, seed=seed, best_known_cut=bf.best_cut, normalize=normalize,
    )  # fmt: skip
    total, cut_sum, ratio_sum = 0, 0, 0.0
    for key, n in result.info["counts"].items():
        bits = _bits_from_key(key)
        ratio_sum += n * balance_ratio(hg, bits)
        if is_balanced(hg, bits, eps):
            total += n
            cut_sum += n * true_cut(hg, bits)
    return {
        "design": design, "instance": instance, "lambda": lam, "rule_lambda": suggest_lambda(hg, eps), "seed": seed,
        "coarsen_seed": "" if coarsen_seed is None else coarsen_seed, "target": "" if target is None else target,
        "p": P, "shots": shots, "restarts": restarts, "normalize": normalize, "backend": result.info["backend"],
        "qubits": hg.num_nodes, "best_known_cut": bf.best_cut, "best_shot_cut": result.cut,
        "best_shot_balanced": result.balanced, "feasible_rate": result.info["feasible_rate"],
        "p_optimal": result.info["p_optimal"], "mean_balanced_shot_cut": cut_sum / total if total else "",
        "mean_shot_balance_ratio": ratio_sum / shots, "qubo_min_cut": true_cut(hg, bf.energy_argmin),
        "qubo_min_balanced": is_balanced(hg, bf.energy_argmin, eps),
        "qubo_min_balance_ratio": balance_ratio(hg, bf.energy_argmin), "qubo_min_is_optimal": bf.argmin_is_optimal,
        "energy_gap": bf.energy_gap, "runtime_s": result.runtime_s,
    }  # fmt: skip


# --------------------------------------------------------------------------- does the λ rule hold up?

RULE_COLUMNS = [
    "group", "design", "target", "coarsen_seed", "total_weight", "brute_best_cut", "rule_lambda",
    "rule_min_is_optimal", "rule_min_cut", "rule_min_balanced", "rule_states_below_optimum",
    "fixed2_min_is_optimal", "fixed2_min_cut", "fixed2_min_balanced", "fixed2_states_below_optimum",
]  # fmt: skip


def rule_instances() -> list[tuple[str, str, int | None, int | None]]:
    """(group, design, target, coarsening seed).

    "known" instances were looked at while choosing the formula, so they aren't evidence.
    "held_out" ones were not looked at, and the formula won't be changed after seeing them.
    """
    known = [("known", "c17", None, None)] + [("known", "c432", 12, s) for s in range(3)]
    held_out = [("held_out", "c432", 12, s) for s in range(3, 10)]
    held_out += [("held_out", "c880", 12, s) for s in range(10)]
    held_out += [("held_out", d, 20, s) for d in ("c432", "c880") for s in range(3)]
    return known + held_out


def _score_lambda(hg: Hypergraph, lam: float, eps: float) -> dict:
    """Brute force judges one λ. It only scores the rule here; the rule never sees this."""
    bf = brute_force(hg, build_partition_qubo(hg, lam=lam), eps=eps, allow_large=True)
    en = bf.enumeration
    optimal = en.balanced & (en.cuts == bf.best_cut)
    return {
        "best_cut": bf.best_cut,
        "is_optimal": bf.argmin_is_optimal,
        "cut": true_cut(hg, bf.energy_argmin),
        "balanced": is_balanced(hg, bf.energy_argmin, eps),
        # How many states a perfect optimizer would prefer over the real answer. 0 is ideal.
        "below": int((en.energies < en.energies[optimal].min() - 1e-9).sum()),
    }


def rule_row(group: str, design: str, target, coarsen_seed, eps: float = DEFAULT_EPS) -> dict:
    hg = load_benchmark(design)
    if target is not None:
        hg = coarsen(hg, target=target, seed=coarsen_seed).coarsest
    lam = suggest_lambda(hg, eps)
    rule, fixed = _score_lambda(hg, lam, eps), _score_lambda(hg, DEFAULT_LAMBDA, eps)
    return {
        "group": group, "design": design, "target": "" if target is None else target,
        "coarsen_seed": "" if coarsen_seed is None else coarsen_seed, "total_weight": hg.total_weight,
        "brute_best_cut": rule["best_cut"], "rule_lambda": lam, "rule_min_is_optimal": rule["is_optimal"],
        "rule_min_cut": rule["cut"], "rule_min_balanced": rule["balanced"], "rule_states_below_optimum": rule["below"],
        "fixed2_min_is_optimal": fixed["is_optimal"], "fixed2_min_cut": fixed["cut"],
        "fixed2_min_balanced": fixed["balanced"], "fixed2_states_below_optimum": fixed["below"],
    }  # fmt: skip
