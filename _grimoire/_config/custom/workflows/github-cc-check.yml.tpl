# BMAD Completion Contract — CI Check
# Template déployé par bmad-init.sh dans .github/workflows/
#
# Déclenché sur chaque PR modifiant des fichiers vérifiables.
# Lance cc-verify.sh --changed-only et poste le résultat en annotation.

name: BMAD Completion Contract

on:
  pull_request:
    paths:
      - '**.go'
      - '**.ts'
      - '**.tsx'
      - '**.py'
      - '**.tf'
      - '**.tfvars'
      - '**.sh'
      - '**/Dockerfile'
      - '**/Dockerfile.*'
      - '**/docker-compose*.yml'
      - 'k8s/**'
      - 'ansible/**'

jobs:
  cc-check:
    name: Completion Contract
    runs-on: ubuntu-latest

    steps:
      - name: Checkout
        uses: actions/checkout@v4
        with:
          fetch-depth: 0  # historique complet pour --changed-only

      - name: Set up Go
        if: hashFiles('go.mod') != ''
        uses: actions/setup-go@v5
        with:
          go-version-file: 'go.mod'
          cache: true

      - name: Set up Python
        if: hashFiles('requirements.txt') != '' || hashFiles('pyproject.toml') != ''
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Set up Node.js
        if: hashFiles('package.json') != ''
        uses: actions/setup-node@v4
        with:
          node-version: '20'
          cache: 'npm'

      - name: Install dependencies (Node)
        if: hashFiles('package.json') != ''
        run: npm ci --prefer-offline

      - name: Install dependencies (Python)
        if: hashFiles('requirements.txt') != ''
        run: pip install -r requirements.txt

      - name: BMAD — Completion Contract
        run: |
          if [[ -f "_bmad/_config/custom/cc-verify.sh" ]]; then
            bash _bmad/_config/custom/cc-verify.sh --changed-only
          else
            echo "⚠️  cc-verify.sh introuvable — BMAD non initialisé dans ce dépôt."
            echo "    Lancez bmad-init.sh pour installer le Completion Contract."
          fi
