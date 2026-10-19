# Measurable Acceptance Criteria: RepoPeekV3 (APEXGP) × Graphify

## 1. Quality Policy & Verification Gates
To declare full feature parity without speculation, every capability must satisfy an observable, quantifiable acceptance criterion accompanied by automated verification evidence.

A feature is considered **Complete** only when:
1. Automated unit or integration tests pass in both local Windows and Linux environments.
2. The benchmark evaluation suite demonstrates non-regressed execution latency and memory usage.
3. Zero existing baseline tests are broken, modified, or skipped.

---

## 2. Quantitative Parity Criteria Matrix

| Dimension | Metric | Measurable Target | Verification Procedure & Test Harness |
| :--- | :--- | :--- | :--- |
| **1. Regression Safety** | Existing Test Pass Rate | **100% (18/18 tests pass)** | Execute `.venv/Scripts/pytest.exe -k "not test_browser_flows"`. All tests must pass with 0 failures and 0 errors. |
| **2. Graph Integrity** | Dangling Edge Rate | **0.0% (Zero dangling edges)** | SQL query checking `SELECT COUNT(*) FROM graph_edges WHERE target_id NOT IN (SELECT id FROM graph_nodes) AND target_id NOT LIKE 'ext:%'`. Expected result: `0`. |
| **3. Namespace Isolation** | Cross-Language Edge Leakage | **0.0% (Zero false cross-language links)** | Test on polyglot fixture with same-named symbols in Python (`def next()`) and TypeScript (`function next()`). Assert no `CALLS` edge bridges the nodes. |
| **4. External Stubs** | Unindexed Import Handling | **100% of external imports mint typed stubs** | Index repository with external dependencies (`import requests`, `import express`). Assert all external targets have prefix `ext:*` and `external=True`. |
| **5. Tree-sitter ABI Safety** | Grammar Load Success Rate | **100% of supported files parse without ABI version mismatch** | Verify that `tree-sitter>=0.25,<0.26` loads all Tier 1 grammars (Python, TS/JS, Rust, Go, C#, Java, C/C++) with zero `Incompatible Language version` errors. |
| **6. Language Coverage** | Tier 1 Language AST Extraction | **100% of valid syntax produces typed nodes** | Unit test suite (`tests/unit/test_polyglot_parsers.py`) testing syntax extracts for Python, TS, JS, Rust, Go, C#, Java, C, C++. |
| **7. Cross-File Resolution** | Relative & Package Import Accuracy | **>= 98% resolution of intra-repo imports** | Test suite evaluating resolution across relative imports (`./utils`, `../core`), TSConfig aliases (`@/*`), and Python package dots. |
| **8. Shortest Path Correctness** | Relational Path Finding | **100% optimal BFS path discovery within max depth** | Execute `trace_relationships()` on known paths in `tests/fixtures/sample_repo`. Path length and evidence steps must match topological ground truth. |
| **9. Blast-Radius Accuracy** | Change Impact Completeness | **100% recall of direct and transitive consumers** | Execute `analyze_change_impact()` on modified symbol. Assert all upstream callers and referencing test files are included in `affected_nodes`. |
| **10. Community Detection** | Clustering Modularity & Sizing | **Modularity Q >= 0.40; No cluster > 100 nodes** | Run Louvain/Leiden clustering on indexed repository. Assert all clusters satisfy `len(nodes) <= 100` via recursive community splitting. |
| **11. God Nodes Detection** | Bottleneck Precision | **>= 90% top-10 precision against manual architectural review** | Run `get_god_nodes()`. Assert built-in standard library noise (`str`, `dict`, `List`, `None`, `Mock`) is 100% excluded. |
| **12. Cycle Detection** | Circular Dependency Detection | **100% recall of cyclic import loops** | Run `detect_cycles()` on fixture with circular imports (`A -> B -> C -> A`). Assert loop is returned with full edge trace. |
| **13. Incremental Indexing Speed**| Single-File Delta Indexing Latency | **< 100ms for single file update on 500-file repo** | Benchmark `index_file_incremental()` on a modified file. Time delta update using `time.perf_counter()`. |
| **14. Cache Invalidation** | AST Cache Hit vs Miss Accuracy | **100% cache hit on unchanged files; 100% re-parse on modified files** | Verify cache check returns hit when file mtime/hash match, and forces re-parse when file is touched with new content. |
| **15. Serialization Determinism** | `graph.json` Idempotency | **Byte-identical JSON output on repeated runs** | Generate `graph.json` twice on unchanged repository. Assert `hashlib.sha256(json1) == hashlib.sha256(json2)`. |
| **16. Standalone HTML Export** | Offline Visualizer Validness | **Generates valid, standalone HTML with size < 5MB** | Run `export_to_html()`. Verify HTML opens cleanly in headless browser, renders Vis.js canvas, and initializes interactive physics simulation. |
| **17. Agent MCP Compatibility** | Tool Execution Latency & Validity | **100% of 18 MCP tools return valid JSON within < 150ms** | Run automated MCP test harness invoking all 18 tools via JSON-RPC 2.0. Check schema validity against Pydantic definitions. |
| **18. Token Reduction** | Agent Context Efficiency | **>= 85% token reduction vs raw file dumps** | Measure tokens consumed on the 5 progressive benchmark tasks (`benchmarks/run_new_5tasks.py`) against raw file reading. |

---

## 3. Verification Protocol & Acceptance Sign-off
Before any phase is marked as accepted:
1. Run linting: `ruff check packages/ apps/ tests/` (zero errors).
2. Run type checking: `pyright packages/` (zero type errors on core interfaces).
3. Run test suite: `.venv/Scripts/pytest.exe -v` (all tests passing).
4. Run benchmark evaluation: `python benchmarks/run_new_5tasks.py` (measure latency and token reduction).
5. Document verification evidence in PR description before merge.
