#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KIT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PY="$KIT_ROOT/.venv/bin/python"

cd "$KIT_ROOT"

if [[ ! -x "$PY" ]] || ! "$PY" -c "import sys" >/dev/null 2>&1; then
  echo "Environnement virtuel invalide ($PY). Lancez la tache grimoire: bootstrap-venv."
  exit 1
fi

printf "=== LINT ===\n"

targets=()
for dir in framework/tools tests; do
  [[ -e "$dir" ]] && targets+=("$dir")
done

if [[ ${#targets[@]} -eq 0 ]]; then
  echo "Aucune cible lint trouvee - skip"
else
  set +e
  lint_output="$($PY -m ruff check "${targets[@]}" --statistics 2>&1)"
  lint_status=$?
  set -e

  printf "%s\n" "$lint_output"

  if [[ $lint_status -ne 0 ]]; then
    exit 1
  fi

  set +e
  lint_json="$($PY -m ruff check "${targets[@]}" --output-format json 2>/dev/null)"
  json_status=$?
  set -e

  if [[ $json_status -eq 0 ]]; then
    finding_count="$($PY -c 'import json,sys; print(len(json.loads(sys.stdin.read() or "[]")))' <<< "$lint_json")"
    if [[ "${finding_count:-0}" -gt 0 ]]; then
      echo "Ruff reports lint findings - failing quickcheck"
      exit 1
    fi
  fi

  echo "Ruff OK"
fi

printf "\n=== TESTS (modifies) ===\n"

mapfile -t changed_files < <(
  {
    git diff --name-only --diff-filter=ACMR HEAD -- tests
    git ls-files --others --exclude-standard -- tests
  } | grep '^tests/.*test_.*\.py$' | sort -u || true
)

if [[ ${#changed_files[@]} -eq 0 ]]; then
  echo "Aucun test modifie - skip"
else
  "$PY" -m pytest "${changed_files[@]}" -q --tb=line -x
fi