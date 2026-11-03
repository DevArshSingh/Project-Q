"""Run Yosys on every benchmarks/*.v and save the JSON to benchmarks/netlists/<name>.json.

Run: python scripts/export_netlists.py

The saved netlists are committed so the notebook (and anyone without Yosys)
can load them directly. Re-run this after adding or changing a .v file.
"""

import sys
from pathlib import Path

from project_q.parse_yosys import load_hypergraph, run_yosys, save_netlist

BENCHMARKS = Path(__file__).resolve().parents[1] / "benchmarks"


def main() -> int:
    sources = sorted(BENCHMARKS.glob("*.v"))
    if not sources:
        print(f"no .v files in {BENCHMARKS}", file=sys.stderr)
        return 1
    for v in sources:
        netlist = run_yosys(v)
        out = save_netlist(netlist, BENCHMARKS / "netlists" / f"{v.stem}.json")
        hg = load_hypergraph(netlist)
        print(f"{v.name}: {hg.num_nodes} nodes, {len(hg.nets)} nets -> {out.relative_to(BENCHMARKS.parent)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
