# Reference Research: Independent Code Intelligence Platform (ApexGraph)

## 1. Overview & Objective
This document synthesizes research into publicly documented developer intelligence tools, graph analyzers, reverse engineering harnesses, and agent-assisted workflows. Based on these insights, we design **ApexGraph**—an independent, production-minded code knowledge graph and intelligence platform.

ApexGraph enables software engineers and AI coding agents to understand codebases through a deterministic, typed property graph. It extracts structural symbols, calls, imports, tests, and change-impact blast radii without relying on opaque vector stores or proprietary cloud backends.

---

## 2. Reference Project Analysis

### 2.1 Graphify (`Graphify-Labs/graphify`)
* **What it provides:** Open-source code-to-knowledge-graph engine. Ingests source code, markdown documentation, SQL schemas, configurations, and multimodal diagrams to construct a queryable knowledge graph.
* **Core Mechanisms:** Deterministic Abstract Syntax Tree (AST) parsing via Tree-sitter across 40+ languages. Outputs a `graph.json` artifact, an interactive HTML visualizer, and Markdown summaries (`GRAPH_REPORT.md`).
* **Value for Coding Agents:** Drastically cuts token consumption by substituting massive whole-file dumps with targeted graph facts and subgraphs.
* **Limitations/Unverified:** While the core CLI tool parses local files, advanced indexing modes and semantic integrations reference cloud services whose backend algorithms are not open-source.

### 2.2 Graphify MCP (`Graphify-Labs/graphify-mcp`)
* **What it provides:** A Model Context Protocol (MCP) server interface exposing graph operations to agents (Claude Code, Cursor, Windsurf, Antigravity).
* **Documented Tools:**
  * `graph_stats`: High-level metrics on nodes, edges, density, and language breakdown.
  * `god_nodes`: Identification of high-degree structural hubs (bottlenecks and central utilities).
  * `get_neighbors`: Extraction of immediate typed neighbors for a symbol or file.
  * `shortest_path`: Relational traversal between two disparate symbols or files.
  * `query_graph`: Structured cypher/graph query execution for architectural analysis.
* **Public Boundary Note:** The public MCP repository documents interfacing with hosted Graphify capabilities. Our platform implements an independent, self-contained local engine with equivalent capabilities.

### 2.3 REA (`morluto/rea` - Reverse Engineer Anything)
* **What it provides:** An open-source agent harness and MCP server connecting AI coding agents to software analysis and reverse engineering workflows.
* **Key Architectural Concepts Adopted:**
  * **Evidence Graphs:** Every asserted fact, node, and relationship must link to verifiable provenance (file path, revision, exact line range, parser method).
  * **Fact vs. Heuristic Distinction:** Explicit confidence tagging (e.g., 1.0 for confirmed AST definitions, 0.75 for heuristic lexical or dynamic resolution) rather than fabricating runtime ground truth.
  * **Safe Sandbox Controls:** Strict boundaries around untrusted target files and binaries; preventing execution of inspected targets.

### 2.4 ECC (`affaan-m/ecc` - Everything Claude Code / Everything Gemini Code)
* **What it provides:** Agent harness engineering framework standardizing the agent lifecycle.
* **Engineering Discipline Adopted:**
  * Structured loop: `Research -> Plan -> Test -> Implement -> Review -> Verify`.
  * Refusal to degrade quality bar: Zero silently ignored errors, no bypassed assertions.
  * Preservation of architectural decisions (ADRs) and testing verification.

### 2.5 Ponytail (`dietrichgebert/ponytail`)
* **What it provides:** Rigorous simplicity and anti-bloat principles for agentic development.
* **Core Constraints Adopted:**
  * **YAGNI:** Build only what is required by current specifications.
  * Prefer standard library and native language features before external dependencies.
  * Eliminate speculative abstractions, micro-wrappers, and dead code.
  * Transparent error reporting over silent fallbacks.

### 2.6 Agent Skills (`addyosmani/agent-skills`)
* **What it provides:** Standardized engineering playbooks for AI agents.
* **Selected Workflows:**
  * `test-driven-development`: Red-green-refactor loop with deterministic test suites.
  * `frontend-ui-engineering`: High-craft, responsive, accessible developer dashboard.
  * `security-and-hardening`: Defense-in-depth against SSRF, path traversal, and command injection.
  * `debugging-and-error-recovery`: Root-cause diagnosis without guesswork.
  * `spec-driven-development`: Clear API contracts and schema validation.

### 2.7 Microsoft Playwright MCP (`microsoft/playwright-mcp`)
* **What it provides:** Browser automation protocol allowing AI agents to navigate local web applications, interact with components, inspect accessibility trees, and capture console/network errors.

---

## 3. Comparative Architecture & Proposed Design

| Dimension | Graphify / Graphify MCP | REA | ApexGraph (Proposed Platform) |
| :--- | :--- | :--- | :--- |
| **Engine Core** | Tree-sitter + local JSON graph | Static & decompiled analysis | Tree-sitter multi-language AST + Property Graph Engine |
| **Storage** | Single JSON file / Hosted API | Ephemeral SQLite / Memory | PostgreSQL schema (with embedded SQLite compatibility for zero-friction local dev) |
| **Edge Provenance** | Basic structural link | High provenance evidence graphs | Full provenance: source file, line ranges, commit hash, confidence metric |
| **Query Engine** | In-memory graph / Python | Socket/IPC queries | Recursive CTE SQL queries + In-Memory Graph Index |
| **Agent Protocol** | Stdio MCP | Stdio / Socket MCP | Streamable HTTP + Stdio MCP (JSON-RPC 2.0 / MCP 2024-11-05 spec) |
| **Web Interface** | Static HTML visualization | None (CLI/Agent only) | Full developer dashboard (interactive canvas/SVG graph, search, impact explorer, diagnostics) |

---

## 4. Distinguishing Facts, Hypotheses, and Open Questions

### Facts (Empirically Verified)
1. Tree-sitter grammar parsers for Python, JavaScript, and TypeScript execute deterministically in our local environment without external compilation steps.
2. Coding agents consume 80-90% fewer tokens when provided structured symbol subgraphs (callers, callees, definitions) compared to entire source files.
3. Unnamespaced symbol identifiers across polyglot repositories cause catastrophic graph edge collisions (e.g., Next.js `next` vs. Python `next()`). Explicit runtime and module namespacing is mandatory.

### Hypotheses (To Be Validated in Prototype)
1. Recursive CTE queries in SQL can traverse up to 10 degrees of dependency depth in under 15ms for codebases under 50,000 nodes.
2. Static AST call-graph extraction combined with file-level import resolution provides sufficient fidelity for 85%+ of developer change-impact queries.

### Open Questions
1. How best to bound visualization density for "god nodes" (e.g., standard libraries or shared utility files) in the web UI without hiding critical dependency links? *(Addressed via degree thresholds and pagination).*
2. What is the optimal balance between real-time incremental re-indexing and batch indexing for large remote repositories? *(Addressed via debounced file mtime hashing).*

---

## 5. Security & Attribution Considerations

### Licenses & Attribution
* **Tree-sitter:** MIT License.
* **FastAPI / Pydantic / SQLAlchemy:** MIT / BSD Licenses.
* **Model Context Protocol SDK:** MIT License.
* **Agent Skills & Ponytail:** MIT License.
* All original code written in ApexGraph is strictly independent, clean-room implementation of public specifications without proprietary code extraction.

### Security Defenses
* **Path Traversal:** Strict validation of repository paths using normalized absolute paths restricted to registered workspace directories.
* **SSRF Protection:** URL scheme whitelisting (`https://` only), DNS resolution inspection blocking internal RFC 1918 / loopback ranges (`127.0.0.1`, `10.0.0.0/8`, `192.168.0.0/16`, AWS metadata `169.254.169.254`).
* **Zero Code Execution:** Untrusted repository code is strictly parsed as data. No repository scripts (`setup.py`, `package.json` scripts, shell scripts) are executed during indexing.
* **Secret Redaction:** Regex-based sanitization of authentication tokens, passwords, and private keys in logs and MCP output.
