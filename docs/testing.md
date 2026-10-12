# ApexGraph Testing & Quality Assurance Strategy

## 1. Testing Philosophy
In alignment with ECC and Ponytail engineering disciplines:
* Every core capability is accompanied by automated tests.
* Tests run deterministically against local fixture repositories with known structural relationships.
* Browser interactions, search responsiveness, and console errors are verified end-to-end with Playwright.
* No mock data in vertical slice validation; tests parse real files into real graph tables.

---

## 2. Test Suite Architecture

### 2.1 Unit Tests (`tests/unit/`)
* `test_parsers.py`: Verifies AST parsing of Python, TypeScript, and JavaScript code. Tests functions, classes, interfaces, types, decorators, method signatures, line ranges, and docstrings.
* `test_resolvers.py`: Verifies cross-file import resolution, relative path normalization, and prevention of polyglot node ID collisions.
* `test_graph_traversal.py`: Verifies recursive callers, callees, dependency chains, shortest-path algorithms, and cycle termination.
* `test_security.py`: Verifies path traversal prevention, dangerous scheme rejection, and SSRF address filtering.

### 2.2 Integration Tests (`tests/integration/`)
* `test_api_endpoints.py`: Tests FastAPI REST routes (`/api/workspaces`, `/api/repositories`, `/api/search`, `/api/impact`, `/api/graph`).
* `test_mcp_server.py`: Directly invokes MCP JSON-RPC protocol tools and validates schema adherence.
* `test_indexing_lifecycle.py`: Tests indexing, incremental re-indexing on file change, error reporting on syntax anomalies, and repository deletion.

### 2.3 Browser E2E Tests (`tests/e2e/`)
* `test_dashboard_flows.py`: Playwright automated tests that load the dashboard, select repositories, perform symbol search, click graph nodes, and verify zero uncaught console errors.

---

## 3. Running Automated Tests

Run the full Python test suite:
```powershell
.\.venv\Scripts\pytest -v --tb=short tests/
```

Run focused unit tests:
```powershell
.\.venv\Scripts\pytest -v tests/unit/
```

Run Playwright E2E browser tests:
```powershell
.\.venv\Scripts\pytest -v tests/e2e/
```
