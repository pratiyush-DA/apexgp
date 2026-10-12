# ApexGraph Development Workflow Guide

## 1. Engineering Principles
This repository operates under two unified engineering standards:
* **ECC Discipline:** Research before implementation -> Plan before coding -> Test-driven implementation -> Adversarial security & regression review -> Verification.
* **Ponytail Simplicity:** YAGNI (You Aren't Gonna Need It), native standard library capabilities before third-party bloat, minimal indirection, and explicit error handling.

---

## 2. Feature Development Loop

For every new capability, parser, API endpoint, or MCP tool:

```
[1. Spec & Research]  -->  [2. Acceptance Plan]  -->  [3. Test Fixture]
         |                                                   |
         v                                                   v
[6. Verify & Browser QA] <-- [5. Adversarial Review] <-- [4. Focused Impl]
```

1. **Spec & Research:** Inspect existing AST structures, database schemas, and protocol requirements.
2. **Acceptance Plan:** Write explicit criteria covering happy path, syntax error handling, and malformed inputs.
3. **Test Fixture:** Create miniature polyglot source code fixtures with known ground truth relationships.
4. **Focused Implementation:** Implement the minimal code satisfying requirements without speculative extras.
5. **Adversarial Review:** Check for:
   * Polyglot node ID collisions.
   * Path traversal attempts (`../../etc/passwd` or `..\..\Windows`).
   * Infinite recursion in cyclic graph dependencies.
   * Unhandled AST node types or syntax errors.
6. **Automated Verification:** Run `pytest`, integration test suites, and Playwright browser verifications.

---

## 3. Environment & Local Testing

* **Backend Test Runner:**
  ```powershell
  .\.venv\Scripts\pytest -v tests/
  ```
* **Linting & Type Checking:**
  Ensure strict Pydantic v2 schemas and typed function signatures.
* **Playwright Browser Testing:**
  Automated headless/headed verification of search, graph visualization, and dashboard panels.
