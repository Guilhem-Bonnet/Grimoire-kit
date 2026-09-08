"""``grimoire task dispatch`` — cascade par classe de vérifiabilité (issue #323).

Le lot 3 (#311) attend le moteur de flow (#204) pour orchestrer un run
multi-étapes ; en attendant, la topologie hybride de l'épic — l'hôte tient
la session, le kit remet une tranche de travail à un exécuteur puis applique
le gate — peut déjà se livrer au niveau d'une tâche unique, en branchant deux
briques déjà posées : la classe de vérifiabilité (#309, ``verifiability.py``)
et le registre par palier de coût (#310, ``providers/``).

Le principe qui gouverne tout ce module : **la classe décide qui a le droit
d'appeler, jamais le contraire.** Une tâche V2 (aucun verdict mécanique ni
revue reconnue) est refusée avant tout calcul — déléguer un travail que rien
ne sait juger produirait un vert qui ne prouve rien. Une tâche V0 peut
cascader depuis le palier le moins cher ; une tâche V1, seulement à partir de
``mid`` (le jugement humain qui suit un jour justifie un modèle plus capable
dès le premier essai), et son vert n'est jamais une fermeture — seulement un
passage en revue (``NEEDS_VERIFICATION``), parce que le check mécanique n'est
ici qu'un indice, pas le verdict qu'une tâche V1 exige réellement.

Chaque tentative — qu'elle échoue à l'appel (429, timeout) ou échoue au check
— laisse un événement ``task.dispatched`` dans le Mission Ledger : c'est
l'historique brut dont le lot 4 (#312, budget et coûts) a besoin, et il doit
survivre même quand la cascade entière finit rouge.
"""

from __future__ import annotations

import json
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from grimoire.core.exceptions import GrimoireMissionError
from grimoire.missions.schemas import TaskState
from grimoire.missions.verifiability import Verifiability, classify
from grimoire.providers.registry import SUPPORTED_MODEL_TIERS, ProviderSpec
from grimoire.providers.routing import candidates as provider_candidates
from grimoire.providers.state import record_failure, record_success

if TYPE_CHECKING:
    from grimoire.missions.schemas import MissionTask
    from grimoire.missions.service import TaskService

__all__ = [
    "CHECK_TIMEOUT_S",
    "DEFAULT_CALL_TIMEOUT_S",
    "CheckResult",
    "DispatchAttempt",
    "DispatchReport",
    "build_prompt",
    "render_invocation",
    "run_dispatch",
    "start_tier_for",
]

#: Palier de départ de la cascade selon la classe de vérifiabilité. V2 n'a pas
#: d'entrée : ``run_dispatch`` refuse avant même de consulter cette table.
_START_TIER: dict[Verifiability, str] = {
    Verifiability.V0: "cheap",
    Verifiability.V1: "mid",
}

#: Timeout par défaut d'un appel fournisseur — configurable (``--timeout``) :
#: un modèle fort peut légitimement prendre plus longtemps qu'un modèle cheap.
DEFAULT_CALL_TIMEOUT_S = 600.0

#: Timeout de chaque ``--check`` — fixe, non exposé en option : c'est
#: l'utilisateur qui écrit la commande, il en connaît le coût attendu, et une
#: valeur par tâche ouvrirait un réglage de plus sans bénéfice net ici.
CHECK_TIMEOUT_S = 600.0

#: Motifs d'échec d'appel reconnus dans la sortie, indépendamment du code de
#: sortie — un fournisseur peut répondre 0 et pourtant décrire un 429 dans son
#: propre format de sortie (CLI headless qui avale l'erreur HTTP).
_RATE_LIMIT_MARKERS = ("429", "rate limit", "rate_limit", "overloaded", "quota")


def start_tier_for(verifiability: Verifiability) -> str | None:
    """Le premier palier autorisé pour cette classe — ``None`` si aucun (V2)."""
    return _START_TIER.get(verifiability)


def _tier_chain(start_tier: str, max_tier: str | None) -> tuple[str, ...]:
    """Les paliers à essayer, dans l'ordre, entre *start_tier* et *max_tier*.

    Vide si *max_tier* est strictement en dessous de *start_tier* — la classe
    exige un palier que la borne posée par l'appelant interdit ; ``()`` le
    dit sans lever, à charge de l'appelant de le traduire en refus.
    """
    start_idx = SUPPORTED_MODEL_TIERS.index(start_tier)
    end_idx = SUPPORTED_MODEL_TIERS.index(max_tier) if max_tier else len(SUPPORTED_MODEL_TIERS) - 1
    if end_idx < start_idx:
        return ()
    return SUPPORTED_MODEL_TIERS[start_idx : end_idx + 1]


def build_prompt(task: MissionTask) -> str:
    """Le contrat autonome envoyé au fournisseur : ce qu'il doit faire, rien de plus.

    Volontairement plus étroit que le context bundle du standard
    (``build_context_bundle`` — board, mémoire, registres...) : un exécuteur
    délégué par cascade n'a besoin ni de la politique de mémoire du projet ni
    du registre de fournisseurs, seulement de ce que la tâche exige et de la
    garde qui rend le vert du check opposable — ne pas retoucher les
    vérifications, sous peine de rendre le verdict qui suit sans objet.
    """
    lignes = [f"Tâche {task.id} : {task.title}"]
    if task.description:
        lignes.append(f"\nContexte : {task.description}")
    if task.acceptance:
        lignes.append("\nCritères d'acceptation :")
        lignes.extend(f"  - {c}" for c in task.acceptance)
    if task.expected_evidence:
        lignes.append("\nPreuve attendue :")
        lignes.extend(f"  - {e}" for e in task.expected_evidence)
    if task.guardrails:
        lignes.append("\nGarde-fous :")
        lignes.extend(f"  - {g}" for g in task.guardrails)
    lignes.append(
        "\nConsigne : réalise ce travail dans le dépôt courant. Ne modifie pas "
        "les commandes de vérification (`--check`) qui jugeront le résultat — "
        "leur code de sortie est le seul verdict qui compte ici."
    )
    return "\n".join(lignes)


def render_invocation(template: str, *, prompt: str, model: str) -> list[str]:
    """La commande à exécuter, ``{prompt}`` et ``{model}`` substitués token par token.

    ``shlex.split`` d'abord, substitution ensuite : le prompt devient le
    contenu d'UN argument de ``subprocess`` (``shell=False``), jamais un
    fragment de ligne de commande réinterprété par un shell. Une invocation
    mal écrite (``{prompt}`` absent) échouera à l'appel, pas silencieusement —
    ``choose``/``candidates`` a déjà écarté les fournisseurs sans
    ``invocation``, mais pas ceux dont le gabarit oublie le placeholder.
    """
    return [tok.replace("{prompt}", prompt).replace("{model}", model) for tok in shlex.split(template)]


def _looks_rate_limited(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _RATE_LIMIT_MARKERS)


def _extract_cost_usd(stdout: str) -> float | None:
    """``total_cost_usd`` si la sortie est un JSON qui le porte — ``None`` sinon.

    Best-effort : la plupart des fournisseurs headless ne rendent pas de JSON
    du tout, et ce n'est pas un échec — juste un coût qui restera inconnu pour
    cette tentative.
    """
    try:
        data = json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    cost = data.get("total_cost_usd")
    return float(cost) if isinstance(cost, (int, float)) else None


@dataclass(frozen=True, slots=True)
class CheckResult:
    """Le verdict d'une commande ``--check`` : verte ou non, rien d'autre à savoir."""

    cmd: str
    ok: bool

    def to_dict(self) -> dict[str, Any]:
        return {"cmd": self.cmd, "ok": self.ok}


@dataclass(frozen=True, slots=True)
class DispatchAttempt:
    """Une tentative de la cascade : un fournisseur, un palier, un verdict.

    ``verdict`` prend l'une de cinq valeurs : ``"green"`` (appel et checks
    au vert), ``"red"`` (appel réussi, un check au moins a échoué),
    ``"rate_limit"``, ``"timeout"`` ou ``"error"`` (l'appel lui-même a
    échoué — saturation, délai dépassé, ou panne locale du fournisseur ; le
    fournisseur suivant du même palier prend le relais, les checks ne
    tournent pas).
    """

    attempt: int
    tier: str
    provider: str
    model: str
    exit_code: int | None
    duration_s: float
    checks: tuple[CheckResult, ...]
    verdict: str
    cost_usd: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt": self.attempt,
            "tier": self.tier,
            "provider": self.provider,
            "model": self.model,
            "exit_code": self.exit_code,
            "duration_s": round(self.duration_s, 3),
            "checks": [c.to_dict() for c in self.checks],
            "verdict": self.verdict,
            "cost_usd": self.cost_usd,
        }


#: Motif de refus posé avant toute tentative — la cascade n'a jamais appelé
#: personne. Distinct de la chaîne épuisée (des tentatives ont eu lieu, aucune
#: n'est allée au vert).
_REFUSAL_MESSAGES: dict[str, str] = {
    "v2": "classe de vérifiabilité V2 : aucun verdict ne peut juger ce travail, pas de délégation",
    "no_check": "au moins un `--check` est requis : la classe dit que le verdict est mécanique, la commande dit lequel",
    "no_tier": "aucun palier disponible entre le plancher de la classe et --max-tier",
    "no_provider": "aucun fournisseur invocable sur la chaîne de paliers prévue",
}


@dataclass(frozen=True, slots=True)
class DispatchReport:
    """Le résultat complet d'un ``run_dispatch`` — ce que le CLI affiche et sérialise."""

    task_id: str
    verifiability: str
    dry_run: bool
    planned_chain: tuple[str, ...]
    prompt: str
    attempts: tuple[DispatchAttempt, ...] = ()
    refusal: str | None = None
    transitioned_to: str | None = None
    transition_refused: str | None = None

    @property
    def refusal_message(self) -> str | None:
        """Le motif de refus en clair — ``None`` si la cascade n'a pas été refusée."""
        if self.refusal is None:
            return None
        return _REFUSAL_MESSAGES.get(self.refusal, self.refusal)

    @property
    def succeeded(self) -> bool:
        return any(a.verdict == "green" for a in self.attempts)

    @property
    def exit_code(self) -> int:
        """0 vert, 1 chaîne épuisée, 2 refus — voir la surface de la commande."""
        if self.dry_run:
            return 0
        if self.refusal is not None:
            return 2
        return 0 if self.succeeded else 1

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "task_id": self.task_id,
            "verifiability": self.verifiability,
            "dry_run": self.dry_run,
            "planned_chain": list(self.planned_chain),
            "prompt": self.prompt,
            "attempts": [a.to_dict() for a in self.attempts],
            "exit_code": self.exit_code,
        }
        if self.refusal is not None:
            data["refusal"] = self.refusal
            data["refusal_reason"] = self.refusal_message
        if self.transitioned_to is not None:
            data["transitioned_to"] = self.transitioned_to
        if self.transition_refused is not None:
            data["transition_refused"] = self.transition_refused
        return data


def _run_provider_call(
    project_root: Path, provider: ProviderSpec, model: str, prompt: str, *, call_timeout: float
) -> tuple[int | None, str, str, float, str | None]:
    """Exécute l'invocation rendue ; rend (code, stdout, stderr, durée, kind d'échec).

    *kind* vaut ``"timeout"``, ``"rate_limit"`` (sortie évoquant un 429 ou
    une limite), ``"error"`` (code non nul sans trace de limite) ou ``None``
    (appel réussi). ``invocation`` est
    garanti non vide par ``candidates()`` en amont ; ``shlex.split`` d'une
    chaîne vide rendrait de toute façon une commande vide, refusée par
    ``subprocess`` — pas de garde supplémentaire nécessaire ici.
    """
    argv = render_invocation(provider.invocation or "", prompt=prompt, model=model)
    started = time.monotonic()
    try:
        completed = subprocess.run(
            argv, cwd=project_root, capture_output=True, text=True, timeout=call_timeout, shell=False
        )
    except subprocess.TimeoutExpired:
        return None, "", "", time.monotonic() - started, "timeout"
    duration = time.monotonic() - started
    # Une saturation (429, quota) et une panne locale (CLI absent, script qui
    # plante) n'ont pas le même sens pour l'état runtime : la première dit que
    # le fournisseur est bon mais occupé, la seconde qu'il est cassé ici. Les
    # deux refroidissent, mais sous un nom distinct, pour que `providers
    # status` et l'historique du lot 4 ne confondent pas les deux.
    if _looks_rate_limited(completed.stdout) or _looks_rate_limited(completed.stderr):
        kind = "rate_limit"
    elif completed.returncode != 0:
        kind = "error"
    else:
        kind = None
    return completed.returncode, completed.stdout, completed.stderr, duration, kind


def _run_checks(checks: tuple[str, ...], *, project_root: Path) -> tuple[CheckResult, ...]:
    """Chaque ``--check`` en ``shell=True`` — c'est l'utilisateur qui l'a écrit.

    Toutes tournent, même après un premier échec : le rapport final doit
    montrer l'état complet, pas seulement la première commande rouge.
    """
    results: list[CheckResult] = []
    for cmd in checks:
        try:
            # shell=True est délibéré : `cmd` est la commande de vérification que
            # l'utilisateur a lui-même écrite via `--check`, pas une entrée non
            # fiable — même contrat que `subprocess` ailleurs pour les checks de
            # gate. Le rendu du fournisseur, lui, reste `shell=False` (voir
            # `_run_provider_call`) car le prompt qui le traverse n'est pas écrit
            # par l'utilisateur au clavier de cette commande.
            completed = subprocess.run(cmd, cwd=project_root, shell=True, timeout=CHECK_TIMEOUT_S)  # noqa: S602
            ok = completed.returncode == 0
        except subprocess.TimeoutExpired:
            ok = False
        results.append(CheckResult(cmd=cmd, ok=ok))
    return tuple(results)


def _dispatch_event_payload(attempt: DispatchAttempt, task_id: str) -> dict[str, Any]:
    payload = attempt.to_dict()
    payload["task_id"] = task_id
    return payload


def run_dispatch(
    service: TaskService,
    task_id: str,
    *,
    checks: tuple[str, ...] = (),
    max_tier: str | None = None,
    provider_id: str | None = None,
    dry_run: bool = False,
    call_timeout: float = DEFAULT_CALL_TIMEOUT_S,
    actor: str = "cli",
) -> DispatchReport:
    """Cascade la tâche *task_id* à travers les paliers de fournisseurs.

    Refuse avant tout appel si la classe est V2 ou si ``checks`` est vide.
    Sinon, essaie chaque palier de la chaîne, du moins cher au plus cher (ou
    depuis ``mid`` pour une tâche V1) : à chaque palier, chaque fournisseur
    disponible est tenté jusqu'à un appel qui réussit ; un check rouge fait
    passer au palier suivant (le fournisseur n'est pas en cause, le résultat
    l'est), un échec d'appel (429, timeout) fait passer au fournisseur
    suivant du même palier. Chaque tentative — y compris un échec d'appel —
    laisse un événement ``task.dispatched`` au ledger.
    """
    task = service.require(task_id)
    verifiability = classify(task)
    prompt = build_prompt(task)

    start_tier = start_tier_for(verifiability)
    if start_tier is None:
        return DispatchReport(
            task_id=task_id,
            verifiability=verifiability.value,
            dry_run=dry_run,
            planned_chain=(),
            prompt=prompt,
            refusal="v2",
        )
    if not checks:
        return DispatchReport(
            task_id=task_id,
            verifiability=verifiability.value,
            dry_run=dry_run,
            planned_chain=(),
            prompt=prompt,
            refusal="no_check",
        )

    chain = _tier_chain(start_tier, max_tier)
    if not chain:
        return DispatchReport(
            task_id=task_id,
            verifiability=verifiability.value,
            dry_run=dry_run,
            planned_chain=(),
            prompt=prompt,
            refusal="no_tier",
        )

    if dry_run:
        return DispatchReport(
            task_id=task_id,
            verifiability=verifiability.value,
            dry_run=True,
            planned_chain=chain,
            prompt=prompt,
        )

    root = service.project_root
    attempts: list[DispatchAttempt] = []
    attempt_no = 0
    for tier in chain:
        # Un fournisseur sans `invocation` déclarée n'est jamais candidat ici :
        # `candidates()` sert aussi `providers status`, où un fournisseur sans
        # commande d'appel reste une information utile à afficher — seule la
        # cascade, qui doit réellement l'exécuter, a besoin de l'exclure.
        tier_candidates = tuple(p for p in provider_candidates(root, tier) if p.invocation)
        if provider_id:
            tier_candidates = tuple(p for p in tier_candidates if p.id == provider_id)
        tier_settled = False
        for provider in tier_candidates:
            model = provider.models_for_tier(tier)[0].id
            attempt_no += 1
            code, stdout, _stderr, duration, failure_kind = _run_provider_call(
                root, provider, model, prompt, call_timeout=call_timeout
            )
            if failure_kind is not None:
                record_failure(root, provider.id, kind=failure_kind)
                attempt = DispatchAttempt(
                    attempt=attempt_no,
                    tier=tier,
                    provider=provider.id,
                    model=model,
                    exit_code=code,
                    duration_s=duration,
                    checks=(),
                    verdict=failure_kind,
                    cost_usd=None,
                )
                attempts.append(attempt)
                service.ledger.append_event(
                    "task.dispatched", task_id, "task", actor, _dispatch_event_payload(attempt, task_id)
                )
                continue  # fournisseur suivant, même palier

            record_success(root, provider.id)
            check_results = _run_checks(checks, project_root=root)
            green = all(c.ok for c in check_results)
            attempt = DispatchAttempt(
                attempt=attempt_no,
                tier=tier,
                provider=provider.id,
                model=model,
                exit_code=code,
                duration_s=duration,
                checks=check_results,
                verdict="green" if green else "red",
                cost_usd=_extract_cost_usd(stdout),
            )
            attempts.append(attempt)
            service.ledger.append_event(
                "task.dispatched", task_id, "task", actor, _dispatch_event_payload(attempt, task_id)
            )
            tier_settled = True
            break  # appel réussi : ce palier a son verdict, vert ou rouge

        if tier_settled and attempts[-1].verdict == "green":
            break  # succès : la cascade s'arrête ici
        # rouge, ou palier épuisé sans appel réussi : palier suivant

    if not attempts:
        # Aucun fournisseur candidat sur toute la chaîne : la cascade n'a
        # jamais appelé personne. Distinct d'une chaîne épuisée (des
        # tentatives ont eu lieu, aucune n'est allée au vert) — c'est un
        # refus, pas un échec de la délégation elle-même.
        return DispatchReport(
            task_id=task_id,
            verifiability=verifiability.value,
            dry_run=False,
            planned_chain=chain,
            prompt=prompt,
            refusal="no_provider",
        )

    report = DispatchReport(
        task_id=task_id,
        verifiability=verifiability.value,
        dry_run=False,
        planned_chain=chain,
        prompt=prompt,
        attempts=tuple(attempts),
    )
    if not report.succeeded:
        return report
    if verifiability is not Verifiability.V1:
        return report
    try:
        service.transition(
            task_id,
            TaskState.NEEDS_VERIFICATION,
            actor,
            reason="grimoire task dispatch : cascade verte, vérification humaine requise (V1)",
        )
    except GrimoireMissionError as exc:
        return DispatchReport(
            task_id=report.task_id,
            verifiability=report.verifiability,
            dry_run=False,
            planned_chain=report.planned_chain,
            prompt=report.prompt,
            attempts=report.attempts,
            transition_refused=str(exc),
        )
    return DispatchReport(
        task_id=report.task_id,
        verifiability=report.verifiability,
        dry_run=False,
        planned_chain=report.planned_chain,
        prompt=report.prompt,
        attempts=report.attempts,
        transitioned_to=TaskState.NEEDS_VERIFICATION.value,
    )
