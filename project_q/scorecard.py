"""One honest JSON record per solver run (SPEC pipeline step 5).

Every number is computed from the hypergraph and the solver's final assignment. The
record never uses QUBO energy as the score, so all solvers are judged the same way.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from project_q.parse_yosys import Hypergraph, side_weights, true_cut
from project_q.solvers import SolverResult

# Only these solvers' search depends on λ. The others ignore the QUBO, so a λ on their record would mislead.
USES_LAMBDA = {"sa", "qaoa"}


def balance_ratio(hg: Hypergraph, assignment) -> float:
    """Heavier side ÷ ⌈W/2⌉. 1.0 is as even as possible; a split is balanced iff this is ≤ 1 + ε."""
    return max(side_weights(hg, assignment)) / math.ceil(hg.total_weight / 2)


def gap_percent(cut: int, best_known_cut: int | None, balanced: bool) -> float | None:
    """How far above the best known cut, in %. 0 means it matches.

    None when the comparison would mislead: there is no best known cut, or the split
    is unbalanced (breaking the balance rule can make a cut look better than the best),
    or the best known cut is 0 and this one isn't (a percentage of 0 is undefined).
    """
    if best_known_cut is None or not balanced:
        return None
    if best_known_cut == 0:
        return 0.0 if cut == 0 else None
    return 100.0 * (cut - best_known_cut) / best_known_cut


@dataclass
class ScoreRecord:
    design: str
    solver: str
    k: int
    cut: int
    best_known_cut: int | None
    gap_percent: float | None
    balance_ratio: float
    qubits: int | None  # None for classical solvers
    shots: int | None  # None for classical solvers
    runtime_s: float
    backend: str  # "aer_simulator" or "classical"
    lam: float | None  # written as "lambda" in JSON, since `lambda` is a Python keyword
    p: int | None
    seed: int | None
    # Beyond the SPEC's list, so that a reader can check the result:
    balanced: bool = False
    eps: float = 0.03
    best_known_source: str | None = None  # "brute_force" (proven) or "best_of_this_run" (not proven)
    assignment: list[int] = field(default_factory=list)
    info: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["lambda"] = data.pop("lam")
        return data


def make_record(
    design: str,
    hg: Hypergraph,
    result: SolverResult,
    lam: float,
    eps: float,
    best_known_cut: int | None = None,
    best_known_source: str | None = None,
) -> ScoreRecord:
    info = result.info
    # Recomputed here on purpose, so a wrong SolverResult can't reach the scorecard.
    cut = true_cut(hg, result.assignment)
    return ScoreRecord(
        design=design,
        solver=result.solver,
        k=2,
        cut=cut,
        best_known_cut=best_known_cut,
        gap_percent=gap_percent(cut, best_known_cut, result.balanced),
        balance_ratio=balance_ratio(hg, result.assignment),
        qubits=info.get("qubits"),
        shots=info.get("shots"),
        runtime_s=result.runtime_s,
        backend=info.get("backend", "classical"),
        lam=lam if result.solver in USES_LAMBDA else None,
        p=info.get("p"),
        seed=info.get("seed"),
        balanced=result.balanced,
        eps=eps,
        best_known_source=best_known_source,
        assignment=list(result.assignment),
        info=info,
    )


def save_record(record: ScoreRecord, results_dir: str | Path = "results") -> Path:
    """Write one new JSON file per run. The timestamp keeps earlier runs from being overwritten."""
    folder = Path(results_dir)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    path = folder / f"{record.design}_{record.solver}_{stamp}.json"
    path.write_text(json.dumps(record.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path
