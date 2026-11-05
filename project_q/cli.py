"""Command-line entry point: `project-q ...` or `python -m project_q.cli ...`.

    partition  one design, one solver: prints the answer and saves one JSON record
    benchmark  one design, all solvers: prints a comparison table and saves one JSON per solver

QAOA runs on the Aer simulator only; nothing here contacts IBM hardware. Solver imports
sit inside the functions so that `--help` doesn't have to load Qiskit first.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from project_q import __version__

# `benchmark` runs BASE_SOLVERS only (keeps the table fast). Multilevel is available via `partition`.
BASE_SOLVERS = ["brute", "random", "sa", "kl", "kahypar", "qaoa"]
MULTILEVEL_SOLVERS = ["multilevel-qaoa", "multilevel-sa", "multilevel-random", "multilevel-brute"]
SOLVERS = BASE_SOLVERS + MULTILEVEL_SOLVERS


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="project-q",
        description="Partition a Verilog circuit into 2 balanced groups (QAOA vs classical baselines).",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("design", help="Verilog file (e.g. benchmarks/c17.v) or Yosys JSON netlist")
    # λ = weight of the balance penalty in the QUBO; 2.0 gives c17 a positive energy gap (see SPEC.md).
    common.add_argument("--lam", type=float, default=2.0, help="Balance penalty weight λ (default 2.0)")
    # ε = allowed imbalance: max(w(A), w(B)) <= ceil(W/2) * (1 + ε).
    common.add_argument("--eps", type=float, default=0.03, help="Balance tolerance ε (default 0.03)")
    common.add_argument("--p", type=int, default=1, help="QAOA depth (number of layers)")
    common.add_argument("--shots", type=int, default=4000)
    common.add_argument("--restarts", type=int, default=20, help="QAOA optimizer starts (default 20)")
    common.add_argument("--seed", type=int, default=0)
    common.add_argument("--results-dir", default="results", help="Folder for the JSON records (default results)")

    common.add_argument(
        "--target",
        type=int,
        default=20,
        help="Multilevel coarsen until ≤ this many supernodes (default 20)",
    )
    part = sub.add_parser("partition", parents=[common], help="Partition one design with one solver.")
    part.add_argument("--solver", choices=SOLVERS, default="qaoa")
    sub.add_parser("benchmark", parents=[common], help="Run all solvers on one design and print a comparison table.")
    return parser


def load_design(path_str: str):
    """Return (design name, hypergraph) from Verilog, or from a saved Yosys JSON netlist.

    Without Yosys, a .v file falls back to its committed netlist in netlists/<name>.json,
    and says so, because that netlist won't include any later edits to the .v file.
    """
    from project_q.parse_yosys import hypergraph_from_verilog, load_hypergraph, load_netlist

    path = Path(path_str)
    if not path.is_file():
        raise FileNotFoundError(f"design not found: {path}")
    if path.suffix == ".json":
        return path.stem, load_hypergraph(load_netlist(path))
    if shutil.which("yosys"):
        return path.stem, hypergraph_from_verilog(path)
    saved = path.parent / "netlists" / f"{path.stem}.json"
    if saved.is_file():
        print(f"Yosys not installed: using the committed netlist {saved}", file=sys.stderr)
        return path.stem, load_hypergraph(load_netlist(saved))
    raise RuntimeError(f"Yosys is not installed and there is no saved netlist at {saved}")


def run_solver(name: str, hg, qubo, args, best_known_cut: int | None = None):
    from project_q.coarsen import multilevel
    from project_q.solvers import brute_force, kahypar_solver, kernighan_lin, qaoa, random_balanced, simulated_annealing

    if name == "brute":
        return brute_force(hg, qubo, eps=args.eps).best
    if name == "random":
        return random_balanced(hg, qubo, eps=args.eps, seed=args.seed)
    if name == "sa":
        return simulated_annealing(hg, qubo, eps=args.eps, seed=args.seed)
    if name == "kl":
        return kernighan_lin(hg, qubo, eps=args.eps, seed=args.seed)
    if name == "kahypar":
        return kahypar_solver(hg, qubo, eps=args.eps, seed=args.seed)
    if name.startswith("multilevel-"):
        return multilevel(
            hg,
            coarse_solver=name.removeprefix("multilevel-"),
            target=getattr(args, "target", 20),
            lam=args.lam,
            eps=args.eps,
            seed=args.seed,
            p=args.p,
            shots=args.shots,
            restarts=args.restarts,
        )
    return qaoa(
        hg, qubo, p=args.p, shots=args.shots, restarts=args.restarts, eps=args.eps, seed=args.seed,
        best_known_cut=best_known_cut,
    )


def proven_best(hg, qubo, eps: float) -> tuple[int | None, str | None]:
    """The exact best cut from brute force when the design is small enough, else unknown."""
    from project_q.solvers import BRUTE_FORCE_MAX_N, brute_force

    if hg.num_nodes > BRUTE_FORCE_MAX_N:
        return None, None
    return brute_force(hg, qubo, eps=eps).best_cut, "brute_force"


def skip_reason(name: str, hg) -> str | None:
    """Why `benchmark` can't run this solver on this design, or None if it can."""
    from project_q.solvers import BRUTE_FORCE_MAX_N, QAOA_MAX_QUBITS

    if name == "brute" and hg.num_nodes > BRUTE_FORCE_MAX_N:
        return f"{hg.num_nodes} nodes > brute-force limit of {BRUTE_FORCE_MAX_N}"
    if name == "qaoa" and hg.num_nodes > QAOA_MAX_QUBITS:
        return f"{hg.num_nodes} qubits > limit of {QAOA_MAX_QUBITS}; use --solver multilevel-qaoa"
    if name == "multilevel-brute" and getattr(hg, "num_nodes", 0) > BRUTE_FORCE_MAX_N:
        # Coarse target defaults to 20, which is within the brute cap — always ok.
        pass
    return None


def _percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f}%"


def _cmd_partition(args) -> int:
    from project_q.qubo import build_partition_qubo
    from project_q.scorecard import make_record, save_record

    design, hg = load_design(args.design)
    reason = skip_reason(args.solver, hg)
    if reason:
        raise ValueError(f"solver {args.solver} can't run on {design}: {reason}")
    qubo = build_partition_qubo(hg, lam=args.lam)
    best, source = proven_best(hg, qubo, args.eps)
    result = run_solver(args.solver, hg, qubo, args, best)
    record = make_record(design, hg, result, args.lam, args.eps, best, source)

    print(f"{design}: {hg.num_nodes} nodes, {len(hg.nets)} nets, solver {record.solver}")
    best_text = "unknown" if best is None else f"{best} ({source})"
    print(f"cut {record.cut}  best known {best_text}  gap {_percent(record.gap_percent)}")
    print(f"balanced {record.balanced}  balance ratio {record.balance_ratio:.3f}  runtime {record.runtime_s:.2f} s")
    if record.solver == "qaoa":
        print(f"balanced shots {record.info['feasible_rate']:.1%}  optimal shots "
              f"{'n/a' if record.info['p_optimal'] is None else format(record.info['p_optimal'], '.1%')}")
    if hg.num_nodes <= 24:
        for label, value in (("A", 0), ("B", 1)):
            print(f"side {label}: {', '.join(n for n, b in zip(hg.nodes, record.assignment) if b == value)}")
    if not record.balanced:
        print("WARNING: this split breaks the balance rule, so its cut can't be compared with balanced splits.")
    print(f"saved {save_record(record, args.results_dir)}")
    return 0


def _cmd_benchmark(args) -> int:
    from project_q.qubo import build_partition_qubo
    from project_q.scorecard import make_record, save_record

    design, hg = load_design(args.design)
    qubo = build_partition_qubo(hg, lam=args.lam)
    best, source = proven_best(hg, qubo, args.eps)

    results, skipped = [], []
    for name in BASE_SOLVERS:
        reason = skip_reason(name, hg)
        if reason:
            skipped.append((name, reason))
        else:
            results.append(run_solver(name, hg, qubo, args, best))
    if best is None:
        # Without brute force we only know the best answer found here, which isn't proven optimal.
        cuts = [r.cut for r in results if r.balanced]
        if cuts:
            best, source = min(cuts), "best_of_this_run"
    records = [make_record(design, hg, r, args.lam, args.eps, best, source) for r in results]

    print(f"{design}: {hg.num_nodes} nodes, {len(hg.nets)} nets. Best known cut: {best} ({source})")
    print(f"{'solver':<12} {'cut':>4} {'gap':>8} {'balanced':>8} {'ratio':>6} {'qubits':>6} {'shots':>6} {'time s':>8}")
    for r in records:
        print(
            f"{r.solver:<12} {r.cut:>4} {_percent(r.gap_percent):>8} {str(r.balanced):>8} {r.balance_ratio:>6.3f} "
            f"{'-' if r.qubits is None else r.qubits:>6} {'-' if r.shots is None else r.shots:>6} {r.runtime_s:>8.3f}"
        )
    for name, reason in skipped:
        print(f"{name:<12} skipped: {reason}")
    if source == "best_of_this_run":
        print("Gaps compare against the best cut found in this run, which isn't proven optimal.")
    paths = [save_record(r, args.results_dir) for r in records]
    print(f"saved {len(paths)} JSON records to {args.results_dir}/")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return _cmd_partition(args) if args.command == "partition" else _cmd_benchmark(args)
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
