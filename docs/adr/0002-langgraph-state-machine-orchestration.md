# ADR 0002: LangGraph State Machine for Agent Orchestration

## Status
Accepted

## Date
2026-10-30

## Context
When coordinating multi-step AI workflows involving ingest, quality analysis, conditional fallbacks, database persistence, and artifact generation, developers typically choose between:
1. **Procedural scripts / Linear Pipelines:** Simple, but fragile when handling non-linear branches, error recovery, cycles, and retries.
2. **Autonomous Multi-Agent Frameworks (CrewAI, AutoGen):** Autonomous emergent behaviors and open-ended ReAct loops introduce non-determinism, unpredictable token consumption, and debugging difficulty in mission-critical financial accounting systems.
3. **Graph-based State Machines (LangGraph):** Explicit state management, deterministic node transitions, conditional routing, and testability.

## Decision
We chose **LangGraph (`StateGraph`)** as the core orchestration framework for the document processing lifecycle.

The system is modeled as a compiled state graph with:
- A strictly typed state (`EstadoAgente`).
- Independent execution nodes with discrete single responsibilities (`ingesta`, `ocr`, `estructurar`, `vision_fallback`, `persistir`, `exportar`).
- Pure conditional router functions (`enrutar_post_ocr`, `enrutar_post_estructura`).
- Dependency injection on graph creation (`construir_grafo(motor_ocr, llm)`).

## Consequences

### Positive
- **Deterministic Traceability:** Every state transition is observable and auditable, critical for financial regulatory compliance.
- **Unit Testability:** Routing functions are pure Python functions without external I/O dependencies, allowing 100% test coverage with sub-millisecond execution.
- **Fault Isolation:** Failures in OCR or structured parsing are intercepted by graph branches rather than crashing the runtime.
- **Enterprise Extensibility:** Seamless path toward distributed persistence and Human-in-the-Loop review using LangGraph Checkpointers (`PostgresSaver`).

### Negative
- Minor learning curve regarding LangGraph's state reducer semantics compared to imperative Python functions.
- Requires explicit state dictionary synchronization across all participating nodes.
