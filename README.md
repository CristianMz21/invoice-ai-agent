# Autonomous Invoice AI Agent | LangGraph & Pydantic

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![LangGraph](https://img.shields.io/badge/orchestration-LangGraph-orange.svg)](https://github.com/langchain-ai/langgraph)
[![Pydantic v2](https://img.shields.io/badge/validation-Pydantic%20v2-e92063.svg)](https://docs.pydantic.dev/)
[![Streamlit](https://img.shields.io/badge/ui-Streamlit-red.svg)](https://streamlit.io/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

An enterprise-grade **Proof of Concept (PoC)** demonstrating an autonomous document processing agent. The pipeline ingests unstructured and semi-structured invoices (PDF and images), applies a high-efficiency local OCR tier, structures the payload into strictly typed schemas using **Google Gemini**, implements automatic **Multimodal Vision Fallback**, and persists transactional records into SQLite and Excel.

---

## Architecture & Core Design Decisions

Designed from the ground up following Clean Architecture, strict typing, and cost-efficient LLM engineering principles.

```mermaid
flowchart TD
    A[Input: Invoice PDF / Image] --> B[ingesta: Normalize & Rasterize]
    B --> C[ocr: RapidOCR ONNX Local]
    C --> D{Quality Gate Heuristic}
    
    D -- "Pass (Conf >= 0.5, Chars >= 40)" --> E[estructurar: Gemini Structured Output]
    D -- "Fail / Low Quality" --> F[vision_fallback: Gemini Multimodal Vision]
    
    E --> G{Schema Validation}
    G -- "Success" --> H[persistir: SQLite Transaction]
    G -- "Validation Error" --> F
    
    F --> H
    H --> I[exportar: Sync to Excel]
    I --> J([END: Ready for Human-in-the-Loop])
```

### 1. Cost & Token Optimization: Hybrid Two-Tier OCR Strategy
- **Why not send all images directly to Gemini Vision?** Feeding multi-page high-resolution invoice images directly into multimodal models consumes 1,500–3,000+ tokens per image, increases latency, and scales costs linearly with document volume.
- **The Solution:** We leverage **RapidOCR** (ONNX runtime, ~15MB weight footprint, zero GPU requirements) locally. The text output is piped to Gemini as a compact text prompt (~300–500 tokens).
- **Result:** ~85% reduction in token consumption and significantly lower p95 latency on standard legible invoices.

### 2. Autonomous Contingency: Multimodal Vision Fallback
- If the local OCR extraction fails the quality gate (e.g., scan noise, handwritten fields, text length < 40 chars, or average confidence < 0.5), or if structured JSON parsing fails, the agent autonomously routes the raw image to **Gemini Multimodal Vision**.
- Guarantees high extraction recall without manual human intervention.

### 3. StateGraph Orchestration (LangGraph)
- Built on LangGraph's compiled `StateGraph` with an explicit, strongly typed `EstadoAgente` schema.
- Routing logic is decoupled into pure deterministic router functions (`enrutar_post_ocr`, `enrutar_post_estructura`), enabling 100% test coverage without network calls or LLM billing.

### 4. Resilient Structured Outputs with Pydantic v2
- Utilizes `llm.with_structured_output(InvoiceModel)` to enforce schema adherence directly at the model engine level.
- Custom field validators normalize inconsistent locale patterns:
  - Currency strings (e.g., `$`, `€`, `USD`, spaces) and decimal formats (`1.234,56` vs `1,234.56`) normalized to `float`.
  - Heterogeneous date strings parsed into canonical ISO-8601 (`YYYY-MM-DD`).
  - Strict system prompt guardrails against hallucinations: missing values fall back to `null` rather than synthetic data.

### 5. Dependency Injection & Decoupling
- Factory functions (`construir_grafo(motor_ocr, llm)`) accept dependencies at build time.
- Decouples core graph logic from concrete SDK clients, making components effortlessly swappable, extensible, and mockable.

### 6. Relational Persistence & Idempotent Reporting
- Backed by **SQLAlchemy 2.0** models (`facturas` and `items_factura`) on SQLite.
- Excel synchronization (`reporte_facturas.xlsx`) is fully idempotent: SQLite remains the single source of truth, and sheets (`Facturas`, `Items`) are regenerated atomically.

---

## Project Structure

```text
.
├── agent.py               # LangGraph StateGraph, nodes, and router functions
├── app.py                 # Streamlit web application & live graph execution
├── db_handler.py          # SQLAlchemy 2.0 ORM models, migrations, & Excel exporter
├── main.py                # CLI runner and entrypoint for batch processing
├── models.py              # Pydantic v2 domain schemas and data normalization
├── ocr_engine.py          # RapidOCR (ONNX) wrapper & PDF rasterizer
├── pyproject.toml         # Dependencies and project metadata (uv-managed)
├── .env.example           # Environment template (safe for VCS)
├── scripts/
│   └── crear_factura_demo.py # Synthetic sample invoice generator (PDF/PNG)
└── tests/
    └── test_agent.py      # Unit tests for routers, validators, and heuristics
```

---

## Quickstart

### Prerequisites

- **Python 3.12+**
- **uv** (recommended package manager): [Install uv](https://docs.astral.sh/uv/getting-started/installation/)
- **poppler-utils** (for PDF rasterization):
  - Ubuntu/Debian: `sudo apt install poppler-utils`
  - macOS: `brew install poppler`

### 1. Installation

Clone the repository and install all dependencies in a virtual environment:

```bash
git clone https://github.com/<your-username>/invoice-ai-agent.git
cd invoice-ai-agent
uv sync
```

### 2. Configure Environment

Create a `.env` file from the provided template:

```bash
cp .env.example .env
```

Edit `.env` and set your Google AI Studio API key:

```env
GEMINI_API_KEY=your_google_ai_studio_api_key_here
# Optional model override (defaults to gemini-1.5-flash):
# GEMINI_MODEL=gemini-2.0-flash
```

---

## Running the Application

### Option A: Interactive Web UI (Streamlit)

Launch the web interface to upload invoices, inspect live graph step execution, review structured tables, and download reports:

```bash
uv run streamlit run app.py
```
Open [http://localhost:8501](http://localhost:8501) in your browser.

### Option B: CLI Batch Processing

Generate a synthetic demo invoice and run the autonomous agent via command line:

```bash
# 1. Generate sample invoice (PDF & PNG in data/)
uv run python scripts/crear_factura_demo.py

# 2. Process all invoices in ./data
uv run python main.py

# Or process a specific invoice file directly:
uv run python main.py path/to/invoice.pdf --debug
```

Generated outputs will be saved to `facturas.db` (SQLite) and `reporte_facturas.xlsx`.

---

## Running Tests

Execute the comprehensive unit test suite (33 tests covering pure routers, heuristics, currency parsers, and schema validation):

```bash
uv run pytest
```

All unit tests run offline in milliseconds without making external API calls.

---

## Production Roadmap

- [ ] **LangGraph Checkpointer**: Add persistent SQLite/PostgreSQL checkpointing for fault-tolerant pause/resume and Human-in-the-Loop review.
- [ ] **Duplicate Detection**: Unique hash fingerprints and invoice number + vendor deduplication before database commit.
- [ ] **Distributed Queue**: Celery/RabbitMQ or AWS SQS integration for asynchronous high-throughput document ingestion.
