# Security Policy

## 1. Supported Versions

Security updates and patches are actively applied to the latest `main` branch.

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | :white_check_mark: |
| < 0.1.0 | :x:                |

---

## 2. Reporting a Vulnerability

We take the security and integrity of this project seriously. If you discover a potential vulnerability, please report it responsibly.

### Disclosure Process
1. **Do NOT open a public GitHub issue.**
2. Send an email to the project maintainer at `zaidarellano@protonmail.com` with:
   - A clear description of the issue.
   - Step-by-step reproduction steps or a minimal Proof of Concept (PoC).
   - Potential impact assessment.
3. You will receive an initial response within 48 hours acknowledging receipt.
4. Once verified, a patch will be prepared and released before public disclosure.

---

## 3. Data Privacy & Secrets Architecture

### 3.1 Local-First Execution
- The primary OCR tier (**RapidOCR**) executes **100% locally** using CPU-based ONNX inference. No document bytes are transmitted to any external network or third-party service during local OCR.
- Only parsed textual representations (or the raw image in the event of an automated vision fallback) are transmitted over encrypted HTTPS to the configured Google Gemini API endpoint.

### 3.2 Secret Management
- API keys (`GEMINI_API_KEY`) must never be hardcoded or checked into source control.
- Ensure all `.env` files are ignored by git as defined in `.gitignore`.
- If an API key is accidentally committed or exposed in logs, immediately revoke it in Google AI Studio and rotate credentials.
