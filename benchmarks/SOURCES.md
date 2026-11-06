# Benchmark sources

## ISCAS-85 circuits

`c17.v` is the standard ISCAS-85 c17 (6 NAND gates), written during Phase 1 with AI assistance rather than downloaded.

| File | Source | SHA-256 |
|---|---|---|
| `c432.v` | https://raw.githubusercontent.com/jpsety/verilog_benchmark_circuits/b4c6b6203b95b5314d47365f4a8196c08145519b/c432.v | `3b9262109d364dc463f0ee516f5d20c78bd2b8ffb2a38daece4a67cc8af39531` |
| `c880.v` | https://raw.githubusercontent.com/jpsety/verilog_benchmark_circuits/b4c6b6203b95b5314d47365f4a8196c08145519b/c880.v | `43db66681f46bd86eb84eb92cf4c419600e3818dd80f989af3b0968ccb727083` |

Repository: https://github.com/jpsety/verilog_benchmark_circuits, commit `b4c6b62`. Downloaded 2026-11-05.

**These aren't the original ISCAS-85 netlists.** The repository's README says it re-synthesized them with Cadence Genus, and our pipeline then re-synthesizes them again with Yosys and ABC. They should compute the same logic, but gate counts differ from published ISCAS figures, so cut values can't be compared directly with published results.

| Design | Nodes (gates) | Nets |
|---|---|---|
| c432 | 187 | 200 |
| c880 | 480 | 501 |

**License:** GitHub's API reported no license for that repository. Check this before redistributing the files outside the hackathon.

## KaHyPar configuration

`project_q/kahypar_cut.ini` is an unchanged copy of `config/cut_kKaHyPar_sea20.ini` from KaHyPar v1.3.7:
https://raw.githubusercontent.com/kahypar/kahypar/v1.3.7/config/cut_kKaHyPar_sea20.ini

SHA-256: `64ebd58a4f98b95bf87ae3de5341b2be00b09e122a0526ef65a799e39bb63dd8`

KaHyPar's GitHub repository lists GPL-3.0 and its README says GPLv3, while the classifier on its Python wheel says GPLv3 or later. This project adds no license of its own. KaHyPar is optional (`requirements-optional.txt`), and its wheel doesn't include this file.
