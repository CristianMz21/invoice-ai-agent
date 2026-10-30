#!/usr/bin/env bash
set -euo pipefail

# 1. Initialize local Git repository
git init -b main

# 2. Stage tracked files (honoring .gitignore)
git add .

# 3. Pre-commit check: prevent accidental staging of sensitive files
if git diff --cached --name-only | grep -E '(^\.env$|\.env\.local$|\.db$|\.sqlite$|\.xlsx$|^data/|^uploads/|^facturas/)'; then
    echo "❌ ERROR: Sensitive or ignored files detected in staging area! Aborting." >&2
    exit 1
fi

# 4. Create initial conventional commit
git commit -m "feat: initial commit - LangGraph Autonomous Agent PoC"

# 5. Create remote public repository on GitHub and push code
gh repo create invoice-ai-agent --public --source=. --remote=origin --push

echo "✅ Repository created and pushed successfully."
