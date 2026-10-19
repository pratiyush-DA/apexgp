# Dependency & License Audit: RepoPeekV3 (APEXGP) × Graphify

## 1. Intellectual Property & Licensing Analysis

### 1.1 Project Licenses
- **RepoPeekV3 (APEXGP):** MIT License (Copyright 2026 ApexGraph Team).
- **Graphify Reference Repository:** Apache License 2.0 (Copyright 2026 Safi Shamsi and the Graphify contributors), with historical portions under MIT License retained in `LICENSE-MIT`.
- **License Compatibility:** **Fully Compatible.** Under US and international copyright law, Apache 2.0-licensed code can be incorporated into or distributed alongside MIT-licensed software, provided the specific conditions of the Apache 2.0 license are satisfied.

### 1.2 Mandatory Compliance Requirements
To maintain complete legal compliance when absorbing Graphify capabilities into RepoPeekV3:
1. **Preserve Copyright & Attribution Notices:** Retain the original copyright notices from Graphify in all ported files.
2. **Include Apache 2.0 License Text:** Add `LICENSE-APACHE` under `Workflow_Documentation/licenses/` or root.
3. **Retain Upstream NOTICE File:** Include Graphify's `NOTICE` file in RepoPeekV3's documentation and distribution packages:
   ```
   Graphify
   Copyright 2026 Safi Shamsi and the Graphify contributors.
   This product includes software developed under the Apache License, Version 2.0.
   ```
4. **Document Changes in Ported Files:** Any file adapted directly from Graphify must carry a header comment declaring modifications (Section 4.b of Apache 2.0):
   ```python
   # Portions adapted from Graphify (https://github.com/Graphify-Labs/graphify)
   # Licensed under the Apache License, Version 2.0.
   # Modified for RepoPeekV3 relational property graph integration.
   ```
5. **No Trademark Claims:** The project name remains RepoPeekV3 (APEXGP). The name "Graphify" is used solely for upstream attribution and compatibility labeling.

---

## 2. Dependency Inventory & License Matrix

### 2.1 Core Runtime Dependencies

| Package | Purpose in Unified System | Minimum Version | Upstream License | Wheel Availability (Win/Mac/Linux) |
| :--- | :--- | :--- | :--- | :--- |
| `fastapi` | REST API service | `>=0.115.0` | MIT | Pure Python (All platforms) |
| `uvicorn` | ASGI server | `>=0.34.0` | BSD-3-Clause | Prebuilt wheels |
| `sqlalchemy` | ORM & relational graph storage | `>=2.0.36` | MIT | Prebuilt wheels |
| `pydantic` | Data validation & schemas | `>=2.10.0` | MIT | Prebuilt wheels |
| `tree-sitter` | Core AST parsing engine | `>=0.25.0,<0.26` | MIT | Prebuilt ABI 15 wheels |
| `tree-sitter-python` | Python AST grammar | `>=0.25.0` | MIT | Prebuilt wheels |
| `tree-sitter-javascript` | JavaScript AST grammar | `>=0.25.0` | MIT | Prebuilt wheels |
| `tree-sitter-typescript` | TypeScript AST grammar | `>=0.23.0,<0.25` | MIT | Prebuilt wheels |
| `networkx` | Graph algorithms, cycles, Louvain | `>=3.4` | BSD-3-Clause | Pure Python (All platforms) |
| `numpy` | Numerical arrays for clustering | `>=1.21` | BSD-3-Clause | Prebuilt wheels |
| `rapidfuzz` | High-speed fuzzy string search | `>=3.0` | MIT | Prebuilt wheels |
| `mcp` | Model Context Protocol SDK | `>=2.3.0` | MIT | Pure Python (All platforms) |

### 2.2 Phase 5 Language Expansion Grammars (Tier 1)

| Package | Language | Minimum Version | License | Wheel Availability |
| :--- | :--- | :--- | :--- | :--- |
| `tree-sitter-rust` | Rust | `>=0.23.0,<0.25` | MIT | Prebuilt wheels (All platforms) |
| `tree-sitter-go` | Go | `>=0.23.0,<0.26` | MIT | Prebuilt wheels (All platforms) |
| `tree-sitter-c-sharp` | C# | `>=0.23.0,<0.25` | MIT | Prebuilt wheels (All platforms) |
| `tree-sitter-java` | Java | `>=0.23.0,<0.25` | MIT | Prebuilt wheels (All platforms) |
| `tree-sitter-cpp` | C++ | `>=0.23.0,<0.25` | MIT | Prebuilt wheels (All platforms) |
| `tree-sitter-c` | C | `>=0.23.0,<0.25` | MIT | Prebuilt wheels (All platforms) |

### 2.3 Optional Plug-in Dependencies

| Package Extra | Dependencies | Purpose | License | Rationale for Keeping Optional |
| :--- | :--- | :--- | :--- | :--- |
| `clustering` | `graspologic-native>=1.3.1,<2` | Native Rust Leiden clustering | MIT / Apache 2.0 | Pure Python Louvain fallback is built-in; Leiden is an optional speed boost. |
| `docs` | `pypdf>=6.16.1`, `markdownify` | Ingestion of PDF documentation | BSD-3-Clause / MIT | Not all code intelligence users need PDF ingestion. |
| `sql` | `tree-sitter-sql` | SQL schema extraction | MIT | Only needed for projects with `.sql` schemas. |
| `watch` | `watchdog>=4.0` | Native OS filesystem watcher | Apache 2.0 | Standard library polling provides zero-dependency fallback. |

### 2.4 Excluded Upstream Dependencies (Anti-Bloat Policy)
The following upstream dependencies are **deliberately excluded** from RepoPeekV3 in accordance with the Ponytail anti-bloat directives:
- `faster-whisper`, `yt-dlp`: Audio/video transcription adds multiple gigabytes of weights with zero relevance to code graphs.
- `openai`, `anthropic`, `boto3`: Third-party cloud LLM API wrappers. RepoPeek strictly performs local, private, deterministic AST parsing.
- `tree-sitter-dm`: Lacks pre-built Linux and macOS binary wheels (requires local C++ compilation).

---

## 3. ABI & Version Compatibility Constraints

### 3.1 Tree-Sitter Runtime ABI 15 Pinned Floor
- **Hazard Identified in Graphify:** Tree-sitter `0.23` and `0.24` cap out at ABI 14 (`LANGUAGE_VERSION=14`). Modern grammars (`tree-sitter-javascript 0.25.0`, `tree-sitter-powershell 0.26.x`) emit ABI 15.
- If a package manager resolves `tree-sitter 0.23` with `tree-sitter-javascript 0.25`, every parse fails catastrophically with `Incompatible Language version 15`.
- **Enforced Floor:** RepoPeek pins `tree-sitter>=0.25.0,<0.26` in `pyproject.toml`, guaranteeing that all loaded grammars share ABI 15.

---

## 4. Security & Hardening Audit

### 4.1 Path Traversal Defense
- All user-supplied repository and file paths pass through `packages/core/security.py`:
  - Normalized via `os.path.realpath`.
  - Boundary verified using `os.path.commonpath` to ensure target resides strictly within registered workspace directories.
  - Rejection of relative escapes (`../`, `..\`) and directory traversal vectors.

### 4.2 SSRF & Remote URL Protection
- `validate_git_url()` strictly whitelists `https://` schemes.
- Rejects local file protocols (`file://`, `gopher://`, `ftp://`).
- Performs DNS pre-resolution checking against loopback and private RFC 1918 subnets (`127.0.0.0/8`, `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`) and cloud metadata services (`169.254.169.254`).

### 4.3 Zero Code Execution Invariant
- Repositories under inspection are treated strictly as untrusted data.
- Under no circumstances does RepoPeek execute build scripts (`setup.py`, `package.json` scripts, `Makefile`, `CMakeLists.txt`) or import target modules dynamically. Parsing is 100% static AST traversal.

### 4.4 Secret Redaction & Token Hygiene
- The MCP server and REST endpoints sanitize sensitive credentials using regex patterns for GitHub Personal Access Tokens, AWS keys, Bearer tokens, and database passwords before emitting graph node docstrings or debug logs.
