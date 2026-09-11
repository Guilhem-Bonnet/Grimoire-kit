"""Quelle tâche une session porte — la règle de résolution (#138).

Ordre : ``GRIMOIRE_TASK_ID``, puis le claim actif du Mission Ledger (restreint à
``GRIMOIRE_ACTOR`` s'il est nommé), puis l'unique carte ``in_progress`` du board,
puis ``bootstrap``. Une ambiguïté saute le niveau au lieu de deviner.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from grimoire.core import standard_state
from grimoire.core.standard_generation import STANDARD_PROFILE_FILE
from grimoire.core.standard_state import (
    LEDGER_RELPATH,
    TASK_BOARD_RELPATH,
    active_profile_id,
    active_task_id,
    claimed_task_ids,
    invalidate_cache,
    resolve_active_task,
)
from grimoire.missions.ledger import MissionLedger
from grimoire.missions.schemas import TaskState

_CACHE_RELPATH = Path("_grimoire-output") / ".runs" / "standard-state-cache.json"


def _ledger(root: Path) -> MissionLedger:
    return MissionLedger(root / LEDGER_RELPATH)


def _tache(ledger: MissionLedger, titre: str) -> str:
    missions = ledger.list_missions()
    mission = missions[0] if missions else ledger.create_mission(title="Travaux", origin="test")
    task = ledger.create_task(mission.id, titre, acceptance=("ok",), owner="x")
    ledger.transition_task(task.id, TaskState.READY)
    return task.id


def _board(root: Path, *statuts: tuple[str, str]) -> None:
    lignes = ["tasks:"]
    for tid, statut in statuts:
        lignes += [f"  - task_id: {tid}", f"    status: {statut}"]
    (root / TASK_BOARD_RELPATH).parent.mkdir(parents=True, exist_ok=True)
    (root / TASK_BOARD_RELPATH).write_text("\n".join(lignes) + "\n", encoding="utf-8")


def test_sans_rien_c_est_bootstrap(tmp_path: Path) -> None:
    assert resolve_active_task(tmp_path, env={}).source == "bootstrap"
    assert active_task_id(tmp_path, env={}) == "bootstrap"


def test_lire_ne_cree_pas_de_ledger(tmp_path: Path) -> None:
    """Le hook tourne à chaque appel d'outil : il ne doit pas semer un dossier
    de ledger dans chaque projet qu'il inspecte."""
    resolve_active_task(tmp_path, env={})
    assert not (tmp_path / LEDGER_RELPATH).exists()


def test_le_claim_du_ledger_prime_sur_le_board(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    tid = _tache(ledger, "Une")
    ledger.claim_task(tid, "claude", "local")
    _board(tmp_path, ("autre", "in_progress"))
    active = resolve_active_task(tmp_path, env={})
    assert (active.task_id, active.source) == (tid, "ledger_claim")


def test_une_tache_en_cours_compte_comme_reclamee(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    tid = _tache(ledger, "Une")
    ledger.claim_task(tid, "claude", "local")
    ledger.transition_task(tid, TaskState.RUNNING)
    assert active_task_id(tmp_path, env={}) == tid


def test_deux_claims_sont_ambigus_et_le_board_tranche(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    a, b = _tache(ledger, "A"), _tache(ledger, "B")
    ledger.claim_task(a, "claude", "local")
    ledger.claim_task(b, "copilot", "local")
    _board(tmp_path, (b, "in_progress"))
    active = resolve_active_task(tmp_path, env={})
    assert (active.task_id, active.source) == (b, "board")


def test_l_acteur_nomme_ne_voit_que_ses_claims(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    a, b = _tache(ledger, "A"), _tache(ledger, "B")
    ledger.claim_task(a, "claude", "local")
    ledger.claim_task(b, "copilot", "local")
    assert claimed_task_ids(tmp_path) == [a, b]
    assert claimed_task_ids(tmp_path, actor="copilot") == [b]
    assert active_task_id(tmp_path, env={"GRIMOIRE_ACTOR": "copilot"}) == b
    # Un acteur sans claim ne se fait pas prêter celui d'un autre.
    assert active_task_id(tmp_path, env={"GRIMOIRE_ACTOR": "gemini"}) == "bootstrap"


def test_l_environnement_prime_sur_tout(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    tid = _tache(ledger, "Une")
    ledger.claim_task(tid, "claude", "local")
    active = resolve_active_task(tmp_path, env={"GRIMOIRE_TASK_ID": "sprint-9"})
    assert (active.task_id, active.source) == ("sprint-9", "env")


def test_une_tache_close_n_est_plus_courante(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    tid = _tache(ledger, "Une")
    ledger.claim_task(tid, "claude", "local")
    ledger.transition_task(tid, TaskState.READY)
    assert active_task_id(tmp_path, env={}) == "bootstrap"


def test_un_ledger_illisible_ne_casse_pas_le_hook(tmp_path: Path) -> None:
    (tmp_path / LEDGER_RELPATH).mkdir(parents=True)
    (tmp_path / LEDGER_RELPATH / "events.jsonl").write_text("{pas du json\n", encoding="utf-8")
    assert active_task_id(tmp_path, env={}) == "bootstrap"


# ── Cache d'état du standard (issue #419, second lot) ────────────────────────
#
# ``active_profile_id``/``resolve_active_task`` dérivaient leur réponse d'un
# parse YAML (``ruamel.yaml``) à chaque appel — ~8-9 ms rien que pour la
# première charge du parseur dans un process, payés à chaque appel d'outil.
# Le cache JSON sous ``_grimoire-output/.runs/`` évite ce parse tant que
# l'empreinte (mtime + taille) du YAML source n'a pas bougé.


def _ecrire_profil(root: Path, profile_id: str) -> None:
    path = root / STANDARD_PROFILE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"profile: {profile_id}\n", encoding="utf-8")


def test_le_cache_est_cree_au_premier_appel(tmp_path: Path) -> None:
    _ecrire_profil(tmp_path, "governed")
    assert not (tmp_path / _CACHE_RELPATH).is_file()

    assert active_profile_id(tmp_path) == "governed"

    cache_path = tmp_path / _CACHE_RELPATH
    assert cache_path.is_file()
    data = json.loads(cache_path.read_text(encoding="utf-8"))
    assert data["entries"]["profile_id"]["value"] == "governed"


def test_un_cache_a_jour_evite_toute_relecture_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Empreinte inchangée -> ``_load_mapping`` (donc ``ruamel``) n'est pas rappelé."""
    _ecrire_profil(tmp_path, "governed")
    assert active_profile_id(tmp_path) == "governed"  # premier appel : cache froid, écrit le cache

    calls: list[Path] = []
    original = standard_state._load_mapping

    def _counting_load_mapping(path: Path) -> dict[str, object]:
        calls.append(path)
        return original(path)

    monkeypatch.setattr(standard_state, "_load_mapping", _counting_load_mapping)

    assert active_profile_id(tmp_path) == "governed"
    assert calls == [], f"cache chaud : _load_mapping a quand même été appelé sur {calls}"


def test_source_modifiee_regenere_le_cache_et_change_la_decision(tmp_path: Path) -> None:
    _ecrire_profil(tmp_path, "starter")
    assert active_profile_id(tmp_path) == "starter"

    # Une taille différente change forcément l'empreinte [mtime_ns, size],
    # indépendamment de la granularité de l'horloge du système de fichiers.
    _ecrire_profil(tmp_path, "production")
    assert active_profile_id(tmp_path) == "production"

    data = json.loads((tmp_path / _CACHE_RELPATH).read_text(encoding="utf-8"))
    assert data["entries"]["profile_id"]["value"] == "production"


def test_cache_corrompu_retombe_sur_la_lecture_yaml(tmp_path: Path) -> None:
    _ecrire_profil(tmp_path, "governed")
    cache_path = tmp_path / _CACHE_RELPATH
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text("{ceci n'est pas du json", encoding="utf-8")

    assert active_profile_id(tmp_path) == "governed"

    # La lecture a aussi réparé le cache : il redevient un JSON valide.
    data = json.loads(cache_path.read_text(encoding="utf-8"))
    assert data["entries"]["profile_id"]["value"] == "governed"


def test_cache_au_mauvais_format_retombe_sur_la_lecture_yaml(tmp_path: Path) -> None:
    """Un JSON valide mais de forme inattendue (version absente, ``entries`` non-dict) vaut absence."""
    _ecrire_profil(tmp_path, "governed")
    cache_path = tmp_path / _CACHE_RELPATH
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps({"entries": "pas une table"}), encoding="utf-8")

    assert active_profile_id(tmp_path) == "governed"


def test_cache_absent_est_relu_et_cree(tmp_path: Path) -> None:
    _ecrire_profil(tmp_path, "governed")
    assert not (tmp_path / _CACHE_RELPATH).exists()
    assert active_profile_id(tmp_path) == "governed"
    assert (tmp_path / _CACHE_RELPATH).is_file()


def test_le_board_beneficie_du_meme_cache(tmp_path: Path) -> None:
    """``resolve_active_task`` (branche board) suit la même règle d'empreinte."""
    _board(tmp_path, ("T-1", "in_progress"))
    active = resolve_active_task(tmp_path, env={})
    assert (active.task_id, active.source) == ("T-1", "board")

    _board(tmp_path, ("T-2", "in_progress"))
    active = resolve_active_task(tmp_path, env={})
    assert (active.task_id, active.source) == ("T-2", "board")


def test_invalidate_cache_purge_le_fichier(tmp_path: Path) -> None:
    _ecrire_profil(tmp_path, "governed")
    active_profile_id(tmp_path)
    assert (tmp_path / _CACHE_RELPATH).is_file()

    invalidate_cache(tmp_path)
    assert not (tmp_path / _CACHE_RELPATH).exists()


def test_invalidate_cache_sur_projet_sans_cache_ne_leve_pas(tmp_path: Path) -> None:
    invalidate_cache(tmp_path)  # ne doit pas lever, même sans _grimoire-output/
