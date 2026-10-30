# ADR 0003: Pydantic v2 for Schema Enforcement and Structured Outputs

## Status
Accepted

## Date
2026-10-30

## Context
Financial document extraction demands absolute data fidelity. Unvalidated LLM outputs are susceptible to:
1. **Schema Non-Compliance:** Missing required fields, incorrect data types (e.g. strings instead of floating-point numbers), or malformed nested lists.
2. **Locale Inconsistencies:** Currency symbols (`$`, `€`, `USD`), European vs. US thousand/decimal separators (`1.234,56` vs `1,234.56`), and varied date formats (`DD/MM/YYYY` vs `MM/DD/YYYY` vs `YYYY-MM-DD`).
3. **Hallucinations:** When information is absent or illegible in the invoice, foundation models tend to infer or fabricate reasonable-looking values.

## Decision
We adopted **Pydantic v2** coupled with Google Gemini's native structured output engine (`llm.with_structured_output(InvoiceModel)`).

Key implementation choices:
1. **Native Schema Enforcement:** Schema constraints are translated into JSON Schema and injected directly into Gemini's grammar/decoding constraints.
2. **Custom Normalization Validators:**
   - Pre-validation regex sanitizers strip non-numeric characters and harmonize decimal separators.
   - Date fields are parsed into standard ISO-8601 `datetime.date` objects.
3. **Anti-Hallucination Policy:** Prompt directives explicitly instruct the model that illegible or absent fields must be assigned `null` (`None`). Inventions are strictly penalized.

## Consequences

### Positive
- **Compile-Time & Runtime Safety:** Downstream relational database operations are guaranteed to receive valid types.
- **Resilient Parsing:** Robust handling of multi-currency and international financial documents without manual boilerplate parsing.
- **Fail-Fast Semantics:** Any payload failing validation immediately triggers the graph's `vision_fallback` route.

### Negative
- Very complex or multi-page nested line item tables occasionally challenge the single-pass structured decoding grammar if prompt token limits are constrained.
