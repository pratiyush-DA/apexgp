# ApexGraph (ApexGP)

[![Python: >=3.10](https://img.shields.io/badge/python->=3.10-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![MCP: 2.3.0](https://img.shields.io/badge/mcp-2.3.0-orange.svg)](https://modelcontextprotocol.io/)
[![Tests: 251+ Passing](https://img.shields.io/badge/tests-251+_passing-brightgreen.svg)](#12-testing--benchmarks)
[![Languages: 9 Supported](https://img.shields.io/badge/languages-9_polyglot-purple.svg)](#7-supported-languages-matrix)

> **Independent Code Knowledge Graph & Developer Intelligence Engine for AI Coding Agents**

ApexGraph (ApexGP) parses polyglot codebases into a typed, deterministic property graph backed by SQLAlchemy 2.0 (SQLite or PostgreSQL). It extracts structural symbols, calls, imports, tests, and change-impact blast radii—exposing real-time code intelligence to AI coding assistants via the Model Context Protocol (MCP) and an interactive local web console.

* **Project Identity:** **ApexGP** is the official GitHub repository and project identity ([https://github.com/pratiyush-DA/apexgp.git](https://github.com/pratiyush-DA/apexgp.git)). **ApexGraph** is the underlying Python package (`apexgraph`), core engine, and API service.
* **Autonomous & Local:** Runs 100% locally with zero cloud dependencies, zero external telemetry, and zero runtime code execution.

---

## Table of Contents

1. [Architectural Overview](#1-architectural-overview)
2. [Key Capabilities](#2-key-capabilities)
3. [Quick Start (3-Minute Setup)](#3-quick-start-3-minute-setup)
4. [First End-to-End Workflow](#4-first-end-to-end-workflow)
5. [AI Coding Agent Integration (MCP)](#5-ai-coding-agent-integration-mcp)
6. [Complete MCP Tools Reference (16 Tools)](#6-complete-mcp-tools-reference-16-tools)
7. [Supported Languages Matrix (9 Languages)](#7-supported-languages-matrix-9-languages)
8. [Graph Data Model & Intelligence Engine](#8-graph-data-model--intelligence-engine)
9. [Web Console & REST API Reference](#9-web-console--rest-api-reference)
10. [Configuration & Database Backends](#10-configuration--database-backends)
11. [Containerized Deployment (Docker Compose)](#11-containerized-deployment-docker-compose)
12. [Security & Enterprise Posture](#12-security--enterprise-posture)
13. [Testing & Benchmarks](#13-testing--benchmarks)
14. [Troubleshooting & FAQ](#14-troubleshooting--faq)
15. [Contributing & License](#15-contributing--license)

---

## 1. Architectural Overview

```
                          +-------------------------------------------------+
                          |            AI Coding Agents / IDEs              |
                          | (Google Antigravity, Claude Code, Cursor, Windsurf) |
                          +------------------------+------------------------+
                                                   |
                                                   | MCP Protocol (Stdio / SSE)
                                                   v
+-----------------------+              +------------------------------------+
|     Web Dashboard     |              |        ApexGraph MCP Server        |
|  (Interactive Canvas) |              |  (16 Tools / packages.mcp.server)  |
+-----------+-----------+              +-----------------+------------------+
            |                                            |
            | HTTP / REST                                | Python Query API
            v                                            v
+-----------------------+              +------------------------------------+
|  FastAPI REST Engine  | <----------> |         Graph Query Engine         |
|  (apps.api.main:8000) |              |   (packages.core.query_engine)     |
+-----------+-----------+              +-----------------+------------------+
            |                                            |
            +--------------------+-----------------------+
                                 |
                                 v
               +-----------------------------------+
               |       SQLAlchemy 2.0 Engine       |
               | (SQLite Default / PostgreSQL Prod)|
               +-----------------+-----------------+
                                 ^
                                 | Graph Writes & Provenance
               +-----------------+-----------------+
               |     Repository Indexer Core       |
               |    (packages.core.indexer)        |
               +-----------------+-----------------+
                                 ^
                                 | AST Extraction
               +-----------------+-----------------+
               |   Polyglot Tree-sitter Parsers    |
               | (9 Languages / Static Only / No Run) |
               +-----------------------------------+
```

---

## 2. Key Capabilities

* **16-Tool Model Context Protocol (MCP) Server:** Standardized stdio and SSE transport interfaces for Google Antigravity, Claude Code, Claude Desktop, Cursor, and Windsurf.
* **Blast-Radius Change-Impact Engine:** Traverses upstream callers and structural dependents from modified symbols or files, identifying downstream breakage and linked unit tests before edits are made.
* **Deterministic 9-Language Property Graph:** Static AST extraction across Python, TypeScript, JavaScript, Go, Rust, Bash, SQL, JSON, and YAML with zero runtime execution.
* **Full Evidence Provenance:** Every asserted node and edge includes source file paths, 1-indexed line numbers, confidence metrics (0.0 to 1.0), and verbatim code snippets.
* **Sub-4ms Graph Queries & Retrieval:** Hybrid lexical-structural search featuring compound token splitting, inflection stem matching, and contextual architectural boosting for classes and interfaces.
* **Interactive Local Web Console:** HTML5 Canvas force-directed graph visualization, live symbol inspection, and blast-radius exploration.
* **Enterprise Security Guardrails:** Strict static parsing (no `eval` or execution of indexed code), path traversal defense, SSRF blocking on remote Git URLs, and automated credential redaction.

---

## 3. Quick Start (3-Minute Setup)

Follow these verified steps to get ApexGraph running from a clean checkout.

### Step 1: Clone the Repository

```bash
git clone https://github.com/pratiyush-DA/apexgp.git apexgp
cd apexgp
```

### Step 2: Create and Activate a Virtual Environment

ApexGraph requires **Python 3.10 or newer**.

**Windows PowerShell:**
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

**Linux / macOS:**
```bash
python3 -m venv .venv
source .venv/bin/activate
```

### Step 3: Install Dependencies

**Standard Installation:**
```bash
pip install .
```
*(Alternatively, install via requirements: `pip install -r requirements.txt`)*

**Contributor / Editable Installation (includes development, test, and browser automation dependencies):**
```bash
pip install -e ".[dev]"
```

> **Note on Console Commands:** Installing via `pip install .` or `pip install -e .` registers both `apexgp` and `apexgraph` in your environment's PATH. If installing strictly via `requirements.txt`, invoke the modules using `python -m packages.mcp.server`.

### Step 4: Configure the Environment (Optional)

ApexGraph defaults to an embedded SQLite database (`./data/apexgraph.db`) with zero external service dependencies. To customize ports or database settings:

**Windows PowerShell:**
```powershell
Copy-Item .env.example .env
```

**Linux / macOS:**
```bash
cp .env.example .env
```

### Step 5: Initialize the Database

Run the database migration script to generate all required tables, constraints, and indexes. The script automatically creates the `./data/` directory if using SQLite:

```bash
python -m packages.core.migrations
```

### Step 6: Verify Installation

Verify the MCP server and inspect all 16 registered tools:

```bash
apexgp --test
```
*(Or invoke via module: `python -m packages.mcp.server --test`)*

**Expected output:**
```text
ApexGraph MCP Server initialized with 16 tools:
 - list_workspaces: Lists all registered workspaces in ApexGraph....
 - list_repositories: Lists repositories in ApexGraph with indexing status and lan...
 - get_repository_status: Returns indexing status, last indexed revision, and statisti...
 - search_code: Performs structural and lexical search across code symbols a...
 - find_symbol: Locates symbol definitions matching the given name with exac...
 - get_symbol_details: Returns complete symbol definition, incoming calls, and outg...
 - get_callers: Finds all functions/methods that call this symbol directly o...
 - get_callees: Finds all functions/methods called by the given symbol direc...
 - get_dependencies: Returns file imports and structural dependencies of a file o...
 - trace_relationships: Calculates the shortest relational path connecting two symbo...
 - analyze_change_impact: Calculates blast radius: all affected symbols, files, and li...
 - find_related_tests: Discovers unit and integration tests covering the target sym...
 - get_graph_neighborhood: Returns immediate N-hop graph neighborhood for visualization...
 - detect_import_cycles: Detects cyclic import dependencies across internal files in ...
 - god_nodes: Identifies architectural hub nodes (god nodes) with high con...
 - sync_repository: Incrementally synchronizes a repository with disk, updating ...
Self-test check passed successfully.
```

### Step 7: Launch the Web Console & REST API

Start the local server (runs FastAPI on port 8000 by default):

```bash
python -m apps.api.main
```

Open your browser to: **`http://127.0.0.1:8000`**

---

## 4. First End-to-End Workflow

> **Prerequisite:** The Model Context Protocol (MCP) server provides code intelligence on repositories that have been indexed. Before querying code via an AI assistant, index your repository through the Web Console or REST API.

### Workflow Walkthrough

1. **Start the Service:**
   Launch the server via `python -m apps.api.main` and open `http://127.0.0.1:8000`.
2. **Create a Workspace:**
   In the top-left sidebar, enter a workspace name (e.g. `default` or `my-project`) and click **Create Workspace**.
3. **Register a Repository:**
   Under **Add Repository**:
   * Name: `my-service`
   * Source Type: Select `local`
   * Source Path: Provide the absolute filesystem path (e.g., `C:/projects/my-service` or `/home/user/projects/my-service`).
   * Click **Register Repository**.
4. **Trigger Indexing:**
   Locate your repository card in the dashboard and click **Index**. ApexGraph will parse all supported source files, construct the typed property graph, and link calls, imports, and definitions.
5. **Confirm Graph Statistics:**
   The repository status transitions from `pending` to `indexed`. The dashboard displays node count, edge count, language distribution, and commit SHA.
6. **Inspect in Web Console:**
   * **Canvas Explorer:** Zoom and pan the interactive force-directed graph. Click any node to open the symbol drawer displaying docstrings, signatures, and code snippets.
   * **Search:** Search for symbols (e.g., `UserService`, `authenticate`, `calculate_tax`).
   * **Impact Simulation:** Select a symbol or file to run a real-time blast-radius calculation.
7. **Query via MCP Assistant:**
   Connect your AI assistant (see Section 5 below) and ask:
   > *"Run analyze_change_impact on symbol authenticate in repository <repo_id> to identify affected callers and unit tests."*

---

## 5. AI Coding Agent Integration (MCP)

ApexGraph implements the standard Model Context Protocol (MCP) specification over **Stdio** (local process) and **SSE / Streamable HTTP** (networked).

### Google Antigravity Setup

Add ApexGraph to your workspace or global Antigravity MCP configuration (`.agents/mcp_config.json`):

```json
{
  "mcpServers": {
    "apexgraph": {
      "command": "python",
      "args": ["-m", "packages.mcp.server", "--transport", "stdio"],
      "cwd": "/path/to/apexgp",
      "env": {
        "PYTHONPATH": "/path/to/apexgp",
        "DATABASE_URL": "sqlite:////path/to/apexgp/data/apexgraph.db"
      }
    }
  }
}
```

### Claude Code Setup

Configure via Claude Code CLI:

```bash
claude mcp add apexgraph -- python -m packages.mcp.server --transport stdio
```

### Claude Desktop Setup (`claude_desktop_config.json`)

On Windows (`%APPDATA%\Claude\claude_desktop_config.json`) or macOS (`~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "apexgraph": {
      "command": "python",
      "args": ["-m", "packages.mcp.server", "--transport", "stdio"],
      "cwd": "/path/to/apexgp"
    }
  }
}
```

### Cursor Setup (`.cursor/mcp.json`)

```json
{
  "mcpServers": {
    "apexgraph": {
      "command": "python",
      "args": ["-m", "packages.mcp.server", "--transport", "stdio"],
      "cwd": "/path/to/apexgp"
    }
  }
}
```

### Windsurf Setup

In your Windsurf Cascade MCP settings:

```json
{
  "mcpServers": {
    "apexgraph": {
      "command": "python",
      "args": ["-m", "packages.mcp.server", "--transport", "stdio"],
      "cwd": "/path/to/apexgp"
    }
  }
}
```

### Remote / Networked Agent Setup (SSE Transport)

To host ApexGraph on a central workstation or server:

```bash
apexgp --transport sse --host 127.0.0.1 --port 8001
```

Connect your agent to the SSE endpoint: `http://127.0.0.1:8001/sse`.

> **Security Warning:** The built-in SSE server does not provide authentication or encryption. Never bind to `0.0.0.0` or expose the SSE port over the public internet without placing it behind an authenticated, encrypted reverse proxy (such as Nginx, Caddy, or an SSH/WireGuard tunnel).

---

## 6. Complete MCP Tools Reference (16 Tools)

ApexGraph provides 16 specialized tools to give AI agents surgical structural awareness.

### Category 1: Workspace & Repository Management

| Tool Name | Parameters | Purpose | Example Use Case |
|---|---|---|---|
| `list_workspaces` | *(none)* | Lists all registered workspaces. | Identify available workspace containers. |
| `list_repositories` | `workspace_id` *(optional string)* | Lists repositories, indexing status, commit SHA, and language stats. | Find the repository UUID needed for queries. |
| `get_repository_status` | `repo_id` *(required string)* | Returns indexing state, node/edge counts, and last indexed timestamp. | Verify that a repository is indexed before querying. |
| `sync_repository` | `repo_id` *(required string)* | Performs incremental hash-based synchronization with disk changes. | Update the graph after editing code files (<100ms). |

### Category 2: Symbol Discovery & Search

| Tool Name | Parameters | Purpose | Example Use Case |
|---|---|---|---|
| `search_code` | `query` *(string)*, `repo_id` *(string)*, `node_type` *(optional)*, `file_path` *(optional)*, `limit` *(int, default 20)* | Hybrid lexical & structural search across symbols and files. | Locate functions or classes matching natural terms. |
| `find_symbol` | `name` *(string)*, `repo_id` *(string)*, `scope` *(optional string)* | Locates exact or qualified symbol definitions. | Jump to the declaration of `PaymentGateway`. |
| `get_symbol_details` | `node_id` *(required string)* | Retrieves full metadata, docstrings, signature, and verbatim code evidence. | Inspect a function's exact signature and source lines. |

### Category 3: Call & Dependency Traversal

| Tool Name | Parameters | Purpose | Example Use Case |
|---|---|---|---|
| `get_callers` | `node_id` *(string)*, `max_depth` *(int, 1-15, default 5)* | Traces all functions/methods calling this symbol up to depth N. | Identify all upstream code that invokes an API method. |
| `get_callees` | `node_id` *(string)*, `max_depth` *(int, 1-15, default 5)* | Traces all functions/methods called by this symbol up to depth N. | Understand downstream execution flow. |
| `get_dependencies` | `node_id` *(required string)* | Returns outgoing imports and structural dependencies of a file/symbol. | Audit module imports and third-party packages. |
| `trace_relationships` | `source_id` *(string)*, `target_id` *(string)*, `max_depth` *(int, 1-15, default 6)* | Computes the shortest graph path connecting two symbols. | Trace how component A interacts with database model B. |
| `get_graph_neighborhood` | `node_id` *(string)*, `radius` *(int, 1-3, default 1)*, `limit` *(int, default 100)* | Returns the immediate relational subgraph surrounding a node. | Provide local architectural context to an LLM. |

### Category 4: Change Impact & Test Discovery

| Tool Name | Parameters | Purpose | Example Use Case |
|---|---|---|---|
| `analyze_change_impact` | `target` *(symbol ID or file path)*, `repo_id` *(string)*, `max_depth` *(int, 1-15, default 5)* | Calculates complete blast radius: affected files, symbols, and test suites. | Evaluate risk before refactoring a core function. |
| `find_related_tests` | `node_id` *(required string)* | Discovers all unit and integration tests covering the target symbol. | Identify which tests to execute after a bugfix. |

### Category 5: Architectural Diagnostics

| Tool Name | Parameters | Purpose | Example Use Case |
|---|---|---|---|
| `detect_import_cycles` | `repo_id` *(required string)* | Analyzes the `IMPORTS` graph for circular dependencies. | Detect architectural anti-patterns and circular imports. |
| `god_nodes` | `repo_id` *(string)*, `limit` *(int, default 10)*, `scope` *(optional string)* | Identifies architectural bottlenecks with disproportionately high degree centrality. | Find high-risk god classes and over-coupled modules. |

---

## 7. Supported Languages Matrix (9 Languages)

ApexGraph uses safe, static Tree-sitter and AST parsers. It **never compiles or executes** target code.

| Language | Supported Extensions | Parser Engine | Extracted Structural Entities & Relations |
|---|---|---|---|
| **Python** | `.py` | Tree-sitter Python (iterative state machine) | Classes, methods, functions, decorators, imports, calls, docstrings, type hints. |
| **TypeScript** | `.ts`, `.tsx` | Tree-sitter TypeScript (with multi-root tsconfig) | Classes, interfaces, type aliases, functions, methods, imports, JSX components, calls. |
| **JavaScript** | `.js`, `.jsx`, `.mjs`, `.cjs` | Tree-sitter JavaScript | ES Modules, CommonJS `require`, classes, functions, calls. |
| **Go** | `.go` | Tree-sitter Go | Packages, structs, interfaces, methods, functions, imports, calls. |
| **Rust** | `.rs` | Tree-sitter Rust | Structs, enums, traits, `impl` blocks, functions, macros, `use` statements. |
| **Shell / Bash** | `.sh`, `.bash`, `.zsh` | Tree-sitter Bash | Shell functions, command invocations, scripts. |
| **SQL** | `.sql` | `sqlglot` | Table schemas, views, stored procedures, joins. |
| **JSON** | `.json` | Tree-sitter JSON | Structured key-value hierarchies, configuration objects. |
| **YAML** | `.yaml`, `.yml` | `pyyaml` AST | Service configurations, pipeline blocks, mapping hierarchies. |

---

## 8. Graph Data Model & Intelligence Engine

### Node Types (9)
`FILE`, `MODULE`, `CLASS`, `INTERFACE`, `FUNCTION`, `METHOD`, `VARIABLE`, `TYPE`, `TEST`.

### Edge Types (8)
`DEFINES`, `CONTAINS`, `IMPORTS`, `CALLS`, `EXTENDS`, `IMPLEMENTS`, `REFERENCES`, `TESTS`.

### Verifiable Provenance
Every node and edge records:
* `file_path`: Relative source file path.
* `start_line` / `end_line`: 1-indexed source code line boundaries.
* `confidence`: Float metric (0.0 to 1.0) reflecting AST extraction certainty.
* `snippet`: Verbatim source code definition excerpt.

### Query Engine Innovations
* **Compound Token Splitting:** Normalizes camelCase, snake_case, and kebab-case identifiers into atomic searchable tokens.
* **Inflection Stem Matching (`_terms_match`):** Resolves morphological variations (e.g., `registration` matches `registry` and `register`).
* **Contextual Structural Boost:** Applies a +0.14 score boost to `CLASS` and `INTERFACE` nodes when queries include architectural keywords (`service`, `manager`, `engine`, `registry`, `handler`, `factory`).
* **Deterministic Tie-Breaking:** Breaks score ties in favor of structural containers over helper methods.

---

## 9. Web Console & REST API Reference

The FastAPI service exposes both the interactive HTML5 Canvas dashboard and a complete REST API.

* **Web Dashboard:** `http://127.0.0.1:8000/`
* **Interactive OpenAPI / Swagger Documentation:** `http://127.0.0.1:8000/docs`

### Key REST Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/health` | Health check verifying database connectivity and service state. |
| `POST` | `/api/workspaces` | Creates a new workspace container (`{"name": "string"}`). |
| `GET` | `/api/workspaces` | Lists all workspaces. |
| `POST` | `/api/repositories` | Registers a repository (`{"workspace_id": "...", "name": "...", "source_type": "local", "source_path": "..."}`). |
| `GET` | `/api/repositories` | Lists registered repositories. |
| `POST` | `/api/repositories/{id}/index` | Triggers full AST indexing on the target repository. |
| `DELETE` | `/api/repositories/{id}` | Deletes a repository and removes its graph nodes and edges. |
| `GET` | `/api/repositories/{id}/search` | Queries symbols (`?q=query&type=CLASS&limit=50`). |
| `GET` | `/api/repositories/{id}/impact` | Calculates change impact blast radius (`?target=node_id_or_file&depth=5`). |
| `GET` | `/api/nodes/{node_id}/details` | Retrieves full symbol details, signature, and evidence. |
| `GET` | `/api/nodes/{node_id}/callers` | Traces callers up to specified depth (`?depth=5`). |
| `GET` | `/api/nodes/{node_id}/callees` | Traces callees up to specified depth (`?depth=5`). |
| `GET` | `/api/nodes/{node_id}/dependencies` | Retrieves outgoing imports and dependencies. |
| `GET` | `/api/nodes/{node_id}/tests` | Finds related unit tests asserting the symbol. |
| `GET` | `/api/repositories/{id}/trace` | Traces relational path between two nodes (`?source=...&target=...`). |

---

## 10. Configuration & Database Backends

Configuration is managed via environment variables (loaded from `.env` if present):

| Environment Variable | Default Value | Description |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./data/apexgraph.db` | SQLAlchemy connection string. |
| `APEX_HOST` | `127.0.0.1` | Host address for REST API and Web Dashboard. |
| `APEX_PORT` | `8000` | Port for REST API and Web Dashboard. |
| `LOG_LEVEL` | `INFO` | Application log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`). |

### Database Switching: SQLite to PostgreSQL

ApexGraph runs out-of-the-box on SQLite with Write-Ahead Logging (`WAL`) enabled. For high-concurrency or multi-tenant deployments, configure PostgreSQL:

```bash
# In your .env file:
DATABASE_URL=postgresql://apex_user:secure_password@localhost:5432/apexgraph_db
```

Apply migrations to initialize PostgreSQL schema:
```bash
python -m packages.core.migrations
```

---

## 11. Containerized Deployment (Docker Compose)

ApexGraph includes a production-grade Docker configuration pairing the API, MCP server, and a dedicated PostgreSQL 16 database.

### Start Services

```bash
docker compose up -d --build
```

### Services Started:
* `apexgraph-api`: Web dashboard and REST API on port `8000`.
* `apexgraph-mcp`: MCP server running over SSE on port `8001`.
* `apexgraph-db`: PostgreSQL 16 database with persistent health checks and volume storage.

### Stop Services

```bash
docker compose down
```

---

## 12. Security & Enterprise Posture

ApexGraph is designed to run safely across proprietary and enterprise codebases:

* **Static Parsing Without Execution:** Code is analyzed strictly through Tree-sitter concrete syntax trees and `sqlglot`. Target code is never loaded, imported, compiled, or executed.
* **Path Traversal Defense:** [packages/core/security.py](file:///c:/Users/pkumar2/OneDrive%20-%20Data%20Axle/Documents/AI_INIT/RepopeekV3/packages/core/security.py) enforces strict directory jail validation on all local repository paths, preventing directory traversal escapes (`../`).
* **SSRF Protection:** Remote repository URLs are validated against private, link-local, loopback, and cloud metadata IP ranges (`169.254.169.254`).
* **Secret Redaction:** Code evidence snippets pass through regex scrubbing filters before database persistence, redacting API keys, bearer tokens, passwords, and private certificates.

---

## 13. Testing & Benchmarks

### Running the Test Suite

ApexGraph includes comprehensive unit, integration, and parser regression test suites:

```bash
# Run all unit and integration tests (251 verified passing tests)
pytest -v tests/unit/ tests/integration/

# Run Playwright end-to-end browser tests
pytest -v tests/e2e/test_browser_flows.py
```

### Running Native Benchmarks

Execute the native benchmark harness to evaluate query latency, retrieval precision, and change impact accuracy:

```bash
python benchmarks/native/runner.py
```

**Measured Benchmark Results (Frozen Suite):**
* **Overall Benchmark Accuracy:** 10/10 (100.0%)
* **Retrieval Hit@1:** 100.0% (N=6 positive retrieval tasks)
* **Retrieval Recall@3:** 100.0%
* **Mean Reciprocal Rank (MRR):** 1.000
* **Distractor Rejection Accuracy:** 100.0% (0 distractors on negative queries)
* **Change-Impact File Recall:** 100.0%
* **Linked Test Discovery Recall:** 100.0%
* **Mean Query Latency:** Sub-10ms across all structural graph operations.

---

## 14. Troubleshooting & FAQ

### 1. `ModuleNotFoundError: No module named 'tree_sitter_go'`
* **Cause:** The command was executed in the global Python environment instead of the project virtual environment.
* **Solution:** Activate your virtual environment before running commands (`.\.venv\Scripts\Activate.ps1` on Windows or `source .venv/bin/activate` on Linux/macOS).

### 2. MCP queries return empty results or "Repository not found"
* **Cause:** The repository has not been indexed yet.
* **Solution:** The MCP server reads from the indexed database. Start the web console (`python -m apps.api.main`), register your repository, and click **Index**. Once indexed, MCP queries will return complete structural intelligence.

### 3. Windows PowerShell: `Activate.ps1 cannot be loaded because running scripts is disabled`
* **Cause:** Windows PowerShell execution policy restrictions.
* **Solution:** Run `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser` in PowerShell, then activate `.venv`.

---

## 15. Contributing & License

### Contributor Setup

1. Fork the repository on GitHub: [https://github.com/pratiyush-DA/apexgp.git](https://github.com/pratiyush-DA/apexgp.git).
2. Clone your fork locally and install in editable mode with development dependencies:
   ```bash
   pip install -e ".[dev]"
   ```
3. Run test suites prior to submitting pull requests:
   ```bash
   pytest tests/unit/ tests/integration/
   ```

### License

ApexGraph is released under the **MIT License**. See [LICENSE](LICENSE) for details.
