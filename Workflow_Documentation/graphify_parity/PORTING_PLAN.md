# Porting Plan: RepoPeekV3 (APEXGP) × Graphify Integration

## 1. Architectural Strategy & Ground Rules
- **RepoPeekV3 Primacy:** RepoPeekV3 (APEXGP) remains the single primary product and core repository. We do not replace RepoPeekV3 with Graphify or run two conflicting indexing engines.
- **Extend Over Replace:** Existing verified parsers, database models, impact engine, and MCP tools are extended rather than replaced.
- **Surgical Pull-Request-Sized Iterations:** Work is divided into 11 discrete, testable phases. Each phase has an explicit completion gate and test requirements.
- **No Test Deletions:** All 18 existing passing unit and integration tests must remain 100% green at every step.

---

## 2. Phase-by-Phase Implementation Roadmap

```
Phase 1: Audit & Baseline (Completed)
   │
   ▼
Phase 2: Unified Schema & Adapters (packages/core/models.py, graphify_adapter.py)
   │
   ▼
Phase 3: Namespace Isolation & Interop Guard (packages/core/indexer.py, models.py)
   │
   ▼
Phase 4: Core Resolution Enhancement (TSConfig paths, Python decorators/star imports)
   │
   ▼
Phase 5: High-Priority Language Expansion (Rust, Go, C#, JVM, C/C++)
   │
   ▼
Phase 6: Graph Analytics (Leiden/Louvain Clustering, God Nodes, Cycles, Diff)
   │
   ▼
Phase 7: Incremental Indexing & AST Cache (packages/core/cache.py, watch.py)
   │
   ▼
Phase 8: Documentation & Config Ingestion (Markdown, JSON manifests, SQL schemas)
   │
   ▼
Phase 9: Visualization, Reports & Exports (graph.html, GRAPH_REPORT.md, wiki)
   │
   ▼
Phase 10: Unified CLI & Agent MCP Suite (New CLI commands, MCP tools, agent rules)
   │
   ▼
Phase 11: End-to-End Evaluation & Benchmarks (Regression verification, dcnc benchmark)
```

---

### Phase 1: Architecture Audit & Feature Parity Documentation
- **Status:** **Completed (Current Audit)**
- **Scope:** In-depth audit of RepoPeekV3 (`3e8c33106827a6c19eabe248ed3a61b342683398`) and Graphify upstream (`5b74d7d74911cf435c8f1636b6f96ea202cc6246`). Production of 7 parity specifications under `Workflow_Documentation/graphify_parity/`.
- **Completion Gate:** Audit report approved by user. Zero code changes in application tree.

---

### Phase 2: Unified Graph Schema & Compatibility Adapters
- **Objective:** Add community clustering support to relational models and establish bidirectional NetworkX / `graph.json` conversion without breaking existing SQL entities.
- **Files Modified:**
  - `packages/core/models.py`: Add `community_id` to `GraphNodeModel`, add `GraphHyperedgeModel`.
  - `packages/core/database.py`: Include table creation migration.
- **New Files:**
  - `packages/core/adapters/graphify_adapter.py`: Functions `to_networkx(repo_id: str) -> nx.Graph`, `from_networkx(G: nx.Graph, repo_id: str)`, `to_graph_json(repo_id: str, path: str)`, `load_graph_json(path: str) -> dict`.
- **Existing Components Reused:**
  - `GraphNodeModel`, `GraphEdgeModel`, `get_db_session()`.
- **Dependencies:** `networkx>=3.4`.
- **Tests:** `tests/unit/test_graphify_adapter.py` validating 1:1 round-trip conversion without loss of line numbers, signatures, or confidence scores.
- **Complexity:** Low-Medium.
- **Completion Gate:** Unit tests pass verifying database nodes serialize to standard `graph.json` and reconstruct back into SQLite/PostgreSQL with identical counts.

---

### Phase 3: Graph Integrity, Provenance & Namespace Isolation
- **Objective:** Prevent polyglot cross-language graph pollution and eliminate phantom edges.
- **Files Modified:**
  - `packages/core/indexer.py`: Integrate `_EDGE_LANG_FAMILY` table into `RepositoryIndexer`. Enforce that CALLS edges cannot cross language boundaries unless in verified interop families (C/C++, JVM, JS/TS).
  - `packages/core/query_engine.py`: Ensure external stubs (`ext:*`) and built-in noise are excluded from caller and blast-radius traversals.
- **Existing Components Reused:**
  - `RepositoryIndexer._resolve_import_path()`, `GraphNodeModel.id` namespacing.
- **Tests:** `tests/unit/test_namespace_isolation.py` ensuring Python `next()` and Next.js `next` do not link; external package imports resolve to stubs without dangling targets.
- **Complexity:** Low.
- **Completion Gate:** Zero dangling edges and zero cross-language call false-positives on polyglot test fixture (`tests/fixtures/sample_repo`).

---

### Phase 4: Core AST & Cross-File Resolution Enhancement
- **Objective:** Enhance existing Python and TypeScript/JavaScript parsers to handle monorepo configurations and advanced language features.
- **Files Modified:**
  - `packages/core/parsers/python_parser.py`: Extract decorator calls (`@app.get` -> CALLS), nested class qualifications (`Outer.Inner`), star import re-exports.
  - `packages/core/parsers/ts_js_parser.py`: Load `tsconfig.json` path aliases (`@/components/*`), resolve barrel file star exports (`export * from './button'`).
  - `packages/core/indexer.py`: Second-pass polymorphic interface dispatch connecting interface method calls to implementing classes.
- **Existing Components Reused:**
  - `PythonParser`, `TypeScriptJavaScriptParser`, `BaseParser`.
- **Tests:** `tests/unit/test_parsers.py` (expanded with decorators, tsconfig paths, star exports).
- **Complexity:** Medium.
- **Completion Gate:** Parser unit tests pass on complex nested Python and TypeScript test files.

---

### Phase 5: Language & File-Format Expansion (Tier 1 Languages)
- **Objective:** Add high-impact tree-sitter parsers for Rust, Go, C#, Java, and C/C++.
- **Files Modified:**
  - `packages/core/parsers/__init__.py`: Register new file extensions (`.rs`, `.go`, `.cs`, `.java`, `.cpp`, `.c`, `.h`).
- **New Files:**
  - `packages/core/parsers/rust_parser.py`: Rust struct, trait, impl, and call extraction.
  - `packages/core/parsers/go_parser.py`: Go package, interface, struct, and method extraction.
  - `packages/core/parsers/csharp_parser.py`: C# class, interface, method, and partial class extraction.
  - `packages/core/parsers/jvm_parser.py`: Java class, interface, method, and package extraction.
  - `packages/core/parsers/cpp_parser.py`: C/C++ class, struct, function, and header-to-impl linking.
- **Existing Components Reused:**
  - `BaseParser`, `ExtractedNode`, `ExtractedCall`, `ExtractedImport`, `ParseResult`.
- **Dependencies:** `tree-sitter-rust`, `tree-sitter-go`, `tree-sitter-c-sharp`, `tree-sitter-java`, `tree-sitter-cpp`, `tree-sitter-c`.
- **Tests:** `tests/unit/test_polyglot_parsers.py` with multi-language sample fixtures.
- **Complexity:** High.
- **Completion Gate:** All five language parsers pass deterministic AST extraction tests on official code snippets.

---

### Phase 6: Graph Intelligence & Architectural Analysis
- **Objective:** Implement community detection, architectural bottleneck analysis (god nodes), cycle detection, and graph diffing.
- **Files Modified:**
  - `packages/core/query_engine.py`: Expose methods for god nodes, cycles, and suggested questions.
- **New Files:**
  - `packages/core/clustering.py`: Implements Louvain (NetworkX) with optional Leiden (`graspologic-native`), community splitting for >100 nodes, and cohesion scoring.
  - `packages/core/analysis.py`: God node ranking with noise filtering, Tarjan's cycle detection, surprising cross-community connections, and structural graph diffing.
- **Existing Components Reused:**
  - `GraphQueryEngine`, `to_networkx()`.
- **Dependencies:** `networkx>=3.4`, optional `graspologic-native`.
- **Tests:** `tests/unit/test_clustering_and_analysis.py`.
- **Complexity:** Medium.
- **Completion Gate:** Clustering successfully partitions code into functional domains; god nodes correctly identifies central abstractions while filtering built-ins.

---

### Phase 7: Incremental Indexing & Watch Daemon
- **Objective:** Enable sub-second incremental graph updates for modified files and provide a file-watching background daemon.
- **Files Modified:**
  - `packages/core/indexer.py`: Implement `index_file_incremental(repo_id: str, rel_path: str)` which surgically removes stale nodes/edges for the single file and updates cross-file links.
- **New Files:**
  - `packages/core/cache.py`: File-level SHA-256 and mtime cache for AST parse results.
  - `packages/core/watcher.py`: Background filesystem watcher with 3.0s debounce for automated incremental re-indexing.
- **Existing Components Reused:**
  - `RepositoryIndexer`, `RepositoryModel`.
- **Dependencies:** Python stdlib (or optional `watchdog`).
- **Tests:** `tests/unit/test_incremental_indexing.py` testing single-file add, edit, and delete scenarios.
- **Complexity:** Medium.
- **Completion Gate:** Incremental update completes in <100ms for a single modified file on a 500-file repository without full rebuild.

---

### Phase 8: Documentation, Schema & Manifest Ingestion
- **Objective:** Ingest Markdown documentation, SQL schemas, and project manifests into the code graph.
- **New Files:**
  - `packages/core/parsers/markdown_parser.py`: Ingests `.md` files, extracts sections/headings as nodes, and parses wikilinks and cross-document markdown links.
  - `packages/core/parsers/manifest_parser.py`: Ingests `package.json`, `Cargo.toml`, `pyproject.toml` extracting project dependencies as external concept nodes.
  - `packages/core/parsers/sql_parser.py`: Parses `.sql` files into table, column, and foreign key relationships.
- **Existing Components Reused:**
  - `BaseParser`, `packages/core/indexer.py`.
- **Dependencies:** `tree-sitter-sql` (optional), Python stdlib.
- **Tests:** `tests/unit/test_doc_and_manifest_parsers.py`.
- **Complexity:** Medium.
- **Completion Gate:** Markdown documentation links and package dependencies appear as queryable nodes in the repository graph.

---

### Phase 9: Visualization, Reporting & Artifact Generation
- **Objective:** Add generation of offline single-file `graph.html`, architectural `GRAPH_REPORT.md`, community wikis, and graph format exports.
- **New Files:**
  - `packages/core/exporters/html_exporter.py`: Generates standalone Vis.js `graph.html` with dark theme, search, community colors, and inspector panel.
  - `packages/core/exporters/report_generator.py`: Generates `GRAPH_REPORT.md` markdown report.
  - `packages/core/exporters/wiki_exporter.py`: Generates Markdown wiki per community.
  - `packages/core/exporters/cypher_exporter.py`: Generates Cypher queries for Neo4j / FalkorDB.
- **Existing Components Reused:**
  - `GraphQueryEngine`, `apps/web/app.js` (color palette alignment).
- **Tests:** `tests/unit/test_exporters.py`.
- **Complexity:** Low-Medium.
- **Completion Gate:** Valid offline HTML file generated and validated via headless browser; `GRAPH_REPORT.md` generated with complete sections.

---

### Phase 10: Unified CLI, Agent MCP Suite & Antigravity Rules
- **Objective:** Provide a comprehensive developer CLI and expand MCP tools to match Graphify agent capabilities.
- **Files Modified:**
  - `packages/mcp/server.py`: Add MCP tools:
    - `get_god_nodes(repo_id: str, top_n: int = 10)`
    - `detect_cycles(repo_id: str)`
    - `get_communities(repo_id: str)`
    - `suggest_questions(repo_id: str)`
    - `export_graph(repo_id: str, format: str, output_path: str)`
  - `.agents/rules/repopeek.md`: Update agent prompt directives.
- **New Files:**
  - `packages/cli/main.py`: Unified CLI command dispatcher (`apexgp index`, `apexgp query`, `apexgp impact`, `apexgp path`, `apexgp report`, `apexgp watch`, `apexgp export`).
- **Existing Components Reused:**
  - `packages/mcp/server.py`, `packages/core/query_engine.py`.
- **Dependencies:** `mcp>=2.3.0`, `argparse`.
- **Tests:** `tests/integration/test_cli_commands.py`, `tests/integration/test_mcp_server.py`.
- **Complexity:** Low-Medium.
- **Completion Gate:** All CLI commands execute cleanly; all 18 MCP tools list and execute via JSON-RPC 2.0.

---

### Phase 11: End-to-End Evaluation & Regression Verification
- **Objective:** Run full test suite, regression tests, and token-efficiency benchmarks against baseline.
- **Files Executed:**
  - `pytest tests/` (All unit, integration, and E2E tests).
  - `benchmarks/run_new_5tasks.py` on `testing/dcnc`.
- **Success Criteria:**
  - 100% test pass rate across all new and existing tests.
  - Zero regression in benchmark task execution times.
  - 85%+ token reduction compared to raw file reading on benchmark tasks.
- **Completion Gate:** Final parity audit signed off with measurable performance metrics.
