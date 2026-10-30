# Contributing Guidelines

Thank you for contributing to the **Autonomous Invoice AI Agent** project. To maintain enterprise software quality and architectural integrity, all contributions must adhere to the engineering standards detailed below.

---

## 1. Core Engineering Principles

1. **Concepts > Code:** Understand the underlying system architecture and data flows before implementing changes. Review [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and existing [ADRs](docs/adr/).
2. **Strict Typing:** All Python code must include complete type annotations (`from __future__ import annotations`, explicit return types, TypedDict schemas for states).
3. **Pure Routing Functions:** All LangGraph conditional edges must be implemented as pure, deterministic functions without network or filesystem I/O.
4. **Deterministic Testing (Strict TDD):** Write unit tests before or alongside implementation. Unit tests must execute 100% offline without hitting live LLM or OCR endpoints.

---

## 2. Development Setup

We use [`uv`](https://docs.astral.sh/uv/) for fast, deterministic dependency resolution.

```bash
# 1. Clone your fork
git clone https://github.com/<your-username>/invoice-ai-agent.git
cd invoice-ai-agent

# 2. Synchronize virtual environment
uv sync

# 3. Setup local environment
cp .env.example .env
# Edit .env with your personal GEMINI_API_KEY for manual end-to-end testing
```

---

## 3. Testing & Verification

Before submitting any code, verify that all test suites pass:

```bash
# Run unit tests
uv run pytest

# Run tests with verbose output and coverage
uv run pytest -v
```

### Guidelines for Writing Tests
- Place tests under the `tests/` directory.
- Model validators must be tested against both valid, noisy, and pathological inputs (e.g. malformed currency, unexpected date locales).
- Router logic must test all state permutation boundaries (edge cases, threshold transitions).

---

## 4. Git & Commit Guidelines

We enforce the [Conventional Commits](https://www.conventionalcommits.org/) specification.

### Commit Format
```text
<type>(<scope>): <short description>
```

- **Types:**
  - `feat`: A new capability or feature.
  - `fix`: A bug fix or resilience patch.
  - `docs`: Documentation updates or ADR additions.
  - `refactor`: Code changes that neither fix a bug nor add a feature.
  - `test`: Adding or correcting tests.
  - `chore`: Dependency updates, tooling, or build scripts.
- **Rules:**
  - Do NOT include AI tool attributions, co-author tags, or auto-generated footers.
  - Keep commit messages imperative, concise, and professional (e.g., `feat(ocr): add adaptive contrast preprocessing`).

---

## 5. Pull Request Checklist

Before submitting a Pull Request:
- [ ] Code follows project formatting and type annotations.
- [ ] All unit tests pass via `uv run pytest`.
- [ ] Any new architecture decision is documented in a corresponding ADR under `docs/adr/`.
- [ ] No secrets, `.env` files, temporary artifacts, or personal databases are tracked.
- [ ] PR title follows Conventional Commits.
