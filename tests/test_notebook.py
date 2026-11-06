"""The notebook deliverable: up to date, well-formed, and runs top to bottom without Yosys."""

import importlib.util
import json
import re
import sys
from pathlib import Path

import nbformat
import pytest
from nbconvert.preprocessors import ExecutePreprocessor

ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK = ROOT / "notebooks" / "project_q.ipynb"


def _builder():
    spec = importlib.util.spec_from_file_location("build_notebook", ROOT / "scripts" / "build_notebook.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_notebook_matches_builder_script():
    # Edit scripts/build_notebook.py, not the .ipynb; then run it. This catches forgetting either step.
    assert nbformat.read(NOTEBOOK, as_version=4) == _builder().build(), "run: python scripts/build_notebook.py"


def test_every_code_cell_has_markdown_before_it():
    cells = nbformat.read(NOTEBOOK, as_version=4).cells
    assert cells[0].cell_type == "markdown"
    for prev, cell in zip(cells, cells[1:]):
        if cell.cell_type == "code":
            assert prev.cell_type == "markdown", f"code cell {cell.id} has no explanation before it"


def test_notebook_runs_without_yosys(monkeypatch):
    # PATH = only the venv's bin dir, so `shutil.which("yosys")` fails inside the kernel,
    # exactly like a judge's laptop. Yosys lives in /usr/bin, which is not included.
    monkeypatch.setenv("PATH", str(Path(sys.executable).parent))
    nb = nbformat.read(NOTEBOOK, as_version=4)
    ExecutePreprocessor(timeout=300, kernel_name="python3").preprocess(nb, {"metadata": {"path": str(NOTEBOOK.parent)}})

    text = "\n".join(o.get("text", "") for c in nb.cells if c.cell_type == "code" for o in c.outputs)
    assert "Yosys not installed" in text
    assert "64/64" in text
    assert "λ = 2.0: best balanced cut 2, lowest-energy state is optimal: True, ΔE = 1.0" in text
    assert "20 balanced splits" in text
    # Phases 3-6: live cells agree with brute force and the saved results, and the saved verdicts appear.
    assert "Hamiltonian energy == QUBO energy on 64/64 basis states" in text
    assert "solver reported cut 0; scorecard recorded cut 2" in text
    assert "Live QAOA+FM split matches the saved seed 0 row (cut 12): True" in text
    assert "Random+FM matches QAOA+FM" in text
    assert "Recomputed verdicts match results/ablation_verdict.txt: True" in text
    assert "at 20 qubits: 0 of 6 runs" in text
    assert re.search(r"Held out: all\s+11/23\s+8/23", text)
    assert "Recomputed from the raw counts, matches the saved rates: True" in text
    assert sum("image/png" in o.get("data", {}) for c in nb.cells if c.cell_type == "code" for o in c.outputs) == 4


def test_limitations_section_is_honest():
    limits = next(c.source for c in _builder().build().cells if c.source.startswith("## 8. Limitations"))
    for phrase in (
        "no claim of quantum advantage",
        "Classical tools win at this size",
        "λ = 2.0 doesn't suit weighted supernodes",  # the 3 Known limitations in docs/DEFENSE.md
        "FM can get stuck when balance is tight",
        "The CLI didn't use multilevel",
        "One IBM hardware job has been run",
    ):
        assert phrase in limits, phrase
    assert "No IBM hardware job has been run" not in limits


def test_limitations_hardware_numbers_match_json():
    limits = next(c.source for c in _builder().build().cells if c.source.startswith("## 8. Limitations"))
    report = json.loads((ROOT / "results" / "hardware_c17.json").read_text(encoding="utf-8"))
    assert report["job_id"] in limits and report["backend"] in limits
    guessing = report["uniform_guessing"]
    for rate in (
        report["measured"]["feasible_rate"],
        report["aer"]["feasible_rate"],
        guessing["feasible_rate"],
        report["measured"]["optimal_split_probability"],
        report["aer"]["optimal_split_probability"],
        guessing["optimal_split_probability"],
    ):
        percent = f"{100 * rate:.3f}".rstrip("0").rstrip(".") + "%"  # exact, no rounding: 0.085 -> 8.5%
        assert re.search(rf"(?<![\d.]){re.escape(percent)}", limits), percent


def test_executed_copy_matches_builder_and_has_outputs():
    # The copy judges read. Same cells as the builder, so it can't silently go stale.
    executed = nbformat.read(NOTEBOOK.with_name("project_q_executed.ipynb"), as_version=4).cells
    built = _builder().build().cells
    assert [c.source for c in executed] == [c.source for c in built], "run: python scripts/build_notebook.py --execute"
    for cell in executed:
        if cell.cell_type == "code":
            assert cell.outputs, f"code cell {cell.id} has no output"
            for output in cell.outputs:
                assert output.output_type != "error", cell.id
                text = str(output.get("text", "")) + str(output.get("data", {}).get("text/plain", ""))
                assert "/home/" not in text and str(ROOT) not in text, f"absolute path in {cell.id}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
