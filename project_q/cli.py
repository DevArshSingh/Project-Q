"""Command-line entry point: `project-q ...` or `python -m project_q.cli ...`.

Phase 0 stub: the argument layout is final, the solvers are wired up in Phase 4.
"""

import argparse
import sys

from project_q import __version__

SOLVERS = ["brute", "random", "sa", "kl", "kahypar", "qaoa"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="project-q",
        description="Partition a Verilog circuit into 2 balanced groups (QAOA vs classical baselines).",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    part = sub.add_parser("partition", help="Partition one design with one solver.")
    part.add_argument("design", help="Path to a Verilog file, e.g. benchmarks/c17.v")
    part.add_argument("--solver", choices=SOLVERS, default="qaoa")
    # λ = weight of the balance penalty in the QUBO; 2.0 gives c17 a positive energy gap (see SPEC.md).
    part.add_argument("--lam", type=float, default=2.0, help="Balance penalty weight λ (default 2.0)")
    # ε = allowed imbalance: max(w(A), w(B)) <= ceil(W/2) * (1 + ε).
    part.add_argument("--eps", type=float, default=0.03, help="Balance tolerance ε (default 0.03)")
    part.add_argument("--p", type=int, default=1, help="QAOA depth (number of layers)")
    part.add_argument("--shots", type=int, default=4000)
    part.add_argument("--seed", type=int, default=0)

    bench = sub.add_parser("benchmark", help="Run all solvers on one design and print a comparison table.")
    bench.add_argument("design", help="Path to a Verilog file")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    print(f"`{args.command}` is not implemented yet (Phase 4).", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
