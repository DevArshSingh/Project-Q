# Project Q: judge Q&A prep

Two likely judge questions per phase, with short answers we can defend. Read alongside `docs/DEFENSE.md`. We never claim quantum advantage.

---

## Phase 0: Environment

**Q: Why Qiskit Aer for development, and only one IBM hardware run?**
A: Aer is an exact, noise-free simulator. It lets us check that the algorithm is correct before noise gets involved, and it's free. Our hardware budget is about 10 minutes of QPU time, so we spend it on one small fixed-parameter job (c17, 6 qubits) and report whatever it gives, noise included.

**Q: Can someone without Yosys reproduce your results?**
A: Yes. Yosys's output for each benchmark is committed in `benchmarks/netlists/`, and the notebook loads those files. A test runs the notebook with Yosys hidden from PATH, and another test checks the committed file matches a live Yosys run whenever Yosys is installed.

---

## Phase 1: Verilog → hypergraph

**Q: Why a hypergraph and not an ordinary graph?**
A: One wire can connect three or more gates (c17 has two 3-gate nets). Cutting that wire costs one connection, however its gates are split. A graph would have to break it into pairs, and counting cut pairs over-counts. A hyperedge counts the wire once, which is the real cost.

**Q: Why does input pin N3 count as a net when it isn't driven by a gate?**
A: N3 feeds two gates. If those gates land on different chips, the N3 signal has to be routed to both, which costs a pin and a wire. So it is a real, cuttable connection. Wires that touch only one gate can never be cut, so we drop them.

---

## Phase 2: QUBO, brute force, baselines → Gate 1

**Q: How do you know the QUBO encodes the right problem?**
A: On c17 we check every one of the 64 possible assignments. The QUBO's cut energy equals the true cut on all 64, and brute force shows the lowest-energy state is exactly the best balanced split (cut 2), with an energy gap of 1.0 to the next state. Every solver's final answer is re-scored by plain-Python `true_cut`, never by the QUBO energy.

**Q: Why λ = 2.0 for the balance penalty?**
A: λ trades cut against balance. On c17 the best lopsided split sits only 1.0 above the optimum at λ = 1, but 2.0 above it at λ = 2. That makes a noisy solver less likely to drift to an unbalanced answer, while the cut terms still decide among balanced splits. We chose it on c17 only, so the λ sweep re-checks it on larger designs.

---

## Phase 3: QUBO → Hamiltonian → QAOA on Aer

**Q: 4000 shots on only 64 possible states. Wouldn't random guessing also find the answer?**
A: Yes, almost certainly, so finding cut 2 on c17 proves very little. We compare how often good answers appear instead. Over 5 seeds at p=1, 9.2% of shots were optimal and 62.4% were balanced, against 3.1% and 31.2% for uniform guessing. At p=2 it was 18.0% and 82.4%. That shows the tuned circuit is doing something, but it isn't quantum advantage: c17 is easy for classical solvers too, and this is a noise-free simulator.

**Q: How do you know the Hamiltonian and the QAOA code are right?**
A: We use IBM's `qiskit-addon-opt-mapper` for the conversion, but because it's at version 0.1.0, we test its output. The Hamiltonian's energy matches our QUBO formula on all 64 c17 states and on a random QUBO with linear terms, which catches sign errors c17 can't. A separate grid scan confirms the p=1 minimum energy of about 4.03. The optimizer doesn't always reach it: one start succeeds about 15% of the time, so we use 20 starts, and 1 of our 5 seeds still missed. The final answer is always re-scored classically with `true_cut`.

---

## Phase 4: Scorecard, CLI and larger benchmarks

**Q: How can we trust the numbers in your scorecard?**
A: Solvers don't report their own score. `make_record` recomputes the cut from each solver's final split using plain-Python `true_cut`, and balance comes from the one `is_balanced` rule, so every solver is judged the same way. Each JSON record keeps the split itself, so anyone can recheck it. If a split is unbalanced, we leave its gap empty so it can't look better than the best answer.

**Q: KaHyPar gets cut 11 on c432 in about 0.03 s, and QAOA can't even run on it. Why use QAOA at all?**
A: For partitioning today, you should use KaHyPar, and our scorecard shows that plainly. Our contribution is the bridge from Verilog to a quantum-ready problem, plus an honest way to compare solvers on it. That lets a hardware engineer measure whether quantum solvers ever catch up. We don't claim quantum advantage. Note that 11 is the best cut found in that run, not a proven optimum.

---

## Phase 5: Coarsening + FM → Gate 2 (part 2)

**Q: How do you run QAOA on a 187-gate circuit when you only have 24 qubits?**
A: Multilevel: heavy-edge matching merges gates that share many nets into weighted supernodes until ≤ ~12–20 remain. QAOA (or SA/random/brute) solves that small core; then we project the split back level by level and run Fiduccia–Mattheyses (FM) single-node moves that lower `true_cut` while keeping balance. CLI: `--solver multilevel-qaoa`.

**Q: Is QAOA doing the work, or is FM?**
A: Fair question — Phase 6's ablation swaps the coarse solver for random/SA/brute with the same FM. Gate 2 part 2 only checks that QAOA matches brute force on the coarsened ~12-node core itself (before blaming FM).

