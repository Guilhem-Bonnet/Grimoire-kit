"""Aligner un projet sur le kit installé, depuis l'UI locale.

Mettre à jour un projet est un flow, pas un `up` nu (issue #490) : ce module
lance ``grimoire upgrade-flow run`` — sauvegarde, aperçu, orphelins,
application, propositions (overrides en dérive, fiches mémoire non
raccordées, besoins/hôtes non déclarés), vérification — plutôt que
d'appeler `grimoire up` seul. Ce module l'expose aux hôtes locaux sans le
réimplémenter — le jour où la commande change, l'UI suit.

Deux règles, parce que cette action écrit dans le dépôt de quelqu'un :

* **l'aperçu d'abord** — ``dry_run=True`` par défaut, pour que l'UI puisse
  montrer ce qui changerait avant que qui que ce soit décide. Un aperçu
  s'arrête après le nœud `preview` (``--dry-run`` du flow) ; la réponse
  porte le contenu du rapport (``_grimoire-output/upgrade/<date>/preview.md``)
  pour que l'UI l'affiche tel quel, jamais un résumé reformulé ;
* **le sous-processus, pas l'import** — la commande touche au système de
  fichiers, écrit sur des consoles Rich et peut sortir en erreur. L'isoler
  évite qu'un ``typer.Exit`` remonte dans le serveur et le tue.

Une confirmation (``dry_run=False``) lance le flow complet sous
``--executor interactive`` (mécanique, jamais un fournisseur) : il s'arrête
toujours, non décidé, au nœud `destructive` — cette commande ne soumet
jamais de décision de checkpoint à la place de qui que ce soit. Les nœuds de
jugement (overrides, memory, needs-hosts) n'écrivent jamais qu'une
proposition ; la réponse porte le compte des propositions en attente, pour
que l'UI puisse renvoyer vers Piloter plutôt que prétendre avoir décidé à sa
place.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

#: Un alignement complet lance sauvegarde, aperçu, orphelins, application,
#: propositions et vérification — plus de nœuds qu'un `up` nu, même borne
#: généreuse qu'avant (#490).
UPDATE_TIMEOUT_S = 600

#: L'ordre fixe des neuf nœuds du blueprint `project-upgrade` — jamais un
#: autre (orphans avant apply, cf. la migration du 2026-09-11) — dupliqué ici
#: en dur plutôt qu'importé du blueprint : c'est la forme d'affichage d'une
#: UI, pas une décision d'exécution, et ce module n'a pas besoin de charger
#: le moteur de flow pour la connaître.
NODE_ORDER: tuple[str, ...] = (
    "backup", "preview", "orphans", "apply",
    "overrides", "memory", "needs-hosts",
    "verify", "destructive",
)

#: Nœuds V1 (jugement, docstring de `cmd_upgrade_flow.upgrade_flow_run`) :
#: leur seul effet, une fois « fait », est d'avoir écrit une proposition —
#: jamais une modification directe. L'UI (issue #506) le montre comme un
#: statut à part (« proposition ») plutôt que comme un « fait » identique à
#: `backup`/`apply`, qui ont, eux, réellement changé quelque chose.
_JUDGEMENT_NODES = frozenset({"overrides", "memory", "needs-hosts"})


def _stopped_at_node(raw: str | None) -> str | None:
    """Le nœud cité par `stopped_at`, ou ``None`` s'il n'en nomme pas un.

    `stopped_at` porte parfois une note lisible plutôt qu'un identifiant nu —
    ``"orphans (--dry-run : arrêté après preview)"`` pour un aperçu qui
    s'arrête avant même de lancer ce nœud. Le premier mot reste toujours le
    nœud concerné : c'est tout ce que :func:`_node_statuses` a besoin de
    savoir pour décider que ce nœud (et ceux d'après) n'ont jamais tourné.
    """
    if not raw:
        return None
    head = raw.split(" ", 1)[0]
    return head if head in NODE_ORDER else None


def _node_statuses(
    done: list[str], stopped_at_raw: str | None, failed_node: str | None
) -> list[dict[str, str]]:
    """Un statut par nœud du blueprint, dans l'ordre fixe — jamais déduit du
    texte libre `output`, toujours de `done`/`stopped_at`/`failed_node`, les
    seuls champs structurés que le flow rend (issue #506).

    - ``fait`` — un nœud V0/V2 mécanique que `done` porte.
    - ``proposition`` — un nœud V1 que `done` porte : son seul effet est une
      proposition écrite, jamais une modification directe.
    - ``erreur`` — le nœud que la boucle de `run` a quitté en levant ou en se
      voyant refuser par `engine.resume` (`_fail_run`, `cmd_upgrade_flow.py`).
    - ``checkpoint en attente`` — `destructive` (V2), quand `run` s'y arrête
      sans jamais avoir été décidé à la place de l'humain.
    - ``sauté`` — tout le reste : jamais atteint, aperçu arrêté plus tôt, ou
      après le nœud en erreur.
    """
    done_set = set(done)
    stopped_node = _stopped_at_node(stopped_at_raw)
    statuses = []
    for node in NODE_ORDER:
        if node == failed_node:
            status = "erreur"
        elif node in done_set:
            status = "proposition" if node in _JUDGEMENT_NODES else "fait"
        elif node == "destructive" and node == stopped_node:
            status = "checkpoint en attente"
        else:
            status = "sauté"
        statuses.append({"id": node, "status": status})
    return statuses

#: Un verrou par projet. Le serveur est multi-thread et le bouton est
#: cliquable : deux ``grimoire up`` concurrents écriraient les mêmes fichiers
#: en même temps. La commande est idempotente, pas réentrante.
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(path: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(path, threading.Lock())


def update_project(project_root: Path, *, dry_run: bool = True) -> dict[str, Any]:
    """Exécute ``grimoire up`` sur le projet et rend son compte rendu.

    Ne lève pas sur échec de la commande : un projet qui refuse de s'aligner
    est un résultat à afficher, pas une panne du serveur.
    """
    root = project_root.expanduser().resolve()
    if not root.is_dir():
        msg = f"pas un dossier : {root}"
        raise FileNotFoundError(msg)

    lock = _lock_for(str(root))
    if not lock.acquire(blocking=False):
        return {
            "ok": False, "dryRun": dry_run, "path": str(root),
            "error": "une mise à jour de ce projet est déjà en cours", "output": "",
        }
    try:
        return _run_upgrade_flow(root, dry_run=dry_run)
    finally:
        lock.release()


def _run_upgrade_flow(root: Path, *, dry_run: bool) -> dict[str, Any]:
    cmd = [sys.executable, "-m", "grimoire", "upgrade-flow", "run", "--project-root", str(root), "--json"]
    if dry_run:
        cmd.append("--dry-run")
    else:
        cmd.extend(["--executor", "interactive"])
    try:
        result = subprocess.run(
            cmd,
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=UPDATE_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False, "dryRun": dry_run, "path": str(root),
            "error": f"délai dépassé ({UPDATE_TIMEOUT_S} s)", "output": "",
        }
    except OSError as exc:
        # Le processus n'a même pas démarré (interpréteur absent, descripteurs
        # épuisés…). C'est un échec à rapporter comme un autre : le laisser
        # remonter donnerait un 500 sans explication à une UI qui attend un
        # compte rendu.
        return {
            "ok": False, "dryRun": dry_run, "path": str(root),
            "error": f"lancement impossible : {exc}", "output": "",
        }

    output = (result.stdout or "") + (result.stderr or "")
    ok = result.returncode == 0
    # `--json` rend une unique ligne JSON sur stdout (`grimoire.cli.
    # cmd_upgrade_flow._fail`/`upgrade_flow_run`) ; une sortie qui ne l'est
    # pas (crash avant le premier `typer.echo`, mock de test) ne doit jamais
    # faire échouer CE rapport, seulement laisser ses champs dérivés vides.
    payload: dict[str, Any] = {}
    try:
        payload = json.loads((result.stdout or "").strip())
    except (ValueError, TypeError):
        payload = {}

    done = payload.get("done") or []
    stopped_at = payload.get("stopped_at")
    report: dict[str, Any] = {
        "ok": ok,
        "dryRun": dry_run,
        "path": str(root),
        "code": result.returncode,
        "runId": payload.get("run_id"),
        "done": done,
        "stoppedAt": stopped_at,
        # Statut par nœud (issue #506) — calculé que le run ait réussi ou
        # échoué : `_fail_run` (cmd_upgrade_flow.py) porte `done`/`failed_node`
        # même sur un refus, pour qu'une UI montre où ça s'est arrêté plutôt
        # que juste un message d'erreur.
        "nodes": _node_statuses(done, stopped_at, payload.get("failed_node")),
        "backupPath": payload.get("backup_path"),
        "repairsProposed": payload.get("repairs_proposed") or 0,
        # Le compte rendu brut tient en quelques lignes ; on borne quand
        # même, une UI n'a pas à recevoir un journal entier.
        "output": output.strip()[-8000:],
        "error": None if ok else (payload.get("error") or "grimoire upgrade-flow a échoué"),
    }
    if ok:
        report["preview"] = _read_report(root, "preview.md")
        if not dry_run:
            report["report"] = _read_report(root, "report.md")
            report["proposals"] = _pending_proposals(root)
    return report


def _read_report(root: Path, name: str) -> str | None:
    """Le contenu de ``_grimoire-output/upgrade/<date>/<name>``, ou ``None``.

    Jamais reformulé — l'UI affiche ce texte tel quel (même doctrine que
    ``docs/upgrade.md`` : le rapport dit ce qui a été vérifié, pas un résumé
    qui pourrait diverger de ce que le flow a réellement écrit).
    """
    try:
        from grimoire.tools.project_upgrade import run_output_dir

        path = run_output_dir(root) / name
    except Exception:  # un rapport manquant n'est jamais l'erreur à remonter
        return None
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _pending_proposals(root: Path) -> list[dict[str, str]]:
    """Les propositions encore ``pending`` — jamais resynchronisées ici.

    Lecture pure : la synchro (déclencheur de non-choix, #395) et l'écriture
    (``accept_proposal``) restent la porte de ``grimoire.proposals`` /
    Piloter, jamais dupliquées dans cette route. Un échec de lecture rend une
    liste vide plutôt qu'un 500 — la mise à jour elle-même a réussi.
    """
    try:
        from grimoire.proposals import list_proposals

        proposals = list_proposals(root, sync=False)
    except Exception:  # une lecture annexe ne doit jamais masquer le résultat du flow
        return []
    return [
        {"slug": p.slug, "artifactType": p.artifact_type, "specialty": p.specialty}
        for p in proposals
        if p.status == "pending"
    ]
