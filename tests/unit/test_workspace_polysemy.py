"""Fin du point polysémique (P30) : la preuve sans navigateur.

Le point coloré portait six sens sur une seule carte de tâche (projet, session,
priorité, consigne, risque, preuve). Ce module verrouille :

- ``executer.js`` n'appelle plus ``dot()`` que pour un ÉTAT, trois fois au plus
  (17 appels avant P30) ;
- chaque table d'icônes de l'espace (type, risque, vivacité, hôte) couvre
  exactement les valeurs du kit et ne cite que des symboles du sprite ;
- Source écrit ses statuts de fichier en lettres façon git (A, M, D, U, O).

Le rendu réel est prouvé par ``tests/e2e/test_workspace_polysemy.py``.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = ROOT / "web" / "workspace"
EXECUTER = WORKSPACE / "spaces" / "executer.js"
SOURCE = WORKSPACE / "spaces" / "source.js"


def _symbols() -> set[str]:
    return set(re.findall(r'<symbol id="([a-z0-9-]+)"', (WORKSPACE / "icons.svg").read_text(encoding="utf-8")))


def _table(text: str, name: str) -> dict[str, str]:
    """Les paires `clé: 'valeur'` d'une table JS littérale `const NAME = { … };`."""
    match = re.search(rf"const {name} = \{{(.*?)\}};", text, re.DOTALL)
    assert match, f"table {name} introuvable"
    return dict(re.findall(r"""['"]?([\w-]+|[^\s'":,]+)['"]?\s*:\s*'([^']*)'""", match.group(1)))


def test_executer_n_appelle_plus_dot_que_pour_trois_etats() -> None:
    text = EXECUTER.read_text(encoding="utf-8")
    calls = re.findall(r"\bdot\(", text)
    assert len(calls) <= 3, f"{len(calls)} appels à dot() : une catégorie a repris un point"


def test_chaque_type_de_tache_a_son_icone() -> None:
    from grimoire.missions.schemas import TaskType

    labels = _table(EXECUTER.read_text(encoding="utf-8"), "TYPE_LABEL")
    assert set(labels) == {t.value for t in TaskType}
    assert {f"i-task-{t}" for t in labels} <= _symbols()


def test_chaque_profil_de_risque_a_son_glyphe() -> None:
    from grimoire.missions.schemas import RiskProfile

    icons = _table(EXECUTER.read_text(encoding="utf-8"), "RISK_ICON")
    assert set(icons) == {r.value for r in RiskProfile}
    assert set(icons.values()) <= _symbols()


def test_chaque_vivacite_a_sa_forme() -> None:
    icons = _table(EXECUTER.read_text(encoding="utf-8"), "SESSION_ICON")
    # Les quatre valeurs de K5 (#770), écrites par `liveness.py`.
    assert set(icons) == {"vivant", "inactif", "terminé", "inconnu"}
    assert len(set(icons.values())) == 4, "deux vivacités partagent une forme"
    assert set(icons.values()) <= _symbols()


def test_les_hotes_ont_un_pictogramme_generique_sans_hote_invente() -> None:
    from grimoire.bridges.schemas import HostId

    icons = _table(EXECUTER.read_text(encoding="utf-8"), "HOST_ICON")
    assert set(icons) <= {h.value for h in HostId}
    assert set(icons.values()) <= {s for s in _symbols() if s.startswith("i-host-")}


def test_la_vivacite_et_le_projet_ne_sont_plus_des_points() -> None:
    text = EXECUTER.read_text(encoding="utf-8")
    assert "livenessDot" not in text
    assert "dot('acc')" not in text, "l'identité de projet prend un monogramme"
    assert "PRIORITY_DOT" not in text and "RISK_DOT" not in text


def test_source_ecrit_ses_statuts_en_lettres_facon_git() -> None:
    text = SOURCE.read_text(encoding="utf-8")
    body = re.search(r"function fileBadge\(entry\) \{(.*?)\n\}", text, re.DOTALL)
    assert body
    letters = set(re.findall(r"letter: '([A-Z])'", body.group(1)))
    assert letters == {"A", "M", "D", "U", "O"}
    assert "stateIcon" not in body.group(1)
    explorer = re.search(r"function renderExplorer\(ctx, root\) \{(.*?)\n\}", text, re.DOTALL)
    assert explorer and "stateIcon" not in explorer.group(1) and "sr-git" in explorer.group(1)


def test_les_huit_compositions_d_etat_vide_sont_au_sprite() -> None:
    empties = {s for s in _symbols() if s.startswith("i-empty-")}
    assert empties == {
        f"i-empty-{name}" for name in ("tasks", "blueprints", "memory", "sessions", "hooks", "fleet", "inbox", "search")
    }
