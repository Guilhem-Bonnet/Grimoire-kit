"""Capsule de reprise : l'état de travail d'une session, borné, relu à la reprise.

Avant ce module, la capsule de compaction ne portait que l'id de tâche, le
profil et les gates — et aucun code ne la relisait. Une session compactée
(ou reprise) repartait donc sans objectif, sans liste de fichiers, sans
dernier test. Ici, la capsule devient un état de reprise de ``MAX_CAPSULE_CHARS``
caractères au plus, assemblé uniquement depuis des sources que le kit tient déjà :

- le Mission Ledger (titre, état, consignes humaines, incidents ouverts) ;
- le journal de preuve de la tâche (``evidence-log.jsonl`` : fichiers écrits,
  dernier test, PR citées dans les commandes) ;
- la mémoire de session (``memory-<session>.json`` : le dernier message humain) ;
- ``git`` (branche courante, modifications non commitées) — deux appels bornés.

Aucune collecte nouvelle : rien n'est enregistré de plus à chaque appel d'outil.
Tout ce module est best-effort : une source illisible disparaît de la capsule,
elle ne casse ni un hook ni la commande ``grimoire host handoff``.

Le texte est nettoyé deux fois, à l'écriture et à la relecture : secrets
reconnaissables, chemin de la zone de contrôle de Grimoire (sous le HOME),
chemins de fichiers de secrets.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from grimoire.core.standard_generation import CONTEXT_DIR

__all__ = [
    "CAPSULE_FILENAME",
    "FRESH_SECONDS",
    "MAX_CAPSULE_CHARS",
    "build_resume_state",
    "capsule_path",
    "latest_memory_session",
    "read_resume_capsule",
    "scrub",
    "write_capsule",
]

#: Taille maximale de l'état de reprise, en caractères (~400 tokens).
MAX_CAPSULE_CHARS = 1500
CAPSULE_FILENAME = "compaction-capsule.md"
#: Une capsule plus vieille que cela n'est plus offerte à une session neuve :
#: l'état de la veille n'est pas celui d'aujourd'hui. Compaction et reprise
#: (``source=compact|resume``) ne regardent pas l'âge : la session est la même.
FRESH_SECONDS = 24 * 3600
#: Plafond de lecture d'un fichier de capsule : au-delà, ce n'est plus la nôtre.
_MAX_FILE_BYTES = 16 * 1024
#: Fin du journal de preuve relue : un journal de session longue ne se parcourt pas en entier.
_JOURNAL_TAIL_BYTES = 96 * 1024
_MAX_FILES_LISTED = 6
_FLOW_PREFIX = "[Grimoire — flow actif]"
_GIT_TIMEOUT_S = 3

# Le dossier de contrôle de Grimoire ne doit jamais atterrir dans un texte injecté.
_CONTROL_RE = re.compile(r"[^\s'\"`]*\.grimoire" + r"[/\\]" + "control" + r"[^\s'\"`]*")
_SECRET_ASSIGN_RE = re.compile(
    r"(?i)\b(api[_-]?key|secret|token|passw(?:or)?d|authorization|client[_-]?secret)\b(\s*[=:]\s*)\S+"
)
_SECRET_TOKEN_RE = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"
    r"|xox[baprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}|glpat-[A-Za-z0-9_-]{16,}|AIza[0-9A-Za-z_-]{30,})\b"
)
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}")
_BLOB_RE = re.compile(r"\b[A-Za-z0-9+/_=-]{48,}\b")
_PR_RE = re.compile(r"\bgh\s+pr\s+(?:create|view|ready|merge|checks|comment|edit|status|diff)\b[^|;&\n]*?\b(\d{1,6})\b")
_PR_URL_RE = re.compile(r"/pull/(\d{1,6})\b")


def scrub(text: str) -> str:
    """Retire d'un texte ce qui ne doit pas voyager dans la capsule."""
    text = _CONTROL_RE.sub("[zone de contrôle]", text)
    text = _SECRET_ASSIGN_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}***", text)
    text = _BEARER_RE.sub("Bearer ***", text)
    text = _SECRET_TOKEN_RE.sub("***", text)
    text = _BLOB_RE.sub("***", text)
    try:
        home = str(Path.home())
    except (RuntimeError, KeyError):
        home = ""
    if len(home) > 1:
        text = text.replace(home, "~")
    return text


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: max(limit - 1, 0)].rstrip() + "…"


def capsule_path(project_root: Path, task_id: str) -> Path:
    return project_root / CONTEXT_DIR / task_id / CAPSULE_FILENAME


def write_capsule(project_root: Path, task_id: str, text: str) -> Path:
    """Écrit la capsule de *task_id* (``OSError`` si le disque refuse). Retourne le chemin."""
    dest = capsule_path(project_root, task_id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(scrub(text).rstrip() + "\n", encoding="utf-8")
    return dest


def latest_memory_session(project_root: Path) -> str:
    """Le ``session_id`` de la mémoire de session la plus récente, ``""`` s'il n'y en a pas."""
    runs = project_root / "_grimoire-output" / ".runs"
    try:
        found = sorted(runs.glob("memory-*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return ""
    return found[0].stem.removeprefix("memory-") if found else ""


# --------------------------------------------------------------------- sources


def _journal_events(project_root: Path, task_id: str) -> list[dict[str, Any]]:
    from grimoire.core.standard_checks.evidence_journal import evidence_log_relpath

    path = project_root / evidence_log_relpath(task_id)
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(size - _JOURNAL_TAIL_BYTES, 0))
            raw = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    lines = raw.splitlines()
    if size > _JOURNAL_TAIL_BYTES and lines:
        lines = lines[1:]  # première ligne coupée au milieu
    events: list[dict[str, Any]] = []
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def _relative_files(project_root: Path, events: list[dict[str, Any]]) -> tuple[list[str], int]:
    """Fichiers écrits, du plus récent au plus ancien, sans doublon ni fichier hors projet ou secret."""
    from grimoire.hosts.secrets import secret_patterns

    secret_res = [re.compile(p) for p in secret_patterns()]
    root = project_root.resolve()
    seen: list[str] = []
    for event in reversed(events):
        if event.get("type") != "file_write":
            continue
        raw = str(event.get("path") or "")
        if not raw:
            continue
        candidate = Path(raw)
        if candidate.is_absolute():
            try:
                raw = candidate.resolve().relative_to(root).as_posix()
            except (OSError, ValueError):
                continue  # hors du projet : ni nommé ni compté
        if any(r.search(raw) for r in secret_res) or _CONTROL_RE.search(raw):
            continue
        if raw not in seen:
            seen.append(raw)
    return seen[:_MAX_FILES_LISTED], max(len(seen) - _MAX_FILES_LISTED, 0)


def _last_test(events: list[dict[str, Any]]) -> tuple[str, int | None] | None:
    for event in reversed(events):
        if event.get("type") == "test_run":
            code = event.get("exit_code")
            return str(event.get("command") or ""), code if isinstance(code, int) else None
    return None


def _pull_requests(events: list[dict[str, Any]]) -> list[str]:
    numbers: list[str] = []
    for event in events:
        if event.get("type") not in ("bash", "test_run"):
            continue
        command = str(event.get("command") or "")
        for match in (*_PR_RE.finditer(command), *_PR_URL_RE.finditer(command)):
            number = match.group(1)
            if number in numbers:
                numbers.remove(number)
            numbers.append(number)
    return [f"#{n}" for n in numbers[-3:]]


def _git(project_root: Path, *args: str) -> str:
    try:
        done = subprocess.run(
            ["git", "-C", str(project_root), *args],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def _git_line(project_root: Path) -> str:
    branch = _git(project_root, "branch", "--show-current")
    if not branch:
        return ""
    dirty = len(_git(project_root, "status", "--porcelain", "--untracked-files=no").splitlines())
    return f"{branch}" + (f", {dirty} modification(s) non commitée(s)" if dirty else ", arbre propre")


def _ledger_view(project_root: Path, task_id: str) -> dict[str, Any]:
    """Titre, état, consigne humaine non lue, incidents ouverts ; ``{}`` sans ledger ni tâche."""
    try:
        from grimoire.missions.schemas import IncidentStatus
        from grimoire.missions.service import TaskService

        service = TaskService(project_root)
        if not service.has_ledger:
            return {}
        task = service.ledger.get_task(task_id)
        if task is None:
            return {}
        humans = [d for d in task.directives if d.is_human]
        incidents = [
            i for i in service.ledger.list_incidents(task_id) if i.status is IncidentStatus.OPEN
        ]
        return {
            "title": task.title,
            "status": task.status_label,
            "notes": [d.text for d in humans[-2:]],
            "unread": [d.text for d in humans if not d.acknowledged_at][-1:],
            "incidents": [f"{i.kind}" for i in incidents[:2]],
            "acceptance": task.acceptance[0] if task.acceptance else "",
        }
    except Exception:
        return {}


def _objective(project_root: Path, session_id: str) -> str:
    if not session_id:
        return ""
    try:
        from grimoire.hosts.decisions.session_memory import load_session_memory

        messages = load_session_memory(project_root, session_id).user_messages
    except Exception:
        return ""
    if not messages:
        return ""
    substantial = [m for m in messages if len(m.strip()) >= 40]
    return (substantial or messages)[-1]


# ----------------------------------------------------------------- assemblage


def build_resume_state(
    project_root: Path,
    task_id: str,
    *,
    session_id: str = "",
    gates_ok: bool | None = None,
    gates_hint: str = "",
    budget: int = MAX_CAPSULE_CHARS,
) -> str:
    """L'état de reprise de *task_id*, au plus *budget* caractères ; ``""`` si aucune source ne répond.

    Règle de la « prochaine étape » (déterministe, première qui s'applique) :
    consigne humaine non accusée > dernier test rouge > gates rouges > premier
    critère d'acceptation de la tâche. Sans aucune de ces sources, pas de ligne.
    """
    ledger = _ledger_view(project_root, task_id)
    events = _journal_events(project_root, task_id)
    files, more = _relative_files(project_root, events)
    test = _last_test(events)
    prs = _pull_requests(events)
    git = _git_line(project_root)
    objective = _objective(project_root, session_id)

    state: list[str] = []
    if ledger.get("title"):
        state.append(f"« {_clip(str(ledger['title']), 90)} » ({ledger['status']})")
    if git:
        state.append(f"branche {git}")
    if prs:
        state.append("PR " + ", ".join(prs))

    next_step = ""
    if ledger.get("unread"):
        next_step = f"traiter la consigne : {_clip(str(ledger['unread'][0]), 160)}"
    elif test is not None and test[1] not in (None, 0):
        next_step = f"corriger puis relancer `{_clip(test[0], 120)}`"
    elif gates_ok is False:
        next_step = f"compléter les preuves ({_clip(gates_hint, 120)})" if gates_hint else "compléter les preuves"
    elif ledger.get("acceptance"):
        next_step = f"vérifier le critère : {_clip(str(ledger['acceptance']), 140)}"

    candidates: list[str] = []
    if state:
        candidates.append("État de reprise : " + " ; ".join(state))
    if objective:
        candidates.append("Objectif (dernier message humain) : " + _clip(objective, 220))
    if next_step:
        candidates.append("Prochaine étape : " + next_step)
    if files:
        candidates.append("Fichiers touchés : " + ", ".join(files) + (f" (+{more})" if more else ""))
    if test is not None:
        verdict = "exit ?" if test[1] is None else f"exit {test[1]}"
        candidates.append(f"Dernier test : `{_clip(test[0], 120)}` → {verdict}")
    notes = [*ledger.get("notes", []), *(f"incident ouvert {k}" for k in ledger.get("incidents", []))]
    if notes:
        candidates.append("Consignes et pièges : " + " | ".join(_clip(str(n), 120) for n in notes))

    out: list[str] = []
    used = 0
    for line in candidates:
        line = scrub(line)
        cost = len(line) + (1 if out else 0)
        if used + cost <= budget:
            out.append(line)
            used += cost
        elif (room := budget - used - 1) >= 80:
            out.append(_clip(line, room))
            break
    return "\n".join(out)


# --------------------------------------------------------------------- relecture


def read_resume_capsule(
    project_root: Path,
    task_id: str,
    *,
    source: str,
    task_is_real: bool,
    live_flow: bool = False,
    now: float | None = None,
) -> str:
    """La capsule de *task_id* prête à être injectée, ``""`` quand il n'y a rien à reprendre.

    Règle : ``source`` ``compact`` ou ``resume`` (même session) → toujours, quel
    que soit son âge. Toute autre source (``startup``, ``clear``, hôte qui n'en
    dit pas) = session neuve : seulement si la tâche active est une vraie tâche
    (pas le repli ``bootstrap``) et la capsule a moins de :data:`FRESH_SECONDS`.
    *live_flow* : les lignes de flow de la capsule sont retirées, la session
    start les relit en direct (une capsule ancienne dirait un nœud périmé).
    """
    path = capsule_path(project_root, task_id)
    try:
        stat = path.stat()
        if not path.is_file() or stat.st_size > _MAX_FILE_BYTES:
            return ""
        body = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    age = (now if now is not None else time.time()) - stat.st_mtime
    if source not in ("compact", "resume") and (not task_is_real or age > FRESH_SECONDS):
        return ""
    lines = [ln for ln in body.splitlines() if ln.strip()]
    if live_flow:
        lines = [ln for ln in lines if not ln.startswith(_FLOW_PREFIX)]
    text = scrub("\n".join(lines)).strip()
    if not text:
        return ""
    if len(text) > MAX_CAPSULE_CHARS + 400:  # gouvernance incluse ; au-delà, fichier trafiqué
        text = text[: MAX_CAPSULE_CHARS + 400].rstrip() + "…"
    return f"[Grimoire — reprise de session]\n{text}"
