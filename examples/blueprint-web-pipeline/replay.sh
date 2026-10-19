#!/usr/bin/env bash
# Rejeu de l'exemple blueprint-web-pipeline : sans modele, sans reseau, deterministe.
# Prerequis : la commande `grimoire` du kit dans le PATH.
# Sortie attendue : examples/blueprint-web-pipeline/expected.json (cle replay).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."

grimoire blueprint validate registry/blueprints/web-pipeline.blueprint.json
grimoire blueprint evals registry/blueprints/web-pipeline.blueprint.json \
  --record examples/blueprint-web-pipeline/record.json
