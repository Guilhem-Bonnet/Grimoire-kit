"""CLI ``grimoire providers`` — disponibilité et routage par palier de coût.

Enveloppe ``grimoire.providers`` (issue #310, lot 2) : ``status`` répond à
« qui puis-je appeler maintenant, pour quel palier ? » en croisant le
registre déclaratif et l'état de refroidissement runtime ; ``cooldown``
enregistre un échec à la main, pour les hooks et scripts qui viennent de voir
un 429 ou un timeout et n'ont pas de raison d'attendre le prochain appel
raté pour que ``choose()`` en tienne compte. ``audit`` (issue #330) sonde les
fournisseurs activés sans dépenser (PATH, ``--version``, modèles Ollama) et
journalise le résultat dans l'état — ``status`` l'affiche ensuite. ``history``
(issue #312) compte les dispatchs passés par couple (type de tâche, classe de
vérifiabilité) depuis le Mission Ledger, et la recommandation de palier de
départ que ``grimoire task dispatch`` en tire.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table

from grimoire.providers.audit import audit_providers
from grimoire.providers.registry import SUPPORTED_MODEL_TIERS, ProviderRegistryError, ProviderSpec, read_registry
from grimoire.providers.routing import choose
from grimoire.providers.state import ProviderRuntimeState, load_state, record_failure

providers_app = typer.Typer(
    help="Fournisseurs LLM : disponibilité par palier de coût, refroidissement après échec.",
    no_args_is_help=True,
)
console = Console()

_PROJECT_ROOT_OPTION = typer.Option("--project-root", help="Racine du projet.", show_default=False)
_JSON_OPTION = typer.Option("--json", help="Sortie JSON.")
_REASON_OPTION = typer.Option("--reason", help="Motif de l'échec : rate_limit | timeout.")

#: Même défaut que ``grimoire task`` (``cmd_task._DEFAULT_LEDGER``) — l'historique
#: des dispatchs (issue #312) lit le même Mission Ledger que `task dispatch` écrit.
_DEFAULT_LEDGER = Path("_grimoire-runtime-output/ledger")
_LEDGER_ROOT_OPTION = typer.Option("--ledger-root", help="Racine du Mission Ledger.")

#: Motifs reconnus par la table de refroidissement (grimoire.providers.state).
#: Un autre libellé est accepté (repli 5 min) mais on préfère le signaler ici
#: plutôt que laisser un fournisseur silencieusement mal calibré.
_KNOWN_REASONS = ("rate_limit", "timeout")


def _get_fmt(ctx: typer.Context) -> str:
    """Format hérité du ``-o/--output`` global, si l'appelant ne force pas ``--json``."""
    return str((ctx.obj or {}).get("output", "text") or "text")


def _state_label(provider_id: str, state: dict[str, ProviderRuntimeState], *, now: datetime) -> str:
    entry = state.get(provider_id)
    if entry is not None and entry.is_cooling_down(now=now):
        until = entry.cooldown_until.isoformat(timespec="seconds") if entry.cooldown_until else "?"
        return f"[yellow]refroidi jusqu'à {until}[/yellow]"
    return "[green]disponible[/green]"


def _availability_label(provider_id: str, state: dict[str, ProviderRuntimeState]) -> str:
    """Ce que le dernier ``providers audit`` sait de *provider_id* — pas le refroidissement."""
    entry = state.get(provider_id)
    if entry is None or entry.probed_at is None:
        return "[dim]non sondé[/dim]"
    return "[green]oui[/green]" if entry.available else "[red]non[/red]"


def _models_by_tier(provider: ProviderSpec) -> str:
    parts = [
        f"{tier}: " + ", ".join(model.id for model in provider.models_for_tier(tier))
        for tier in SUPPORTED_MODEL_TIERS
        if provider.models_for_tier(tier)
    ]
    return " · ".join(parts) if parts else "—"


def _provider_json(provider: ProviderSpec, state: dict[str, ProviderRuntimeState], *, now: datetime) -> dict[str, Any]:
    entry = state.get(provider.id)
    cooling_down = entry is not None and entry.is_cooling_down(now=now)
    return {
        "id": provider.id,
        "enabled": provider.enabled,
        "currency": provider.currency,
        "invocation": provider.invocation,
        "models": [{"id": model.id, "tier": model.tier} for model in provider.models],
        "cooling_down": cooling_down,
        "cooldown_until": entry.cooldown_until.isoformat() if entry and entry.cooldown_until else None,
        "failure_count": entry.failure_count if entry else 0,
        # Issue #330 : dernier résultat de `providers audit`, jamais du registre.
        "available": entry.available if entry is not None else True,
        "probed_at": entry.probed_at if entry is not None else None,
        "models_seen": list(entry.models_seen) if entry is not None else [],
        "probe_note": entry.probe_note if entry is not None else None,
    }


@providers_app.command("status")
def providers_status(
    ctx: typer.Context,
    project_root: Annotated[Path, _PROJECT_ROOT_OPTION] = Path(),
    json_output: Annotated[bool, _JSON_OPTION] = False,
) -> None:
    """Fournisseurs déclarés : activation, monnaie, modèles par palier, disponibilité."""
    root = project_root.resolve()
    try:
        providers = read_registry(root)
    except ProviderRegistryError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    now = datetime.now(UTC)
    state = load_state(root)
    next_choice = {tier: choose(root, tier, now=now) for tier in SUPPORTED_MODEL_TIERS}

    if json_output or _get_fmt(ctx) == "json":
        payload = {
            "providers": [_provider_json(provider, state, now=now) for provider in providers],
            "next_choice": {tier: (spec.id if spec else None) for tier, spec in next_choice.items()},
        }
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return

    if not providers:
        console.print("[dim]Aucun registre de fournisseurs (`grimoire standard init`).[/dim]")
        return

    table = Table(title="Fournisseurs LLM")
    for column in ("Fournisseur", "Activé", "Monnaie", "Modèles par palier", "Disponible (audit)", "Refroidissement"):
        table.add_column(column)
    for provider in providers:
        table.add_row(
            provider.id,
            "[green]oui[/green]" if provider.enabled else "[dim]non[/dim]",
            provider.currency or "—",
            _models_by_tier(provider),
            _availability_label(provider.id, state),
            _state_label(provider.id, state, now=now),
        )
    console.print(table)

    console.print("\n[bold]Prochain choix par palier[/bold]")
    for tier in SUPPORTED_MODEL_TIERS:
        spec = next_choice[tier]
        label = spec.id if spec is not None else "[yellow]aucun fournisseur disponible[/yellow]"
        console.print(f"  {tier}: {label}")


@providers_app.command("cooldown")
def providers_cooldown(
    provider_id: Annotated[str, typer.Argument(help="Identifiant du fournisseur (voir `grimoire providers status`).")],
    project_root: Annotated[Path, _PROJECT_ROOT_OPTION] = Path(),
    reason: Annotated[str, _REASON_OPTION] = "rate_limit",
) -> None:
    """Enregistrer manuellement un échec (429, timeout...) pour ce fournisseur.

    Utile depuis un hook ou un script qui vient de voir l'appel échouer :
    ``choose()`` écarte ce fournisseur jusqu'à l'expiration du
    refroidissement sans attendre qu'il retente lui-même.
    """
    if reason not in _KNOWN_REASONS:
        console.print(
            f"[yellow]![/yellow] motif {reason!r} non reconnu ({', '.join(_KNOWN_REASONS)}) — "
            "refroidissement par défaut appliqué quand même."
        )
    root = project_root.resolve()
    entry = record_failure(root, provider_id, reason)
    until = entry.cooldown_until.isoformat(timespec="seconds") if entry.cooldown_until else "?"
    console.print(
        f"[yellow]●[/yellow] {provider_id} refroidi jusqu'à {until} (échec n°{entry.failure_count}, motif {reason})"
    )


@providers_app.command("audit")
def providers_audit(
    ctx: typer.Context,
    project_root: Annotated[Path, _PROJECT_ROOT_OPTION] = Path(),
    json_output: Annotated[bool, _JSON_OPTION] = False,
) -> None:
    """Sonder les fournisseurs activés sans dépenser (issue #330).

    Pour chaque fournisseur activé : présence du premier mot de
    ``invocation`` sur le ``PATH``, ``--version`` pour un exécutable connu,
    modèles Ollama via ``GET /api/tags`` pour un fournisseur local — jamais
    l'invocation complète, jamais de prompt. Le résultat (``available``,
    ``models_seen``, ``probe_note``) est écrit dans l'état runtime ;
    ``enabled`` reste une décision de gouvernance que l'audit ne touche pas.
    """
    root = project_root.resolve()
    try:
        results = audit_providers(root)
    except ProviderRegistryError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    if json_output or _get_fmt(ctx) == "json":
        payload = {
            "providers": [
                {
                    "id": result.provider_id,
                    "available": result.available,
                    "models_seen": list(result.models_seen),
                    "probe_note": result.probe_note,
                }
                for result in results
            ],
        }
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return

    if not results:
        console.print("[dim]Aucun fournisseur activé à sonder (`grimoire providers status`).[/dim]")
        return

    table = Table(title="Audit fournisseurs")
    for column in ("Fournisseur", "Disponible", "Modèles vus", "Note"):
        table.add_column(column)
    for result in results:
        table.add_row(
            result.provider_id,
            "[green]oui[/green]" if result.available else "[red]non[/red]",
            ", ".join(result.models_seen) if result.models_seen else "—",
            result.probe_note,
        )
    console.print(table)


def _escalation_label(stats: Any | None) -> str:
    """Colonne « escalade depuis X » — ``—`` sans observation à ce palier."""
    if stats is None or stats.rate is None:
        return "—"
    return f"{stats.rate:.0%} ({stats.escalations}/{stats.observations})"


@providers_app.command("history")
def providers_history(
    ctx: typer.Context,
    project_root: Annotated[Path, _PROJECT_ROOT_OPTION] = Path(),
    ledger_root: Annotated[Path, _LEDGER_ROOT_OPTION] = _DEFAULT_LEDGER,
    json_output: Annotated[bool, _JSON_OPTION] = False,
) -> None:
    """Historique des dispatchs par couple (type de tâche, classe) — issue #312.

    Compte, pour chaque couple observé dans le Mission Ledger, le nombre de
    dispatchs et le taux d'escalade (part des dispatchs où le palier de
    départ n'a pas suffi) depuis `cheap` et depuis `mid`. Pas de classifieur,
    pas d'entraînement — des compteurs. La colonne « départ recommandé » est
    exactement ce que le prochain `grimoire task dispatch` de ce couple
    retiendra, sauf `--start-tier` explicite.
    """
    from grimoire.missions.dispatch_history import compute_dispatch_history
    from grimoire.missions.ledger import MissionLedger

    root = project_root.resolve()
    ledger_path = ledger_root if ledger_root.is_absolute() else root / ledger_root
    as_json = json_output or _get_fmt(ctx) == "json"

    if not (ledger_path / "events.jsonl").is_file():
        if as_json:
            typer.echo(json.dumps({"couples": []}, indent=2, ensure_ascii=False))
            return
        console.print(f"[dim]Aucun Mission Ledger sous {ledger_path}.[/dim]")
        return

    histories = compute_dispatch_history(MissionLedger(ledger_path))

    if as_json:
        typer.echo(json.dumps({"couples": [h.to_dict() for h in histories]}, indent=2, ensure_ascii=False))
        return

    if not histories:
        console.print("[dim]Aucun dispatch enregistré dans le Mission Ledger (`grimoire task dispatch`).[/dim]")
        return

    table = Table(title="Historique des dispatchs")
    for column in ("Type de tâche", "Classe", "Observations", "Escalade cheap", "Escalade mid", "Départ recommandé"):
        table.add_column(column)
    for history in histories:
        table.add_row(
            history.task_type,
            history.verifiability,
            str(history.observations),
            _escalation_label(history.by_start_tier.get("cheap")),
            _escalation_label(history.by_start_tier.get("mid")),
            history.recommended_start_tier,
        )
    console.print(table)
    for history in histories:
        console.print(f"[dim]{history.task_type}/{history.verifiability} : {history.reason}[/dim]")
