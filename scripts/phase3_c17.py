"""Phase 3 report for c17: QAOA on the Aer simulator, compared with uniform guessing.

Run: python scripts/phase3_c17.py

Finding cut 2 is not enough on its own: 4000 uniform random shots on 64 states would
almost surely include an optimum. So every number is printed beside what uniform
guessing gets. Simulator only: this script never contacts IBM hardware.
"""

import sys
from pathlib import Path
from statistics import mean

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from c17_reference import reference_c17  # noqa: E402

from project_q.qubo import build_partition_qubo  # noqa: E402
from project_q.solvers import brute_force, qaoa, qubo_to_ising  # noqa: E402

SEEDS = range(5)
UNIFORM_FEASIBLE = 20 / 64  # 20 of the 64 states are balanced
UNIFORM_OPTIMAL = 2 / 64  # the optimal split and its mirror image


def main() -> None:
    hg = reference_c17()
    qubo = build_partition_qubo(hg)
    best_cut = brute_force(hg, qubo).best_cut
    op, offset = qubo_to_ising(qubo)
    print(f"Hamiltonian: {len(op)} terms on {op.num_qubits} qubits, offset {offset:.3f}")
    print(f"Brute force: best balanced cut {best_cut}, λ = {qubo.lam}")

    print(f"\nUniform guessing: feasible {UNIFORM_FEASIBLE:.1%}, optimal {UNIFORM_OPTIMAL:.1%}, ⟨E⟩ {offset:.3f}")
    print(f"\n{'p':>2} {'seed':>4} {'cut':>3} {'bal':>5} {'⟨E⟩':>7} {'feasible':>9} {'optimal':>8} {'time s':>7}")
    rows = {1: [], 2: []}
    for p in rows:
        for seed in SEEDS:
            r = qaoa(hg, qubo, p=p, seed=seed, best_known_cut=best_cut)
            rows[p].append(r)
            i = r.info
            print(
                f"{p:>2} {seed:>4} {r.cut:>3} {str(r.balanced):>5} {i['expected_energy']:>7.3f} "
                f"{i['feasible_rate']:>9.1%} {i['p_optimal']:>8.1%} {r.runtime_s:>7.2f}"
            )

    print(f"\nMean over {len(SEEDS)} seeds:")
    for p, rs in rows.items():
        print(
            f"  p={p}: ⟨E⟩ {mean(r.info['expected_energy'] for r in rs):.3f}, "
            f"feasible {mean(r.info['feasible_rate'] for r in rs):.1%}, "
            f"optimal {mean(r.info['p_optimal'] for r in rs):.1%}, "
            f"found cut {best_cut} in {sum(r.cut == best_cut and r.balanced for r in rs)}/{len(rs)} runs"
        )

    r = rows[1][0]
    print(f"\nBest angles, p=1, seed 0: γ = {r.info['gammas'][0]:.4f}, β = {r.info['betas'][0]:.4f}")
    print("Most frequent shots, p=1, seed 0 (rightmost character = qubit 0):")
    for key, n in sorted(r.info["counts"].items(), key=lambda kv: -kv[1])[:5]:
        print(f"  {key}  {n:>5}")


if __name__ == "__main__":
    main()
