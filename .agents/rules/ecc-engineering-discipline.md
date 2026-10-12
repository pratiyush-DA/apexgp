# ECC Engineering Discipline & Quality Bar

This project adheres to the engineering lifecycle and quality discipline established by ECC (Everything Claude Code / Everything Gemini Code):

## Core Cycle: Research -> Plan -> Test -> Implement -> Review -> Verify
1. **Research Before Implementation:** Inspect environment, existing code, dependencies, and requirements before writing code. Ground choices in official documentation.
2. **Plan Changes:** Formulate explicit acceptance criteria and concise step-by-step plans before making non-trivial modifications.
3. **Automated Verification:** Write tests for new capabilities. Verify behavior with automated test runners and Playwright browser tests.
4. **Adversarial Review:** Review diffs for regressions, performance issues, and security vulnerabilities (SSRF, path traversal, injection, secret leaks).
5. **Independent Verification:** Never claim tests or tasks pass without actually executing them and observing success.
6. **Preserve Reusable Knowledge:** Record architectural decisions, failure recovery procedures, and project constraints in documentation.
