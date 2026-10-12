# ApexGraph — Independent Code Knowledge Graph & Intelligence Platform

ApexGraph is an independent, production-minded developer intelligence platform that parses polyglot codebases into a deterministic, typed property graph. It extracts structural symbols, calls, imports, tests, and change-impact blast radii—exposing code intelligence through Model Context Protocol (MCP) to AI coding assistants and a web dashboard.

Inspired by publicly documented principles from **Graphify** (deterministic AST graph construction) and **REA** (evidence graphs with provenance tracking), ApexGraph runs standalone locally without external cloud dependencies.

---

## 1. Key Capabilities

* **Multi-Language AST Parsing:** Deterministic Tree-sitter extraction for Python, TypeScript, and JavaScript with qualified namespacing to prevent cross-language collisions.
* **Evidence Graphs & Provenance:** Every asserted node and edge includes source file path, exact line numbers, confidence metrics, and verbatim code evidence.
* **Change-Impact Blast-Radius Engine:** Traverses upstream callers and dependents from modified symbols/files to calculate blast radii and discover unit tests.
* **13 MCP Tools:** Exposes the Model Context Protocol over Stdio and Streamable HTTP for Antigravity, Claude Code, Cursor, and Windsurf.
* **Web Console:** Interactive Canvas force-directed graph explorer, live symbol search, blast-radius calculator, and indexing diagnostics.
* **Enterprise Security Posture:** Zero code execution during indexing, path traversal prevention, SSRF IP blocking for remote Git URLs, and regex secret redaction.

---

## 2. Architecture Overview

```
                      +-----------------------------------+
                      |      AI Coding Agents / IDE       |
                      | (Antigravity, Claude Code, Cursor)|
                      +-----------------+-----------------+
                                        | (MCP Protocol)
+-------------------+                   v
|   Web Dashboard   | ------> +-------------------+
|  (Interactive UI) | (HTTP)  |   FastAPI Service |
+-------------------+         |   (Port 8000)     |
                              +---------+---------+
                                        |
                 +----------------------+----------------------+
                 |                                             |
                 v                                             v
     +-----------------------+                     +-----------------------+
     | Tree-sitter AST Core  |                     | SQLAlchemy 2.0 Engine |
     | (Python, TS, JS)      |                     | (SQLite / PostgreSQL) |
     +-----------------------+                     +-----------------------+
```

---

## 3. Quickstart & Local Installation

### Prerequisites
* Python 3.10+
* Git 2.40+

### Step 1: Clone and Create Virtual Environment
```powershell
git clone <repo-url> RepopeekV3
cd RepopeekV3

# Create and activate virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### Step 2: Install Dependencies
```powershell
pip install -r requirements.txt
```

### Step 3: Configure Environment
```powershell
Copy-Item .env.example .env
```
*(Default uses zero-setup SQLite database file at `./data/apexgraph.db`)*

### Step 4: Apply Database Migrations
```powershell
python -m packages.core.migrations
```

### Step 5: Start API & Web Dashboard
```powershell
python -m apps.api.main
```
Open your browser to: **`http://127.0.0.1:8000`**

---

## 4. Connecting the MCP Server to AI Coding Agents

ApexGraph exposes 13 intelligence tools over standard Stdio MCP.

### Google Antigravity Setup (`.agents/mcp_config.json`)
```json
{
  "mcpServers": {
    "apexgraph": {
      "command": "python",
      "args": ["-m", "packages.mcp.server", "--transport", "stdio"],
      "cwd": "C:/Users/pkumar2/OneDrive - Data Axle/Documents/AI_INIT/RepopeekV3",
      "env": {
        "PYTHONPATH": "C:/Users/pkumar2/OneDrive - Data Axle/Documents/AI_INIT/RepopeekV3"
      }
    }
  }
}
```

### Claude Code Setup (`claude_desktop_config.json`)
```json
{
  "mcpServers": {
    "apexgraph": {
      "command": "python",
      "args": ["-m", "packages.mcp.server", "--transport", "stdio"],
      "cwd": "/path/to/RepopeekV3"
    }
  }
}
```

Verify MCP tools directly via self-test:
```powershell
python -m packages.mcp.server --test
```

---

## 5. Running Automated Tests

Run the complete test suite:
```powershell
# Run all unit and integration tests
pytest -v tests/unit/ tests/integration/

# Run Playwright E2E browser tests
pytest -v tests/e2e/test_browser_flows.py
```

---

## 6. Containerized Deployment (Docker Compose)

For multi-tenant environments with dedicated PostgreSQL:
```bash
docker compose up -d --build
```
Stops services:
```bash
docker compose down
```

---

## 7. Documentation Index

* [Architecture & Data Model](docs/architecture.md)
* [Reference Research & Comparative Analysis](docs/reference-research.md)
* [Development Workflow & Engineering Discipline](docs/development-workflow.md)
* [MCP Setup & Client Configuration](docs/mcp-setup.md)
* [Security Posture & Threat Model](docs/security.md)
* [Testing Strategy & QA Plan](docs/testing.md)
* [Deployment & Operations Guide](docs/deployment.md)
* [Troubleshooting & Diagnostics Guide](docs/troubleshooting.md)
