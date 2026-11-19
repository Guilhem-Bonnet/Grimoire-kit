"""Ce que le compteur d'écritures de la session compte — et ce qu'il ne compte pas.

Le ``Stop`` d'un profil bloquant refuse une clôture hors tâche dès que la session a écrit
(:func:`grimoire.hosts.decisions.evidence_gate.decide_evidence_gate`). Il lit le compteur que
``PostToolUse`` tient (:func:`grimoire.policies.session_state.note_session_mutation`) : un
compteur gonflé est un refus à tort, et chaque refus coûte un tour entier à l'utilisateur.

Mesuré sur un poste réel : 185 refus en 14 jours, et dans une session qui n'avait fait que
lire et lancer des sous-agents de lecture, un compteur monté de 4 à 65. Ce qui comptait :

- toute commande d'un sous-agent que le classement lecture seule ne reconnaît pas
  (``find | xargs grep``, ``pytest``, ``git fetch``…) ;
- les redirections vers le brouillon hors projet (``cat > /…/scratchpad/x <<EOF``) — seule
  l'écriture par outil (``Write``/``Edit``) était déjà filtrée hors projet (#875) ;
- les commandes de vérification (tests, lint), qui ne sont pas des écritures ;
- les outils de tenue de session sans fichier (``TodoWrite``), classés écriture par leur nom.

La règle retenue (:func:`counts_as_session_write`) :

1. jamais une action classée lecture seule ;
2. une écriture par outil compte si au moins une de ses cibles est dans le projet ; sans
   aucune cible (``TodoWrite``), elle ne compte pas ;
3. une commande qui écrit, de façon prouvée, dans le projet compte toujours (``cp /hors/a
   src/a``) ; sinon une commande de la session principale compte, sauf une commande de vérification
   reconnue (même partage que le gate « fini », :mod:`.done_gate`) ou une commande dont
   toutes les cibles identifiables sont hors du projet (même règle que le gate « fini ») ;
   ce qu'on ne sait pas lire (``make``, ``./script``) compte : la session elle-même reste
   jugée en échec fermé ;
4. une commande d'un sous-agent ne compte que si elle écrit, de façon prouvée, dans le
   projet (:func:`grimoire.hosts.decisions.write_proof.proves_write`) ou si elle est
   destructive. Un sous-agent qui lit ne compte donc jamais ; ses écritures par outil
   suivent la règle 2.

Analyse de texte et résolution de chemins seulement ; jamais une exception vers le hook.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from grimoire.policies.schemas import ActionKind, MutationClass

if TYPE_CHECKING:
    from grimoire.hosts.decisions._shared import HookInput
    from grimoire.hosts.decisions.tool_facts import ToolFacts

#: Valeur d'un mot que l'analyse de :mod:`.write_proof` ne sait pas lire (substitution de commande).
_UNKNOWN = "\x00"


def writes_outside_project(hook: HookInput, facts: ToolFacts) -> bool:
    """Issue #875 : vrai si l'écriture par outil n'a que des cibles hors de ``project_root``.

    Brouillon sous le répertoire temporaire, mémoire de l'utilisateur : ce n'est pas du
    travail du projet, ni pour le compteur de ``Stop`` ni pour le rappel de preuve. Une cible
    relative se lit depuis ``project_root`` ; une cible illisible reste comptée (fail-closed).
    """
    if facts.kind is not ActionKind.FILE_WRITE or not facts.targets:
        return False
    try:
        root = hook.project_root.resolve()
        for target in facts.targets:
            path = Path(target).expanduser()
            if (path if path.is_absolute() else root / path).resolve().is_relative_to(root):
                return False
        return True
    except Exception:
        return False


def is_session_bookkeeping(facts: ToolFacts) -> bool:
    """Un outil « d'écriture » sans aucun fichier cible : la liste de tâches de l'hôte (``TodoWrite``)."""
    return facts.kind is ActionKind.FILE_WRITE and not facts.targets


class _ProjectTarget:
    """Le projet vu par :mod:`.write_proof` : un mot le désigne si son chemin résolu est dedans."""

    def __init__(self, root: Path, cwd: Path) -> None:
        self.root, self.cwd = root, cwd

    def _path(self, word: str, cd: tuple[str, ...]) -> Path | None:
        if _UNKNOWN in word or any(_UNKNOWN in d for d in cd):
            return None
        base = self.cwd
        for directory in cd:
            base = base / Path(directory).expanduser()
        path = Path(word).expanduser()
        return (path if path.is_absolute() else base / path).resolve()

    def names(self, word: str, cd: tuple[str, ...]) -> bool:
        path = self._path(word, cd)
        return path is not None and path.is_relative_to(self.root)

    def carries(self, word: str, cd: tuple[str, ...]) -> bool:
        path = self._path(word, cd)
        return path is not None and self.root.is_relative_to(path)


def _hook_cwd(hook: HookInput, root: Path) -> Path:
    raw = hook.raw.get("cwd") if isinstance(hook.raw, dict) else None
    if isinstance(raw, str) and raw.strip():
        path = Path(raw)
        if path.is_absolute():
            return path.resolve()
    return root


def _proves_project_write(hook: HookInput, command: str) -> bool:
    from grimoire.hosts.decisions.write_proof import proves_write

    root = hook.project_root.resolve()
    return proves_write(command, _ProjectTarget(root, _hook_cwd(hook, root)))


def _command_counts(hook: HookInput, facts: ToolFacts) -> bool:
    if facts.mutation is MutationClass.DESTRUCTIVE or _proves_project_write(hook, facts.command):
        return True
    if hook.agent_id:
        return False
    from grimoire.core.standard_checks.evidence_journal import is_test_command
    from grimoire.hosts.decisions.done_gate import bash_targets_all_outside_root

    if is_test_command(facts.command):
        return False
    return not bash_targets_all_outside_root(facts.command, hook.project_root)


def counts_as_session_write(hook: HookInput, facts: ToolFacts) -> bool:
    """Vrai si cet appel est une écriture réelle dans le projet — voir la règle du module."""
    if facts.mutation is MutationClass.READ_ONLY:
        return False
    try:
        if facts.command:
            return _command_counts(hook, facts)
        if facts.kind is ActionKind.FILE_WRITE:
            return not is_session_bookkeeping(facts) and not writes_outside_project(hook, facts)
        return True
    except Exception:  # une analyse en panne compte : la garde reste en échec fermé
        return True
