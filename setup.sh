#!/bin/bash
set -e

PLUGIN_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== CIDector Plugin Setup ==="
echo "Plugin directory: $PLUGIN_DIR"
echo ""

# 1. Python dependencies
echo "[1/4] Installing Python dependencies..."
pip3 install -r "$PLUGIN_DIR/requirements.txt" --quiet
echo "  Done."

# 2. Playwright browser (recommended for China sources / dynamic pages)
echo ""
echo "[2/4] Checking Playwright browser..."
if python3 -c "import playwright" 2>/dev/null; then
  if python3 -m playwright install chromium >/dev/null 2>&1; then
    echo "  Chromium browser is ready."
  else
    echo "  Could not install Chromium automatically."
    echo "  You can retry later with: python3 -m playwright install chromium"
  fi
else
  echo "  Playwright package is not available."
  echo "  Dynamic pages and some China sources may be unavailable until Chromium is installed."
fi

# 3. Environment variables
echo ""
echo "[3/4] Checking environment variables..."

if [ -f "$PLUGIN_DIR/.env" ]; then
  echo "  .env file already exists at $PLUGIN_DIR/.env"
else
  if [ -f "$PLUGIN_DIR/.env.example" ]; then
    cp "$PLUGIN_DIR/.env.example" "$PLUGIN_DIR/.env"
    echo "  Created .env from .env.example"
    echo ""
    echo "  *** IMPORTANT: Edit $PLUGIN_DIR/.env to add your API keys ***"
    echo ""
    echo "  Required:"
    echo "    TAVILY_API_KEY=tvly-xxxxx   (https://tavily.com)"
    echo ""
    echo "  Optional:"
    echo "    NCBI_EMAIL=your@email.com   (for PubMed)"
    echo "    NCBI_API_KEY=xxxxx          (raises PubMed rate limit)"
  else
    echo "  WARNING: No .env.example found."
  fi
fi

# 4. Post-install self-check
echo ""
echo "[4/4] Running self-check..."
if python3 "$PLUGIN_DIR/benchmarks/self_check.py"; then
  echo "  Self-check completed."
else
  echo "  Self-check reported one or more issues."
  echo "  Setup is still complete, but some capabilities may be unavailable until dependencies or API keys are fixed."
  echo "  You can rerun it later with: python3 \"$PLUGIN_DIR/benchmarks/self_check.py\""
fi

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Alternatively, set environment variables globally in your shell profile:"
echo "  export TAVILY_API_KEY=tvly-xxxxx"
echo "  export NCBI_EMAIL=your@email.com"
echo ""
echo "Usage in Claude Code:"
echo "  - The 'cidector' skill auto-activates for pharma/biotech questions"
echo "  - /research-plan <query>  — generate a research plan"
echo "  - /fact-check <claims>    — verify key facts"
echo ""
echo "Usage in Codex:"
echo "  - Start Codex inside this repo: codex"
echo "  - AGENTS.md provides the project instructions automatically"
echo "  - For orchestrator auto-fix, choose backend with:"
echo "      python3 orchestrate/orchestrator.py fix --backend codex"
