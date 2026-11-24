"""Environnement de l'agent cloud Copilot : ``.github/workflows/copilot-setup-steps.yml`` (épique #1280, lot P3).

Source : https://docs.github.com/en/copilot/how-tos/copilot-on-github/customize-copilot/customize-cloud-agent/customize-the-agent-environment
(lue le 2026-11-23 ; l'ancienne adresse ``…/use-copilot-agents/coding-agent/…`` y redirige).
Le fichier est un workflow GitHub Actions ordinaire qui doit porter un seul job,
nommé ``copilot-setup-steps`` (sinon Copilot ne le prend pas) ; ses étapes tournent
avant que l'agent commence, sur GitHub Actions seulement. Du job, Copilot ne lit
que ``steps``, ``permissions``, ``runs-on``, ``services``, ``snapshot`` et
``timeout-minutes`` (59 au plus) : le reste est ignoré. Sans ``actions/checkout``,
Copilot clone le dépôt lui-même après les étapes ; une étape en échec arrête les
suivantes et l'agent démarre dans l'état atteint.

Ce que le kit écrit : Python, puis ``grimoire-kit`` épinglé à la version qui émet
le fichier — celle que ``grimoire up`` enregistre aussi dans
``_grimoire/kit/.up-version`` — pour que les hooks (``grimoire-hook``) et le
serveur MCP (``grimoire-mcp``) existent dans l'environnement de l'agent.

Rien d'identifiant : ni secret, ni variable, ni jeton ; ``permissions`` réduites à
``contents: read`` (Copilot reçoit son propre jeton). Le fichier porte le marqueur
de gestion : un ``copilot-setup-steps.yml`` écrit par le projet (sans marqueur) ou
modifié depuis son émission n'est jamais réécrit — ``host sync`` le préserve et le
signale en conflit (``foreign`` / ``edited``), ``hosts.preserve`` le déclare sien.
"""

from __future__ import annotations

from pathlib import Path

from grimoire.__version__ import __version__
from grimoire.hosts.emitters.base import EmittedFile, managed_header

SETUP_STEPS = Path(".github") / "workflows" / "copilot-setup-steps.yml"
#: Nom imposé par Copilot : un autre nom de job n'est pas pris.
JOB_NAME = "copilot-setup-steps"
#: Plancher de ``requires-python`` du kit.
PYTHON_VERSION = "3.12"
#: Même épinglage par empreinte que les workflows du kit (une étiquette se déplace, un SHA non).
SETUP_PYTHON = "actions/setup-python@6d4fd22b20465ccc0f7354476588e8df7c9ca0e0  # v7"
#: Borne de Copilot : 59 minutes au plus ; l'installation tient largement en 15.
TIMEOUT_MINUTES = 15


def setup_steps_file(version: str = __version__) -> EmittedFile:
    """Le workflow ``copilot-setup-steps.yml`` qui installe ``grimoire-kit==<version>``."""
    content = f"""{managed_header(".yml")}
# Régénéré par `grimoire host sync --host copilot` : éditez-le et il ne sera plus
# réécrit (conflit signalé) ; déclarez-le dans `hosts.preserve` pour le garder à vous.
#
# Prépare l'environnement de l'agent cloud Copilot avant qu'il commence : installe
# grimoire-kit à la version du projet, pour que ses hooks et son serveur MCP existent.
name: "Copilot Setup Steps"

# Rejoué quand le fichier change (validation dans la PR), et à la main depuis l'onglet Actions.
on:
  workflow_dispatch:
  pull_request:
    paths:
      - .github/workflows/copilot-setup-steps.yml

jobs:
  # Copilot ne prend que ce job, sous ce nom exact.
  {JOB_NAME}:
    runs-on: ubuntu-latest
    timeout-minutes: {TIMEOUT_MINUTES}
    # Copilot reçoit son propre jeton : ces étapes ne lisent que le dépôt.
    permissions:
      contents: read
    steps:
      - name: Set up Python
        uses: {SETUP_PYTHON}
        with:
          python-version: "{PYTHON_VERSION}"
      - name: Install grimoire-kit {version}
        run: python -m pip install "grimoire-kit=={version}"
      - name: Check grimoire
        run: grimoire --version
"""
    return EmittedFile(relpath=SETUP_STEPS, content=content)
