"""Le rôle ``merger`` : la file série qui fusionne les tâches relues READY, puis les accepte (#876, lot 4, verrou 12).

Étape du tick, après le relecteur, active seulement si ``orchestrator.roles.merger.enabled`` (désactivé par défaut).
Du code, sans LLM : il ne lance aucune session, il ne fait que reprendre celle du worker quand la fusion échoue.

**Qui entre dans la file.** Une tâche lancée (``task.session_launched``), en ``needs_verification``, dont la dernière
relecture de l'entrée en revue en cours est jugée ``READY`` (``task.review_judged``), et pas encore fusionnée. L'ordre
de la file est celui des jugements ``READY``. Le merger n'ouvre pas de PR : c'est le worker qui l'ouvre ; sans PR, il
attend.

**GitHub fait foi, pas les refs locales.** Le dépôt GitHub de la tâche (``owner/repo``) et l'URL de ``origin`` sont
figés au journal d'autorité au lancement, avant le worker ; chaque ``gh`` du merger vise ce dépôt (``-R``). La tête et la
base de la PR viennent de ``gh pr view``, ses fichiers de l'API REST (``gh api …/pulls/<n>/files``, anciens noms d'un
renommage compris) ; le worker écrit les refs et la configuration du dépôt local, pas GitHub. Quand le merger a besoin d'objets (réaligner, constater une fusion), il les récupère à refspec explicite vers
des refs à lui (``refs/grimoire/tick/…``), depuis l'URL de ``origin`` vérifiée égale au dépôt de la PR, et compare ce
qu'il obtient à ce que GitHub annonce. Il ne lance aucune commande dans le worktree du worker.

**À chaque tick, pour chaque tâche de la file**, dans cet ordre, chaque garde échouant fermée :

1. **le worktree** : celui du ledger, sur ``task/<id>``, du même dépôt que le projet ;
2. **la PR** : ouverte, du même dépôt (pas une PR d'un fork), vers la cible du worktree ; fusionnée, elle est
   constatée (étape 7) ; fermée sans fusion, refus ;
3. **le READY tient** : la tête GitHub de la PR est la tête que le relecteur du tick a **scellée au journal d'autorité**
   (:mod:`.review_seal`), ou un commit de réalignement noté au même journal par le merger ; reçu et pack inchangés à
   l'octet. Tête relue pas encore poussée : on attend ; sinon nouvelle relecture (``task.merge_requeued``). Une PR
   restée en brouillon dont la tête est ainsi couverte sort du brouillon (``gh pr ready``, ``task.merge_undrafted``,
   #993), une fois par tête et jamais si elle touche un chemin qui escalade (la question « fusionner ? » est posée) :
   c'est ici, et pas chez le relecteur, que la tête GitHub est comparée au sceau ; la CI est attendue au tick suivant.
   Remise en brouillon après cela, elle y reste : un humain la retient ;
4. **la CI** (``gh pr checks``) : les checks requis (``--required``) ; seulement si gh dit qu'aucun n'est requis, tous
   les checks hors ``ignored_checks`` (défaut ``codecov/*``). Vert = au moins un ``pass``, rien d'autre que
   ``pass``/``skipping``, et le même verdict au tick précédent pour cette tête. Rouge : retour au worker (``claude --bg
   --resume``). En attente, aucun check ou gh en échec : on attend ;
5. **la cible** : la tête et la base récupérées de la remote doivent être celles que GitHub annonce ; une base
   GitHub en retard sur la remote (``baseRefOid`` périmé, #993) cède à la base récupérée qui en descend. Tout autre
   désaccord attend, au plus :data:`MAX_DESYNC_WAITS` ticks de suite (tant qu'un compte est ouvert, la remote est
   relue même sur une CI en attente, et tout tick sans désaccord le remet à zéro), puis renvoie au worker ; une PR que GitHub dit en conflit (``CONFLICTING``, ``DIRTY``) hors ``CHANGELOG.md``
   y retourne aussitôt. Tête en retard sur la base, le merger réaligne **en plomberie** (``git merge-tree``,
   ``commit-tree -p <tête GitHub> -p <base GitHub>``), sans toucher au worktree. Un conflit limité à ``CHANGELOG.md`` est
   résolu par l'union des deux côtés, refusée si une ligne de la cible manque ; tout autre conflit renvoie au worker.
   Le commit est noté au journal d'autorité, puis poussé sans forcer ; on attend la nouvelle CI. Seules les
   ``realign_max`` premières tâches de la file sont réalignées (défaut 1) ; au-delà, une PR en conflit hors
   ``CHANGELOG.md`` est renvoyée au worker sans attendre son tour ;
6. **la fusion**, pour la tête de file seulement (WIP 1) : ``gh pr merge <n> --squash --match-head-commit <tête>``,
   jamais ``--admin`` ni ``--auto``, **seulement** si :func:`~grimoire.missions.mandate.require_authority` l'autorise
   sous :func:`~grimoire.missions.mandate.acting_as_orchestrator` (mandat qui couvre ``merge``, politique qui le
   délègue), si aucune catégorie de la tâche n'est gardée à l'humain, si **aucun fichier de la PR** (liste GitHub) n'est
   dans les chemins qui escaladent (:data:`DEFAULT_PROTECTED_PATHS`, plus ``protected_paths``, comparés en
   ``casefold()``) et si le reçu est attesté au journal d'autorité (verrou 7). Sinon, et si le dépôt refuse la fusion,
   une question **non bloquante** « fusionner ? » est posée une fois : la tâche reste en revue, l'humain fusionne
   lui-même et le tick le constate. ``task.merged`` n'est noté que constaté (PR ``MERGED``, commit de fusion contenu
   dans la base récupérée) ; le geste est inscrit au journal d'autorité avant, une fois ;
7. **l'acceptation** : la fusion revérifiée, la tête fusionnée couverte par le sceau, les fichiers de la PR hors des
   chemins qui escaladent, sous un mandat qui couvre ``accept`` et une politique qui le délègue, par
   :func:`~grimoire.missions.human_acceptance.accept_task`, puis clôture (gates déclarées par le projet) ; sinon une
   question non bloquante « accepter ? » donne la commande ``grimoire task accept``.

**Tâche faite à la main (#994).** Une tâche sans lancement de worker que le relecteur du tick a relue sous mandat
(:mod:`.manual_review`) entre dans la même file : son « worktree » est le worktree détaché de vérification, sa branche
celle de la PR, sa cible la base de la PR, et chaque ``gh pr view`` la vise par son numéro. Le dépôt est celui figé à sa
relecture. Sans session de worker à reprendre, un retour (CI rouge, conflit, désaccord) escalade à l'humain.
Ce qui fait foi est le lancement scellé au journal d'autorité (PR, base, branche, dépôt, dossier), pas le ledger : un
écart refuse. Le merger ne réaligne que les branches ``task/<id>`` : une autre branche en retard sur la cible est laissée
à l'humain, sans aucune poussée. Une tâche fusionnée sans pack de preuve, ou dont la clôture est refusée, passe en
``waiting(human)`` avec la raison.

Le merger ne lit aucune réponse humaine du ledger. Ce qu'une question cite de la PR est échappé. Chaque étape est notée
au ledger et n'est faite qu'une fois ; le tick est rejouable. Un échec technique de ``gh`` ou de ``git`` est compté au
disjoncteur ; un refus de fusion du dépôt ne l'est pas. Disjoncteur ouvert : rien n'est fait.
"""

from __future__ import annotations

import fnmatch
import json
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from grimoire.core.exceptions import GrimoireError
from grimoire.core.safe_git import git_env, same_repo
from grimoire.evidence.service import EvidenceService
from grimoire.missions.breaker import read_breaker, record_failure
from grimoire.missions.launcher import LAUNCHED_EVENT, default_exec, launched_sessions, session_env
from grimoire.missions.review_receipt import READY, read_receipt
from grimoire.missions.reviewer import REVIEW_JUDGED_EVENT, REVIEW_LAUNCHED_EVENT, entered_review_at
from grimoire.missions.schemas import ORIGIN_ORCHESTRATOR, TaskState
from grimoire.missions.task_worktree import MERGED_EVENT, TASK_BRANCH_PREFIX, find_task_worktree
from grimoire.missions.waiting import ABANDON, HUMAN_ANSWERED_EVENT, new_question_id

if TYPE_CHECKING:
    from grimoire.missions.launcher import Exec
    from grimoire.missions.orchestrator_policy import OrchestratorPolicy
    from grimoire.missions.review_receipt import ReviewReceipt
    from grimoire.missions.schemas import LedgerEvent, MissionTask
    from grimoire.missions.service import TaskService
    from grimoire.missions.task_worktree import TaskWorktree

__all__ = [
    "DEFAULT_PROTECTED_PATHS",
    "MAX_DESYNC_WAITS",
    "MERGER_ACTOR",
    "MERGE_ACCEPTED_EVENT",
    "MERGE_BOUNCED_EVENT",
    "MERGE_CI_EVENT",
    "MERGE_ESCALATED_EVENT",
    "MERGE_PUSHED_EVENT",
    "MERGE_QUEUED_EVENT",
    "MERGE_REALIGNED_EVENT",
    "MERGE_REQUESTED_EVENT",
    "MERGE_REQUEUED_EVENT",
    "MERGE_RERUN_EVENT",
    "MERGE_UNDRAFTED_EVENT",
    "MERGE_WAITED_EVENT",
    "MergeStage",
    "MergerOutcome",
]

MERGER_ACTOR = "merger"
MERGE_QUEUED_EVENT = "task.merge_queued"
MERGE_REQUEUED_EVENT = "task.merge_requeued"
MERGE_REALIGNED_EVENT = "task.merge_realigned"
MERGE_PUSHED_EVENT = "task.merge_pushed"
MERGE_BOUNCED_EVENT = "task.merge_bounced"
MERGE_CI_EVENT = "task.merge_ci"
#: Une relance des jobs en échec de la tête (``rerun-failed-jobs``) : une seule par tête, ``outcome`` dit si GitHub l'a prise.
MERGE_RERUN_EVENT = "task.merge_rerun"
MERGE_REQUESTED_EVENT = "task.merge_requested"
MERGE_ESCALATED_EVENT = "task.merge_escalated"
MERGE_ACCEPTED_EVENT = "task.merge_accepted"
#: Une PR en brouillon sortie du brouillon par le merger, sa tête couverte par le READY scellé (#993).
MERGE_UNDRAFTED_EVENT = "task.merge_undrafted"
#: Une attente « la remote et GitHub ne disent pas la même chose » ; ``cleared`` : le désaccord a cessé (#993).
MERGE_WAITED_EVENT = "task.merge_waited"
#: Attentes de désaccord consécutives tolérées avant le renvoi au worker : jamais une attente sans fin (#993).
MAX_DESYNC_WAITS = 3
#: Ce que GitHub dit d'une PR en conflit avec sa cible (``mergeable``, ``mergeStateStatus``).
_CONFLICTING = frozenset({"CONFLICTING", "DIRTY"})
#: Le début du détail CI quand GitHub ne rapporte aucun check : le cas d'une PR en conflit, dont il ne lance pas la CI.
_NO_CHECKS = "aucun check rapporté"
CHANGELOG = "CHANGELOG.md"
#: Les chemins qui escaladent à l'humain, quoi que dise le mandat (``protected_paths`` de la politique s'y ajoute) :
#: le code des gardes, la politique, les workflows et hooks, le packaging, ce qui s'exécute à l'import ou se compile.
DEFAULT_PROTECTED_PATHS: tuple[str, ...] = (
    "src/grimoire/missions/*", "src/grimoire/hosts/decisions/*", "src/grimoire/core/*", "src/grimoire/flows/attestation.py",
    "_grimoire/standard/*", ".github/*", ".githooks/*", ".husky/*", "framework/hooks/*", "codeowners", "*/codeowners",
    ".claude/*", ".mcp.json", ".vscode/*", ".pre-commit-config.yaml",
    "pyproject.toml", "setup.py", "setup.cfg", ".gitattributes", ".gitmodules", "*conftest.py",
    "*.pyc", "*.pyo", "*.pyd", "*.so", "*.dll", "*.dylib", "*.exe", "*.whl", "*.egg", "*.pth", "*sitecustomize*",
    "*usercustomize*",
)
#: Catégories propres aux gestes : le mandat et la politique les tranchent ; les autres sont celles de la tâche.
_GESTURE_CATEGORIES = frozenset({"merge", "accept"})
_SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
#: ``gh pr checks`` : 0 tout passe, 1 un check échoue, 8 des checks attendent — le JSON vaut dans les trois cas.
_GH_CHECKS_EXIT = (0, 1, 8)
_RED_BUCKETS = frozenset({"fail", "cancel"})
_GREEN_BUCKETS = frozenset({"pass", "skipping"})
#: Ce qui fait d'un échec de ``gh pr merge`` une panne (comptée) plutôt qu'un refus du dépôt (escaladé).
_TECHNICAL = ("could not resolve", "timeout", "timed out", "connection", "network", "http 5", "error connecting",
              "authentication", "gh auth login", "rate limit", "unexpected eof", "binaire introuvable", "délai")
_PUSH_TIMEOUT_S = 600
_EVIDENCE = Path("_grimoire-runtime-output") / "evidence"
#: Les refs du tick : jamais ``refs/remotes/…``, que le worker peut écrire et que ``remote.*.fetch`` redirige.
_TICK_REFS = "refs/grimoire/tick"
#: Ce qui sort d'un texte que la PR contrôle avant d'entrer dans une question : contrôles, échappements, balisage.
_UNSAFE_TEXT = re.compile(r"[\x00-\x1f\x7f-\x9f`<>*\[\]|\\]")


def _clean(text: str, limit: int = 600) -> str:
    """Un texte venu de la PR (chemin, message de gh), rendu inoffensif pour un terminal et pour un rendu Markdown."""
    return _UNSAFE_TEXT.sub("?", str(text))[:limit]


def _repo_key(url: str) -> str:
    """Un dépôt, quelle que soit l'écriture de son URL (``https://``, ``git@hôte:``, ``.git`` final, casse)."""
    text = url.strip()
    text = re.sub(r"^[A-Za-z][A-Za-z0-9+.-]*://", "", text)
    text = re.sub(r"^[^@/]+@", "", text)
    if re.match(r"^[^/:]+:[^/]", text) and not re.match(r"^[A-Za-z]:[\\/]", text):
        text = text.replace(":", "/", 1)
    return text.rstrip("/").removesuffix(".git").casefold()


@dataclass(frozen=True, slots=True)
class MergerOutcome:
    """Ce que l'étape a fait d'une tâche : ``action`` (queue, wait, stale, undraft, bounce, realign, merge, unconfirmed,
    accept, close, escalate, human, refused) et ``status`` (done, waiting, skipped, refused, failed)."""

    task_id: str
    action: str
    status: str
    detail: str = ""
    pr: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"task_id": self.task_id, "action": self.action, "status": self.status, "detail": self.detail, "pr": self.pr}


@dataclass(frozen=True, slots=True)
class _PullRequest:
    """Ce que GitHub dit de la PR : c'est ce qui fait foi, jamais une ref locale."""

    number: int
    state: str
    head: str
    base: str
    base_oid: str
    draft: bool
    merge_commit: str
    cross: bool
    files: tuple[str, ...]  # chemins touchés, anciens noms d'un renommage compris
    entries: int  # fichiers listés par l'API (un renommage compte pour un)
    changed: int
    slug: str  # owner/repo figé au lancement de la tâche : tous les gh du merger le visent (-R)
    origin: str  # l'URL de origin figée au lancement
    mergeable: str = ""  # MERGEABLE, CONFLICTING, UNKNOWN : ce que GitHub en dit, calculé à sa guise
    merge_state: str = ""  # mergeStateStatus : CLEAN, BEHIND, DIRTY, BLOCKED…

    @property
    def conflicting(self) -> bool:
        return self.mergeable in _CONFLICTING or self.merge_state in _CONFLICTING


def _merge_hint(into: str) -> str:
    """Le geste demandé au worker quand sa branche est en conflit avec la cible *into*."""
    return (f"Fusionne la cible dans ta branche et résous les conflits : `git fetch origin {into}` puis "
            f"`git merge origin/{into}` (ni rebase ni push forcé). ")


def _err(done: subprocess.CompletedProcess[Any] | Any) -> str:
    return (str(done.stderr or "") or str(done.stdout or "")).strip()[:300] or f"sortie {done.returncode}"


def _index(events: list[LedgerEvent], event: LedgerEvent | None) -> int:
    if event is None:
        return -1
    return next((i for i, e in enumerate(events) if e.id == event.id), -1)


def _last(events: list[LedgerEvent], kind: str, after: int = -1) -> LedgerEvent | None:
    found = [e for e in events[after + 1:] if e.event_type == kind]
    return found[-1] if found else None


def _last_launch(events: list[LedgerEvent]) -> int:
    return max((i for i, e in enumerate(events) if e.event_type == LAUNCHED_EVENT), default=-1)


class MergeStage:
    """L'étape ``merger`` du tick : accepte les tâches fusionnées, avance la file."""

    def __init__(
        self, service: TaskService, policy: OrchestratorPolicy, *, exec_: Exec = default_exec,
        claude_bin: str = "claude", remote: str = "origin", now: Any = None,
    ) -> None:
        self._service = service
        self._policy = policy
        self._exec = exec_
        self._claude = claude_bin
        self._remote = remote
        self._now = now
        self._home = service.home_root
        #: #994 : tâche faite à la main → sa dernière relecture manuelle (worktree de vérification, PR, branche, cible).
        self._manual: dict[str, dict[str, Any]] = {}

    # -- git, sur le dépôt du projet (objets et refs), jamais dans le worktree du worker -----------------

    def _git(self, *args: str, timeout: float = 120, stdin: bytes | None = None) -> subprocess.CompletedProcess[str]:
        """Git en octets (aucune traduction de fins de ligne, même sous Windows), sorties décodées en UTF-8."""
        try:
            done = subprocess.run(  # arguments fixes, sans shell ; dans le tick, la config qui exécute est neutralisée
                ["git", "-C", str(self._home), *args], capture_output=True, timeout=timeout, check=False, env=git_env(),
                input=stdin,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return subprocess.CompletedProcess(["git", *args], 124, "", f"git {args[0]} : {exc}")
        return subprocess.CompletedProcess(
            done.args, done.returncode, done.stdout.decode("utf-8", "replace"), done.stderr.decode("utf-8", "replace"),
        )

    def _git_bytes(self, *args: str) -> tuple[int, bytes]:
        try:
            done = subprocess.run(["git", "-C", str(self._home), *args], capture_output=True, timeout=120, check=False, env=git_env())
        except (OSError, subprocess.TimeoutExpired):
            return 124, b""
        return done.returncode, done.stdout

    def _rev(self, ref: str) -> str:
        done = self._git("rev-parse", "--verify", "--quiet", ref)
        return done.stdout.strip() if done.returncode == 0 else ""

    def _blob(self, spec: str) -> bytes | None:
        code, body = self._git_bytes("cat-file", "blob", spec)
        return body if code == 0 else None

    def _remote_url(self, pr: _PullRequest) -> tuple[str, str]:
        """``(url, "")`` : l'URL de ``origin`` si c'est le dépôt de la PR ; sinon ``("", raison)``. Échec fermé."""
        url = self._git("config", "--get", f"remote.{self._remote}.url").stdout.strip()
        if not url or _repo_key(url) != _repo_key(pr.origin):
            return "", (f"{self._remote} ({_clean(url, 120) or 'absent'}) n'est pas le dépôt figé au lancement de la tâche "
                        f"({_clean(pr.origin, 120)})")
        rewrites = self._git("config", "--get-regexp", r"^url\..*\.(push)?insteadof$")
        if rewrites.stdout.strip():
            return "", "la configuration du dépôt réécrit des URL (url.*.insteadOf) : rien n'est récupéré ni poussé"
        return url, ""

    def _fetch(self, pr: _PullRequest, *branches: str) -> tuple[dict[str, str], str]:
        """Récupère *branches* depuis le dépôt figé de la tâche vers ``refs/grimoire/tick/…``, ou la raison d'un refus."""
        url, refused = self._remote_url(pr)
        if refused:
            return {}, refused
        return self._fetch_from(url, *branches)

    def _fetch_from(self, url: str, *branches: str) -> tuple[dict[str, str], str]:
        """``({branche: sha}, "")`` : *branches* de *url*, à refspec explicite, sans sous-module ; sinon la raison."""
        specs = [f"+refs/heads/{b}:{_TICK_REFS}/{b}" for b in branches]
        done = self._git("fetch", "-q", "--no-tags", "--no-write-fetch-head", "--no-recurse-submodules", url, *specs,
                         timeout=_PUSH_TIMEOUT_S)
        if done.returncode != 0:
            return {}, f"git fetch : {_err(done)}"
        return {b: self._rev(f"{_TICK_REFS}/{b}^{{commit}}") for b in branches}, ""

    # -- étape ---------------------------------------------------------------

    def __call__(self) -> tuple[MergerOutcome, ...]:
        if not self._policy.merger.enabled:
            return ()
        from grimoire.missions.manual_review import manual_reviews

        launched = launched_sessions(self._service)
        self._manual = {tid: self._sealed_manual(tid, p) for tid, p in manual_reviews(self._service).items() if tid not in launched}
        launched = {**self._manual, **launched}
        merged, queue = self._classify(launched)
        breaker = read_breaker(self._home)
        if breaker.open:
            why = f"disjoncteur ouvert : {breaker.reason}"
            return tuple(MergerOutcome(tid, "wait", "skipped", why) for tid in (*merged, *(t for t, _ in queue)))
        outcomes = [self._guarded(tid, lambda t: self._accept(t)) for tid in merged]
        position = 0  # le rang des seules tâches qui restent dans la file : une tête refusée ou à l'humain ne bloque pas
        for tid, judged in queue:
            outcome = self._guarded(tid, lambda t, j=judged, p=position: self._advance(t, j, p, launched[t]))
            outcomes.append(outcome)
            if (outcome.status in ("waiting", "failed") and outcome.action != "human") or outcome.action in ("realign", "undraft"):
                position += 1
        return tuple(outcomes)

    def _guarded(self, tid: str, step: Any) -> MergerOutcome:
        """Une exception sur une tâche devient un échec rapporté et compté : les autres tâches suivent leur chemin."""
        try:
            outcome: MergerOutcome = step(tid)
        except Exception as exc:
            return self._failed(tid, "error", f"{type(exc).__name__}: {exc}")
        return outcome

    def _classify(self, launched: dict[str, dict[str, Any]]) -> tuple[list[str], list[tuple[str, LedgerEvent]]]:
        """``(fusionnées à accepter, file ordonnée)``."""
        merged: list[str] = []
        queue: list[tuple[str, LedgerEvent]] = []
        for tid in launched:
            task = self._service.ledger.get_task(tid)
            if task is None or task.status is not TaskState.NEEDS_VERIFICATION:
                continue
            events = self._service.ledger.events_for(tid)
            if _last(events, MERGED_EVENT, _last_launch(events)) is not None:
                merged.append(tid)
                continue
            judged = self._ready_judgment(tid, events)
            if judged is not None:
                queue.append((tid, judged))
        queue.sort(key=lambda item: (item[1].created_at, item[0]))
        return merged, queue

    def _ready_judgment(self, tid: str, events: list[LedgerEvent]) -> LedgerEvent | None:
        """Le ``task.review_judged`` READY de la dernière relecture de l'entrée en revue en cours."""
        entered = entered_review_at(self._service, tid)
        reviews = [e for e in events if e.event_type == REVIEW_LAUNCHED_EVENT and e.created_at >= entered]
        if not reviews:
            return None
        judged = [e for e in events if e.event_type == REVIEW_JUDGED_EVENT and e.payload.get("review") == reviews[-1].id]
        return judged[-1] if judged and judged[-1].payload.get("verdict") == READY else None

    def _sealed_manual(self, tid: str, noted: dict[str, Any]) -> dict[str, Any]:
        """#994 : la relecture manuelle telle que le journal d'autorité l'a scellée au lancement ; ``refused`` dit
        pourquoi elle ne vaut pas (aucun lancement scellé, écart avec le ledger)."""
        from grimoire.missions.review_seal import launch_entry

        try:
            sealed = launch_entry(self._home, tid, str(noted.get("session_sha256") or ""))
        except Exception as exc:
            return {**noted, "refused": f"journal d'autorité illisible ({type(exc).__name__})"}
        if sealed is None:
            return {**noted, "refused": "aucun lancement de relecture scellé au journal d'autorité"}
        pairs = {"pr": "pr", "base": "into", "branch": "branch", "head": "head", "cwd": "cwd"}
        if drift := sorted(k for k, n in pairs.items() if str(sealed.get(k) or "") != str(noted.get(n) or "")):
            return {**noted, "refused": f"la relecture notée au ledger diffère de celle scellée ({', '.join(drift)})"}
        return {**noted, "sealed": sealed}

    def _worktree(self, tid: str) -> tuple[TaskWorktree | None, str]:
        """Le worktree du ledger, s'il est celui de ``task/<id>`` et du même dépôt que le projet ; sinon pourquoi.

        Tâche faite à la main (#994) : le worktree détaché de vérification de sa dernière relecture, sur la branche de sa PR.
        """
        if tid in self._manual:
            from grimoire.missions.manual_review import manual_worktree

            if refused := str(self._manual[tid].get("refused") or ""):
                return None, refused
            manual = manual_worktree(tid, self._manual[tid]["sealed"])
            if not manual.path.is_dir() or not manual.branch or not manual.into:
                return None, "worktree de vérification de la tâche introuvable"
            if refused := same_repo(self._home, manual.path):
                return None, refused
            return manual, ""
        wt = find_task_worktree(self._service, tid)
        if wt is None or not wt.path.is_dir():
            return None, "worktree de la tâche introuvable"
        if wt.branch != TASK_BRANCH_PREFIX + tid:
            return None, f"branche {_clean(wt.branch, 80)!r} notée au ledger : seule task/{tid} est fusionnée"
        if refused := same_repo(self._home, wt.path):
            return None, refused
        return wt, ""

    # -- la file ---------------------------------------------------------------

    def _advance(self, tid: str, judged: LedgerEvent, position: int, launch: dict[str, Any]) -> MergerOutcome:
        task = self._service.require(tid)
        wt, refused = self._worktree(tid)
        if wt is None:
            return MergerOutcome(tid, "refused", "refused", f"{refused} : rien à fusionner")
        events = self._service.ledger.events_for(tid)
        if not any(e.event_type == MERGE_QUEUED_EVENT and e.payload.get("judged") == judged.id for e in events):
            self._note(MERGE_QUEUED_EVENT, tid, {"judged": judged.id, "review": judged.payload.get("review"), "position": position + 1})
        pin, refused = self._pin(tid)
        if pin is None:
            return MergerOutcome(tid, "refused", "refused", refused)
        pr, error = self._view(self._selector(tid, wt), pin)
        if error:
            return self._failed(tid, "wait", f"gh pr view {wt.branch} : {error}")
        if pr is None:
            return MergerOutcome(tid, "wait", "waiting", f"aucune PR pour {wt.branch} : le worker l'ouvre")
        if pr.cross:
            return MergerOutcome(tid, "refused", "refused", f"PR #{pr.number} venue d'un autre dépôt : jamais fusionnée ici", pr.number)
        if pr.state == "MERGED":
            return self._confirm(task, wt, judged, pr)
        if pr.state != "OPEN":
            return MergerOutcome(tid, "refused", "refused", f"PR #{pr.number} {pr.state} sans fusion", pr.number)
        if pr.base != wt.into:
            return MergerOutcome(tid, "refused", "refused", f"PR #{pr.number} vers {_clean(pr.base, 80)}, pas vers la cible {wt.into}", pr.number)
        receipt, kind, why = self._sealed_state(task, wt, judged, pr.head)
        if kind == "wait":
            return MergerOutcome(tid, "wait", "waiting", why, pr.number)
        if kind or receipt is None:
            return self._requeue(task, judged, why, pr.number)
        if pr.draft:  # après le sceau : seule une tête couverte par le READY attesté sort du brouillon
            return self._draft(task, wt, judged, pr)
        ci, detail = self._stable_ci(tid, pr, wt.branch)
        if ci == "unknown":
            return self._failed(tid, "wait", f"CI illisible : {detail}", pr.number)
        if ci == "red":
            if (retry := self._rerun_once(tid, pr, detail)) is not None:
                return retry
            return self._bounce(task, launch, "ci_red", detail, pr.number)
        # GitHub ne lance pas la CI d'une PR en conflit : l'attendre ne finirait jamais, le réalignement passe d'abord.
        stalled = pr.conflicting and ci == "pending" and detail.startswith(_NO_CHECKS)
        if ci != "green" and not self._desync_waits(tid) and not stalled:
            return MergerOutcome(tid, "wait", "waiting", f"CI : {detail}", pr.number)
        # CI verte, ou désaccord en cours de compte : la remote est relue (un tick sans désaccord remet le compte à zéro).
        got, refused = self._fetch(pr, wt.into, wt.branch)
        if refused:
            return MergerOutcome(tid, "refused", "refused", refused, pr.number)
        remote_base = got.get(wt.into, "")
        target, desync, side = self._target(pr, got.get(wt.branch, ""), remote_base)
        if desync:
            return self._desynced(task, launch, wt, pr, desync, side, remote_base)
        if self._desync_waits(tid):
            self._note(MERGE_WAITED_EVENT, tid, {"cleared": True, "head": pr.head, "base": target})
        behind = self._git("merge-base", "--is-ancestor", target, pr.head).returncode != 0
        if ci != "green" and not (stalled and behind):
            return MergerOutcome(tid, "wait", "waiting", f"CI : {detail}", pr.number)
        if behind:
            if position >= self._policy.merger.realign_max:
                if pr.conflicting and (hard := self._hard_conflicts(pr.head, target)):
                    return self._bounce(task, launch, "conflict", f"conflit avec {wt.into} sur {hard}", pr.number,
                                        hint=_merge_hint(wt.into))
                return MergerOutcome(tid, "wait", "waiting", f"en retard sur {wt.into}, en file (position {position + 1}) : "
                                     f"{self._policy.merger.realign_max} réalignement(s) à la fois", pr.number)
            return self._realign(task, wt, pr, judged, launch, target)
        if position > 0:
            return MergerOutcome(tid, "wait", "waiting", f"prête, en file (position {position + 1}) : une fusion à la fois", pr.number)
        return self._merge(task, wt, judged, receipt, pr)

    def _target(self, pr: _PullRequest, remote_head: str, remote_base: str) -> tuple[str, str, str]:
        """``(base à rejoindre, "", "")`` ; ``("", raison, "head"|"base")`` quand la remote et GitHub ne disent pas la
        même chose, et de quel côté.

        Un ``baseRefOid`` que GitHub n'a pas rafraîchi (#993) n'est pas un désaccord : la base récupérée, du dépôt figé
        de la tâche, en descend ; c'est elle que la tête doit contenir, et elle que le réalignement fusionne.
        """
        if remote_head != pr.head:
            return "", f"tête de la remote {remote_head[:12] or 'absente'}, tête de la PR {pr.head[:12]}", "head"
        if remote_base == pr.base_oid:
            return remote_base, "", ""
        if remote_base and self._git("merge-base", "--is-ancestor", pr.base_oid, remote_base).returncode == 0:
            return remote_base, "", ""
        return "", f"base de la remote {remote_base[:12] or 'absente'}, base de la PR {pr.base_oid[:12]}, sans filiation", "base"

    def _desync_waits(self, tid: str) -> int:
        """Les attentes de désaccord consécutives depuis la dernière remise à zéro (nouveau READY, push, retour, relecture)."""
        resets = (MERGE_QUEUED_EVENT, MERGE_PUSHED_EVENT, MERGE_BOUNCED_EVENT, MERGE_REQUEUED_EVENT)
        count = 0
        for event in reversed(self._service.ledger.events_for(tid)):
            if event.event_type == MERGE_WAITED_EVENT and not event.payload.get("cleared"):
                count += 1
            elif event.event_type == MERGE_WAITED_EVENT or event.event_type in resets:
                break
        return count

    def _desynced(
        self, task: MissionTask, launch: dict[str, Any], wt: TaskWorktree, pr: _PullRequest, why: str, side: str, remote_base: str,
    ) -> MergerOutcome:
        """La remote et GitHub en désaccord : on attend, borné (:data:`MAX_DESYNC_WAITS`), puis retour au worker ; une PR
        en conflit hors ``CHANGELOG.md`` (que le réalignement unit) y retourne aussitôt."""
        tid = task.id
        if pr.conflicting and remote_base and (hard := self._hard_conflicts(pr.head, remote_base)):
            said = _clean(pr.mergeable if pr.mergeable in _CONFLICTING else pr.merge_state, 40)
            return self._bounce(task, launch, "conflict", f"GitHub dit la PR #{pr.number} en conflit avec {wt.into} ({said}) "
                                f"sur {hard} ; {why}", pr.number, hint=_merge_hint(wt.into))
        waits = self._desync_waits(tid)
        if waits >= MAX_DESYNC_WAITS:
            hint = (f"La tête de {wt.branch} sur la remote n'est pas celle de la PR : vérifie qu'elle est poussée "
                    f"(`git status`, `git push origin {wt.branch}`, jamais forcé) et que la PR la suit. "
                    if side == "head" else _merge_hint(wt.into))
            return self._bounce(task, launch, "desync", f"la remote et GitHub en désaccord {waits} tick(s) de suite ({why})",
                                pr.number, hint=hint)
        self._note(MERGE_WAITED_EVENT, tid, {"head": pr.head, "base": pr.base_oid, "why": why[:300], "count": waits + 1})
        return MergerOutcome(tid, "wait", "waiting", f"la remote et GitHub ne disent pas encore la même chose ({why}) : revu au "
                             f"tick suivant ({waits + 1}/{MAX_DESYNC_WAITS})", pr.number)

    def _hard_conflicts(self, head: str, target: str) -> str:
        """Les chemins en conflit entre *head* et *target* hors ``CHANGELOG.md`` (que l'union résout) ; ``""`` sinon."""
        merged = self._git("merge-tree", "--write-tree", "--name-only", "--no-messages", head, target)
        if merged.returncode != 1:
            return ""
        conflicted = sorted({ln for ln in merged.stdout.splitlines()[1:] if ln.strip()} - {CHANGELOG})
        return ", ".join(_clean(p, 120) for p in conflicted[:10])

    def _draft(self, task: MissionTask, wt: TaskWorktree, judged: LedgerEvent, pr: _PullRequest) -> MergerOutcome:
        """Une PR en brouillon au READY scellé : chemins qui escaladent → question ; déjà sortie pour cette tête → un
        humain l'y a remise, elle y reste ; sinon elle sort du brouillon."""
        tid = task.id
        if kept := self._kept_to_human(task, pr):
            return self._ask_human(task, wt, "merge", judged.id, f"{kept} ; PR en brouillon, le merger ne l'en sort pas "
                                   f"(`gh pr ready {pr.number}` d'abord)", pr.number)
        events = self._service.ledger.events_for(tid)
        if any(e.event_type == MERGE_UNDRAFTED_EVENT and e.payload.get("head") == pr.head for e in events):
            return MergerOutcome(tid, "wait", "waiting", f"PR #{pr.number} remise en brouillon par un humain après sa sortie "
                                 f"à {pr.head[:12]} : le merger ne l'en ressort pas", pr.number)
        return self._undraft(tid, pr)

    def _undraft(self, tid: str, pr: _PullRequest) -> MergerOutcome:
        """#993 : la PR en brouillon dont la tête GitHub est couverte par le READY scellé passe « prête à relire ».

        Ici plutôt qu'au relecteur : la PR vient du dépôt figé, vise la cible, et sa tête GitHub vient d'être comparée au
        sceau. Ni fusion ni vote de CI dans le même tick : une PR prête peut lancer d'autres checks que son brouillon.
        """
        result = self._gh(pr.slug, "pr", "ready", str(pr.number))
        if result.returncode != 0:
            return self._failed(tid, "undraft", f"gh pr ready #{pr.number} : {_clean(_err(result), 200)}", pr.number)
        self._note(MERGE_UNDRAFTED_EVENT, tid, {"pr": pr.number, "head": pr.head})
        return MergerOutcome(tid, "undraft", "done", f"PR #{pr.number} sortie du brouillon (READY scellé sur {pr.head[:12]}) : "
                             "sa CI est attendue", pr.number)

    def _sealed_state(self, task: MissionTask, wt: TaskWorktree, judged: LedgerEvent, head: str) -> tuple[ReviewReceipt | None, str, str]:
        """``(reçu, genre, raison)`` : genre ``""`` si *head* est couverte par le READY scellé ; ``wait`` si la tête relue
        n'est pas encore poussée ; ``stale`` sinon. Le reçu et le pack sont comparés octet pour octet au sceau."""
        from grimoire.missions.review_seal import branch_head, verify

        review_id = str(judged.payload.get("review") or "")
        refusal = verify(self._home, task, wt.path, review_id=review_id, head=head)
        if refusal:
            local = branch_head(self._home, task.id)
            if (local and local != head and not verify(self._home, task, wt.path, review_id=review_id, head=local)
                    and self._git("merge-base", "--is-ancestor", head, local).returncode == 0):
                return None, "wait", f"la tête relue {local[:12]} n'est pas encore poussée (PR à {head[:12]})"
            return None, "stale", refusal
        try:
            receipt = read_receipt(wt.path, task.id)
        except ValueError as exc:
            return None, "stale", f"reçu illisible ({exc})"
        if receipt is None or receipt.verdict != READY:
            return None, "stale", "le reçu scellé n'est plus READY"
        if receipt.criteria and tuple(c.criterion for c in receipt.criteria) != tuple(task.acceptance):
            return None, "stale", "les critères ont changé depuis le READY"
        return receipt, "", ""

    # -- GitHub --------------------------------------------------------------

    def _pin(self, tid: str) -> tuple[dict[str, str] | None, str]:
        """Le dépôt figé au journal d'autorité au lancement de la tâche, avant le worker ; sinon pourquoi le merger refuse."""
        from grimoire.missions.review_seal import frozen_repository

        if tid in self._manual:  # #994 : le dépôt scellé au lancement de la relecture, lui-même tiré du pin du mandat
            sealed = self._manual[tid].get("sealed") or {}
            if not sealed.get("repo") or not sealed.get("origin_url"):
                return None, "dépôt de la tâche faite à la main non scellé au lancement de sa relecture"
            return {"repo": str(sealed["repo"]), "origin_url": str(sealed["origin_url"])}, ""
        try:
            pin = frozen_repository(self._home, tid)
        except Exception as exc:
            return None, f"journal d'autorité illisible ({type(exc).__name__}) : dépôt de la tâche non vérifiable"
        if pin is None or not pin.get("repo") or not pin.get("origin_url"):
            return None, "dépôt GitHub de la tâche non figé à son lancement : rien n'est fusionné sous mandat"
        return pin, ""

    def _selector(self, tid: str, wt: TaskWorktree) -> str:
        """Ce que ``gh pr view`` reçoit : la branche ``task/<id>``, ou le numéro de la PR d'une tâche faite à la main."""
        number = int(self._manual.get(tid, {}).get("pr") or 0)
        return str(number) if number else wt.branch

    def _gh(self, slug: str, *args: str) -> Any:
        """Un appel ``gh`` qui vise le dépôt figé (``-R``) : jamais celui que les remotes locales désignent."""
        return self._exec(["gh", *args, "-R", slug], self._home, None)

    def _view(self, branch: str, pin: dict[str, str]) -> tuple[_PullRequest | None, str]:
        fields = ("number,state,headRefOid,baseRefOid,baseRefName,isDraft,mergeCommit,isCrossRepository,changedFiles,"
                  "mergeable,mergeStateStatus")
        result = self._gh(pin["repo"], "pr", "view", branch, "--json", fields)
        if result.returncode != 0:
            if "no pull requests found" in result.stderr.lower():
                return None, ""
            return None, f"sortie {result.returncode} : {_clean(result.stderr.strip(), 200)}"
        try:
            data = json.loads(result.stdout)
            commit = data.get("mergeCommit") or {}
            cross = data.get("isCrossRepository")
            if not isinstance(cross, bool):
                return None, "gh pr view ne dit pas si la PR vient d'un autre dépôt (isCrossRepository)"
            number = int(data["number"])
            files, entries, error = self._files(pin["repo"], number)
            if error:
                return None, error
            pr = _PullRequest(
                number, str(data["state"]).upper(), str(data.get("headRefOid") or ""),
                str(data.get("baseRefName") or ""), str(data.get("baseRefOid") or ""), bool(data.get("isDraft")),
                str(commit.get("oid") or "") if isinstance(commit, dict) else "", cross, files, entries,
                int(data.get("changedFiles") or 0), pin["repo"], pin["origin_url"],
                str(data.get("mergeable") or "").upper(), str(data.get("mergeStateStatus") or "").upper(),
            )
        except (ValueError, KeyError, TypeError, AttributeError):
            return None, "réponse de gh pr view illisible"
        if not _SHA.fullmatch(pr.head) or not _SHA.fullmatch(pr.base_oid):
            return None, "gh pr view ne donne pas la tête ou la base de la PR (headRefOid, baseRefOid)"
        return pr, ""

    def _files(self, slug: str, number: int) -> tuple[tuple[str, ...], int, str]:
        """Les chemins de la PR selon l'API REST (``filename`` et ``previous_filename`` d'un renommage), et leur nombre.

        ``gh pr view --json files`` ne donne que le nouveau chemin d'un renommage : un fichier protégé déplacé hors de sa
        place y échapperait (cli/cli#14575).
        """
        result = self._exec(["gh", "api", f"repos/{slug}/pulls/{number}/files", "--paginate",
                             "--jq", ".[] | [.filename, .previous_filename]"], self._home, None)
        if result.returncode != 0:
            return (), 0, f"gh api …/pulls/{number}/files : {_clean(result.stderr.strip(), 200)}"
        paths: list[str] = []
        entries = 0
        for line in (ln for ln in result.stdout.splitlines() if ln.strip()):
            row = json.loads(line)
            if not isinstance(row, list) or not row or not isinstance(row[0], str):
                return (), 0, "réponse de gh api …/files illisible"
            entries += 1
            paths.extend(str(x) for x in row if isinstance(x, str) and x)
        return tuple(paths), entries, ""

    def _checks(self, slug: str, branch: str, *extra: str) -> tuple[str, list[dict[str, Any]]]:
        """``("ok"|"none"|"error", checks)`` : ``none`` seulement quand gh dit lui-même qu'il n'y a aucun check."""
        result = self._gh(slug, "pr", "checks", branch, "--json", "name,bucket", *extra)
        said = result.stderr.lower()
        if result.returncode == 1 and not result.stdout.strip() and "checks reported" in said and "no " in said:
            return "none", []
        if result.returncode not in _GH_CHECKS_EXIT or not result.stdout.strip():
            return "error", []
        try:
            rows = json.loads(result.stdout)
        except ValueError:
            return "error", []
        if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
            return "error", []
        return "ok", rows

    def head_ci(self, task_id: str, head: str, *, selector: str = "") -> tuple[str, str]:
        """La CI de la PR de ``task/<id>`` lue comme le merger la lit, pour le relecteur (#993) : ``(verdict, détail)``.

        ``green``, ``red``, ``pending`` ou ``unknown`` (gh en échec), seulement si la tête GitHub de la PR est *head* ;
        ``""`` quand ce n'est pas la CI de *head* (dépôt non figé, aucune PR, PR à une autre tête) : rien n'en est conclu.
        *selector* (#994) : la PR d'une tâche faite à la main, par son numéro, au lieu de ``task/<id>``.
        """
        pin, why = self._pin(task_id)
        if pin is None:
            return "", why
        branch = selector or TASK_BRANCH_PREFIX + task_id
        pr, error = self._view(branch, pin)
        if error:
            return "unknown", f"gh pr view {branch} : {error}"
        if pr is None:
            return "", f"aucune PR pour {branch}"
        if pr.head != head:
            return "", f"PR #{pr.number} à {pr.head[:12]}, tête relue {head[:12]}"
        return self._ci(pr.slug, branch, red=frozenset({"fail"}))  # un check annulé reste au merger

    def _ci(self, slug: str, branch: str, *, red: frozenset[str] = _RED_BUCKETS) -> tuple[str, str]:
        """``(green|red|pending|unknown, détail)`` : les checks requis, ou tous hors ignorés si gh dit qu'aucun n'est requis.

        *red* : les états qui font un rouge (``fail`` et ``cancel`` pour le merger) ; les autres non verts sont en cours."""
        state, required = self._checks(slug, branch, "--required")
        if state == "error":
            return "unknown", "gh pr checks --required en échec : on n'en conclut rien"
        if state == "ok" and required:
            rows, scope = required, "checks requis"
        else:
            state, every = self._checks(slug, branch)
            if state == "error":
                return "unknown", "gh pr checks illisible ou en échec"
            ignored = self._policy.merger.ignored_checks
            rows = [r for r in every if not any(fnmatch.fnmatchcase(str(r.get("name", "")), pat) for pat in ignored)]
            scope = f"checks hors {', '.join(ignored)}" if ignored else "tous les checks"
        if not rows:
            return "pending", f"{_NO_CHECKS} ({scope}) : la CI verte est exigée"
        buckets = [str(r.get("bucket", "")) for r in rows]
        failed = [_clean(str(r.get("name")), 80) for r, b in zip(rows, buckets, strict=True) if b in red]
        if failed:
            return "red", f"{len(failed)}/{len(rows)} {scope} en échec : {', '.join(failed[:5])}"
        if any(b not in _GREEN_BUCKETS for b in buckets):
            return "pending", f"{sum(1 for b in buckets if b not in _GREEN_BUCKETS)}/{len(rows)} {scope} en cours"
        if "pass" not in buckets:
            return "pending", f"{len(rows)} {scope} tous sautés : rien n'a tourné"
        return "green", f"{len(rows)} {scope} verts"

    def _rerun_once(self, tid: str, pr: _PullRequest, detail: str) -> MergerOutcome | None:
        """Une tête rouge vue pour la première fois : ses jobs en échec sont relancés (un test instable n'a rien à corriger).

        ``None`` : pas de relance, le retour au worker suit comme avant — la tête a déjà eu la sienne (prise ou refusée :
        jamais une seconde, jamais de boucle), ou GitHub la refuse (403 : l'App sans ``actions:write``). Une nouvelle tête
        (push) est une autre clé : elle remet le droit.
        """
        events = self._service.ledger.events_for(tid)
        if any(e.event_type == MERGE_RERUN_EVENT and e.payload.get("head") == pr.head for e in events):
            return None
        listed = self._exec(["gh", "api", f"repos/{pr.slug}/actions/runs?head_sha={pr.head}&per_page=100", "--jq",
                             '.workflow_runs[] | select(.conclusion=="failure" or .conclusion=="timed_out" '
                             'or .conclusion=="cancelled") | .id'], self._home, None)
        runs = [ln.strip() for ln in listed.stdout.splitlines() if ln.strip().isdigit()] if listed.returncode == 0 else []
        errors = [] if listed.returncode == 0 else [_clean(listed.stderr.strip(), 200)]
        done: list[str] = []
        for run in runs:
            result = self._exec(["gh", "api", "-X", "POST", f"repos/{pr.slug}/actions/runs/{run}/rerun-failed-jobs"],
                                self._home, None)
            if result.returncode == 0:
                done.append(run)
            else:
                errors.append(_clean(result.stderr.strip(), 200))
        outcome = "rerun" if done else "denied" if errors else "none"
        self._note(MERGE_RERUN_EVENT, tid, {"head": pr.head, "outcome": outcome, "runs": done, "detail": detail[:600],
                                            "error": errors[0] if errors else ""})
        if not done:
            return None
        return MergerOutcome(tid, "wait", "waiting", f"CI rouge ({detail}) : jobs en échec relancés une fois "
                             f"(run {', '.join(done)}), le retour au worker attend le verdict de cette relance", pr.number)

    def _stable_ci(self, tid: str, pr: _PullRequest, branch: str) -> tuple[str, str]:
        """Le verdict CI, vert seulement s'il l'était déjà au tick précédent pour cette tête (``task.merge_ci``)."""
        head = pr.head
        verdict, detail = self._ci(pr.slug, branch)
        events = self._service.ledger.events_for(tid)
        seen = next((e for e in reversed(events) if e.event_type == MERGE_CI_EVENT and e.payload.get("head") == head), None)
        was_green = seen is not None and seen.payload.get("verdict") == "green"
        if verdict == "green" and not was_green:
            self._note(MERGE_CI_EVENT, tid, {"head": head, "verdict": "green", "detail": detail})
            return "pending", f"{detail} — à confirmer au tick suivant (checks d'un push tout juste inscrits ?)"
        if verdict != "green" and was_green:
            self._note(MERGE_CI_EVENT, tid, {"head": head, "verdict": verdict, "detail": detail})
        return verdict, detail

    # -- réalignement, en plomberie ------------------------------------------

    def _realign(
        self, task: MissionTask, wt: TaskWorktree, pr: _PullRequest, judged: LedgerEvent, launch: dict[str, Any], target: str,
    ) -> MergerOutcome:
        """La base (GitHub, ou récupérée qui en descend) fusionnée dans la tête GitHub, sur objets git ; le worktree et son
        ``HEAD`` ne sont jamais lus."""
        from grimoire.missions.review_seal import seal_realignment, tree_of

        tid, head = task.id, pr.head
        if wt.branch != TASK_BRANCH_PREFIX + tid:  # #994 : jamais une poussée sur une branche qui n'est pas task/<id>
            return self._ask_human(task, wt, "merge", judged.id, f"{wt.branch} est en retard sur {wt.into} : le merger ne "
                                   f"réaligne que les branches {TASK_BRANCH_PREFIX}<id>, fusionne la cible dans la PR", pr.number)
        merged = self._git("merge-tree", "--write-tree", "--name-only", "--no-messages", head, target)
        lines = [ln for ln in merged.stdout.splitlines() if ln.strip()]
        if merged.returncode not in (0, 1) or not lines:
            return self._failed(tid, "realign", f"git merge-tree : {_err(merged)}", pr.number)
        tree, union = lines[0], False
        if merged.returncode == 1:
            conflicted = sorted(set(lines[1:]))
            if conflicted != [CHANGELOG]:
                shown = ", ".join(_clean(p, 120) for p in conflicted[:10])
                return self._bounce(task, launch, "conflict", f"conflit avec {wt.into} sur {shown}", pr.number,
                                    hint=_merge_hint(wt.into))
            tree, refused = self._union_changelog(tree, head, target)
            if refused:
                return self._bounce(task, launch, "conflict", refused, pr.number, hint=_merge_hint(wt.into))
            union = True
        made = self._git("commit-tree", tree, "-p", head, "-p", target, "-m", f"Merge {wt.into} into {wt.branch} (merger, #876)")
        new = made.stdout.strip()
        if made.returncode != 0 or not _SHA.fullmatch(new):
            return self._failed(tid, "realign", f"git commit-tree : {_err(made)}", pr.number)
        review = str(judged.payload.get("review") or "")
        produced = tree_of(self._home, new)
        seal_realignment(self._home, tid, review, head, new, produced)  # au journal d'autorité : ce que le sceau couvre
        self._note(MERGE_REALIGNED_EVENT, tid, {
            "from": head, "to": new, "into": wt.into, "into_sha": target, "changelog_union": union, "review": review, "tree": produced,
        })
        url, refused = self._remote_url(pr)
        if refused:
            return MergerOutcome(tid, "refused", "refused", refused, pr.number)
        # Ni forcé ni bail : la remote refuse un push qui n'avance pas la branche depuis la tête de la PR (branche qui a bougé).
        pushed = self._git("push", "-q", "--no-verify", url, f"{new}:refs/heads/{wt.branch}", timeout=_PUSH_TIMEOUT_S)
        if pushed.returncode != 0:
            return self._failed(tid, "realign", f"git push {wt.branch} : {_err(pushed)}", pr.number)
        self._note(MERGE_PUSHED_EVENT, tid, {"head": new, "branch": wt.branch})
        how = " (CHANGELOG : union des deux côtés)" if union else ""
        return MergerOutcome(tid, "realign", "done", f"{wt.into} fusionnée dans {wt.branch}{how}, poussée à {new[:12]} : la CI repart", pr.number)

    def _union_changelog(self, tree: str, head: str, target: str) -> tuple[str, str]:
        """``(arbre, "")`` avec le CHANGELOG résolu par l'union des deux côtés, ou ``("", raison)`` s'il perdrait une ligne."""
        base = self._git("merge-base", head, target).stdout.strip()
        ours, theirs = self._blob(f"{head}:{CHANGELOG}"), self._blob(f"{target}:{CHANGELOG}")
        if ours is None or theirs is None:
            return "", f"{CHANGELOG} absent d'un côté de la fusion : pas d'union possible"
        common = (self._blob(f"{base}:{CHANGELOG}") if base else None) or b""
        with tempfile.TemporaryDirectory(prefix="grimoire-union-") as scratch:
            files = [Path(scratch) / name for name in ("ours", "base", "theirs")]
            for path, body in zip(files, (ours, common, theirs), strict=True):
                path.write_bytes(body)
            code, result = self._git_bytes("merge-file", "-p", "--union", *(str(p) for p in files))
        if code < 0 or code > 127:
            return "", "git merge-file --union a échoué"
        target_lines = theirs.decode("utf-8", "replace").splitlines()
        kept = result.decode("utf-8", "replace").splitlines()
        missing = [ln for ln in target_lines if target_lines.count(ln) > kept.count(ln)]
        if missing:
            return "", f"union du {CHANGELOG} refusée : {len(missing)} ligne(s) de la cible perdue(s), dont {_clean(missing[0], 80)!r}"
        blob = self._git("hash-object", "-w", "--no-filters", "--stdin", stdin=result)
        listing = self._git("ls-tree", "-z", tree)
        if blob.returncode != 0 or listing.returncode != 0:
            return "", "objets git du CHANGELOG non écrits"
        entries = []
        for entry in (e for e in listing.stdout.split("\0") if e):
            meta, name = entry.split("\t", 1)
            if name == CHANGELOG:
                mode, kind, _sha = meta.split()
                meta = f"{mode} {kind} {blob.stdout.strip()}"
            entries.append(f"{meta}\t{name}")
        made = self._git("mktree", "-z", stdin=("\0".join(entries) + "\0").encode("utf-8"))
        new_tree = made.stdout.strip()
        if made.returncode != 0 or not _SHA.fullmatch(new_tree):
            return "", f"git mktree : {_err(made)}"
        return new_tree, ""

    # -- fusion --------------------------------------------------------------

    def _kept_to_human(self, task: MissionTask, pr: _PullRequest) -> str:
        """Pourquoi ce geste reste humain quoi que dise le mandat : catégorie gardée, chemin qui escalade ; ``""`` sinon.

        Les chemins sont ceux que GitHub annonce pour la PR, jamais un diff de refs locales.
        """
        from grimoire.missions.mandate import load_escalation, task_categories

        policy = load_escalation(self._home)
        guarded = sorted(c for c in task_categories(task) if c not in _GESTURE_CATEGORIES and not policy.delegates(c))
        if guarded:
            return f"catégorie gardée à l'humain : {', '.join(guarded)}"
        if pr.changed != pr.entries:
            return f"GitHub annonce {pr.changed} fichier(s) et en liste {pr.entries} : liste incomplète, non vérifiable"
        patterns = tuple(p.casefold() for p in (*DEFAULT_PROTECTED_PATHS, *self._policy.merger.protected_paths))
        kept = sorted(p for p in pr.files if any(fnmatch.fnmatchcase(p.casefold(), pat) for pat in patterns))
        if kept:
            return f"la PR touche des chemins qui reviennent à l'humain : {', '.join(_clean(p, 120) for p in kept[:8])}"
        return ""

    def _merge(self, task: MissionTask, wt: TaskWorktree, judged: LedgerEvent, receipt: ReviewReceipt, pr: _PullRequest) -> MergerOutcome:
        from grimoire.missions.mandate import (
            MandateRefusedError,
            MandateStore,
            acting_as_orchestrator,
            require_authority,
        )

        tid, head = task.id, pr.head
        kept = self._kept_to_human(task, pr)
        if kept:
            return self._ask_human(task, wt, "merge", judged.id, kept, pr.number)
        provenance = {"kind": "receipt", "ref": f"{tid}:review={judged.payload.get('review')}:head={head[:12]}"}
        try:
            with acting_as_orchestrator():
                auth = require_authority(
                    self._home, "merge", task, ORIGIN_ORCHESTRATOR, what=f"Fusion refusée pour {tid}",
                    provenance=provenance, record=False,
                )
            if auth is None:  # pragma: no cover - l'origine est orchestrator : jamais None
                raise MandateRefusedError("guard_error", "autorité sans mandat")
            MandateStore(self._home).check_receipt_provenance(task, receipt)  # verrou 7 : la relecture qui fonde la fusion
        except (MandateRefusedError, GrimoireError) as exc:
            return self._ask_human(task, wt, "merge", judged.id, str(exc), pr.number)
        self._note(MERGE_REQUESTED_EVENT, tid, {
            "pr": pr.number, "head": head, "origin": ORIGIN_ORCHESTRATOR, "mandate_id": auth.mandate_id, "provenance": provenance,
        })
        result = self._gh(pr.slug, "pr", "merge", str(pr.number), "--squash", "--match-head-commit", head)
        if result.returncode != 0:
            said = (result.stderr or result.stdout).strip()[:300]
            if result.returncode in (124, 126, 127) or any(w in said.lower() for w in _TECHNICAL):
                return self._failed(tid, "merge", f"gh pr merge #{pr.number} : {_clean(said, 300)}", pr.number)
            # Le dépôt refuse (revue exigée, protection, file de fusion) : ni une panne, ni un geste à forcer.
            return self._ask_human(task, wt, "merge", judged.id, f"le dépôt refuse la fusion ({said})", pr.number)
        after, error = self._view(self._selector(tid, wt), {"repo": pr.slug, "origin_url": pr.origin})
        if error or after is None:
            return self._failed(tid, "unconfirmed", f"fusion demandée, non constatée : {error or 'PR introuvable'}", pr.number)
        return self._confirm(task, wt, judged, after)

    def _merged_on_remote(self, wt: TaskWorktree, pr: _PullRequest) -> tuple[str, str]:
        """``("yes"|"no"|"error", détail)`` : PR ``MERGED`` et commit de fusion contenu dans la base récupérée."""
        if pr.state != "MERGED" or not _SHA.fullmatch(pr.merge_commit):
            return "no", f"PR #{pr.number} {pr.state} : fusion non constatée"
        got, refused = self._fetch(pr, wt.into)
        if refused:
            return "error", refused
        target = got.get(wt.into, "")
        if not target or self._git("merge-base", "--is-ancestor", pr.merge_commit, target).returncode != 0:
            return "no", f"commit de fusion {pr.merge_commit[:12]} absent de {wt.into}"
        return "yes", ""

    def _confirm(self, task: MissionTask, wt: TaskWorktree, judged: LedgerEvent, pr: _PullRequest) -> MergerOutcome:
        """``task.merged`` seulement si la remote le dit ; le geste sous mandat au journal d'autorité d'abord."""
        tid = task.id
        merged, why = self._merged_on_remote(wt, pr)
        if merged == "error":
            return self._failed(tid, "unconfirmed", why, pr.number)
        if merged == "no":
            return MergerOutcome(tid, "unconfirmed", "waiting", f"{why}, revue au tick suivant", pr.number)
        events = self._service.ledger.events_for(tid)
        requested = next((e for e in reversed(events) if e.event_type == MERGE_REQUESTED_EVENT and e.payload.get("head") == pr.head), None)
        self._record_gesture(tid, requested)  # dédoublonné : une coupure avant ``task.merged`` le retrouve inscrit
        self._service.ledger.append_event(MERGED_EVENT, tid, "task", MERGER_ACTOR, {
            "into": wt.into, "base_sha": wt.base_sha, "head_sha": pr.head, "merged_sha": pr.merge_commit, "pr": pr.number,
            "via": "gh", "judged": judged.id,
            "origin": str(requested.payload.get("origin", "")) if requested else "remote",
            "mandate_id": str(requested.payload.get("mandate_id", "")) if requested else "",
        })
        accepted = self._accept(tid)
        return MergerOutcome(tid, accepted.action, accepted.status, f"PR #{pr.number} fusionnée à {pr.merge_commit[:12]} ; {accepted.detail}", pr.number)

    def _record_gesture(self, tid: str, requested: LedgerEvent | None) -> None:
        """Inscrit la fusion prise sous mandat au journal d'autorité, une fois, une fois la fusion constatée."""
        from grimoire.missions.mandate import Authorization, MandateStore

        if requested is None or not requested.payload.get("mandate_id"):
            return
        provenance = {str(k): str(v) for k, v in dict(requested.payload.get("provenance") or {}).items()}
        store = MandateStore(self._home)
        if any(g.get("gesture") == "merge" and g.get("provenance") == provenance for g in store.gestures_taken(tid)):
            return
        store.record_gesture(Authorization(str(requested.payload["mandate_id"]), "merge", tid, provenance))

    # -- acceptation ---------------------------------------------------------

    def _accept(self, tid: str) -> MergerOutcome:
        """La fusion revérifiée (le ledger ne suffit pas), le sceau tenu, puis l'acceptation sous mandat."""
        from grimoire.missions.human_acceptance import accept_task
        from grimoire.missions.mandate import MandateRefusedError, MandateStore, acting_as_orchestrator
        from grimoire.missions.review_seal import verify

        task = self._service.require(tid)
        wt, refused = self._worktree(tid)
        if wt is None:
            return MergerOutcome(tid, "accept", "refused", f"{refused} : rien à accepter")
        events = self._service.ledger.events_for(tid)
        noted = _last(events, MERGED_EVENT, _last_launch(events))
        if noted is None:  # pragma: no cover - l'appelant le garantit
            return MergerOutcome(tid, "accept", "refused", "aucune fusion constatée")
        if _last(events, MERGE_ACCEPTED_EVENT, _index(events, noted)) is not None:
            return self._close(tid, wt, int(noted.payload.get("pr") or 0))
        pin, refused = self._pin(tid)
        if pin is None:
            return MergerOutcome(tid, "accept", "refused", refused)
        pr, error = self._view(self._selector(tid, wt), pin)
        if error or pr is None:
            return self._failed(tid, "accept", f"gh pr view {wt.branch} : {error or 'PR introuvable'}")
        merged, why = self._merged_on_remote(wt, pr)
        if merged != "yes":
            return (self._failed(tid, "accept", why, pr.number) if merged == "error"
                    else MergerOutcome(tid, "accept", "refused", f"task.merged au ledger, mais {why}", pr.number))
        provenance = {"kind": "verdict", "ref": f"{tid}:merged={pr.merge_commit[:12]}:judged={noted.payload.get('judged')}"}
        store = MandateStore(self._home)
        if any(g.get("gesture") == "accept" and g.get("provenance") == provenance for g in store.gestures_taken(tid)):
            # Coupure après ``accept_task`` (geste inscrit, gardes passées) : ni second pack, ni second verdict, ni second geste.
            self._note(MERGE_ACCEPTED_EVENT, tid, {"origin": ORIGIN_ORCHESTRATOR, "replayed": True})
            return self._close(tid, wt, pr.number)
        reasons = []
        if sealed := verify(self._home, task, wt.path, head=pr.head):
            reasons.append(sealed)
        if kept := self._kept_to_human(task, pr):
            reasons.append(kept)
        if reasons:
            return self._ask_human(task, wt, "accept", pr.merge_commit, " ; ".join(reasons), pr.number)
        if tid in self._manual and not EvidenceService(wt.path / _EVIDENCE).list_packs(tid):
            return self._escalate(task, f"PR #{pr.number} fusionnée et relue READY, mais aucun pack de preuve dans le "
                                  f"worktree de relecture ({wt.path}) : la clôture ne peut pas l'accepter", pr.number)
        reason = f"PR #{pr.number} fusionnée à {pr.merge_commit[:12]}, relue READY (sceau tenu), CI verte"
        try:
            with acting_as_orchestrator():
                outcome = accept_task(task, self._home, wt.path / _EVIDENCE, reason, MERGER_ACTOR, origin=ORIGIN_ORCHESTRATOR,
                                      provenance=provenance, proof_root=wt.path, head=pr.head)
        except (MandateRefusedError, GrimoireError, ValueError) as exc:
            return self._ask_human(task, wt, "accept", pr.merge_commit, str(exc), pr.number)
        self._note(MERGE_ACCEPTED_EVENT, tid, {
            "verdict": outcome.verdict.id, "pack": outcome.pack.id, "origin": ORIGIN_ORCHESTRATOR,
            "mandate_id": outcome.verdict.created_by.removeprefix(f"{ORIGIN_ORCHESTRATOR}:"),
        })
        return self._close(tid, wt, pr.number)

    def _close(self, tid: str, wt: TaskWorktree, pr: int) -> MergerOutcome:
        """Clôture au ledger du projet : gates **déclarées par le projet**, preuve lue dans le worktree, reçu exigé quel que
        soit le profil que le worktree déclare."""
        from grimoire.missions.gates import check_transition
        from grimoire.missions.review_receipt import STALE, check_receipt, read_receipt, receipt_fingerprint

        task = self._service.require(tid)
        verdict = check_transition(wt.path, task, "review", "done", declarations_root=self._home)
        refusals: list[Any] = list(verdict.refusals) if verdict.blocked else []
        receipt = read_receipt(wt.path, tid)
        if receipt is None or receipt.verdict != READY or receipt.tree_fingerprint != receipt_fingerprint(wt.path):
            refusals.append(f"{STALE} : reçu READY absent ou périmé")
        elif (refused := check_receipt(wt.path, task)) is not None:
            refusals.append(refused)
        if refusals:
            shown = "; ".join(str(r) for r in refusals)[:400]
            if tid in self._manual:  # #994 : une tâche faite à la main ne reste pas bloquée en revue
                return self._escalate(task, f"acceptée, clôture refusée : {shown}", pr)
            return MergerOutcome(tid, "close", "failed", f"acceptée, clôture refusée : {shown}", pr)
        try:
            moved = self._service.ledger.transition_task(
                tid, TaskState.CLOSED, actor_id=MERGER_ACTOR, reason=f"fusionnée (PR #{pr}) et acceptée",
                expected_from=TaskState.NEEDS_VERIFICATION,
            )
            self._service._consolidate(moved, TaskState.CLOSED, actor=MERGER_ACTOR, reason=f"fusionnée (PR #{pr}) et acceptée")
        except (GrimoireError, ValueError) as exc:
            return MergerOutcome(tid, "close", "failed", f"acceptée, clôture refusée : {exc}", pr)
        return MergerOutcome(tid, "accept", "done", f"acceptée et close (PR #{pr}) : le janitor nettoie", pr)

    # -- retours et questions ------------------------------------------------

    def _ask_human(self, task: MissionTask, wt: TaskWorktree, gesture: str, anchor: str, why: str, pr: int) -> MergerOutcome:
        """Une question **non bloquante**, une fois par geste et par READY (ou par fusion) : la tâche reste en revue.

        Le merger ne fait pas ce geste ; l'humain le fait lui-même, par un canal qui vérifie son origine : la fusion
        sur la remote (le tick la constate), ``task accept`` dans le worktree. Aucune réponse n'est lue ici. Ce qui vient
        de la PR (*why* : chemins, message de gh) est échappé.
        """
        from grimoire.missions.questions import QuestionSpec

        tid, safe = task.id, _clean(why)
        if gesture == "merge":
            text = (f"Fusionner la PR #{pr} de {_clean(wt.branch, 80)} ? Le merger ne la fusionne pas : {safe}. Fusionne-la toi-même "
                    f"(`gh pr merge {pr} --squash`) si elle te convient : le tick constatera la fusion.")
        else:
            text = (f"Accepter {tid} (PR #{pr} fusionnée) ? Le merger ne l'accepte pas : {safe}. Accepte-la toi-même : "
                    f"`grimoire task accept {tid} --project-root {wt.path} --reason \"…\"`.")
        key = f"merger:{gesture}:{anchor}"
        try:
            asked = self._service.ask_batch(tid, [QuestionSpec(text, kind="merge" if gesture == "merge" else "choice",
                                                               blocking=False, key=key)], MERGER_ACTOR)
        except (GrimoireError, ValueError) as exc:
            return MergerOutcome(tid, "escalate", "failed", f"question à l'humain non posée ({exc})", pr)
        word = "fusionner" if gesture == "merge" else "accepter"
        if not asked.plan.asked:
            return MergerOutcome(tid, "human", "waiting", f"« {word} ? » posée, l'humain décide : {safe}", pr)
        self._note(MERGE_ESCALATED_EVENT, tid, {"gesture": gesture, "question_id": asked.plan.asked[0].id, "why": safe,
                                                "pr": pr, "anchor": anchor, "blocking": False})
        return MergerOutcome(tid, "escalate", "done", f"« {word} ? » posée à l'humain : {safe}", pr)

    def _requeue(self, task: MissionTask, judged: LedgerEvent, why: str, pr: int) -> MergerOutcome:
        """Un READY qui ne tient plus : la tâche repart à la relecture (une nouvelle entrée en revue), une fois par READY."""
        events = self._service.ledger.events_for(task.id)
        if _last(events, MERGE_REQUEUED_EVENT, _index(events, judged)) is None:
            if self._cycle(events, MERGE_REQUEUED_EVENT) >= self._policy.merger.max_cycles:
                return self._escalate(task, f"READY périmé à répétition ({why})", pr)
            self._note(MERGE_REQUEUED_EVENT, task.id, {"judged": judged.id, "why": why})
        return MergerOutcome(task.id, "stale", "done", f"READY périmé ({why}) : relu de nouveau", pr)

    @staticmethod
    def _cycle(events: list[LedgerEvent], kind: str) -> int:
        """Le rang de ce retour depuis la dernière réponse humaine (« reprendre » remet à zéro ; pas celui de l'orchestrateur)."""
        answered = max((i for i, e in enumerate(events)
                        if e.event_type == HUMAN_ANSWERED_EVENT and e.payload.get("origin") == "human"), default=-1)
        return 1 + sum(1 for e in events[answered + 1:] if e.event_type == kind)

    def _bounce(self, task: MissionTask, launch: dict[str, Any], reason: str, detail: str, pr: int, *, hint: str = "") -> MergerOutcome:
        """CI rouge, conflit, désaccord persistant : retour au worker (reprise de sa session), ou escalade au cycle
        ``max_cycles``. *hint* : le geste attendu du worker (fusionner la cible, pousser sa branche)."""
        tid, max_cycles = task.id, self._policy.merger.max_cycles
        events = self._service.ledger.events_for(tid)
        cycle, worker = self._cycle(events, MERGE_BOUNCED_EVENT), str(launch.get("session_id") or "")
        if cycle >= max_cycles or not worker:
            why = f"{detail} — {cycle} retour(s) de fusion" if worker else f"{detail} — aucune session de worker à reprendre"
            escalated = self._escalate(task, why, pr)
            if escalated.status == "done":
                self._note(MERGE_BOUNCED_EVENT, tid, {"reason": reason, "detail": detail[:600], "cycle": cycle, "escalated": True})
            return escalated
        ledger = self._service.ledger
        try:
            ledger.transition_task(tid, TaskState.RUNNING, actor_id=MERGER_ACTOR, reason=f"fusion refusée ({reason}, cycle {cycle}/{max_cycles})")
        except GrimoireError as exc:
            return MergerOutcome(tid, "bounce", "failed", f"retour au worker refusé : {exc}", pr)
        argv = [self._claude, "--bg", "--resume", worker]
        if self._policy.worker.permission_mode:
            argv += ["--permission-mode", self._policy.worker.permission_mode]
        argv.append(
            f"La fusion de {tid} est refusée : {_clean(detail)}. La branche distante task/{tid} a pu être réalignée par le "
            f"merger (main fusionnée, ton worktree n'est pas touché) : `git pull --no-rebase origin task/{tid}` avant de "
            f"pousser. {hint}"
            f"Corrige dans ce worktree, commite, pousse sur task/{tid} (jamais de push forcé), revérifie avec les "
            f"checks de la tâche, puis remets-la en revue "
            f"(`grimoire task move {tid} --to needs_verification`)."
        )
        result = self._exec(argv, Path(str(launch.get("cwd") or ".")), session_env())
        if result.returncode != 0:
            ledger.transition_task(tid, TaskState.NEEDS_VERIFICATION, actor_id=MERGER_ACTOR, reason="reprise du worker échouée")
            return self._failed(tid, "bounce", f"worker non repris ({_err(result)}), tâche remise en revue", pr)
        self._note(MERGE_BOUNCED_EVENT, tid, {"reason": reason, "detail": detail[:600], "cycle": cycle})
        self._service.comment(tid, f"Fusion refusée (cycle {cycle}/{max_cycles}) : {detail}", MERGER_ACTOR)
        return MergerOutcome(tid, "bounce", "done", f"retour au worker ({reason}, cycle {cycle}/{max_cycles}) : {detail}", pr)

    def _escalate(self, task: MissionTask, why: str, pr: int) -> MergerOutcome:
        """Échecs répétés : ``needs_verification → running → waiting(human)`` « reprendre ou abandonner »."""
        tid, ledger = task.id, self._service.ledger
        qid = new_question_id()
        self._note(MERGE_ESCALATED_EVENT, tid, {"gesture": "bounce", "question_id": qid, "why": why[:600], "pr": pr, "blocking": True})
        try:
            ledger.transition_task(tid, TaskState.RUNNING, actor_id=MERGER_ACTOR, reason=f"escalade : {why[:200]}")
        except GrimoireError as exc:
            return MergerOutcome(tid, "escalate", "failed", f"escalade refusée : {exc}", pr)
        try:
            self._service.ask(tid, f"La fusion de {tid} échoue à répétition ({why}) — reprendre la tâche ou l'abandonner ?",
                              MERGER_ACTOR, kind="choice", options=("reprendre", ABANDON), question_id=qid)
        except (GrimoireError, ValueError) as exc:
            ledger.transition_task(tid, TaskState.NEEDS_VERIFICATION, actor_id=MERGER_ACTOR, reason="escalade non posée")
            return MergerOutcome(tid, "escalate", "failed", f"question à l'humain non posée ({exc}) : tâche remise en revue", pr)
        return MergerOutcome(tid, "escalate", "done", f"« reprendre ou abandonner ? » posée à l'humain : {why}", pr)

    # -- journal -------------------------------------------------------------

    def _note(self, kind: str, tid: str, payload: dict[str, Any]) -> None:
        self._service.ledger.append_event(kind, tid, "task", MERGER_ACTOR, {"task_id": tid, **payload})

    def _failed(self, tid: str, action: str, detail: str, pr: int = 0) -> MergerOutcome:
        """Un échec technique de ``gh`` ou de ``git`` : rapporté, compté au disjoncteur, rejoué au tick suivant."""
        state = record_failure(self._home, self._policy.breaker_threshold, f"{tid} : merger ({action}) : {detail}")
        return MergerOutcome(tid, action, "failed", detail + (" — disjoncteur ouvert" if state.open else ""), pr)
