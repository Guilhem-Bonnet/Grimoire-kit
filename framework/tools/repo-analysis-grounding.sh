#!/usr/bin/env bash
# repo-analysis-grounding.sh — Collecte déterministe de la source de vérité d'un repo
# Usage: repo-analysis-grounding.sh <repo_path> [--max-depth N]
# Output: JSON structuré prêt à injecter dans le workflow repo-analysis (step-01)

set -uo pipefail

REPO_PATH="${1:-$(pwd)}"
MAX_DEPTH=3

shift 2>/dev/null || true
while [[ $# -gt 0 ]]; do
  case "$1" in
    --max-depth) MAX_DEPTH="$2"; shift 2 ;;
    *) shift ;;
  esac
done

if [[ ! -d "$REPO_PATH" ]]; then
  printf '{"error": "Repertoire introuvable: %s"}\n' "$REPO_PATH" >&2
  exit 1
fi

REPO_PATH="$(cd "$REPO_PATH" && pwd)"
REPO_NAME="$(basename "$REPO_PATH")"

TMPDIR_WORK="$(mktemp -d)"
trap 'rm -rf "$TMPDIR_WORK"' EXIT

# --- Arborescence filtrée ---
find "$REPO_PATH" \
  -maxdepth "$MAX_DEPTH" \
  -not -path '*/.git/*' \
  -not -path '*/node_modules/*' \
  -not -path '*/__pycache__/*' \
  -not -path '*/.venv/*' \
  -not -path '*/venv/*' \
  -not -path '*/.pytest_cache/*' \
  -not -path '*/.ruff_cache/*' \
  -not -path '*/dist/*' \
  -not -path '*/build/*' \
  -not -path '*/.next/*' \
  -not -path '*/target/*' \
  2>/dev/null | sort | head -300 > "$TMPDIR_WORK/file_tree.txt" || true

# --- Nombre de fichiers estimé ---
ESTIMATED_FILES=$(find "$REPO_PATH" \
  -type f \
  -not -path '*/.git/*' \
  -not -path '*/node_modules/*' \
  -not -path '*/__pycache__/*' \
  -not -path '*/.venv/*' \
  -not -path '*/venv/*' \
  2>/dev/null | wc -l | tr -d ' ') || ESTIMATED_FILES=0

# --- Token budget mode ---
if [[ "$ESTIMATED_FILES" -lt 100 ]]; then
  TOKEN_BUDGET_MODE="normal"
elif [[ "$ESTIMATED_FILES" -lt 500 ]]; then
  TOKEN_BUDGET_MODE="prioritized"
else
  TOKEN_BUDGET_MODE="stratified"
fi

# --- Assemblage JSON final via Python ---
REPO_NAME="$REPO_NAME" \
REPO_PATH="$REPO_PATH" \
TOKEN_BUDGET_MODE="$TOKEN_BUDGET_MODE" \
ESTIMATED_FILES="$ESTIMATED_FILES" \
FILE_TREE_FILE="$TMPDIR_WORK/file_tree.txt" \
python3 <<'PY'
import json, os

root              = os.environ["REPO_PATH"]
repo_name         = os.environ["REPO_NAME"]
token_budget      = os.environ["TOKEN_BUDGET_MODE"]
estimated_files   = int(os.environ.get("ESTIMATED_FILES", "0"))
file_tree_file    = os.environ["FILE_TREE_FILE"]

# Lire arborescence
with open(file_tree_file, encoding="utf-8") as f:
    file_tree = f.read().strip()

# Fichiers de config racine
config_candidates = [
    ("package.json",        "nodejs"),
    ("package-lock.json",   "nodejs-lock"),
    ("yarn.lock",           "yarn-lock"),
    ("pnpm-lock.yaml",      "pnpm-lock"),
    ("pyproject.toml",      "python"),
    ("setup.py",            "python-legacy"),
    ("setup.cfg",           "python-legacy"),
    ("requirements.txt",    "python-requirements"),
    ("Pipfile",             "pipenv"),
    ("Cargo.toml",          "rust"),
    ("go.mod",              "golang"),
    ("go.sum",              "golang-lock"),
    ("Gemfile",             "ruby"),
    ("composer.json",       "php"),
    ("pom.xml",             "java-maven"),
    ("build.gradle",        "java-gradle"),
    ("build.gradle.kts",    "java-gradle-kotlin"),
    ("CMakeLists.txt",      "cmake"),
    ("Makefile",            "make"),
    ("Dockerfile",          "docker"),
    ("docker-compose.yml",  "docker-compose"),
    ("docker-compose.yaml", "docker-compose"),
    (".env.example",        "env-example"),
    (".env.sample",         "env-sample"),
    ("tsconfig.json",       "typescript"),
    ("nx.json",             "nx-monorepo"),
    ("lerna.json",          "lerna-monorepo"),
    ("turbo.json",          "turborepo"),
]
config_files = [
    {"path": name, "type": kind}
    for name, kind in config_candidates
    if os.path.isfile(os.path.join(root, name))
]

# Points d'entrée
entry_candidates = [
    ("main.py",          "python-main"),
    ("app.py",           "python-app"),
    ("run.py",           "python-run"),
    ("manage.py",        "django"),
    ("wsgi.py",          "wsgi"),
    ("asgi.py",          "asgi"),
    ("index.ts",         "typescript-root"),
    ("index.js",         "javascript-root"),
    ("src/main.ts",      "typescript-src"),
    ("src/main.js",      "javascript-src"),
    ("src/index.ts",     "typescript-src"),
    ("src/index.js",     "javascript-src"),
    ("src/app.ts",       "typescript-app"),
    ("src/app.js",       "javascript-app"),
    ("lib/index.ts",     "typescript-lib"),
    ("lib/index.js",     "javascript-lib"),
    ("cmd/main.go",      "golang-cmd"),
    ("main.go",          "golang-main"),
    ("src/main.rs",      "rust-main"),
    ("src/lib.rs",       "rust-lib"),
]
entry_points = [
    {"path": rel, "type": kind}
    for rel, kind in entry_candidates
    if os.path.isfile(os.path.join(root, rel))
]

# Détection tests
import glob
test_patterns = [
    os.path.join(root, "**", "test_*.py"),
    os.path.join(root, "**", "*_test.py"),
    os.path.join(root, "**", "*.test.ts"),
    os.path.join(root, "**", "*.spec.ts"),
    os.path.join(root, "**", "*.test.js"),
    os.path.join(root, "**", "*Test.java"),
    os.path.join(root, "**", "*_test.go"),
]
test_presence = any(
    glob.glob(p, recursive=True)
    for p in test_patterns
)
test_framework = None
if test_presence:
    if os.path.isfile(os.path.join(root, "pytest.ini")):
        test_framework = "pytest"
    elif os.path.isfile(os.path.join(root, "pyproject.toml")):
        content = open(os.path.join(root, "pyproject.toml")).read()
        if "pytest" in content:
            test_framework = "pytest"
    if not test_framework:
        if glob.glob(os.path.join(root, "**", "*.test.ts"), recursive=True):
            test_framework = "jest/vitest"
        elif glob.glob(os.path.join(root, "**", "*_test.go"), recursive=True):
            test_framework = "go test"
        elif glob.glob(os.path.join(root, "**", "*Test.java"), recursive=True):
            test_framework = "junit"
        else:
            test_framework = "unknown"

# CI/CD
ci_config = []
gh_workflows = os.path.join(root, ".github", "workflows")
if os.path.isdir(gh_workflows):
    for f in sorted(os.listdir(gh_workflows)):
        if f.endswith((".yml", ".yaml")):
            ci_config.append({"path": f".github/workflows/{f}", "type": "github-actions"})
for fname, kind in [
    (".gitlab-ci.yml", "gitlab-ci"),
    ("Jenkinsfile",    "jenkins"),
    (".travis.yml",    "travis"),
]:
    if os.path.isfile(os.path.join(root, fname)):
        ci_config.append({"path": fname, "type": kind})
circleci = os.path.join(root, ".circleci", "config.yml")
if os.path.isfile(circleci):
    ci_config.append({"path": ".circleci/config.yml", "type": "circleci"})

result = {
    "repo_name":         repo_name,
    "repo_path":         root,
    "file_tree":         file_tree,
    "config_files":      config_files,
    "entry_points":      entry_points,
    "test_presence":     test_presence,
    "test_framework":    test_framework,
    "ci_config":         ci_config,
    "estimated_files":   estimated_files,
    "token_budget_mode": token_budget,
}
print(json.dumps(result, indent=2, ensure_ascii=False))
PY
