# ApexGraph Security & Threat Model

## 1. Threat Model & Security Posture
Code intelligence platforms ingest untrusted source code, third-party repositories, and external URLs. The primary security objective of ApexGraph is to ensure that ingested code is strictly treated as passive data, never executed, and isolated from the host environment.

---

## 2. Security Controls & Defenses

### 2.1 Zero Code Execution Policy
* Under no circumstances does the indexing engine execute repository scripts, build systems (`npm install`, `pip install`, `make`), tests, or module initializers (`__init__.py`, `index.js`).
* Code parsing is purely performed through deterministic Tree-sitter AST parsers running in-memory.

### 2.2 Path Traversal & Filesystem Isolation
* All local repository paths must be validated using canonicalized realpaths (`os.path.realpath`).
* Relative paths within indexed repositories are strictly checked to ensure they do not escape the repository boundary:
  ```python
  def is_safe_path(base_dir: str, target_path: str) -> bool:
      base = os.path.realpath(base_dir)
      target = os.path.realpath(os.path.join(base, target_path))
      return os.path.commonpath([base]) == os.path.commonpath([base, target])
  ```
* Attempted traversal sequences (`../`, `..\`, null bytes) trigger immediate rejection.

### 2.3 SSRF (Server-Side Request Forgery) Protection
For remote repository cloning:
1. **Scheme Whitelist:** Strictly `https://` only. `http://`, `file://`, `ftp://`, `ssh://`, `gopher://` are rejected.
2. **DNS & IP Validation:** Destination hostnames are resolved before connection. Connections to the following IP ranges are blocked:
   * Loopback (`127.0.0.0/8`, `::1`)
   * Private RFC 1918 networks (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`)
   * Link-local / Cloud metadata endpoints (`169.254.169.254`, `fe80::/10`)
3. **Redirect Blocking:** Automatic redirects across protocol or into private IP spaces are rejected.

### 2.4 Command Injection Prevention
* All Git operations (e.g. `git clone`, `git log`) avoid shell execution (`shell=False`) and invoke executable lists directly with strict argument arrays:
  ```python
  subprocess.run(["git", "clone", "--depth", "1", safe_url, target_dir], shell=False, check=True)
  ```
* Repository URLs and revision identifiers are sanitized with strict alphanumeric / URL regexes.

### 2.5 MCP Agent Guardrails
* MCP tools expose structural code facts only.
* No arbitrary shell execution tools are exposed through the MCP catalog.
* File content retrieval through MCP is strictly bound to registered repository directories.
* All comments and text inside files are treated strictly as untrusted data, never forwarded as system instructions to the host AI agent.

### 2.6 Secret Redaction & Log Hygiene
* Regex sanitizers mask API keys, bearer tokens, AWS credentials, and private keys in:
  * Application logs
  * Error messages
  * MCP tool returns
  * Indexing diagnostics

### 2.7 Resource Consumption & Quotas
* **File Size Cap:** Maximum 2 MB per source file. Larger files are skipped with a warning.
* **Repository Size Cap:** Maximum 500 MB per repository.
* **Graph Traversal Limits:** Recursive BFS/DFS queries enforce strict `max_depth` (default 5, maximum 15) and node counts (default 500) to prevent denial-of-service and memory exhaustion.
* **Indexing Timeouts:** Configurable hard timeout (e.g., 180 seconds) per indexing job.
