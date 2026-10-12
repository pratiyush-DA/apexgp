# ApexGraph Architecture Specification

## 1. System Mission
**ApexGraph** is an independent, high-performance code intelligence platform and graph engine. It parses polyglot repositories into a typed property graph with complete provenance, provides change-impact blast radius calculations, executes fast sub-second structural searches, and exposes an MCP server for AI coding agents alongside an interactive web console.

---

## 2. Monorepo Organization

```
RepopeekV3/
├── apps/
│   ├── api/             # FastAPI REST service & query endpoints
│   ├── web/             # Modern vanilla/interactive frontend dashboard
│   └── worker/          # Incremental and batch indexing background engine
├── packages/
│   ├── core/            # AST parsers, Graph models, and change impact engine
│   └── mcp/             # Stdio and Streamable HTTP Model Context Protocol server
├── tests/
│   ├── unit/            # Unit tests for parsers, resolvers, graph queries
│   ├── integration/     # End-to-end API and MCP integration tests
│   └── fixtures/        # Sample repositories with verified relationships
├── docs/                # Architecture, security, setup, and research documentation
├── .agents/             # Antigravity rules and agent skills
├── docker-compose.yml   # Multi-service container orchestration
└── .env.example         # Documented configuration template
```

---

## 3. Data Model & Graph Schema

The graph storage model is structured around relational entities designed for PostgreSQL with dual SQLite compatibility:

### 3.1 Entities

#### `workspaces`
* `id` (UUID PK): Unique workspace identifier.
* `name` (VARCHAR): Workspace display name.
* `created_at` (TIMESTAMP): Creation timestamp.

#### `repositories`
* `id` (UUID PK): Unique repository identifier.
* `workspace_id` (UUID FK): References workspaces.
* `name` (VARCHAR): Short name.
* `source_type` (ENUM: `local`, `git`): Ingestion origin.
* `source_path` (TEXT): File path or validated Git URL.
* `current_revision` (VARCHAR): Git commit SHA or local hash.
* `indexing_status` (ENUM: `pending`, `indexing`, `ready`, `failed`): Status.
* `last_indexed_at` (TIMESTAMP): Completion timestamp.
* `error_message` (TEXT): Failure diagnostic.
* `stats` (JSON): Language distribution, file counts, node counts.

#### `graph_nodes`
* `id` (VARCHAR PK): Namespaced deterministic ID (`repo_id:runtime:file_path:symbol_name`).
* `repository_id` (UUID FK): Scoped repository.
* `node_type` (VARCHAR): `FILE`, `DIRECTORY`, `MODULE`, `CLASS`, `INTERFACE`, `FUNCTION`, `METHOD`, `VARIABLE`, `TYPE`, `TEST`.
* `name` (VARCHAR): Unqualified identifier name.
* `qualified_name` (VARCHAR): Full namespace/module path.
* `file_path` (VARCHAR): Relative file path within repository.
* `start_line` (INT): 1-indexed beginning line.
* `end_line` (INT): 1-indexed ending line.
* `start_col` (INT): 0-indexed column.
* `end_col` (INT): 0-indexed column.
* `language` (VARCHAR): `python`, `typescript`, `javascript`, etc.
* `signature` (TEXT): Function/method signature or type declaration.
* `docstring` (TEXT): Extracted comments/docstring summary.
* `metadata` (JSON): Language-specific attributes (async, exported, decorators).

#### `graph_edges`
* `id` (VARCHAR PK): Deterministic edge hash (`source_id:edge_type:target_id`).
* `repository_id` (UUID FK): Scoped repository.
* `source_id` (VARCHAR FK): Origin node ID.
* `target_id` (VARCHAR FK): Destination node ID.
* `edge_type` (VARCHAR): `DEFINES`, `CONTAINS`, `IMPORTS`, `CALLS`, `EXTENDS`, `IMPLEMENTS`, `REFERENCES`, `TESTS`.
* `confidence` (FLOAT): 1.0 (verified AST fact), 0.75 (heuristic resolution).
* `file_path` (VARCHAR): File where relationship occurs.
* `line_number` (INT): Location of relationship.
* `evidence` (TEXT): Exact code snippet or import statement demonstrating relationship.

---

## 4. AST Parser Pipeline

1. **Language Detection & Filtering:** Identifies languages by extension (`.py`, `.ts`, `.tsx`, `.js`, `.jsx`). Ignores vendor directories (`node_modules`, `venv`, `.git`, `dist`, `__pycache__`).
2. **Deterministic Tree-Sitter Traversal:**
   * Python parser extracts: modules, classes, functions, methods, calls, imports (`import x`, `from x import y`), decorators, tests (`test_*` or `pytest`).
   * TypeScript/JavaScript parser extracts: classes, interfaces, types, functions, methods, exports, imports (`import { x } from './y'`), calls, tests (`describe`, `it`, `test`).
3. **Cross-File Symbol Resolution:**
   * Resolves relative imports (`./utils`, `../core`) to canonical file paths.
   * Links function calls to matching definitions within the file or imported modules.
   * Uses namespaced IDs to prevent polyglot collisions (e.g. `npm:react` vs `py_builtin:iter`).

---

## 5. Change-Impact & Blast Radius Engine

Given a target symbol or modified file:
1. **Reverse Traversal (Callers & Dependents):** Traverses incoming `CALLS`, `IMPORTS`, and `REFERENCES` edges recursively up to a configurable max depth (default: 5).
2. **Affected Files & Symbols Aggregation:** Identifies all direct and transitive consumers.
3. **Test Discovery:** Traverses outward to find all nodes with `TESTS` or node type `TEST` that cover the affected graph slice.
4. **Evidence Generation:** Returns step-by-step path traces explaining why each file is impacted.

---

## 6. MCP Protocol Interface

Exposes standard Model Context Protocol tools over Stdio and Streamable HTTP (SSE):
* `list_workspaces`
* `list_repositories`
* `get_repository_status`
* `search_code`
* `find_symbol`
* `get_symbol_details`
* `get_callers`
* `get_callees`
* `get_dependencies`
* `trace_relationships`
* `analyze_change_impact`
* `find_related_tests`
* `get_graph_neighborhood`

---

## 7. Storage Engine Strategy

ApexGraph utilizes SQLAlchemy 2.0 with clean ANSI SQL graph queries and recursive CTEs compatible with both:
* **PostgreSQL:** For multi-tenant production deployments with connection pooling.
* **SQLite:** For zero-setup, self-contained local developer execution without requiring external database services or running Docker daemons.
