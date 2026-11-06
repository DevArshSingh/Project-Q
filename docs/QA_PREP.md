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
A: λ trades cut against balance. On c17 the best lopsided split sits only 1.0 above the optimum at λ = 1, but 2.0 above it at λ = 2. That makes a noisy solver less likely to drift to an unbalanced answer, while the cut terms still decide among balanced splits. We chose it on c17 only, and it fails on some larger designs, so Phase 6 adds a rule that computes λ from the circuit. That rule also gives 2.0 on c17.

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

## Phase 5: Coarsening, FM refinement and multilevel

**Q: QAOA matched brute force on 12 qubits. Doesn't that show it works?**
A: Not by itself. With 4000 shots on only 4096 states, random guessing also matched in 14 of 15 runs, against QAOA's 15 of 15. So we compared shot rates as well. QAOA gave more than twice as many balanced shots as guessing, but among balanced shots it hit the optimum about as often as guessing did (for example 0.55% vs 0.55%). Our test, fixed before running, passed on 2 of 3 instances at p=1. So QAOA learned to balance, and we have no evidence yet that it finds better cuts. Gate 2 passes as the SPEC wrote it, and we report that limit openly. This is not quantum advantage.

**Q: If FM refines everything anyway, what is QAOA contributing on c432?**
A: Right now, nothing measurable. Random+FM got cuts of 11, 14 and 14, simulated annealing+FM 12, 11 and 11, and QAOA+FM 12, 12 and 12, at hundreds of times the runtime. KaHyPar got 11 in about 0.02 s. Most of the improvement comes from FM: coarse cuts of 12 to 32 became final cuts of 11 to 14. Those were 3 seeds on one circuit. Phase 6's larger ablation reached the same conclusion: random+FM matches QAOA+FM. Merging itself can't distort the score, because a coarse split costs exactly what its full-size split costs, and tests check this.

---

## Phase 6: Benchmarks, ablation and the λ rule

**Q: Does QAOA actually improve the final partition?**
A: We found no clear evidence that it does. We kept coarsening and FM the same and swapped only the coarse solver. At 12 supernodes over 10 seeds, random+FM against QAOA+FM averaged 13.5 vs 11.5 on c432 (p = 0.289) and 25.0 vs 26.2 on c880 (p = 0.219). So by a rule we fixed before running, random+FM matches QAOA+FM. FM does most of the work: it takes random's coarse cuts of about 61 on c880 down to 25. Simulated annealing+FM got the same c432 cuts as QAOA+FM in 0.04 s instead of 17.9 s, and KaHyPar alone got 11 in 0.01 s. At 20 qubits QAOA never reached the coarse optimum in 6 runs. Those are only 3 seeds per design, and this is a noise-free simulator at p=1.

**Q: How do you choose λ without brute force, and does it work?**
A: We use λ = d̄ / (δ_out² − δ_min²): going from the most even split to the first illegal one costs about as much as the wires on a typical node. It uses only the circuit and gives exactly 2.0 on c17. We tested it on 23 instances we hadn't looked at, using brute force only to score it. Its lowest-energy state was the true best split on 11 of 23, against 8 of 23 for fixed λ = 2.0, and it was never worse. So it's better, but it isn't solved. It also didn't change what QAOA samples on the instance we swept: balanced shots stayed around 22% at every λ from 0.1 to 10. We didn't adjust the formula after seeing those results.

---

## Phase 8: The single IBM hardware run

These answers describe the design. Until the job has run, say plainly that there are no hardware numbers yet.

**Q: What does your hardware run actually prove?**
A: Only that the circuit our pipeline builds from Verilog can run on a real chip, and how much noise changes its output. It doesn't show quantum beating classical, because brute force solves c17 instantly. Finding cut 2 proves almost nothing, since 4000 random shots on 64 states would almost surely include an optimal split. So we compare the rates of balanced and optimal shots against noise-free Aer (68.65% and 10.15% in our baseline run) and uniform guessing (31.25% and 3.125%). The angles were tuned on Aer, and scoring uses the same classical `true_cut` and `is_balanced` as every other solver.

**Q: What if noise dominates? And why did you try 10 transpile seeds if you won't re-run?**
A: If noise dominates, we report that. The balanced and optimal rates would fall toward guessing, and that's a real result about running this circuit on today's hardware. We get one run: the script records the job before sending it and refuses a second `--submit`, because re-running until the numbers look good would be picking the result we like. The 10 seeds are different. They only compare circuit sizes before any shots are taken, and they use no QPU time, so they can't favour a good-looking answer. We keep the version with the fewest 2-qubit gates and log all 10 in the results.

---

## Phase 9: Notebook and README

**Q: Are the notebook's results real? Can I reproduce them?**
A: Yes. Result tables are computed from `results/summary.csv` when the notebook runs, and a test recomputes the README table from the same file. The notebook also repeats a c432 QAOA+FM run live and checks that it gives exactly the saved split. It doesn't need Yosys or an IBM account. Versions are pinned, and we tested a fresh install on a clean copy. The exceptions are the p=2 and Gate 2 tables, which are quoted from `docs/DEFENSE.md`, with the scripts that reproduce them named. Everything is from the noise-free Aer simulator.

**Q: Your own README says classical wins. So what did you actually build?**
A: A working bridge and an honest way to measure it. A hardware engineer can give it Verilog and get a quantum-ready optimization problem, along with a score that uses the same classical rule for QAOA and classical solvers. Today KaHyPar beats or ties every multilevel mean, in 0.03 s or less, and random+FM matches QAOA+FM by a rule we fixed before running. We don't claim quantum beats classical. Our tool lets anyone re-run this comparison as quantum solvers change, and it's built in the same spirit as QOBLIB, though it isn't a QOBLIB submission.
