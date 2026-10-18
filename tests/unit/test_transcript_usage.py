"""W4-01 : l'usage de tokens est replié depuis le journal de session de l'hôte.

Avant, le coût d'une tâche ne venait que du JSON d'un ouvrier ou d'un
``record-model-call`` lancé à la main : une session interactive ne laissait aucun
chiffre. Le format des lignes est celui relevé par K0 (fixture
``tests/fixtures/hosts/claude-code/08-token-usage.json``) et relu sur un journal
réel : une ligne ``assistant`` par bloc de contenu, le même ``usage`` répété sur
chacune des lignes d'un même ``message.id``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.traces.ledger import TraceLedger
from grimoire.traces.schemas import TokenUsage, TraceOutcome, TraceRecord
from grimoire.traces.transcript_usage import (
    SOURCE_CLAUDE_TRANSCRIPT,
    fold_claude_transcript,
    read_claude_transcript,
    task_usage,
)

runner = CliRunner()
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "hosts" / "claude-code" / "08-token-usage.json"

#: Sans cache : tarifable à la version exacte (sonnet-5-5 : 2 $ / 10 $ par million).
PLAIN_USAGE = {"input_tokens": 1000, "output_tokens": 500, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}


def _k0_usage() -> dict[str, Any]:
    """L'échantillon d'usage d'une ligne assistant, tel que K0 l'a capturé (avec cache)."""
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return dict(data["observed"]["transcript_assistant_usage_sample"][0])


def _line(
    message_id: str,
    uuid: str,
    usage: dict[str, Any],
    *,
    model: str = "claude-sonnet-5-5",
    block: str = "text",
    timestamp: str = "2026-10-16T14:47:37.997Z",
    agent: str = "",
) -> str:
    return json.dumps(
        {
            "type": "assistant",
            "uuid": uuid,
            "sessionId": "00000000-0000-4000-8000-000000000010",
            "timestamp": timestamp,
            **({"attributionAgent": agent} if agent else {}),
            "message": {"id": message_id, "model": model, "role": "assistant", "content": [{"type": block}], "usage": usage},
        }
    )


def _write(path: Path, *lines: str) -> Path:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def ledger(tmp_path: Path) -> TraceLedger:
    return TraceLedger(tmp_path / "traces")


def test_deux_replis_identiques_ne_changent_rien(tmp_path: Path, ledger: TraceLedger) -> None:
    journal = _write(tmp_path / "s.jsonl", _line("msg_1", "u1", _k0_usage()), _line("msg_2", "u2", PLAIN_USAGE))

    premier = fold_claude_transcript(ledger, journal, task_id="GAO-1")
    avant = (tmp_path / "traces" / "traces.jsonl").read_text(encoding="utf-8")
    second = fold_claude_transcript(ledger, journal, task_id="GAO-1")

    assert (premier.new_calls, premier.already_folded) == (2, 0)
    assert (second.new_calls, second.already_folded) == (0, 2)
    assert (tmp_path / "traces" / "traces.jsonl").read_text(encoding="utf-8") == avant


def test_un_groupe_de_blocs_n_est_compte_qu_une_fois(tmp_path: Path, ledger: TraceLedger) -> None:
    """Réflexion, texte et deux outils : quatre lignes, un appel, un seul ``usage``."""
    journal = _write(
        tmp_path / "s.jsonl",
        *(_line("msg_1", f"u{i}", PLAIN_USAGE, block=b) for i, b in enumerate(("thinking", "text", "tool_use", "tool_use"))),
    )

    fold_claude_transcript(ledger, journal, task_id="GAO-1")

    usage = task_usage(ledger.list_traces(task_id="GAO-1"))
    assert (usage["calls"], usage["input_tokens"], usage["output_tokens"]) == (1, 1000, 500)


def test_un_groupe_coupe_entre_deux_replis_n_est_compte_qu_une_fois(tmp_path: Path, ledger: TraceLedger) -> None:
    journal = _write(tmp_path / "s.jsonl", _line("msg_1", "u1", PLAIN_USAGE, block="thinking"))
    fold_claude_transcript(ledger, journal, task_id="GAO-1")

    # Le journal grandit : la suite du groupe msg_1, puis un nouvel appel.
    with journal.open("a", encoding="utf-8") as handle:
        handle.write(_line("msg_1", "u2", PLAIN_USAGE, block="tool_use") + "\n")
        handle.write(_line("msg_2", "u3", PLAIN_USAGE) + "\n")
    second = fold_claude_transcript(ledger, journal, task_id="GAO-1")

    assert (second.new_calls, second.already_folded) == (1, 1)
    assert task_usage(ledger.list_traces(task_id="GAO-1"))["calls"] == 2


def test_memes_identifiants_dans_deux_fichiers_restent_deux_appels(tmp_path: Path, ledger: TraceLedger) -> None:
    ligne = _line("msg_1", "u1", PLAIN_USAGE)
    principal = _write(tmp_path / "principal.jsonl", ligne)
    sous_agent = _write(tmp_path / "agent-a1.jsonl", ligne)

    fold_claude_transcript(ledger, principal, task_id="GAO-1")
    fold_claude_transcript(ledger, sous_agent, task_id="GAO-1")

    assert task_usage(ledger.list_traces(task_id="GAO-1"))["calls"] == 2


def test_usage_source_et_cache_sont_exposes(tmp_path: Path, ledger: TraceLedger) -> None:
    usage_k0 = _k0_usage()
    journal = _write(tmp_path / "s.jsonl", _line("msg_1", "u1", usage_k0))

    fold_claude_transcript(ledger, journal, task_id="GAO-1")
    usage = task_usage(ledger.list_traces(task_id="GAO-1"))

    assert usage["usage_source"] == SOURCE_CLAUDE_TRANSCRIPT
    assert usage["input_tokens"] == usage_k0["input_tokens"]
    assert usage["output_tokens"] == usage_k0["output_tokens"]
    assert usage["cache_read_tokens"] == usage_k0["cache_read_input_tokens"]
    assert usage["cache_creation_tokens"] == usage_k0["cache_creation_input_tokens"]
    # La table de tarifs ne modélise pas le cache : le coût est inconnu, pas un prix d'entrée inventé.
    assert usage["cost_known"] is False
    assert usage["cost_usd"] is None
    assert usage["cost_status"] == "unknown"


def test_cout_connu_seulement_si_tout_est_tarife(tmp_path: Path, ledger: TraceLedger) -> None:
    journal = _write(
        tmp_path / "s.jsonl",
        _line("msg_1", "u1", PLAIN_USAGE),
        _line("msg_2", "u2", PLAIN_USAGE, model="claude-sonnet-5-5-20261001"),
    )
    fold_claude_transcript(ledger, journal, task_id="GAO-1")

    usage = task_usage(ledger.list_traces(task_id="GAO-1"))
    assert usage["cost_known"] is True
    assert usage["cost_usd"] == pytest.approx(2 * (1000 * 2.0 + 500 * 10.0) / 1_000_000)

    inconnu = _write(tmp_path / "t.jsonl", _line("msg_9", "u9", PLAIN_USAGE, model="modele-sans-tarif"))
    fold_claude_transcript(ledger, inconnu, task_id="GAO-1")
    mixte = task_usage(ledger.list_traces(task_id="GAO-1"))
    assert mixte["cost_known"] is False
    assert mixte["cost_status"] == "lower_bound"
    assert mixte["unpriced_calls"] == 1


def test_compteurs_invalides_sont_ecartes_et_comptes(tmp_path: Path, ledger: TraceLedger) -> None:
    mauvais_bool = {**PLAIN_USAGE, "output_tokens": True}
    mauvais_negatif = {**PLAIN_USAGE, "input_tokens": -4}
    sans_usage = json.dumps({"type": "assistant", "uuid": "u0", "message": {"id": "msg_0", "model": "claude-sonnet-5-5"}})
    synthetique = _line("msg_s", "us", PLAIN_USAGE, model="<synthetic>")
    journal = _write(
        tmp_path / "s.jsonl",
        _line("msg_1", "u1", PLAIN_USAGE),
        _line("msg_2", "u2", mauvais_bool),
        _line("msg_3", "u3", mauvais_negatif),
        sans_usage,
        synthetique,
        '{"type": "assistant", "uuid": "tronq',
    )

    lecture = read_claude_transcript(journal)
    resultat = fold_claude_transcript(ledger, journal, task_id="GAO-1")

    assert [c.message_id for c in lecture.calls] == ["msg_1"]
    assert (resultat.invalid_usage, resultat.unreadable_lines, resultat.synthetic) == (3, 1, 1)
    assert resultat.new_calls == 1


def test_substitut_isole_est_encodable_en_utf8_strict(tmp_path: Path, ledger: TraceLedger) -> None:
    """Un ``message.id`` à substitut isolé ne doit pas faire échouer l'écriture du journal de traces."""
    lone = chr(0xD800)
    brut = json.dumps({"type": "assistant", "uuid": "u1", "message": {"id": f"msg_{lone}", "model": f"m{lone}", "usage": PLAIN_USAGE}})
    journal = tmp_path / "s.jsonl"
    journal.write_bytes((brut + "\n").encode("utf-8", "surrogatepass"))

    resultat = fold_claude_transcript(ledger, journal, task_id="GAO-1")

    assert resultat.new_calls == 1
    (tmp_path / "traces" / "traces.jsonl").read_bytes().decode("utf-8")  # strict : lève sinon


def test_ecart_avec_le_cout_rapporte_est_signale() -> None:
    def trace(recipe: str, cost: float, tags: tuple[str, ...] = ()) -> TraceRecord:
        return TraceRecord(
            id=f"TRC-{recipe}-{cost}",
            run_id="r",
            workflow_instance_id="",
            mission_id="",
            task_id="GAO-1",
            recipe_id=recipe,
            outcome=TraceOutcome.SUCCESS,
            started_at="2026-10-16T00:00:00+00:00",
            token_usage=TokenUsage(prompt_tokens=10, completion_tokens=10, total_tokens=20, estimated_cost_usd=cost),
            tags=tags,
        )

    folded = trace("grimoire.model-call.transcript", 1.0, ("usage.source:claude-code-transcript",))
    assert task_usage([folded, trace("grimoire.dispatch", 1.05)])["discrepancy"] is None
    ecart = task_usage([folded, trace("grimoire.dispatch", 2.0)])["discrepancy"]
    assert ecart == {"transcript_cost_usd": 1.0, "reported_cost_usd": 2.0, "relative_gap": 0.5}


def test_hote_sans_source_se_declare_indisponible_et_rien_replie_se_dit() -> None:
    assert task_usage([], host_usage_available=False)["usage_source"] == "unavailable"
    assert task_usage([], host_usage_available=True)["usage_source"] == "none"
    assert task_usage([])["usage_source"] == "none"
    assert task_usage([])["cost_known"] is False


def _task(projet: Path) -> str:
    res = runner.invoke(app, ["task", "add", "Tâche mesurée", "-a", "la suite passe", "--project-root", str(projet)])
    assert res.exit_code == 0, res.output
    return next(mot for mot in res.output.split() if mot.startswith("GAO-"))


def test_cli_fold_usage_puis_task_show_json_expose_tokens_et_cost_known(tmp_path: Path) -> None:
    tid = _task(tmp_path)
    journal = _write(tmp_path / "s.jsonl", _line("msg_1", "u1", PLAIN_USAGE), _line("msg_2", "u2", _k0_usage()))

    for _ in range(2):  # rejouée : même résultat
        res = runner.invoke(app, ["--output", "json", "task", "fold-usage", tid, str(journal), "--project-root", str(tmp_path)])
        assert res.exit_code == 0, res.output
    assert json.loads(res.output)["new_calls"] == 0

    shown = runner.invoke(app, ["--output", "json", "task", "show", tid, "--project-root", str(tmp_path)])
    usage = json.loads(shown.output)["usage"]
    assert usage["usage_source"] == SOURCE_CLAUDE_TRANSCRIPT
    assert usage["calls"] == 2
    assert usage["input_tokens"] == 1000 + _k0_usage()["input_tokens"]
    assert usage["cost_known"] is False  # le second appel a du cache


def test_cli_fold_usage_refuse_une_tache_inconnue_et_un_journal_absent(tmp_path: Path) -> None:
    tid = _task(tmp_path)
    journal = _write(tmp_path / "s.jsonl", _line("msg_1", "u1", PLAIN_USAGE))

    inconnue = runner.invoke(app, ["task", "fold-usage", "GAO-nope-001", str(journal), "--project-root", str(tmp_path)])
    absent = runner.invoke(app, ["task", "fold-usage", tid, str(tmp_path / "absent.jsonl"), "--project-root", str(tmp_path)])

    assert inconnue.exit_code != 0
    assert absent.exit_code == 1
    assert not (tmp_path / "_grimoire-output" / "traces" / "traces.jsonl").exists()


def test_task_show_ne_cree_pas_le_dossier_de_traces(tmp_path: Path) -> None:
    tid = _task(tmp_path)

    res = runner.invoke(app, ["--output", "json", "task", "show", tid, "--project-root", str(tmp_path)])

    assert json.loads(res.output)["usage"]["usage_source"] == "none"
    assert not (tmp_path / "_grimoire-output" / "traces").exists()


def test_l_agent_du_journal_ne_gonfle_pas_l_usage_des_agents(tmp_path: Path) -> None:
    """``attributionAgent`` est porté par chaque appel API : 40 appels ne sont pas 40 choix d'agent."""
    from grimoire.core.standard_generation import TRACES_DIR
    from grimoire.tools.workspace_api import _agent_usage

    journal = _write(
        tmp_path / "s.jsonl",
        *(_line(f"msg_{i}", f"u{i}", PLAIN_USAGE, agent="security-auditor") for i in range(40)),
    )
    projet = tmp_path / "projet"
    resultat = fold_claude_transcript(TraceLedger(projet / TRACES_DIR), journal, task_id="GAO-1")

    assert resultat.new_calls == 40
    assert _agent_usage(projet) == {}
    # L'attribution n'est pas perdue : elle reste lisible, en tag.
    traces = TraceLedger(projet / TRACES_DIR).list_traces(task_id="GAO-1")
    assert all(t.agent_id == "" for t in traces)
    assert all("usage.agent:security-auditor" in t.tags for t in traces)


def _deux_taches_un_journal(tmp_path: Path) -> Path:
    return _write(
        tmp_path / "s.jsonl",
        _line("msg_a1", "a1", PLAIN_USAGE, timestamp="2026-10-16T09:00:00Z"),
        _line("msg_a2", "a2", PLAIN_USAGE, timestamp="2026-10-16T09:30:00Z"),
        _line("msg_b1", "b1", PLAIN_USAGE, timestamp="2026-10-16T14:00:00Z"),
        _line("msg_b2", "b2", PLAIN_USAGE, timestamp="2026-10-16T14:30:00Z"),
    )


def test_un_journal_sur_deux_fenetres_se_repartit_entre_deux_taches(tmp_path: Path, ledger: TraceLedger) -> None:
    journal = _deux_taches_un_journal(tmp_path)

    a = fold_claude_transcript(ledger, journal, task_id="GAO-A", until="2026-10-16T12:00:00Z")
    b = fold_claude_transcript(ledger, journal, task_id="GAO-B", since="2026-10-16T12:00:00Z")

    assert (a.new_calls, a.outside_window) == (2, 2)
    assert (b.new_calls, b.outside_window, b.held_by_other_tasks) == (2, 2, {})
    assert task_usage(ledger.list_traces(task_id="GAO-A"))["calls"] == 2
    assert task_usage(ledger.list_traces(task_id="GAO-B"))["calls"] == 2


def test_sans_fenetre_les_appels_deja_tenus_par_une_autre_tache_sont_nommes(tmp_path: Path, ledger: TraceLedger) -> None:
    journal = _deux_taches_un_journal(tmp_path)

    fold_claude_transcript(ledger, journal, task_id="GAO-A")
    b = fold_claude_transcript(ledger, journal, task_id="GAO-B")
    a_nouveau = fold_claude_transcript(ledger, journal, task_id="GAO-A")

    assert (b.new_calls, b.already_folded, b.held_by_other_tasks) == (0, 4, {"GAO-A": 4})
    assert b.to_dict()["held_by_other_tasks"] == {"GAO-A": 4}
    assert a_nouveau.held_by_other_tasks == {}  # rejouer sa propre tâche n'est pas un conflit
    assert task_usage(ledger.list_traces(task_id="GAO-B"))["calls"] == 0


def test_fenetre_illisible_ou_vide_est_refusee_et_un_appel_sans_date_est_ecarte(tmp_path: Path, ledger: TraceLedger) -> None:
    journal = _write(tmp_path / "s.jsonl", _line("msg_1", "u1", PLAIN_USAGE, timestamp="pas une date"))

    with pytest.raises(ValueError, match="illisible"):
        fold_claude_transcript(ledger, journal, task_id="GAO-1", since="hier")
    with pytest.raises(ValueError, match="vide"):
        fold_claude_transcript(ledger, journal, task_id="GAO-1", since="2026-10-17T00:00:00Z", until="2026-10-16T00:00:00Z")
    sans_date = fold_claude_transcript(ledger, journal, task_id="GAO-1", since="2026-10-16T00:00:00Z")
    assert (sans_date.new_calls, sans_date.outside_window) == (0, 1)


def test_cli_fold_usage_repartit_par_fenetre_et_nomme_le_detenteur(tmp_path: Path) -> None:
    tid_a, tid_b = _task(tmp_path), _task(tmp_path)
    journal = _deux_taches_un_journal(tmp_path)
    base = ["task", "fold-usage"]
    racine = ["--project-root", str(tmp_path)]

    a = runner.invoke(app, ["--output", "json", *base, tid_a, str(journal), "--until", "2026-10-16T12:00:00Z", *racine])
    b_sans = runner.invoke(app, [*base, tid_b, str(journal), *racine])
    illisible = runner.invoke(app, [*base, tid_b, str(journal), "--since", "hier", *racine])

    assert json.loads(a.output)["new_calls"] == 2
    assert b_sans.exit_code == 0, b_sans.output
    assert tid_a in b_sans.output
    assert illisible.exit_code == 2
