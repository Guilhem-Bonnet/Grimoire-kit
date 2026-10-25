"""Le rapport d'aperçu de mise à jour, lu en données plutôt qu'en texte de console (P44).

``preview.md`` embarque, dans des blocs de code, la sortie de ``grimoire up
--dry-run`` (un tableau Rich dessiné avec des caractères de boîte) et celle de
``grimoire host sync --dry-run``. L'afficher tel quel dans le cockpit montrait
ces caractères. Ce module en extrait les lignes (étape, statut, détail), traduites
en français, que l'interface rend en vrai tableau HTML. Lecture pure.

Un détail que la table de traduction ne connaît pas reste tel que la commande
l'a écrit : on ne reformule jamais une phrase qu'on ne reconnaît pas.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

__all__ = ["HostLine", "PreviewStep", "parse_preview"]

STEP_LABELS = {
    "init": "Initialisation",
    "refresh": "Rafraîchissement du kit",
    "override_review": "Revue des overrides",
    "identity": "Identité du projet",
    "standard": "Standard agentique",
    "task_unification": "Unification des tâches",
    "cadrage": "Cadrage",
    "host_sync": "Synchronisation des hôtes",
    "doctor": "Diagnostic",
}

STATUS_LABELS = {
    "done": ("fait", "ok"),
    "skipped": ("ignorée", ""),
    "failed": ("en échec", "bad"),
    "planned": ("prévue", "acc"),
}

_DETAILS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern), replacement)
    for pattern, replacement in (
        (r"^project already initialized \(project-context\.yaml present\)$", "projet déjà initialisé (project-context.yaml présent)"),
        (r"^would refresh the kit tier$", "rafraîchirait l'étage kit"),
        (r"^no override needs review$", "aucun override à revoir"),
        (r"^identity already in sync$", "identité déjà synchronisée"),
        (r"^standard init \(profile: (.+)\)$", r"initialisation du standard (profil : \1)"),
        (r"^project not enrolled in the standard$", "projet non enrôlé dans le standard"),
        (r"^need '(.+)' not selected$", r"besoin « \1 » non sélectionné"),
        (r"^would sync host surfaces \((.+)\)$", r"synchroniserait les surfaces d'hôtes (\1)"),
        (r"^run `grimoire doctor` for the full report$", "lancez `grimoire doctor` pour le rapport complet"),
        (r"^no project configuration$", "aucune configuration de projet"),
        (r"^kit artifacts already up to date$", "artefacts du kit déjà à jour"),
    )
)

_FENCE = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)
_HOST_COUNTS = re.compile(r"Écrirait\s+(\d+)\s*·\s*inchangé\s+(\d+)")


@dataclass(frozen=True, slots=True)
class PreviewStep:
    """Une ligne du tableau de ``grimoire up --dry-run``, en français."""

    id: str
    label: str
    status: str
    status_label: str
    tone: str
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "label": self.label,
            "status": self.status,
            "statusLabel": self.status_label,
            "tone": self.tone,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class HostLine:
    """Une surface d'hôte de ``grimoire host sync --dry-run``."""

    host: str
    would_write: int
    unchanged: int

    def to_dict(self) -> dict[str, Any]:
        return {"host": self.host, "wouldWrite": self.would_write, "unchanged": self.unchanged}


def translate_detail(detail: str) -> str:
    """La traduction d'un détail connu, sinon le détail tel quel."""
    for pattern, replacement in _DETAILS:
        if pattern.match(detail):
            return pattern.sub(replacement, detail)
    return detail


def _cells(line: str) -> list[str] | None:
    """Les cellules d'une ligne de données du tableau, ou ``None`` pour un filet."""
    stripped = line.strip()
    if not stripped or stripped[0] not in "│┃|":
        return None
    return [cell.strip() for cell in re.split(r"[│┃|]", stripped)[1:-1]]


def _parse_steps(block: str) -> tuple[PreviewStep, ...]:
    rows: list[list[str]] = []
    for line in block.splitlines():
        cells = _cells(line)
        if cells is None or len(cells) < 3:
            continue
        first, second, third = cells[0], cells[1], " ".join(c for c in cells[2:] if c)
        if first.lower() == "step" and second.lower() == "status":
            continue
        if not first and not second and rows:
            rows[-1][2] = f"{rows[-1][2]} {third}".strip()
            continue
        rows.append([first, second, third])
    steps = []
    for step_id, status, detail in rows:
        status_label, tone = STATUS_LABELS.get(status, (status, ""))
        steps.append(
            PreviewStep(
                id=step_id,
                label=STEP_LABELS.get(step_id, step_id),
                status=status,
                status_label=status_label,
                tone=tone,
                detail=translate_detail(detail),
            )
        )
    return tuple(steps)


def _parse_hosts(block: str) -> tuple[HostLine, ...]:
    hosts: list[HostLine] = []
    current: str | None = None
    for line in block.splitlines():
        if not line.strip():
            continue
        match = _HOST_COUNTS.search(line)
        if match and current is not None:
            hosts.append(HostLine(current, int(match.group(1)), int(match.group(2))))
            current = None
        elif not line.startswith((" ", "\t")):
            current = line.strip()
    return tuple(hosts)


def parse_preview(markdown: str | None) -> dict[str, Any] | None:
    """Le rapport d'aperçu structuré, ou ``None`` si ``preview.md`` n'a pas la forme attendue.

    ``None`` (et non un tableau vide) quand aucune ligne d'étape n'est
    reconnue : l'interface retombe alors sur le rendu Markdown du rapport, au
    lieu de prétendre qu'il n'y a rien à faire.
    """
    if not markdown:
        return None
    blocks = _FENCE.findall(markdown)
    steps: tuple[PreviewStep, ...] = ()
    hosts: tuple[HostLine, ...] = ()
    for block in blocks:
        if not steps:
            steps = _parse_steps(block)
            if steps:
                continue
        if not hosts:
            hosts = _parse_hosts(block)
    if not steps:
        return None
    return {
        "steps": [s.to_dict() for s in steps],
        "hosts": [h.to_dict() for h in hosts],
    }
