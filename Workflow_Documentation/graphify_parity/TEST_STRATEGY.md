# Test Strategy: RepoPeekV3 (APEXGP) × Graphify Parity

## 1. Quality Objectives & Invariants
- **Zero Regressions:** All 18 existing passing unit and integration tests must pass unconditionally on every change. No test may be relaxed, weakened, or deleted to accommodate new code.
- **Evidence-Backed Verification:** Every absorbed capability must be accompanied by automated test fixtures with verified expected outcomes.
- **Cross-Platform Determinism:** Tests must execute and pass identically on Windows (local environment), Linux (Ubuntu CI), and macOS.
- **Token Efficiency & Performance Measurement:** All architectural changes must preserve or improve the established token-reduction baselines measured against raw file reading.

---

## 2. Test Pyramid & Classification

```
                  ┌───────────────────────┐
                  │ E2E Browser & Agent   │
                  │ Flows (Playwright/CLI)│
                  ├───────────────────────┴─┐
                  │ Integration Tests       │
                  │ (FastAPI & MCP Stdio)   │
                  ├─────────────────────────┴─┐
                  │ Benchmark Performance     │
                  │ Suite (5 Tasks on dcnc)   │
                  ├───────────────────────────┴─┐
                  │ Unit Tests (Parsers, Engine,│
                  │ Clustering, Adapters)       │
                  └─────────────────────────────┘
```

---

## 3. Existing Baseline Test Suite

Currently passing test suite executed via `.venv/Scripts/pytest.exe`:

| Test Module | Test Name / Scope | Type | Status | Baseline Duration |
| :--- | :--- | :--- | :--- | :--- |
| `tests/fixtures/sample_repo/test_calculator.py` | `test_add`, `test_subtract` | Fixture validation | **Passed** | 0.05s |
| `tests/integration/test_api_endpoints.py` | Workspace & Repo REST CRUD, search, impact endpoints | Integration | **Passed** | 2.22s |
| `tests/integration/test_mcp_server.py` | Stdio MCP initialization, tool listing, query execution | Integration | **Passed** | 0.85s |
| `tests/unit/test_indexer_and_query.py` | End-to-end repository indexing, symbol resolution, BFS path trace | Unit | **Passed** | 0.32s |
| `tests/unit/test_parsers.py` | Python and TypeScript Tree-sitter AST extraction | Unit | **Passed** | 0.08s |
| `tests/unit/test_security.py` | SSRF URL validation, path traversal bounds checking | Unit | **Passed** | 0.04s |
| `tests/e2e/test_browser_flows.py` | Full user dashboard flow in headless Chromium | E2E (Playwright) | Requires live server (`:8000`) | ~8.00s |
| **Total Baseline** | **18 passed, 1 deselected** | | **All Green** | **3.56s** |

---

## 4. New Test Specifications for Absorbed Capabilities

### 4.1 Unit Tests

#### A. Graphify Schema & Adapter Tests (`tests/unit/test_graphify_adapter.py`)
- **Round-Trip Fidelity:** Verify that indexing a sample repository into RepoPeek's SQL database, exporting to `graph.json` via `graphify_adapter.to_graph_json()`, and reloading it produces an isomorphic in-memory NetworkX graph.
- **Edge Direction Invariance:** Assert that directional markers `_src` and `_tgt` match `source_id` and `target_id`.
- **Confidence Mapping:** Verify that float confidences (`1.0`, `0.85`, `0.50`) convert cleanly to `EXTRACTED`, `INFERRED`, `AMBIGUOUS` and back.

#### B. Namespace Isolation & Cross-Language Guards (`tests/unit/test_namespace_isolation.py`)
- **Language Collision Barrier:** Create a polyglot fixture containing identical function names in Python (`def next():`) and JavaScript (`function next() {}`). Assert that no `CALLS` edge bridges the two nodes.
- **External Stub Minting:** Index a file with `import requests` and `import express from 'express'`. Verify that target IDs are minted as `ext:py:requests` and `ext:js:express` with `IMPORTS` edges and zero dangling node references.

#### C. Graph Clustering & Analytics (`tests/unit/test_clustering_and_analysis.py`)
- **Modularity & Partitioning:** Execute Louvain and Leiden community detection on a known synthetic graph. Verify that tightly coupled modules receive the same `community_id`.
- **Oversized Cluster Splitting:** Create a star graph with 120 nodes. Assert that the community splitter divides it into sub-clusters under 100 nodes.
- **God Nodes Noise Filtering:** Feed standard library built-ins (`str`, `dict`, `List`, `None`) and mock objects into `god_nodes()`. Assert that they are filtered out and only user-defined architectural hubs are returned.
- **Cycle Detection:** Create a 3-module import loop (`A -> B -> C -> A`). Assert that `detect_cycles()` returns the exact 3-step circular path.

#### D. Incremental Indexing & Cache Invalidation (`tests/unit/test_incremental_indexing.py`)
- **Single File Update:** Modify one file in a 20-file repository. Assert that `index_file_incremental()` only updates that file's nodes and edges without mutating untouched files.
- **Deleted File Handling:** Delete a file and run incremental update. Assert that all nodes and edges belonging to that file are cleanly purged from the SQL database.
- **Cache Hit Verification:** Re-index an unchanged file and verify that the AST parser is bypassed via SHA-256 cache match.

#### E. Expanded Language Parsers (`tests/unit/test_polyglot_parsers.py`)
- **Rust:** Verify structs, impl blocks, trait implementations, and in-file calls.
- **Go:** Verify packages, exported functions, structs, interface methods, and package-level imports.
- **C# / .NET:** Verify classes, namespaces, methods, and partial class merging.
- **Java:** Verify classes, interfaces, static methods, and import statements.
- **C / C++:** Verify header-to-source linking (`.h` declarations linked to `.cpp` definitions).

#### F. Documentation & Manifest Ingestion (`tests/unit/test_doc_and_manifest_parsers.py`)
- **Markdown Links:** Verify that `[Link](file.md)` and `[[Wikilink]]` create `REFERENCES` edges between document nodes.
- **Package Manifests:** Verify that `dependencies` in `package.json` create `IMPORTS` edges to external concept nodes.

#### G. Exporters (`tests/unit/test_exporters.py`)
- **Standalone HTML Export:** Generate `graph.html`. Assert file exists, contains valid embedded HTML/JS, includes Vis.js configuration, and has size < 5MB.
- **Markdown Report Generation:** Generate `GRAPH_REPORT.md`. Assert presence of table of contents, god nodes table, community breakdown, and cycle warnings.

---

### 4.2 Integration Tests

#### A. Expanded MCP Server Test Suite (`tests/integration/test_mcp_server_expanded.py`)
- Spin up `packages.mcp.server` over stdio transport using `mcp.ClientSession`.
- Test all 18 MCP tools:
  - Repository management: `list_workspaces`, `list_repositories`, `get_repository_status`.
  - Symbol queries: `search_code`, `find_symbol`, `get_symbol_details`.
  - Traversal & trace: `get_callers`, `get_callees`, `get_dependencies`, `trace_relationships`.
  - Impact & tests: `analyze_change_impact`, `find_related_tests`, `get_graph_neighborhood`.
  - Absorbed analytics: `get_god_nodes`, `detect_cycles`, `get_communities`, `suggest_questions`, `export_graph`.
- Verify response payloads match JSON schemas and execute under 100ms.

#### B. Unified CLI Integration (`tests/integration/test_cli_commands.py`)
- Run CLI subcommands via subprocess:
  - `apexgp index --path ./tests/fixtures/sample_repo`
  - `apexgp query "Calculator"`
  - `apexgp impact --target Calculator`
  - `apexgp path --source add --target formatResult`
  - `apexgp report --output ./output/GRAPH_REPORT.md`
  - `apexgp export --format html --output ./output/graph.html`
- Verify return codes are 0 and stdout contains expected structured output.

---

### 4.3 End-to-End & Benchmark Verification

#### A. Benchmark Baselines (`benchmarks/run_new_5tasks.py`)
- **Target Repository:** `testing/dcnc` (Data Cleaning & Normalization Core).
- **Benchmark Tasks:**
  1. *Task 1 (Leaf):* Phone extension support in `PhoneEngine`.
  2. *Task 2 (Localized):* CIDR notation support in `IPAddressEngine`.
  3. *Task 3 (Cross-Module):* Create and register `ISBNEngine`.
  4. *Task 4 (Interface-Wide):* Strict anomaly threshold fail-fast in `PipelineService`.
  5. *Task 5 (Architectural Subsystem):* IQR statistical outlier detection in `NumericWorkflow`.
- **Metrics Tracked:**
  - Indexing execution duration (seconds).
  - Graph query response latency (milliseconds).
  - Memory consumption (RSS MB).
  - Token reduction vs. raw file dumps (% saved, target: >=85%).
  - Zero regression against recorded baseline in `benchmarks/new_benchmark_results.json`.

---

## 5. Continuous Testing Pipeline & Acceptance Gates

```
[Code Change Proposed]
       │
       ▼
 1. Flake8 / Ruff / Pyright Linting & Typecheck
       │
       ▼
 2. Run Baseline Unit Tests (18 existing tests must pass)
       │
       ▼
 3. Run New Capability Unit Tests (Parsers, Adapters, Analytics)
       │
       ▼
 4. Run MCP Stdio & CLI Integration Tests
       │
       ▼
 5. Run Graph Invariance Test (Deterministic round-trip check)
       │
       ▼
 6. Run Benchmark Evaluation Suite (Token & latency check)
       │
       ▼
[Phase Acceptance Gate Passed]
```
