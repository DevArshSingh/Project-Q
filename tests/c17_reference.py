"""Hand-written c17 hypergraph from docs/research.md section 3, shared by the tests."""

from pathlib import Path

from project_q.parse_yosys import Hypergraph

C17_V = Path(__file__).resolve().parents[1] / "benchmarks" / "c17.v"

# 4 internal nets plus input pin N3, which feeds two gates and so is a real (cuttable) net.
EXPECTED_NODES = ["g10", "g11", "g16", "g19", "g22", "g23"]
EXPECTED_NETS = {
    "N3": {"g10", "g11"},
    "N10": {"g10", "g22"},
    "N11": {"g11", "g16", "g19"},
    "N16": {"g16", "g22", "g23"},
    "N19": {"g19", "g23"},
}
OPTIMAL_A = {"g10", "g11", "g22"}  # unique optimal balanced split (cut 2)
NEAR_MISS_A = {"g10", "g16", "g22"}  # cuts N3, N11, N16 (cut 3)


def reference_c17() -> Hypergraph:
    return Hypergraph(
        nodes=list(EXPECTED_NODES),
        nets=[frozenset(s) for s in EXPECTED_NETS.values()],
        net_names=list(EXPECTED_NETS),
    )
