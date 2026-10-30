# ADR 0001: Hybrid Two-Tier OCR Strategy (Local RapidOCR + Multimodal Vision Fallback)

## Status
Accepted

## Date
2026-10-30

## Context
Document understanding pipelines for invoices traditionally choose between two extremes:
1. **Traditional OCR only (Tesseract, AWS Textract):** Fast and relatively inexpensive, but brittle when parsing diverse layouts, non-standard tables, and irregular fonts into structured JSON.
2. **Pure Multimodal LLM (Gemini Vision, GPT-4o):** Extremely capable at layout comprehension, but introduces high latency ($2\text{s}-6\text{s}$ per image), substantial token consumption ($1,500\text{--}3,000+$ image tokens per page), and significant recurring API costs.

Invoices in a corporate finance environment exhibit an 80/20 distribution: the vast majority are clean, machine-printed digital PDFs or high-resolution scans, while a minority are noisy, handwritten, tilted, or corrupted scans.

## Decision
We implemented a **Two-Tier Hybrid Extraction Strategy**:
1. **Tier 1 (Default):** Process all incoming documents using **RapidOCR** (ONNX Runtime, lightweight local CPU inference, zero API token cost). The extracted text is then formatted into a concise text-only prompt for Google Gemini, consuming only $300\text{--}500$ tokens.
2. **Quality Gate:** A deterministic heuristic inspects the OCR result before calling the LLM. If text length $< 40$ characters, confidence $< 0.5$, or no digits are found, the document is flagged.
3. **Tier 2 (Fallback):** Only documents failing Tier 1 quality checks or structured extraction validation are dynamically routed to **Gemini Multimodal Vision**.

## Consequences

### Positive
- **Cost Reduction:** Token expenditure reduced by up to $85\%$ on standard invoice batches compared to an all-vision pipeline.
- **Latency Improvement:** CPU-bound local OCR processes in under $300\text{ms}$, significantly lowering end-to-end processing time for the primary happy path.
- **Resilience:** Unreadable or complex layouts are not dropped; they are gracefully salvaged by the multimodal fallback.
- **Zero GPU Dependency:** RapidOCR with ONNX Runtime executes on modest containerized CPU environments without requiring expensive CUDA instances.

### Negative
- Requires maintaining the local ONNX runtime and system dependencies (`poppler-utils` for PDF rasterization).
- Added routing complexity inside the LangGraph state machine to handle fallback state and metrics.
