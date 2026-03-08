#!/bin/bash
# tests/run-coverage.sh — Mesure de couverture des outils Synapse (BM-46 Story 7.3)
#
# Usage:
#   bash tests/run-coverage.sh           # Full coverage report
#   bash tests/run-coverage.sh --quick   # Term-only (no HTML)
#
set -euo pipefail

cd "$(dirname "$0")/.."

# ── Intelligence Layer test files ──────────────────────────────────────────

TEST_FILES=(
  tests/test_llm_router.py
  tests/test_rag_indexer.py
  tests/test_rag_retriever.py
  tests/test_memory_sync.py
  tests/test_bmad_mcp_tools.py
  tests/test_context_summarizer.py
  tests/test_semantic_cache.py
  tests/test_token_budget.py
  tests/test_tool_registry.py
  tests/test_agent_caller.py
  tests/test_message_bus.py
  tests/test_delivery_contracts.py
  tests/test_conversation_branch.py
  tests/test_background_tasks.py
  tests/test_agent_worker.py
  tests/test_orchestrator.py
  tests/test_context_merge.py
  tests/test_conversation_history.py
  tests/test_synapse_config.py
  tests/test_synapse_trace.py
  tests/test_integration_synapse.py
)

# Filter to existing files
EXISTING=()
for f in "${TEST_FILES[@]}"; do
  if [[ -f "$f" ]]; then
    EXISTING+=("$f")
  fi
done

if [[ ${#EXISTING[@]} -eq 0 ]]; then
  echo "❌ Aucun fichier de test trouvé"
  exit 1
fi

echo "🔍 Coverage Synapse Intelligence Layer"
echo "   ${#EXISTING[@]} suites de tests détectées"
echo ""

# ── Check pytest-cov ──────────────────────────────────────

if ! python3 -c "import pytest_cov" 2>/dev/null; then
  echo "⚠️  pytest-cov non installé. Installation..."
  pip install pytest-cov --quiet
fi

# ── Run ──────────────────────────────────────────────────────

COVDIR="_bmad-output/bench-reports"
mkdir -p "$COVDIR"

if [[ "${1:-}" == "--quick" ]]; then
  python3 -m pytest "${EXISTING[@]}" \
    --cov=framework/tools \
    --cov-report=term-missing \
    --cov-fail-under=70 \
    -q --tb=short
else
  python3 -m pytest "${EXISTING[@]}" \
    --cov=framework/tools \
    --cov-report=term-missing \
    --cov-report="html:$COVDIR/htmlcov" \
    --cov-fail-under=70 \
    -q --tb=short

  # Save text report
  python3 -m pytest "${EXISTING[@]}" \
    --cov=framework/tools \
    --cov-report=term-missing \
    -q --tb=no 2>&1 | tail -n +1 > "$COVDIR/coverage-synapse.txt"

  echo ""
  echo "✅ Coverage reports:"
  echo "   HTML : $COVDIR/htmlcov/index.html"
  echo "   Text : $COVDIR/coverage-synapse.txt"
fi
