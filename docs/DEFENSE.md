# Project Q: Defense notes

What each phase does, why we believe it is correct, and where it falls short.
Written so any team member can answer a judge's "how do you know?" question.
We make no claim of quantum advantage anywhere.

---

## Phase 0: Environment

**What it does.** Sets up a Python 3.12 virtual environment (`uv`), installs Qiskit, Aer, IBM Runtime, opt-mapper, networkx, numpy, scipy, matplotlib and pytest, and packages the code as `project_q` with a `project-q` command (a stub until Phase 4).

**Why it works.** The smoke tests check the Python version, every import, that Yosys runs, and that the CLI help prints. Versions in use: qiskit 2.6.1, qiskit-aer 0.18.2, qiskit-ibm-runtime 0.51.0, qiskit-addon-opt-mapper 0.1.0, Yosys 0.52.

**Where it falls short.**
- Dependencies are not pinned yet (planned for Phase 9), so a fresh install could pull newer, incompatible versions.
- opt-mapper is at version 0.1.0, very new. From Phase 3 we use it to build the Hamiltonian, and tests check its output (see Phase 3).
- Yosys is a system install, and we don't check its version.

---

## Phase 1: Verilog → hypergraph

**What it does.** Yosys synthesizes the Verilog into a flat netlist of NAND gates (`synth -flatten; abc -g NAND`) and writes it as JSON. We read that JSON into a hypergraph: each gate is a node, and each wire touching at least 2 gates is a net (hyperedge). `true_cut` counts the nets whose gates end up on both sides of a split.

**Why it works.**
- The Yosys output for c17 has the same structure as the hand-drawn c17 in `research.md` (the tests check this with a graph isomorphism), so a split has the same cut in both.
- The hand-checked splits give the expected cuts: 2 for {g10, g11, g22} | {g16, g19, g23}, and 3 for {g10, g16, g22} | {g11, g19, g23}.
- `true_cut` is a few lines of plain Python and shares no code with the QUBO, so a bug in the QUBO cannot hide itself.
- Input pins that feed 2 or more gates count as nets (c17's N3 feeds g10 and g11). If those gates end up on different chips, the signal has to reach both, so that is a real cost.

**Where it falls short.**
- ABC re-synthesizes the logic. The netlist we partition is logically equivalent to the original but may not be gate-for-gate identical, and on larger designs the gate count may differ from the published ISCAS numbers.
- Original gate and wire names are lost. Nodes are labelled by the wire they drive (e.g. `N22`, `new_n8`).
- Nets touching more than 50 gates (clock/reset style) are dropped and are not counted in the cut at all.
- Wires touching only one gate are dropped, including output pins, so the cost of I/O pins per chip is ignored.
- Only tested on c17, a 6-gate, combinational (no flip-flops), single-module design. Sequential and multi-module designs are untested.

---

## Phase 2: QUBO, brute force, classical baselines → Gate 1

**What it does.**
- Builds a QUBO: an energy formula over 0/1 variables (one per gate) whose lowest value should be the best balanced split. It has two parts: cut terms for each net, plus a balance penalty λ(Σwᵢxᵢ − W/2)² with λ = 2.0.
- Defines the hard balance rule `is_balanced`: max(w(A), w(B)) ≤ ⌈W/2⌉(1+ε), with ε = 0.03.
- Adds brute force (checks every split, vectorized with numpy) and three classical baselines: random balanced split, simulated annealing on the QUBO, and Kernighan–Lin.

**Why it works.**
- **Cut terms.** Each net contributes every pair of its gates with weight 1/(n−1). For 2- and 3-gate nets this is exact: the tests check that energy equals the true cut on all 64 c17 assignments.
- **Balance term.** Its expansion into linear and pair terms is checked on every state, including with weighted nodes.
- **Gate 1.** On c17 at λ = 2.0, the lowest-energy states are exactly the best balanced split and its mirror image. The energy gap to the next state is 1.0 > 0, and every lopsided split scores at least 2.0 above the optimum (only 1.0 at λ = 1). So the lowest-energy state of the QUBO is the right answer to the real problem.
- Every solver's answer is re-scored with `true_cut` and `is_balanced`, never with the QUBO energy.

**Where it falls short.**
- **Approximate for big nets.** For 4–8-gate nets the pair rule over-scores even splits (a 2|2 split of a 4-gate net scores 4/3, not 1), so the lowest energy may not be the lowest cut. Nets over 8 gates are left out of the QUBO entirely. c17 has neither, so Gate 1 does not test this.
- **Soft vs hard balance.** The penalty pushes toward exactly W/2, while the hard rule allows ⌈W/2⌉(1+ε). They agree on c17, but on weighted supernodes the penalty can push away from splits the hard rule accepts.
- **λ = 2.0 was chosen on c17 only.** The right value depends on node weights and net count, so it must be re-checked on coarsened designs (λ sweep, Phase 6).
- **Brute force is exponential.** It is limited to 20 nodes routinely and 24 at most, so it can't check anything larger.
- **Baseline limits.**
  - Simulated annealing returns the lowest-energy balanced state it visits, which is the lowest cut only if the QUBO is exact.
  - networkx's Kernighan–Lin balances node counts and ignores node weights.
  - Random uses one sample by default (the floor).
- **c17 proves very little.** It is tiny, so brute force, simulated annealing and Kernighan–Lin all find the optimum. Gate 1 shows the formula is right; it says nothing yet about how well QAOA does.

---

## Phase 3: QUBO → Hamiltonian → QAOA on Aer → Gate 2, part 1

**In plain words.**
- A Hamiltonian is the QUBO's energy formula rewritten in the language qubits use. A qubit reads Z = +1 or −1 instead of a bit 0 or 1.
- QAOA is a short quantum circuit with adjustable angles. It starts with every split equally likely, then shifts probability toward low-energy splits.
- p is how many times that shifting step repeats. Each repeat adds one angle pair, γ and β.
- A classical optimizer (COBYLA) tries angles on the simulator and keeps those giving the lowest average energy.
- We then measure the circuit 4000 times. Each measurement ("shot") gives one split.
- Our answer is the balanced shot with the lowest true cut, not the most common shot.
- This all runs on the Aer simulator, with no noise. No IBM hardware was used.

**Results on c17 (5 seeds, 4000 shots, λ = 2.0, 20 restarts).** Run `python scripts/phase3_c17.py` to reproduce them.

| | Average energy ⟨E⟩ | Balanced shots | Optimal shots | Found cut 2 |
|---|---|---|---|---|
| Uniform random guessing | 6.000 | 31.2% | 3.1% | not run |
| QAOA p=1 (mean) | 4.217 | 62.4% | 9.2% | 5/5 |
| QAOA p=2 (mean) | 3.557 | 82.4% | 18.0% | 5/5 |

**Does p=2 help?** On c17, yes on average, in all three measures. It takes about 4 times as long (about 15–23 s vs 4–5 s), and the size of the gain depends on the seed: its optimal rate ranged from 10.7% to 24.7%.

**Why it works.**
- **Hamiltonian.** `qiskit-addon-opt-mapper` substitutes x = (1−z)/2. Because that library is at version 0.1.0, the tests check its output against `qubo_energy`: on all 64 c17 states (hand-written and Yosys versions), and on a random QUBO with linear terms. The second check matters because c17's Hamiltonian has only ZZ terms, so c17 alone could not reveal a flipped sign.
- **Energy.** Aer's Estimator matches an exact `Statevector` calculation.
- **Angles.** A separate grid scan of γ and β puts the true p=1 minimum at about 4.03, and a test checks that seed 0 reaches it.
- **Answers.** Every answer is re-scored by `true_cut` and `is_balanced`, never by energy.

**Where it falls short.**
- **"Found cut 2" proves almost nothing on c17.** There are only 64 states and 2 are optimal, so 4000 uniform random shots would almost surely include an optimum. The meaningful result is the higher rate of good shots compared with guessing.
- **Beating uniform guessing is a low bar.** We have not yet compared QAOA's sampling with simulated annealing's, and c17 is easy for every classical solver here. This is not quantum advantage.
- **The optimizer can miss the best angles.** One COBYLA start at p=1 reaches the minimum only about 15% of the time, so even 20 starts should miss about 4% of the time. p=1 seed 4 did miss, stopping at ⟨E⟩ = 4.982. We don't know the true p=2 minimum because we did not scan it.
- **Lowest energy is not the same as most optimal shots.** The optimizer lowers average energy only. At p=2, seed 0 had lower energy than seed 2 (3.439 vs 3.482) but fewer optimal shots (15.6% vs 24.7%).
- **Only 6 qubits so far.** Larger designs are untested, so Gate 2 stays open until the ~12-node check in Phase 5. The balance penalty couples every pair of qubits (15 terms on c17), so the circuit grows quickly with size and could be hard to run on real hardware.
- **Simulator only.** These results have no noise, so hardware results may be much worse.
- **The mirror split is the same answer.** The Hamiltonian cannot tell a split from its mirror image, so "optimal shots" counts both.
