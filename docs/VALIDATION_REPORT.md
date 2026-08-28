# Validation report

Date: 2026-08-15  
Platform: macOS arm64, Apple clang 21, Python 3.12.13, NumPy 2.3.5,
Pandas 2.2.3.

Upstream revisions used for the integration test:

- DESTINY v2: `276c0ec3e9b8c00b4c95ae8f60b377f4c6faa514`
- NVMExplorer: `16eb289096b69bbe0b88d51cfabd7927c5f9349b`
- HyCSim sample: official `main/traces_dir/example_trace.txt`, bounded by the
  fetcher and hashed in the generated `profile.json`.

Checks completed:

1. DESTINY and NVMExplorer NVSim compiled successfully.
2. The bounded sample fetch read 20,000 valid upstream events and retained
   18,000 after the inserted measurement marker.
3. Two-pass preparation produced all seven mmap arrays and the trace profile.
4. DESTINY smoke points completed for SRAM, STT-MRAM, SOT 12 F², SOT 24 F² and
   PCM.
5. NVMExplorer produced positive array results for SRAM, STT-MRAM, both SOT
   points and PCM; its independent energy formula check was within `1e-9` mJ.
6. HyCSim-compatible replay completed seven policy/arena rows with prefix-only
   tuning and suffix-only scoring.
7. The cross-stack join produced one row for every lifetime row.
8. `validate_results.py` passed: one mix, seven policy rows, five array-profile
   rows, no selection-guard failures.
9. Core unit tests passed for warm-up parsing, profile generation, mmap dtypes
   and tier-specific read/write accounting.

The bounded sample is only an integration test. Its RLI and performance numbers
must not be quoted in the manuscript. The paper decision requires the ten real
mixes and the gates in `INTEGRATION_AND_EXPERIMENT_PROTOCOL.md`.
