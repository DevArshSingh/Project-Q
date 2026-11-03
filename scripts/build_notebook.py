"""Generate notebooks/project_q.ipynb (the main hackathon deliverable) from this file.

Run: python scripts/build_notebook.py

Why generate it: notebook JSON is hard to review in git. Edit the cells here,
re-run, and commit both files. Outputs are left empty; the notebook test
(tests/test_notebook.py) executes it top to bottom without Yosys.

Rules (BUILD_PLAN.md "Notebook rule"): a markdown cell before every code
cell, imports only from `project_q`, loads committed netlists from
benchmarks/netlists/, Yosys optional. Each phase adds its own section.
"""

from pathlib import Path

import nbformat
from nbformat.v4 import new_code_cell, new_markdown_cell, new_notebook

OUT = Path(__file__).resolve().parents[1] / "notebooks" / "project_q.ipynb"

CELLS: list[tuple[str, str]] = [
    # ---------------------------------------------------------------- title
    ("md", """# Project Q: splitting a chip with QAOA

Verilog → gate netlist → hypergraph → QUBO → (QAOA) → a split, scored classically.

We take a real circuit description, turn "cut this chip into two equal halves with as few wires crossing as possible" into an energy function a quantum algorithm can minimise, and check every answer with plain classical code. We make **no claim of quantum advantage**: on these sizes classical solvers win, and we report that."""),
    # ---------------------------------------------------------------- 1
    ("md", """## 1. The problem: balanced circuit partitioning

A design that is too big for one FPGA, or one die, has to be split across two. Every wire (net) that connects gates on both sides becomes a costly off-chip connection. So we want:

- **Small cut**: as few nets as possible span both halves.
- **Balance**: both halves hold about the same amount of logic, so each fits.

This is the classic *min-cut bisection* problem. It is NP-hard, which is why chip tools use heuristics (KaHyPar, hMETIS, Kernighan–Lin). It also maps naturally onto a QUBO, the input format of QAOA. We use the ISCAS-85 benchmark **c17**: 6 NAND gates, small enough to check every possible answer by hand."""),
    # ---------------------------------------------------------------- 2
    ("md", """## 2. The bridge: Verilog → netlist → hypergraph

**Yosys** (an open-source synthesis tool) turns the Verilog into a flat netlist of NAND gates: `synth -flatten; abc -g NAND; write_json`. We committed its output to `benchmarks/netlists/c17.json`, so this notebook runs **without Yosys installed**.

We read that JSON as a **hypergraph**:
- **node** = one gate;
- **net (hyperedge)** = one wire, joining *all* the gates it touches. One wire can feed 3 gates, which an ordinary graph edge (always 2 ends) can't express.

Wires that touch only one gate can never be cut, so they are dropped. An input pin that feeds two gates *does* count (c17's input N3 feeds two gates): if those gates land on different chips, the signal must reach both."""),
    ("code", """import shutil
from itertools import combinations
from pathlib import Path

from project_q.parse_yosys import is_balanced, load_hypergraph, load_netlist, run_yosys, true_cut
from project_q.qubo import build_partition_qubo, qubo_energy
from project_q.solvers import brute_force

# Works whether the notebook runs from notebooks/ or from the repo root.
ROOT = Path.cwd() if (Path.cwd() / "benchmarks").is_dir() else Path.cwd().parent

hg = load_hypergraph(load_netlist(ROOT / "benchmarks" / "netlists" / "c17.json"))
print(f"{hg.num_nodes} gates: {hg.nodes}")
print(f"{len(hg.nets)} nets:")
for name, net in zip(hg.net_names, hg.nets):
    print(f"  {name:<8} joins {sorted(net)}")"""),
    ("md", """Yosys's optimiser (ABC) rebuilds the logic, so the original gate names (g10, g16, …) are gone. Each gate is labelled by the wire it drives (`N22`, `N23` are the outputs). The *structure* is the same as the hand-drawn c17 in `docs/research.md` (our tests check this with a graph isomorphism), so cuts are the same too.

If Yosys is installed, the next cell re-runs it live and confirms it gives the same hypergraph as the committed file."""),
    ("code", """if shutil.which("yosys"):
    live = load_hypergraph(run_yosys(ROOT / "benchmarks" / "c17.v"))
    print("Yosys found. Live run matches committed netlist:", live.nodes == hg.nodes and live.nets == hg.nets)
else:
    print("Yosys not installed: using the committed netlist (identical to a live run).")"""),
    ("md", """**`true_cut`** is our ground truth: the number of nets with gates on both sides. It is a few lines of plain Python and shares no code with the QUBO, so a mistake in the quantum formulation can't hide itself. Below: all gates on one side (cut 0, but useless: not balanced) and one 3/3 split."""),
    ("code", """one_side = [0] * hg.num_nodes
split = [0, 0, 0, 1, 1, 1]  # first three gates on side A, rest on side B
for name, bits in [("all on one side", one_side), ("first 3 | last 3", split)]:
    print(f"{name:<17} cut = {true_cut(hg, bits)}, balanced = {is_balanced(hg, bits)}")"""),
    # ---------------------------------------------------------------- 3
    ("md", """## 3. Problem → QUBO, checked against brute force

QAOA minimises an energy over bit strings. So we give each gate a bit, **x_i = 0 → side A, x_i = 1 → side B**, and write an energy whose minimum is the best split. A **QUBO** allows only linear and pairwise terms:

$$E(x) = \\underbrace{\\sum_{\\text{nets}} \\text{cut terms}}_{\\text{wires crossing}} \\; + \\; \\underbrace{\\lambda \\Big(\\sum_i w_i x_i - \\tfrac{W}{2}\\Big)^2}_{\\text{balance penalty}}$$

- **2-gate net {i, j}:** $x_i + x_j - 2x_ix_j$ is 1 exactly when the two bits differ.
- **3-gate net {i, j, k}:** $x_i + x_j + x_k - x_ix_j - x_jx_k - x_ix_k$ is 1 for any split of the three, 0 if all agree. Also exact.
- **Bigger nets (4–8 gates)** use a pair approximation (weight 1/(n−1) per pair), which over-counts even splits. c17 has none, and every answer is re-scored with `true_cut` anyway.
- **Balance:** a QUBO can't hold hard rules, so balance is a penalty that is 0 at a perfect 3/3 split. λ sets how much it costs to be lopsided.

**Check 1:** with λ = 0 (cut terms only), the energy must equal `true_cut` on *every* one of the 2⁶ = 64 bit strings."""),
    ("code", """cut_only = build_partition_qubo(hg, lam=0.0)
states = [[(k >> i) & 1 for i in range(hg.num_nodes)] for k in range(2 ** hg.num_nodes)]
matches = sum(qubo_energy(cut_only, s) == true_cut(hg, s) for s in states)
print(f"QUBO cut energy == true_cut on {matches}/{len(states)} assignments")

print("\\nCut-only QUBO terms (i == j: linear term, i != j: pair term):")
for (i, j), c in sorted(cut_only.terms.items()):
    label = f"x[{hg.nodes[i]}]" if i == j else f"x[{hg.nodes[i]}]·x[{hg.nodes[j]}]"
    print(f"  {c:+.1f}  {label}")"""),
    ("md", """**Check 2:** brute force over all 64 assignments at the default **λ = 2.0**. The lowest-energy state must be the best *balanced* split. Two numbers tell us the QUBO is safe to hand to a quantum solver:

- **energy gap ΔE**: how far the best wrong answer sits above the optimum. It must be > 0.
- **unbalanced margin**: how far the best *lopsided* split sits above the optimum. At λ = 1 it is 1.0; at λ = 2 it is 2.0, so a noisy solver is less tempted by lopsided splits. That is why the default is 2.0."""),
    ("code", """for lam in (1.0, 2.0):
    bf = brute_force(hg, build_partition_qubo(hg, lam=lam))
    print(
        f"λ = {lam}: best balanced cut {bf.best_cut}, lowest-energy state is optimal: {bf.argmin_is_optimal}, "
        f"ΔE = {bf.energy_gap:.1f}, unbalanced margin = {bf.unbalanced_margin:.1f}"
    )

qubo = build_partition_qubo(hg, lam=2.0)
best = brute_force(hg, qubo).best
side_a = [n for n, b in zip(hg.nodes, best.assignment) if b == 0]
side_b = [n for n, b in zip(hg.nodes, best.assignment) if b == 1]
print(f"\\nOptimal split: A = {side_a} | B = {side_b}  (cut {best.cut}, balanced {best.balanced})")"""),
    ("md", """For the full picture: all C(6,3) = 20 balanced 3/3 splits, with their true cut and QUBO energy at λ = 2.0. They come in mirror pairs (A and B swapped), which have the same cut. Energy equals the cut on every row, because every c17 net has 2 or 3 gates. Only one split (and its mirror) reaches cut 2."""),
    ("code", """rows = []
for a in combinations(hg.nodes, 3):
    bits = [0 if n in a else 1 for n in hg.nodes]
    b = [n for n in hg.nodes if n not in a]
    rows.append((true_cut(hg, bits), qubo_energy(qubo, bits), a, b))
rows.sort()
print(f"{'side A':<26} {'side B':<26} cut  energy")
for cut, energy, a, b in rows:
    print(f"{', '.join(a):<26} {', '.join(b):<26} {cut:>3}  {energy:6.1f}")
print(f"\\n{len(rows)} balanced splits")"""),
    ("md", """**What this shows, and what it doesn't.** The QUBO encodes c17's partitioning problem exactly: its lowest energy is the true best balanced split, by a clear margin. It says nothing yet about whether QAOA *finds* that state; that is the next section. c17 is tiny, so brute force, simulated annealing and Kernighan–Lin all solve it instantly."""),
]


def build() -> nbformat.NotebookNode:
    nb = new_notebook()
    nb.metadata["kernelspec"] = {"display_name": "Python 3", "language": "python", "name": "python3"}
    nb.metadata["language_info"] = {"name": "python"}
    for k, (kind, src) in enumerate(CELLS):
        cell = new_markdown_cell(src) if kind == "md" else new_code_cell(src)
        cell.id = f"cell-{k:02d}"  # fixed ids: rebuilding gives an identical file (no random ids in git)
        nb.cells.append(cell)
    return nb


def main() -> None:
    nb = build()
    nbformat.validate(nb)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    nbformat.write(nb, OUT)
    print(f"wrote {OUT} ({len(nb.cells)} cells)")


if __name__ == "__main__":
    main()
