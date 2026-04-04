#!/bin/bash
set -e

PLUGIN_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== CIDector Plugin Setup ==="
echo "Plugin directory: $PLUGIN_DIR"
echo ""

# 1. Python dependencies
echo "[1/3] Installing Python dependencies..."
pip3 install -r "$PLUGIN_DIR/requirements.txt" --quiet
echo "  Done."

# 2. Playwright browser (optional, for fetch_page --dynamic)
echo ""
echo "[2/3] Playwright browser (optional, for dynamic page fetching)..."
if python3 -c "import playwright" 2>/dev/null; then
  echo "  Playwright already installed. Run 'python3 -m playwright install chromium' if needed."
else
  echo "  Skipped. Install later with: pip3 install playwright && python3 -m playwright install chromium"
fi

# 3. Environment variables
echo ""
echo "[3/3] Checking environment variables..."

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
