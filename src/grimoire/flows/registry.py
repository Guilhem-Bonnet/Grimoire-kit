"""Le registre local d'un flow : version, compatibilité, intégrité, besoins (issue #206).

``flow list`` (issue #208, lot 5 réduit) affichait déjà les runs connus et
leur mesure de dispatch (``TraceLedger.dispatch_outcome_stats().by_flow``).
Ce module ajoute ce que ce lot demande en plus, dérivé du fichier
``.blueprint.json`` lui-même plutôt que d'un run passé :

- **version** / **plage de compatibilité** (``kitMin``/``kitMax``) —
  déclarées par l'auteur au niveau du blueprint, absentes par défaut.
- **empreinte d'intégrité** — ``sha256`` du contenu du fichier racine, même
  fonction que ``grimoire.tools.ext_manager._sha256`` (préfixe ``sha256:``).
  Porte uniquement sur le fichier racine : un sous-flow référencé (``kind:
  "composite"``) a sa propre empreinte, listée séparément si on l'interroge
  directement — ce module n'agrège pas un hash composite, ça mélangerait deux
  objets versionnés indépendamment sous un seul chiffre.
- **besoins requis** — l'union des ``run_need`` déclarés par ce flow et par
  tous ses sous-flows ``composite`` (profondeur bornée par
  :data:`grimoire.flows.blueprint_loader.MAX_COMPOSITE_DEPTH`, la même que le
  chargement applique déjà). Un scan statique du JSON, jamais une résolution
  contre le projet : ``flow list`` doit pouvoir décrire un flow qu'on n'a
  jamais exécuté ici, sur un projet qui n'a peut-être aucun des besoins
  installés.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from grimoire.flows.blueprint_loader import load_blueprint, resolve_composite_ref

__all__ = ["FlowRegistryInfo", "describe_flow"]


@dataclass(frozen=True, slots=True)
class FlowRegistryInfo:
    """Ce que le registre local sait d'un flow, dérivé de son fichier (issue #206)."""

    blueprint_id: str
    version: str
    kit_min: str | None
    kit_max: str | None
    integrity_sha256: str
    required_needs: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "blueprint_id": self.blueprint_id,
            "version": self.version,
            "kit_min": self.kit_min,
            "kit_max": self.kit_max,
            "integrity_sha256": self.integrity_sha256,
            "required_needs": list(self.required_needs),
        }


def _file_sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _collect_run_needs(blueprint: dict[str, Any], *, project_root: Path, blueprint_path: Path) -> set[str]:
    """Les ids ``run_need`` déclarés par *blueprint* et par ses sous-flows composite.

    Scan statique de l'``acceptance`` brute (pas de résolution projet) : une
    entrée ``{"run_need": "..."}`` est comptée, qu'elle soit résolvable ici ou
    non — ce module décrit un flow, il ne vérifie pas qu'il peut tourner sur
    ce projet-ci (c'est le rôle de ``flow run``/``grimoire needs resolve``).
    """
    needs: set[str] = set()
    for node in blueprint.get("nodes", []):
        for entry in node.get("acceptance") or []:
            if isinstance(entry, dict) and "run_need" in entry:
                needs.add(str(entry["run_need"]))
        if node.get("kind") == "composite":
            ref = str(node.get("ref", ""))
            sub_path = resolve_composite_ref(ref, project_root=project_root, blueprint_dir=blueprint_path.parent)
            # Déjà validé (existence, cycle, profondeur) par `load_blueprint`
            # au moment où ce blueprint racine a été chargé — recharger ici
            # ne fait que relire un fichier déjà connu bon.
            sub_blueprint = load_blueprint(sub_path, project_root)
            needs |= _collect_run_needs(sub_blueprint, project_root=project_root, blueprint_path=sub_path)
    return needs


def describe_flow(blueprint_path: Path, project_root: Path) -> FlowRegistryInfo:
    """Le :class:`FlowRegistryInfo` d'un flow, depuis son fichier ``.blueprint.json``.

    Charge (et donc revalide — composition comprise) *blueprint_path* :
    un appelant qui a déjà un run pointant vers un blueprint cassé depuis
    l'obtient comme un refus nommé ici, pas une description silencieusement
    incomplète.
    """
    blueprint = load_blueprint(blueprint_path, project_root)
    needs = _collect_run_needs(blueprint, project_root=project_root, blueprint_path=blueprint_path)
    return FlowRegistryInfo(
        blueprint_id=str(blueprint.get("id", "")),
        version=str(blueprint.get("version") or "0.0.0"),
        kit_min=blueprint.get("kitMin"),
        kit_max=blueprint.get("kitMax"),
        integrity_sha256=_file_sha256(blueprint_path),
        required_needs=tuple(sorted(needs)),
    )
