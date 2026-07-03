#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
PLAYWRIGHT_HOME="${PLAYWRIGHT_HOME:-${ROOT_DIR}/.playwright-mcp}"
PLAYWRIGHT_BROWSERS_DIR="${PLAYWRIGHT_BROWSERS_PATH:-${PLAYWRIGHT_HOME}/ms-playwright}"
PLAYWRIGHT_ARTIFACT_DIR="${PLAYWRIGHT_ARTIFACT_DIR:-${ROOT_DIR}/_grimoire-runtime-output/test-artifacts/playwright}"
PLAYWRIGHT_OUTPUT_DIR="${PLAYWRIGHT_OUTPUT_DIR:-${PLAYWRIGHT_ARTIFACT_DIR}}"
PLAYWRIGHT_CONFIG_PATH="${PLAYWRIGHT_MCP_CONFIG_PATH:-${ROOT_DIR}/.vscode/playwright-mcp.config.json}"
PLAYWRIGHT_RETENTION_MAX_FILES="${PLAYWRIGHT_RETENTION_MAX_FILES:-25}"
PLAYWRIGHT_RETENTION_MAX_DAYS="${PLAYWRIGHT_RETENTION_MAX_DAYS:-7}"
PLAYWRIGHT_JANITOR_POLL_SECONDS="${PLAYWRIGHT_JANITOR_POLL_SECONDS:-3}"
PLAYWRIGHT_JANITOR_ENABLED="${PLAYWRIGHT_JANITOR_ENABLED:-1}"
PLAYWRIGHT_MCP_JANITOR_ONLY="${PLAYWRIGHT_MCP_JANITOR_ONLY:-0}"
PLAYWRIGHT_MCP_SKIP_BROWSER_INSTALL="${PLAYWRIGHT_MCP_SKIP_BROWSER_INSTALL:-0}"
PLAYWRIGHT_JANITOR_MARKER="${PLAYWRIGHT_HOME}/.janitor-start"

export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_DIR}"

mkdir -p "${PLAYWRIGHT_BROWSERS_DIR}" "${PLAYWRIGHT_OUTPUT_DIR}"
touch "${PLAYWRIGHT_JANITOR_MARKER}"

find_new_root_artifacts() {
  find "${ROOT_DIR}" -maxdepth 1 -type f \
    -regextype posix-extended \
    -regex '.*/(page-[0-9T:._-]+\.(png|jpe?g|webp|pdf)|video-[0-9T:._-]+\.webm|trace-[0-9T:._-]+\.zip|storage-state-[0-9T:._-]+\.json)' \
    -newer "${PLAYWRIGHT_JANITOR_MARKER}" \
    -print0
}

next_available_artifact_path() {
  local source_name="$1"
  local candidate="${PLAYWRIGHT_OUTPUT_DIR}/${source_name}"
  local stem="${candidate}"
  local extension=""
  local suffix=1

  if [[ "${source_name}" == *.* ]]; then
    extension=".${source_name##*.}"
    stem="${PLAYWRIGHT_OUTPUT_DIR}/${source_name%.*}"
  fi

  while [[ -e "${candidate}" ]]; do
    candidate="${stem}-${suffix}${extension}"
    ((suffix++))
  done

  printf '%s\n' "${candidate}"
}

move_root_artifacts_into_output_dir() {
  local source_path
  local destination_path

  while IFS= read -r -d '' source_path; do
    destination_path="$(next_available_artifact_path "$(basename "${source_path}")")"
    mv "${source_path}" "${destination_path}"
  done < <(find_new_root_artifacts)
}

prune_output_dir_by_age() {
  if [[ ! "${PLAYWRIGHT_RETENTION_MAX_DAYS}" =~ ^[0-9]+$ ]]; then
    return
  fi

  find "${PLAYWRIGHT_OUTPUT_DIR}" -maxdepth 1 -type f -mtime "+${PLAYWRIGHT_RETENTION_MAX_DAYS}" -delete
}

prune_output_dir_by_count() {
  if [[ ! "${PLAYWRIGHT_RETENTION_MAX_FILES}" =~ ^[0-9]+$ ]]; then
    return
  fi

  if (( PLAYWRIGHT_RETENTION_MAX_FILES < 1 )); then
    find "${PLAYWRIGHT_OUTPUT_DIR}" -maxdepth 1 -type f -delete
    return
  fi

  local entry
  local path
  local index=0

  while IFS= read -r -d '' entry; do
    path="${entry#* }"
    index=$((index + 1))
    if (( index > PLAYWRIGHT_RETENTION_MAX_FILES )); then
      rm -f -- "${path}"
    fi
  done < <(
    find "${PLAYWRIGHT_OUTPUT_DIR}" -maxdepth 1 -type f -printf '%T@ %p\0' \
      | sort -z -rn
  )
}

cleanup_playwright_artifacts() {
  move_root_artifacts_into_output_dir
  prune_output_dir_by_age
  prune_output_dir_by_count
}

start_artifact_janitor() {
  if [[ "${PLAYWRIGHT_JANITOR_ENABLED}" != "1" ]]; then
    return
  fi

  while true; do
    cleanup_playwright_artifacts
    if ! sleep "${PLAYWRIGHT_JANITOR_POLL_SECONDS}"; then
      break
    fi
  done
}

stop_artifact_janitor() {
  if [[ -n "${PLAYWRIGHT_JANITOR_PID:-}" ]]; then
    kill "${PLAYWRIGHT_JANITOR_PID}" >/dev/null 2>&1 || true
    wait "${PLAYWRIGHT_JANITOR_PID}" >/dev/null 2>&1 || true
  fi
}

finish() {
  stop_artifact_janitor
  cleanup_playwright_artifacts
  # Kill Chromium processes that were launched from this session's browser path
  pkill -f "${PLAYWRIGHT_BROWSERS_DIR}" 2>/dev/null || true
}

if [[ "${PLAYWRIGHT_MCP_JANITOR_ONLY}" == "1" ]]; then
  cleanup_playwright_artifacts
  exit 0
fi

if [[ "${PLAYWRIGHT_MCP_SKIP_BROWSER_INSTALL}" != "1" ]]; then
  EXPECTED_BROWSER_DIR="$({
    npx -y @playwright/mcp@latest install-browser --dry-run chromium 2>/dev/null \
      | sed -n 's/^  Install location:[[:space:]]*//p' \
      | head -n 1
  } || true)"

  if [[ -z "${EXPECTED_BROWSER_DIR}" || ! -d "${EXPECTED_BROWSER_DIR}" ]]; then
    echo "[playwright-mcp] Installing Chromium bundle for MCP into ${PLAYWRIGHT_BROWSERS_PATH}" >&2
    npx -y @playwright/mcp@latest install-browser chromium >&2
  fi
fi

trap finish EXIT INT TERM
start_artifact_janitor &
PLAYWRIGHT_JANITOR_PID=$!

npx -y @playwright/mcp@latest \
  --config "${PLAYWRIGHT_CONFIG_PATH}" \
  --output-dir "${PLAYWRIGHT_OUTPUT_DIR}" \
  --image-responses omit \
  "$@"

exit_code=$?
finish
trap - EXIT INT TERM
exit "${exit_code}"