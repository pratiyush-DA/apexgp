# Ponytail Simplicity Principles (YAGNI & Zero Bloat)

Adopt the following simplicity constraints across the entire codebase:

1. **YAGNI (You Aren't Gonna Need It):** Do not build speculative features, premature abstractions, or unused configuration options.
2. **Minimal Layering:** Favor direct, straightforward implementations over excessive wrappers, micro-factories, or indirection.
3. **Standard Library & Native Platform First:** Leverage standard library and native language features before adding external dependencies.
4. **Delete Dead Code:** Remove unused functions, dead paths, and obsolete boilerplate immediately.
5. **Clarity Over Cleverness:** Write transparent, readable code with explicit types and descriptive names.
6. **No Silent Swallowing:** Never use empty catch blocks, broad unhandled exceptions, or fake fallback data where real errors should be surfaced.
