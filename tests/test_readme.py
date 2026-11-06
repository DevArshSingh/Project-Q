"""Phase 9: the README's numbers come from the real results, versions are pinned, and the wording stays honest."""

import re
from importlib.metadata import version
from pathlib import Path

import nbformat
import pytest

from project_q.experiments import read_rows

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")
SUMMARY = read_rows(ROOT / "results" / "summary.csv")

# README row label → (solver name in summary.csv, target). Every row must appear, so none can be dropped quietly.
ROWS = {
    "Random, flat (one split)": ("random", ""),
    "Simulated annealing, flat": ("sa", ""),
    "Kernighan–Lin, flat": ("kl", ""),
    "KaHyPar, flat": ("kahypar", ""),
    "Brute force, flat": ("brute", ""),
    "QAOA, flat": ("qaoa", ""),
    "Random + FM, 12 supernodes": ("multilevel-random", "12"),
    "Simulated annealing + FM, 12": ("multilevel-sa", "12"),
    "Brute force + FM, 12": ("multilevel-brute", "12"),
    "QAOA + FM, 12": ("multilevel-qaoa", "12"),
    "Random + FM, 20 supernodes": ("multilevel-random", "20"),
    "Simulated annealing + FM, 20": ("multilevel-sa", "20"),
    "Brute force + FM, 20": ("multilevel-brute", "20"),
    "QAOA + FM, 20": ("multilevel-qaoa", "20"),
}


def _runs(design, solver, target):
    return [r for r in SUMMARY if r["design"] == design and r["solver"] == solver and r["target"] == target]


def _cut_cell(design, solver, target):
    runs = _runs(design, solver, target)
    if not runs:
        return "not possible" if not target else ""  # flat QAOA/brute can't run; multilevel c17 wasn't run
    cuts = [int(r["cut"]) for r in runs]
    return f"{sum(cuts) / len(cuts):.1f} ({min(cuts)}–{max(cuts)})"


def _time_cell(solver, target):
    means = []
    for design in ("c432", "c880"):
        runs = _runs(design, solver, target)
        if not runs:
            return ""
        means.append(sum(float(r["runtime_s"]) for r in runs) / len(runs))
    return " / ".join("<0.01" if m < 0.005 else f"{m:.2f}" for m in means)


def _readme_table() -> dict[str, list[str]]:
    table = {}
    for line in README.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if line.startswith("|") and cells[0] in ROWS:
            table[cells[0]] = cells[1:]
    return table


def test_readme_table_matches_summary_csv():
    table = _readme_table()
    assert set(table) == set(ROWS)
    for label, (solver, target) in ROWS.items():
        expected = [_cut_cell(d, solver, target) for d in ("c17", "c432", "c880")] + [_time_cell(solver, target)]
        assert table[label] == expected, label


def test_requirements_are_pinned_to_installed_versions():
    pins = [
        line.strip()
        for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert len(pins) == 13
    for pin in pins:
        name, _, wanted = pin.partition("==")
        assert wanted, f"{pin} is not pinned"
        assert version(name) == wanted, name


def test_readme_links_exist():
    links = re.findall(r"\]\((?!https?://)([^)#]+)\)", README)
    assert links
    for link in links:
        assert (ROOT / link).exists(), link


def _texts() -> dict[str, str]:
    notebook = nbformat.read(ROOT / "notebooks" / "project_q.ipynb", as_version=4)
    return {"README.md": README, "notebook": "\n".join(cell.source for cell in notebook.cells)}


@pytest.mark.parametrize("name", ["README.md", "notebook"])
def test_quantum_advantage_only_ever_denied(name):
    lines = [line for line in _texts()[name].splitlines() if "quantum advantage" in line.lower()]
    assert lines, "the no-claim statement must be stated explicitly"
    for line in lines:
        assert re.search(r"\b(no|not|never)\b", line.lower()), line


@pytest.mark.parametrize("name", ["README.md", "notebook"])
def test_no_tokens_or_ibm_submission_code(name):
    text = _texts()[name]
    for forbidden in ("QiskitRuntimeService", "QISKIT_IBM_TOKEN", "least_busy", "--submit"):
        assert forbidden not in text, forbidden
    assert not re.search(r"\b[A-Za-z0-9_-]{40,}\b", text), "looks like a token"


def test_readme_is_honest_about_hardware_while_no_run_exists():
    if not (ROOT / "results" / "hardware_c17.json").exists():
        assert "No job has been sent to IBM" in README
