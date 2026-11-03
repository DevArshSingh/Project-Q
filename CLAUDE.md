# Project Q

Hackathon project (IBM Qiskit Fall Fest 2026). Read `SPEC.md` first, then follow `BUILD_PLAN.md` phase by phase (it is the step-by-step instruction file), then `docs/research.md` for background.

## Rules
- Develop and benchmark on Qiskit Aer. IBM hardware: ONE run only, in Phase 8 (budget ~10 min QPU total). Never submit hardware jobs without the user's explicit go-ahead.
- Never commit or print the IBM API token; load it from env var `QISKIT_IBM_TOKEN` or the saved account, and keep `.env` in .gitignore.
- Python 3.12 in `.venv`, created with `uv venv --python 3.12` (system Python is 3.14, do not use it); Yosys is a system install.
- Always compute the final cut classically from the real hypergraph; never trust the QUBO energy alone.
- Every QUBO change must be checked against brute force on c17 (expected cut = 2).
- Keep qubits ≤ 24.
- Build one phase at a time, write tests, run them, then stop for review before the next phase.
- Comment the quantum/EDA reasoning; the team is new to both.
- Do not claim quantum advantage anywhere (code, README, slides).
- Stretch (routing) only after Gate 2 in SPEC.md.

## Commands
- Setup: `uv venv --python 3.12 && source .venv/bin/activate && uv pip install -r requirements.txt`
- Tests: `pytest -q`
- CLI: `python -m project_q.cli partition benchmarks/c17.v --solver qaoa`
