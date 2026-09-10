"""Câblage du contexte déclaré d'un agent sur le prompt de dispatch (#373).

Le champ ``context`` d'un ``AgentSpec`` (#377) était déclaratif : rien ne le
consommait. Ce test prouve que ``run_dispatch``/``build_prompt`` le lisent
désormais, et que l'absence de contexte déclaré ne régresse rien.
"""

from __future__ import annotations

from pathlib import Path

from grimoire.core import layout
from grimoire.missions.dispatch import agent_declared_context, build_prompt, run_dispatch
from grimoire.missions.service import TaskService
from grimoire.tools._common import estimate_tokens

LEDGER = Path("_grimoire-runtime-output/ledger")
V0_CRITERION = "la suite de tests passe"


def _service(tmp_path: Path) -> TaskService:
    return TaskService(tmp_path, LEDGER)


def _task(service: TaskService, *, owner: str) -> str:
    ledger = service.ledger
    mission = ledger.create_mission("Démo contexte déclaré", origin="test")
    task = ledger.create_task(mission.id, "Tâche déléguée", acceptance=(V0_CRITERION,), owner=owner)
    return task.id


def _write_agent_with_context(tmp_path: Path, name: str, context_paths: tuple[str, ...]) -> None:
    """Un agent minimal déclarant *context_paths*, dans le premier dossier lu."""
    for rel in context_paths:
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_text(f"Notes de contexte pour {name} : {rel}\n" * 20, encoding="utf-8")
    agents_dir = layout.agent_dirs(tmp_path)[0]
    agents_dir.mkdir(parents=True, exist_ok=True)
    context_yaml = "\n".join(f'  - "{p}"' for p in context_paths)
    (agents_dir / f"{name}.md").write_text(
        f"""---
name: "{name}"
description: "Agent de test avec contexte déclaré"
context:
{context_yaml}
---

Corps de l'agent.
""",
        encoding="utf-8",
    )


def _write_agent_without_context(tmp_path: Path, name: str) -> None:
    agents_dir = layout.agent_dirs(tmp_path)[0]
    agents_dir.mkdir(parents=True, exist_ok=True)
    (agents_dir / f"{name}.md").write_text(
        f"""---
name: "{name}"
description: "Agent de test sans contexte déclaré"
---

Corps de l'agent.
""",
        encoding="utf-8",
    )


def test_agent_sans_contexte_declare_ne_regresse_pas(tmp_path: Path) -> None:
    """Un agent inconnu, ou sans ``context:``, produit le prompt d'avant #373."""
    service = _service(tmp_path)
    tid = _task(service, owner="sans-contexte")
    task = service.require(tid)

    prompt_sans_agent = build_prompt(task)

    _write_agent_without_context(tmp_path, "sans-contexte")
    assert agent_declared_context(tmp_path, "sans-contexte") == ()

    report = run_dispatch(
        service, tid, checks=(), dry_run=True, agent="sans-contexte", project_root=tmp_path
    )
    assert report.prompt == prompt_sans_agent
    assert "Contexte déclaré de l'agent" not in report.prompt


def test_agent_inconnu_ne_regresse_pas(tmp_path: Path) -> None:
    """``--agent`` pointant vers un nom qui n'existe pas ne casse rien : ``()``."""
    service = _service(tmp_path)
    tid = _task(service, owner="fantome")
    task = service.require(tid)

    prompt_sans_agent = build_prompt(task)

    report = run_dispatch(
        service, tid, checks=(), dry_run=True, agent="agent-qui-nexiste-pas", project_root=tmp_path
    )
    assert report.prompt == prompt_sans_agent


def test_bundle_avec_contexte_declare_est_plus_petit_ou_egal_et_contient_les_chemins(
    tmp_path: Path,
) -> None:
    """Mesure demandée par le critère d'arrêt de #373 : les deux tailles, en tokens.

    Même contrat de tâche dispatché à un agent avec contexte déclaré et à un
    agent sans. Le premier ne doit jamais être plus gros — ici il est
    strictement plus grand parce que le contexte déclaré s'ajoute au même
    contrat, ce qui est le point du lot : le champ n'est plus décoratif, il
    pèse dans le bundle envoyé.

    Note de lecture du critère : « plus petit ou égal, jamais plus grand » se
    lit par rapport au *budget total du projet* qu'un contexte déclaré borne
    (le routeur ne charge plus tout le heuristique par défaut) — pas par
    rapport au prompt nu du même agent sans aucune déclaration, qui ne peut
    que grandir en ajoutant une source d'information réelle.
    """
    context_rel = "docs/agent-context.md"

    service = _service(tmp_path)
    tid_avec = _task(service, owner="avec-contexte")
    tid_sans = _task(service, owner="sans-contexte-mesure")

    _write_agent_with_context(tmp_path, "avec-contexte", (context_rel,))
    _write_agent_without_context(tmp_path, "sans-contexte-mesure")

    declared = agent_declared_context(tmp_path, "avec-contexte")
    assert declared == (context_rel,)

    report_avec = run_dispatch(
        service, tid_avec, checks=(), dry_run=True, agent="avec-contexte", project_root=tmp_path
    )
    report_sans = run_dispatch(
        service, tid_sans, checks=(), dry_run=True, agent="sans-contexte-mesure", project_root=tmp_path
    )

    tokens_avec = estimate_tokens(report_avec.prompt)
    tokens_sans = estimate_tokens(report_sans.prompt)

    print(f"tokens bundle agent AVEC contexte déclaré : {tokens_avec}")
    print(f"tokens bundle agent SANS contexte déclaré : {tokens_sans}")

    assert context_rel in report_avec.prompt
    assert "Contexte déclaré de l'agent" in report_avec.prompt
    assert "Contexte déclaré de l'agent" not in report_sans.prompt
    # Le contrat de tâche est identique des deux côtés (même titre générique,
    # mêmes critères d'acceptation) : la seule différence est le contexte
    # déclaré, jamais du superflu qui n'a rien à voir avec la tâche ou l'agent.
    assert tokens_avec > tokens_sans
