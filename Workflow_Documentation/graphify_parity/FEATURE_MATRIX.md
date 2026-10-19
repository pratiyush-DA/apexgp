# Feature Matrix: RepoPeekV3 (APEXGP) × Graphify Parity

## 1. Executive Summary & Audit Baseline
This matrix provides a line-item capability comparison between the upstream reference project **Graphify** and **RepoPeekV3 (APEXGP)**.

- **RepoPeekV3 (APEXGP) Commit SHA:** `3e8c33106827a6c19eabe248ed3a61b342683398`
- **Graphify Upstream Commit SHA:** `5b74d7d74911cf435c8f1636b6f96ea202cc6246` (v0.9.82, branch `v8`)
- **Evaluation Date:** 2026-10-18
- **Core Directive:** RepoPeekV3 remains the primary product. Upstream capabilities are absorbed by extending existing abstractions without duplicating graph engines or replacing verified RepoPeek architecture.

---

## 2. Parity Classification Legend
- **Already implemented:** Feature exists in RepoPeekV3 with equivalent or superior capability and passes existing tests.
- **Partially implemented:** Foundational capability exists in RepoPeekV3 but lacks specific upstream edge cases, heuristics, or formats.
- **Missing:** Applicable upstream capability absent in RepoPeekV3 that should be absorbed into RepoPeekV3's engine.
- **Not applicable:** Capabilities tied to proprietary cloud infrastructure, external model lock-in, or redundant external tool wrappers that contradict RepoPeek's architecture.
- **Requires further investigation:** Feasibility, dependency footprint, or performance impact under investigation.

---

## 3. Comprehensive Feature Matrix

### 3.1 Language Parsers & AST Extraction

| Feature / Language | Upstream Implementation (`graphify`) | RepoPeekV3 Implementation | Status | Relevant Tests | Dependencies | Implementation Risks | Proposed Integration Approach |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Python AST Extraction** | `graphify/extract.py`, `graphify/extractors/resolution.py` | `packages/core/parsers/python_parser.py` | **Partially implemented** | Upstream: `tests/test_languages.py`, `tests/test_issue_3405_python_resolution.py`<br>RepoPeek: `tests/unit/test_parsers.py` | `tree-sitter`, `tree-sitter-python` | Upstream handles nested class ID paths, star imports, and decorator metadata. RepoPeek handles basic functions/classes. | Extend `PythonParser` in `packages/core/parsers/python_parser.py` to extract decorators, nested class qualifications, and star import aliases. |
| **TypeScript / JavaScript AST** | `graphify/extract.py`, `graphify/extractors/resolution.py` | `packages/core/parsers/ts_js_parser.py` | **Partially implemented** | Upstream: `tests/test_js_import_resolution.py`, `tests/test_jsx_component_usage_calls.py`<br>RepoPeek: `tests/unit/test_parsers.py` | `tree-sitter`, `tree-sitter-typescript`, `tree-sitter-javascript` | TSConfig path aliases (`paths`, `baseUrl`) and dynamic imports are required for accurate monorepo resolution. | Integrate TSConfig path alias cache and barrel file re-export resolver into `TypeScriptJavaScriptParser`. |
| **Rust Extraction** | `graphify/extractors/rust.py` | None | **Missing** | Upstream: `tests/test_rust.py` | `tree-sitter-rust>=0.23,<0.25` | Binding in-file calls to matching struct types vs standalone functions. | Add `RustParser` implementing `BaseParser` interface under `packages/core/parsers/rust_parser.py` using upstream AST rules. |
| **Go Extraction** | `graphify/extractors/go.py` | None | **Missing** | Upstream: `tests/test_go_import_repoint.py`, `tests/test_go_interface_methods.py` | `tree-sitter-go>=0.23,<0.26` | Go package-to-directory mapping and interface implementation matching. | Add `GoParser` under `packages/core/parsers/go_parser.py` with directory-as-package import resolution. |
| **C / C++ / Objective-C** | `graphify/extract.py`, `graphify/extractors/objc.py` | None | **Missing** | Upstream: `tests/test_cpp_preprocess.py`, `tests/test_cpp_nested_and_cli.py` | `tree-sitter-c`, `tree-sitter-cpp`, `tree-sitter-objc` | Header (`.h`/`.hpp`) vs implementation (`.c`/`.cpp`) linking and compilation unit boundaries. | Add `CppParser` under `packages/core/parsers/cpp_parser.py` adopting upstream declaration-definition linking. |
| **C# / VB.NET / .NET Solutions** | `graphify/extractors/csharp.py`, `vbnet.py`, `sln.py` | None | **Missing** | Upstream: `tests/test_csharp_type_resolution.py`, `tests/test_dotnet.py` | `tree-sitter-c-sharp`, `tree-sitter-vb-dotnet` | Partial class unification across files and `.sln` project references. | Implement `CSharpParser` under `packages/core/parsers/csharp_parser.py` supporting partial classes and namespace imports. |
| **Java / JVM Languages (Kotlin, Scala, Groovy)** | `graphify/extract.py` | None | **Missing** | Upstream: `tests/test_java_type_resolution.py`, `tests/test_kotlin_grammar.py` | `tree-sitter-java`, `tree-sitter-kotlin`, `tree-sitter-scala`, `tree-sitter-groovy` | Anonymous classes, package hierarchies, and JVM bytecode-level sharing. | Implement `JavaParser` under `packages/core/parsers/jvm_parser.py`. |
| **Swift Extraction** | `graphify/extract.py`, `graphify/swift_dispatch.py` | None | **Missing** | Upstream: `tests/test_languages.py` | `tree-sitter-swift>=0.7,<0.9` | Protocol dispatch and SwiftUI view hierarchy noise filtering. | Implement `SwiftParser` under `packages/core/parsers/swift_parser.py`. |
| **SQL Schema Extraction** | `graphify/extractors/sql.py`, `graphify/pg_introspect.py` | None | **Missing** | Upstream: `tests/test_languages.py` | `tree-sitter-sql` | Table DDL references vs procedural PL/pgSQL function calls. | Implement `SQLParser` under `packages/core/parsers/sql_parser.py` extracting tables, columns, foreign keys, and views. |
| **Markdown & Wikilinks** | `graphify/extractors/markdown.py`, `graphify/markdown_resolution.py` | None | **Missing** | Upstream: `tests/test_markdown.py` | Native Python regex / AST | High noise ratio if raw markdown files emit excessive concept nodes. | Implement `MarkdownParser` under `packages/core/parsers/markdown_parser.py` capturing headers, intra-doc links, and code blocks. |
| **JSON & Manifest Ingestion** | `graphify/extractors/json_config.py`, `graphify/manifest_ingest.py`, `graphify/mcp_ingest.py` | None | **Missing** | Upstream: `tests/test_json_schema_config.py` | `tree-sitter-json` or Python `json` stdlib | Oversized lockfiles (`package-lock.json`, `uv.lock`) polluting graph with library noise. | Implement `ManifestParser` with strict whitelist (`package.json`, `tsconfig.json`, `pyproject.toml`, `Cargo.toml`). |
| **Niche Languages (COBOL, Fortran, Pascal, Solidity, Terraform, Verilog, Zig, Dart, Elixir, Erlang, Lua, Julia, R, Robot)** | `graphify/extractors/*` | None | **Requires further investigation** | Upstream language-specific tests | Modular optional dependencies | Dependency bloat from 20+ specialized tree-sitter grammars. | Keep niche languages as pluggable optional parser modules loaded on demand based on file extension. |

---

### 3.2 Cross-File Resolution & Relationship Semantics

| Feature | Upstream Implementation (`graphify`) | RepoPeekV3 Implementation | Status | Relevant Tests | Dependencies | Implementation Risks | Proposed Integration Approach |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Relative & Dot Import Resolution** | `graphify/extractors/resolution.py` | `packages/core/indexer.py` (`_resolve_import_path`) | **Already implemented** | Upstream: `tests/test_import_extension_resolution.py`<br>RepoPeek: `tests/unit/test_indexer_and_query.py` | Python stdlib | Path separator differences across Windows and POSIX. | RepoPeek's `_resolve_import_path` already handles `.py`, `.ts`, `.tsx`, `.js`, `index.*`, and `__init__.py`. Keep RepoPeek's verified resolver. |
| **Cross-Language Namespace Isolation** | `graphify/build.py` (`_EDGE_LANG_FAMILY`, `_cross_language`) | `packages/core/models.py`, `packages/core/indexer.py` | **Partially implemented** | Upstream: `tests/test_cross_language_call_resolution.py`<br>RepoPeek: `tests/unit/test_indexer_and_query.py` | None | Identical symbol names across languages (e.g., Python `next()` vs Next.js `next`) linking falsely. | RepoPeek namespaces node IDs with `{repo_id}:{runtime}:{file_path}:{name}`. Adopt Graphify's `_EDGE_LANG_FAMILY` table in `indexer.py` to forbid cross-family CALLS/IMPORTS. |
| **External Stub Minting (No Phantoms)** | `graphify/build.py` (`_mint_external_stub`, `mint_external_stubs_in_data`) | `packages/core/indexer.py` (lines 275-285) | **Already implemented** | Upstream: `tests/test_external_stub_endpoints.py`<br>RepoPeek: `tests/unit/test_indexer_and_query.py` | None | External stubs polluting god-node ranking. | RepoPeek already mints `ext:{runtime}:{module_path}` targets with `IMPORTS` edges. Ensure external stubs are excluded from centrality calculations. |
| **Interface & Protocol Polymorphic Dispatch** | `graphify/csharp_dispatch.py`, `graphify/swift_dispatch.py`, `graphify/interface_dispatch.py` | None | **Missing** | Upstream: `tests/test_csharp_interface_dispatch.py` | None | Ambiguous multiple implementations creating false-positive edges. | Extend `RepositoryIndexer` to run a second-pass dispatch resolver linking interface method calls to implementing classes with `confidence=0.80`. |
| **Confidence Scoring & Evidence Provenance** | `graphify/build.py` (`EXTRACTED`, `INFERRED`, `AMBIGUOUS`) | `packages/core/models.py` (`confidence: float`, `evidence: str`) | **Already implemented** | Upstream: `tests/test_inferred_confidence_rubric.py`<br>RepoPeek: `tests/unit/test_models.py` | SQLAlchemy | Semantic mismatch between enum strings and numerical floats. | Map Graphify's `EXTRACTED` -> 1.0, `INFERRED` -> 0.85 (or 0.75 for heuristic), `AMBIGUOUS` -> 0.50 in the unified schema. |

---

### 3.3 Graph Intelligence & Structural Analysis

| Feature | Upstream Implementation (`graphify`) | RepoPeekV3 Implementation | Status | Relevant Tests | Dependencies | Implementation Risks | Proposed Integration Approach |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Shortest Path Query** | `graphify/cli.py` (`path`), `graphify/serve.py` (`_shortest_path_text`) | `packages/core/query_engine.py` (`trace_relationships`) | **Already implemented** | Upstream: `tests/test_explain_cli.py`<br>RepoPeek: `tests/unit/test_indexer_and_query.py` | SQLAlchemy | BFS cycle trapping on dense graphs. | RepoPeek implements bidirectional BFS with visited sets and depth limits. Preserve RepoPeek's verified implementation. |
| **Change Impact & Blast Radius** | `graphify/affected.py`, `graphify/prs.py` | `packages/core/query_engine.py` (`analyze_change_impact`) | **Already implemented** | Upstream: `tests/test_affected.py`<br>RepoPeek: `tests/unit/test_indexer_and_query.py` | SQLAlchemy | Traversal explosion on god nodes. | RepoPeek provides deeper impact metrics including affected symbols, affected files, linked unit tests, and evidence traces. Retain RepoPeek engine. |
| **Related Test Discovery** | None (limited heuristic) | `packages/core/query_engine.py` (`find_related_tests`) | **Already implemented** | RepoPeek: `tests/unit/test_indexer_and_query.py` | SQLAlchemy | None | RepoPeek natively tracks `TESTS` edges and test files. Preserve as core advantage. |
| **Community Detection (Leiden / Louvain)** | `graphify/cluster.py` | None | **Missing** | Upstream: `tests/test_cluster.py` | `networkx`, optional `graspologic-native` | Native extension compilation on Windows platforms. | Implement `packages/core/clustering.py` using `networkx.algorithms.community.louvain_communities` with optional `graspologic-native` acceleration. |
| **God Nodes Detection** | `graphify/analyze.py` (`god_nodes`) | None | **Missing** | Upstream: `tests/test_god_nodes_cli.py`, `tests/test_god_nodes_exclude_hubs.py` | None (SQL degree count) | Synthetic hub nodes and standard library noise dominating rankings. | Add `get_god_nodes(repo_id, top_n)` to `GraphQueryEngine` using SQL degree aggregation filtered by `_BUILTIN_NOISE_LABELS`. |
| **Import Cycle Detection** | `graphify/analyze.py` (`find_import_cycles`) | None | **Missing** | Upstream: `tests/test_analyze.py` | `networkx` | Combinatorial explosion in strongly connected components with hundreds of cycles. | Implement `detect_cycles(repo_id)` in `GraphQueryEngine` using Tarjan's strongly connected components algorithm. |
| **Surprising Connections Analysis** | `graphify/analyze.py` (`surprising_connections`) | None | **Missing** | Upstream: `tests/test_analyze.py` | None | False positives from cross-cutting utilities (logging, telemetry). | Add `find_surprising_connections(repo_id)` evaluating cross-community edge weights. |
| **Suggested Architecture Questions** | `graphify/analyze.py` (`suggest_questions`) | None | **Missing** | Upstream: `tests/test_analyze.py` | None | Generic output of low utility to AI agents. | Implement deterministic template synthesizer in `GraphQueryEngine` targeting bridge nodes, circular dependencies, and isolated modules. |
| **Graph Diffing** | `graphify/analyze.py` (`graph_diff`) | None | **Missing** | Upstream: `tests/test_analyze.py` | None | Schema evolution between commits causing false diffs. | Implement `diff_revisions(repo_id, old_rev, new_rev)` comparing node and edge sets. |

---

### 3.4 Incremental Indexing, Persistence & Storage

| Feature | Upstream Implementation (`graphify`) | RepoPeekV3 Implementation | Status | Relevant Tests | Dependencies | Implementation Risks | Proposed Integration Approach |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Relational Graph Storage** | None (in-memory NetworkX dumped to JSON) | `packages/core/database.py`, `packages/core/models.py` | **Already implemented** | RepoPeek: `tests/unit/test_indexer_and_query.py` | SQLAlchemy, SQLite/PostgreSQL | Relational queries slower than pure in-memory pointer chases for deep graph traversals. | Retain PostgreSQL / SQLite as authoritative store; provide an in-memory NetworkX view adapter for Graphify clustering algorithms. |
| **Deterministic JSON Serialization** | `graphify/export.py` (`to_json`) | None | **Missing** | Upstream: `tests/test_export.py`, `tests/test_export_idempotent_writes.py` | Python stdlib | Non-deterministic dict ordering producing noisy Git diffs. | Add `export_to_graph_json(repo_id, output_path)` producing Graphify-compatible canonical JSON with sorted keys. |
| **Incremental File Updates** | `graphify/cache.py`, `graphify/cli.py` (`update`) | Batch full-index only | **Partially implemented** | Upstream: `tests/test_incremental.py`, `tests/test_incremental_mtime_collision.py` | Python stdlib | Incomplete re-indexing leaving dangling references to deleted symbols. | Implement `index_file_incremental(repo_id, rel_path)` in `RepositoryIndexer` deleting old nodes/edges for the file and re-resolving callers. |
| **File Watcher Daemon** | `graphify/watch.py` | None | **Missing** | Upstream: `tests/test_watch.py` | Optional `watchdog` | High CPU usage on large directories or frequent file edits. | Add `apexgp watch --repo-id <id>` using standard library debounced mtime polling or optional `watchdog`. |
| **Safe Transactional Recovery** | `graphify/export.py` (`backup_if_protected`, `write_text_atomic`) | `packages/core/indexer.py` (`db.rollback()`) | **Already implemented** | RepoPeek: `tests/unit/test_indexer_and_query.py` | SQLAlchemy transactions | Partial failure corrupting active repository state. | Retain database transactions; ensure indexing updates are committed atomically or rolled back cleanly on error. |

---

### 3.5 Visualization, Reporting & Artifacts

| Feature | Upstream Implementation (`graphify`) | RepoPeekV3 Implementation | Status | Relevant Tests | Dependencies | Implementation Risks | Proposed Integration Approach |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Interactive Developer Web Dashboard** | None (only static single-file HTML) | `apps/web/index.html`, `apps/web/app.js`, `apps/web/style.css` | **Already implemented** | RepoPeek: `tests/e2e/test_browser_flows.py` | Modern Vanilla JS + CSS | Requires live backend API server. | Retain RepoPeek's dynamic Web Dashboard as the primary interactive UI. |
| **Static Single-File HTML Visualizer** | `graphify/exporters/html.py` | None | **Missing** | Upstream: `tests/test_export.py` | `vis-network` (embedded CDN) | Browser memory limits if graph exceeds 5,000 nodes. | Implement `export_to_html(repo_id, output_path)` generating self-contained offline `graph.html` with node caps and community colors. |
| **Architecture Markdown Report** | `graphify/report.py` | None | **Missing** | Upstream: `tests/test_report.py` | Python stdlib | None | Add `generate_markdown_report(repo_id)` outputting structured `GRAPH_REPORT.md` (summary, god nodes, communities, cycles). |
| **Community Wiki Generation** | `graphify/wiki.py` | None | **Missing** | Upstream: `tests/test_wiki.py` | Python stdlib | Directory write permissions in restricted environments. | Implement `export_to_wiki(repo_id, output_dir)` creating markdown files per community with index. |
| **Multi-Format Graph Exports** | `graphify/export.py` (`to_obsidian`, `to_cypher`, `to_svg`, `to_graphml`, `to_canvas`) | None | **Missing** | Upstream: `tests/test_export.py` | Optional `matplotlib` for SVG | Format schema deprecation in external tools. | Add export utilities under `packages/core/exporters/` for Cypher, GraphML, and Canvas. |

---

### 3.6 Agent MCP Server & CLI Tooling

| Feature | Upstream Implementation (`graphify`) | RepoPeekV3 Implementation | Status | Relevant Tests | Dependencies | Implementation Risks | Proposed Integration Approach |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **MCP Server (Stdio & SSE)** | `graphify/serve.py` | `packages/mcp/server.py` | **Already implemented** | Upstream: `tests/test_serve.py`<br>RepoPeek: `tests/integration/test_mcp_server.py` | `mcp>=2.3.0` | Incompatible MCP schema breaking agent tool invocations. | Preserve RepoPeek's 13 existing MCP tools; add complementary Graphify tools (`god_nodes`, `detect_cycles`, `get_communities`, `suggest_questions`). |
| **Agent Steering Rules & Hooks** | `graphify/always_on/antigravity-rules.md`, `graphify/install.py` | `.agents/rules/repopeek.md` | **Already implemented** | Upstream: `tests/test_antigravity_install.py` | None | Agent context pollution. | Unify rules in `.agents/rules/repopeek.md` enforcing RepoPeek-first graph queries over raw file reading. |
| **Unified CLI Commands** | `graphify/cli.py` (25+ subcommands) | `packages/mcp/server.py` (CLI runner) | **Partially implemented** | Upstream: `tests/test_extract_cli.py` | `argparse` | CLI breaking change for existing users. | Implement unified CLI entry point `packages/cli/main.py` exposing `index`, `query`, `impact`, `path`, `export`, `report`, `watch`, and `serve`. |

---

### 3.7 Non-Code & Multimodal Ingestion

| Feature | Upstream Implementation (`graphify`) | RepoPeekV3 Implementation | Status | Relevant Tests | Dependencies | Implementation Risks | Proposed Integration Approach |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **PDF Ingestion** | `graphify/ingest.py` | None | **Missing** | Upstream: `tests/test_ingest.py` | Optional `pypdf`, `markdownify` | Malformed PDFs causing extraction crashes. | Add optional plugin `packages/core/ingest/pdf.py` converting PDF text to markdown concept nodes. |
| **Audio / Video Transcription** | `graphify/transcribe.py` | None | **Not applicable** | Upstream: `tests/test_transcribe.py` | `faster-whisper`, `yt-dlp` | Massive multi-gigabyte models, heavy GPU/CPU overhead. | Exclude from core; code intelligence engine does not need built-in video transcription. |
| **Cloud LLM Extraction Pass** | `graphify/llm.py` | None | **Not applicable** | Upstream: `tests/test_llm.py` | `openai`, `anthropic`, `boto3` | Exfiltration of private proprietary code to third-party APIs. | RepoPeek adheres strictly to local, deterministic, private parsing. Cloud LLM graph generation will NOT be integrated into core. |
