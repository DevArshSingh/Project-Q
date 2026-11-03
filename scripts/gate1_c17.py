"""Gate 1 report for c17: hypergraph vs QUBO vs brute force.

Run: python scripts/gate1_c17.py

Prints all 20 balanced splits with their true cut and QUBO energy (hand-written
research.md hypergraph, so gate names are readable), then the Gate 1 numbers for
both the hand-written and the Yosys-derived hypergraph, then the baselines.
"""

import sys
from itertools import combinations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from c17_reference import C17_V, reference_c17  # noqa: E402

from project_q.parse_yosys import hypergraph_from_verilog, true_cut  # noqa: E402
from project_q.qubo import build_partition_qubo, qubo_energy  # noqa: E402
from project_q.solvers import brute_force, kernighan_lin, random_balanced, simulated_annealing  # noqa: E402


def balanced_table(hg, qubo) -> None:
    rows = []
    for a in combinations(hg.nodes, 3):
        bits = [0 if n in a else 1 for n in hg.nodes]
        b = [n for n in hg.nodes if n not in a]
        rows.append((true_cut(hg, bits), qubo_energy(qubo, bits), a, b))
    rows.sort(key=lambda r: (r[0], r[2]))
    print(f"All {len(rows)} balanced 3/3 splits (λ = {qubo.lam}):")
    print(f"  {'side A':<18} {'side B':<18} cut  energy")
    for cut, energy, a, b in rows:
        print(f"  {','.join(a):<18} {','.join(b):<18} {cut:>3}  {energy:6.2f}")


def gate_numbers(name, hg) -> None:
    for lam in (1.0, 2.0):
        q = build_partition_qubo(hg, lam=lam)
        bf = brute_force(hg, q)
        print(
            f"  {name:<9} λ={lam}: best cut {bf.best_cut}, optima {bf.n_optimal}, "
            f"min E {bf.min_energy:.2f}, argmin optimal {bf.argmin_is_optimal}, "
            f"gap ΔE {bf.energy_gap:.2f}, unbalanced margin {bf.unbalanced_margin:.2f}"
        )


def main() -> None:
    ref, yos = reference_c17(), hypergraph_from_verilog(C17_V)
    balanced_table(ref, build_partition_qubo(ref))
    print("\nGate 1 numbers (ΔE = min E over non-optimal − E*; must be > 0 at λ = 2.0):")
    gate_numbers("reference", ref)
    gate_numbers("yosys", yos)

    print("\nBaselines on Yosys c17 (λ = 2.0, cut recomputed classically):")
    q = build_partition_qubo(yos)
    results = [
        brute_force(yos, q).best,
        random_balanced(yos, q, seed=0),
        simulated_annealing(yos, q, seed=0),
        kernighan_lin(yos, q, seed=0),
    ]
    for r in results:
        print(f"  {r.solver:<7} cut {r.cut}  balanced {r.balanced}  energy {r.energy:.2f}  {r.runtime_s * 1000:7.1f} ms")


if __name__ == "__main__":
    main()
