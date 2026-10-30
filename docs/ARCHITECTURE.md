# System Architecture Specification

## 1. Executive Summary & System Goals

The **Autonomous Invoice AI Agent** is an enterprise-grade document processing pipeline designed to ingest, normalize, transcribe, and structure heterogeneous financial documents (PDFs and scanned images). 

The primary business objective is maximizing data extraction accuracy while minimizing operational expenditure (OpEx) associated with LLM token usage and latency.

### Key Architectural Tenets
1. **Cost-Aware AI (Frugal Engineering):** Never invoke multimodal vision APIs when lightweight, high-speed local inference can perform the extraction.
2. **Deterministic State Machine:** Agentic workflows must follow strict, verifiable state transitions rather than stochastic black-box autonomous loops.
3. **Type Safety & Schema Integrity:** Data contracts are enforced via Pydantic v2 at the compiler/runtime boundary; hallucinations or schema drift are treated as fatal execution faults.
4. **Clean Decoupling:** Business domain logic, OCR engines, LLM clients, and persistence layers interact via Dependency Injection and strict protocols.

---

## 2. C4 Model Architecture

### 2.1 Level 1: System Context Diagram

```mermaid
C4Context
    title System Context - Autonomous Invoice AI Agent

    Person(operator, "Finance Operator / Analyst", "Uploads invoices and inspects extracted transaction batches.")
    System(agent_system, "Autonomous Invoice Agent", "Ingests, transcribes, structures, and exports financial records.")
    System_Ext(gemini_api, "Google Gemini API", "Structured data extraction and Multimodal Vision fallback.")
    SystemDb_Ext(sql_db, "SQLite / Relational DB", "Relational persistence for auditability and reporting.")
    System_Ext(excel_out, "Excel Storage", "Idempotent downstream reporting (XLSX).")

    Rel(operator, agent_system, "Submits documents / Reviews runs", "HTTP / UI / CLI")
    Rel(agent_system, gemini_api, "Structured extraction & Vision fallback", "HTTPS / JSON")
    Rel(agent_system, sql_db, "Persists invoices & line items", "SQLAlchemy 2.0")
    Rel(agent_system, excel_out, "Regenerates reports", "Atomic File I/O")
```

### 2.2 Level 2: Container Diagram

```mermaid
C4Container
    title Container Diagram - Component Topology

    Container(streamlit_app, "Streamlit UI", "Python / Streamlit", "Interactive web dashboard for document upload, real-time node telemetry, and report download.")
    Container(cli_runner, "CLI Batch Runner", "Python (main.py)", "Headless command-line utility for cron / automated batch directory ingestion.")
    Container(langgraph_engine, "LangGraph State Machine", "LangGraph / Python 3.12", "Orchestrates cyclic execution, fallback routing, and validation gates.")
    Container(rapid_ocr, "Local OCR Engine", "RapidOCR / ONNX Runtime", "Low-latency CPU-based optical character recognition (no external network I/O).")
    ContainerDb(db_store, "Relational Store", "SQLite (Migratable to PostgreSQL)", "Transactional tables for invoices and line items.")

    Rel(streamlit_app, langgraph_engine, "Invokes graph with State", "Python API")
    Rel(cli_runner, langgraph_engine, "Invokes graph with State", "Python API")
    Rel(langgraph_engine, rapid_ocr, "Runs local text extraction", "ONNX Runtime (CPU)")
    Rel(langgraph_engine, db_store, "Stores normalized records", "SQLAlchemy ORM")
```

### 2.3 Level 3: Component Diagram (LangGraph Workflow)

```mermaid
flowchart TD
    subgraph Ingestion["1. Ingestion Layer"]
        A[Input Document: PDF / PNG / JPG] --> B[Node: ingesta]
        B -->|PDF| B1[pdf2image + Poppler]
        B -->|Image| B2[Pillow Normalization]
    end

    subgraph OCR["2. Local Transcription Tier"]
        B1 & B2 --> C[Node: ocr]
        C --> C1[RapidOCR ONNX Engine]
        C1 --> D{Router: enrutar_post_ocr}
    end

    subgraph Extraction["3. Structured Extraction & Tool Tier"]
        D -- "Pass Quality Gate (Conf >= 0.5 & Len >= 40)" --> E[Node: estructurar]
        D -- "Fail Gate" --> F[Node: vision_fallback]
        
        E --> T{Router: enrutar_post_estructurar}
        T -- "Has Tool Calls" --> T1[Node: tools / ToolNode]
        T1 -->|Append Observation| E
        
        T -- "Valid InvoiceModel" --> H[Node: persistir]
        T -- "Validation Failure" --> F
        
        F --> F1[Gemini Multimodal Vision API]
        F1 --> H
    end

    subgraph Persistence["4. Persistence & Egress Tier"]
        H --> H1[Node: persistir]
        H1 --> H2[(SQLite / SQLAlchemy 2.0)]
        H2 --> I[Node: exportar]
        I --> I1[reporte_facturas.xlsx]
        I1 --> J([END State])
    end
```

---

## 3. State Machine & Graph Lifecycle

The core execution graph is defined as a LangGraph `CompiledStateGraph` passing a typed dictionary (`EstadoAgente`) through sequential and conditional nodes.

### 3.1 State Schema Contract (`EstadoAgente`)

| Field | Type | Description |
|---|---|---|
| `archivo_entrada` | `str` | Original file path (PDF or image). |
| `rutas_imagenes` | `list[Path]` | Normalized PNG page paths for processing. |
| `imagen_fallback` | `str \| None` | Path to representative image for multimodal fallback. |
| `texto_ocr` | `str` | Raw text transcribed across all pages. |
| `confianza_ocr` | `float` | Weighted mean confidence score of OCR lines $[0.0, 1.0]$. |
| `ocr_util` | `bool` | Heuristic evaluation flag indicating if text is usable. |
| `factura` | `InvoiceModel \| None` | Pydantic v2 domain model adhering to financial rules. |
| `metodo_extraccion` | `str` | Extraction method (`METODO_OCR_LLM` or `METODO_VISION_FALLBACK`). |
| `registro_id` | `int` | Primary key assigned in SQLite database (`facturas.id`). |
| `error` | `str \| None` | Explanatory message if graph aborted or failed. |
| `messages` | `list[BaseMessage]` | Conversational history with `add_messages` reducer for MCP tool execution. |
| `auditoria_erp` | `dict[str, Any] \| None` | Supplementary accounting verification data from tools. |
| `herramientas_usadas` | `list[str]` | Audit list of tool names executed during extraction. |

### 3.2 Routing & Transition Matrix

| Current State / Node | Decision Condition | Next Node | Rationality |
|---|---|---|---|
| `ingesta` | File normalized and rasterized | `ocr` | Document converted to standard image format. |
| `ingesta` | Missing file or rasterization exception | `END` | Unrecoverable I/O error; state reports descriptive error. |
| `ocr` | `len(texto) >= 40` AND `confianza >= 0.5` AND has digits | `estructurar` | Text quality is sufficient for token-efficient LLM prompt. |
| `ocr` | `texto == ""` OR `confianza < 0.5` OR text too short | `vision_fallback` | Local OCR failed to resolve structure; trigger multimodal vision. |
| `estructurar` | Last message contains `tool_calls` | `tools` | LLM requires auxiliary accounting validation (ERP / FX). |
| `tools` | Execution finished | `estructurar` | Observations appended to `messages`; LLM continues reasoning. |
| `estructurar` | `factura != null` AND `factura_util(factura) == True` | `persistir` | Extraction conforms to Pydantic domain invariants. |
| `estructurar` | Validation error OR missing mandatory fields | `vision_fallback` | Text transcription lacked semantic coherence for schema alignment. |
| `vision_fallback` | `factura != null` | `persistir` | Multimodal model recovered document structure. |
| `vision_fallback` | Extraction failed or unrecoverable error | `END` | Document cannot be transcribed; pipeline aborts with error. |
| `persistir` | Insertion committed | `exportar` | Database state updated; regenerate reports. |
| `exportar` | Report regenerated | `END` | Batch step complete. |

---

## 4. Data Model & Entity Relationship

Persistence is handled by SQLAlchemy 2.0 using relational integrity constraints and cascading deletions.

```mermaid
erDiagram
    FACTURAS ||--o{ ITEMS_FACTURA : contains
    
    FACTURAS {
        int id PK "Autoincrement"
        string numero_factura "Unique identifier"
        date fecha_emision "ISO-8601 Date"
        string emisor_nombre "Vendor / Legal entity"
        string emisor_nif "Tax ID / VAT"
        string receptor_nombre "Customer entity"
        string receptor_nif "Customer Tax ID"
        float subtotal "Pre-tax amount"
        float impuestos "Total tax amount"
        float total "Grand total amount"
        string moneda "ISO 4217 Currency Code"
        string ruta_origen "Original file source"
        boolean metodo_vision "True if processed via Vision Fallback"
        datetime creado_en "Audit timestamp"
    }

    ITEMS_FACTURA {
        int id PK "Autoincrement"
        int factura_id FK "References FACTURAS.id"
        string descripcion "Line item description"
        float cantidad "Billed units"
        float precio_unitario "Per-unit cost"
        float total "Subtotal for line item"
    }
```

---

## 5. Security & Threat Modeling

| Threat Vector | Risk Level | Mitigation Architecture |
|---|---|---|
| **API Key Exposure** | **Critical** | Zero hardcoded secrets. Strict `.gitignore` blocking `.env*`, environment validation at container startup, no secrets committed to VCS. |
| **Data Ingestion Vulnerability (Malicious PDF/Image)** | **High** | Sanitization and buffer boundaries handled by Poppler and Pillow; processing runs in isolated worker context. |
| **Model Hallucination / Financial Drift** | **High** | Pydantic v2 strict validator types. Currency and dates normalized with regex. Prompt rules strictly forbid synthetic fields: missing attributes must map to `null`. |
| **Data Leakage in Public Cloud** | **Medium** | 85%+ of invoices are transcribed locally on-premise/in-container. Only text representations (or images upon fallback) transit encrypted over HTTPS to Google AI Studio. |

---

## 6. Enterprise Scalability Roadmap

For multi-tenant high-throughput enterprise deployments ($>100,000$ invoices/day), the architecture is prepared for the following transitions:
1. **LangGraph Checkpointer on PostgreSQL:** Replace in-memory compiled graphs with `PostgresSaver` to persist graph execution checkpoints across distributed nodes.
2. **Asynchronous Ingestion (Celery / Redis / SQS):** Move from synchronous Streamlit/CLI calls to an event-driven worker pool.
3. **Blob Storage Integration (AWS S3 / GCP Cloud Storage):** Abstract local filesystem paths (`data/`) into signed URIs with lifecycle expiration.
