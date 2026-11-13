"""Le classement en lecture seule relit les mots résolus et refuse ce qu'il ne sait pas lire.

Le classement jugeait une « surface » où tout texte entre guillemets est effacé. Ce
qu'il n'y voyait pas, il l'accordait. Il juge maintenant chaque segment sur ses mots
résolus, guillemets retirés, et échoue fermé : une forme qu'il ne sait pas lire est une
mutation. Un test par classe de défaut, chacun rouge avant le correctif ; à côté, les
lectures courantes qui doivent le rester.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from grimoire.core.agentic_standard import setup_standard_profile
from grimoire.core.standard_checks.evidence_journal import append_evidence_event, build_bash_event, read_evidence_log
from grimoire.core.standard_task_scaffold import scaffold_task_artifacts
from grimoire.hosts.decisions import (
    HookInput,
    Outcome,
    classify_tool,
    decide_evidence_gate,
    decide_evidence_trace,
    run_decision,
)
from grimoire.hosts.decisions.authority_guard import forgery_vector
from grimoire.hosts.events import HookEvent
from grimoire.policies.schemas import MutationClass


def _classe(command: str) -> MutationClass:
    return classify_tool("Bash", {"command": command}).mutation


# ── Un mot cité est jugé comme le programme le reçoit ────────────────────────


@pytest.mark.parametrize(
    "command",
    [
        'find . "-delete"',
        "find . '-exec' rm {} +",
        'git log "--output=f"',
        'sort "-o" sortie entree',
        "'rm' cat",
        '"mv" a b',
        'rtk find . "-delete"',
        "bash -c 'find . \"-delete\"'",
        'uniq "entree" "sortie"',
    ],
)
def test_un_mot_cite_est_juge_comme_le_programme_le_recoit(command: str) -> None:
    assert _classe(command) is not MutationClass.READ_ONLY


# ── Des guillemets que le classement ne décode pas ───────────────────────────


@pytest.mark.parametrize(
    "command",
    [
        "cat $'notes.md'",
        "echo $'\\'' ; rm fichier ; echo \\'",
        "sort -t$'\\t' -k2 f",
        'echo $"texte"',
    ],
)
def test_des_guillemets_que_le_classement_ne_decode_pas_font_une_mutation(command: str) -> None:
    assert _classe(command) is not MutationClass.READ_ONLY


# ── Un commentaire est lu comme un commentaire ───────────────────────────────


@pytest.mark.parametrize(
    "command",
    [
        # lu comme un commentaire (script, ``sh -c``) : son apostrophe n'ouvre aucun texte cité
        "ls # l'état\nrm fichier\n# l'après",
        "git status # d'abord\ntouch drapeau # c'est fait",
        "ls # l'un\ncat <<EOF\n$(rm fichier)\nEOF\n# l'autre",
        # lu comme un caractère ordinaire (zsh interactif) : la suite de la ligne s'exécute
        "echo a # note ; rm fichier",
        "ls # liste && touch drapeau",
    ],
)
def test_ce_qui_suit_un_commentaire_reste_inspecte_dans_les_deux_lectures(command: str) -> None:
    assert _classe(command) is not MutationClass.READ_ONLY


@pytest.mark.parametrize(
    "command",
    [
        "ls # liste du dossier",
        "git status # avant\ngit log -1 # après",
        "grep -n '#' notes.md",
        "echo a#b",
    ],
)
def test_un_commentaire_ne_fait_pas_d_une_lecture_une_mutation(command: str) -> None:
    assert _classe(command) is MutationClass.READ_ONLY


# ── Un verbe qui a une forme qui écrit ou exécute est borné ──────────────────


@pytest.mark.parametrize(
    "command",
    [
        "tree -o sortie.txt",
        "tree -R -H .",
        "xxd entree sortie",
        "xxd -r vidage",
        "rg --pre ./filtre motif",
        "rg --pre-glob '*.pdf' --pre ./filtre motif",
        "rg --hostname-bin ./nom motif",
        "ag --pager ./filtre motif",
        "date -s 2020-01-01",
        "date --set=2020-01-01",
        "date 010112002026",
        "file -C -m magie",
        "less -o journal f",
        "more f",
        "sort --out=sortie f",
        "git grep --open-files-in-pa=less motif",
    ],
)
def test_un_verbe_qui_a_une_forme_qui_ecrit_ou_execute_est_borne(command: str) -> None:
    assert _classe(command) is not MutationClass.READ_ONLY


@pytest.mark.parametrize(
    "command",
    [
        "tree -L 2 -I 'node_modules'",
        "xxd -l 64 f | head",
        "rg --pretty motif src",
        "date +%F",
        "date -d yesterday +%s",
        "date -Iseconds",
        "TZ=UTC date -u",
        "file -b f",
        "find . \\( -name a -o -name b \\)",
        "find . ! -name '*.pyc'",
        "sed '/x/!d' f",
        "git branch --list 'feat/*'",
        "git show HEAD@{1}",
        "git rev-parse v1.0^{commit}",
        "sort f | uniq -c | sort -rn | head",
    ],
)
def test_leurs_formes_de_lecture_restent_des_lectures(command: str) -> None:
    assert _classe(command) is MutationClass.READ_ONLY


# ── Un mot que le shell peut encore changer ──────────────────────────────────


@pytest.mark.parametrize(
    "command",
    [
        # une variable peut porter une option qui écrit
        "find . $OPTIONS",
        'find "$DOSSIER" -name x',
        "find . -name $NOM",
        "sort $OPTIONS f",
        "git log $REVISION",
        "sed -n 1,5p $FICHIER",
        "awk '{print}' $FICHIER",
        "rg motif $DOSSIER",
        "journalctl $OPTIONS",
        "systemctl $ACTION unite",
        "systemctl status $UNITE",
        "gh $RESSOURCE view",
        "gh api $POINT",
        "curl -s $ADRESSE",
        # ou le programme lui-même
        "$PROGRAMME f",
        '"$PROGRAMME" f',
        "X=1 Y=2 \"$PROGRAMME\" f",
        # ou l'argument d'une enveloppe
        "env -u $NOM ls",
        "timeout $DUREE ls",
        # accolades et jokers en tête de mot
        "find . {-delete,x}",
        'find . {"-delete",x}',
        "sort {-o,sortie} f",
        "find * -name x",
        "tree *",
        # rappel d'historique d'un shell interactif
        "find . !$",
        'sort "!*" f',
        "!rm",
    ],
)
def test_un_mot_que_le_shell_peut_changer_ne_passe_pas_pour_un_argument_de_lecture(command: str) -> None:
    assert _classe(command) is not MutationClass.READ_ONLY


@pytest.mark.parametrize(
    "command",
    [
        # aucun de ces verbes n'a d'option qui écrit : l'argument reste un argument
        "for f in a b; do cat $f; done",
        "cat $FICHIER | head -5",
        'grep -n "x" "$FICHIER"',
        "ls *.md",
        "ls src/{a,b}",
        "cd $DOSSIER && ls",
        'echo "$X" | wc -c',
        "command -v $PROGRAMME",
        # la place du mot fixe ce que fait la commande
        "gh pr view $NUMERO",
        "gh pr checks $NUMERO --watch",
        "git log --oneline -- $FICHIER",
    ],
)
def test_un_mot_change_par_le_shell_reste_admis_la_ou_il_ne_peut_pas_ecrire(command: str) -> None:
    assert _classe(command) is MutationClass.READ_ONLY


# ── Une variable de boucle dont les valeurs se lisent sur la ligne ───────────


@pytest.mark.parametrize(
    "command",
    [
        "for f in a.txt b.txt; do sort $f | head -3; done",
        "for f in notes.md src/app.py; do sed -n 1,5p $f; done",
        'for d in src tests; do find "$d" -name \'*.py\' | wc -l; done',
        "for d in src tests; do find ${d} -type f; done",
        "for f in a b; do for g in c d; do rg motif $f/$g; done; done",
        "for f in a b; do git log --oneline -3 $f; done",
        "for f in a b; do awk '{print}' $f; done",
        "for f in feat/a-b v1.2; do tree -L 1 $f; done",
    ],
)
def test_une_variable_de_boucle_est_lue_avec_les_valeurs_de_sa_liste(command: str) -> None:
    assert _classe(command) is MutationClass.READ_ONLY


@pytest.mark.parametrize(
    "command",
    [
        # la valeur est lue : c'est une option qui écrit, ou un script qui écrit
        "for o in -delete; do find . $o; done",
        "for o in x -delete; do find . $o; done",
        "for s in p wsortie; do sed -n $s f; done",
        "for o in a/-delete; do find . $o; done",  # un tiret derrière un séparateur
        # la liste ne se lit pas sur la ligne
        "for o in $LISTE; do find . $o; done",
        "for o in *; do find . $o; done",
        'for o in "a b"; do find . $o; done',
        "for o in {a,-delete}; do find . $o; done",
        # hors du corps de sa boucle, la variable peut valoir autre chose
        "for o in a; do ls; done; find . $o",
        "find . $o; for o in a; do ls; done",
        "for o in a; do ls; done | cat; find . $o",
        # portée par deux boucles imbriquées
        "for o in a; do for o in b; do ls; done; find . $o; done",
        # indice, modificateur, autre nom, affectation dans le corps
        "for o in a; do find . $o[1]; done",
        "for o in a; do find . $o:t; done",
        "for o in a; do find . $oo; done",
        "for o in a; do find . ${o:-x}; done",
        "for o in a; do o=-delete; find . $o; done",
        # une affectation n'est pas une lecture, la variable qu'elle porte n'est pas connue
        "W=/chemin; sed -n 1,5p $W/f",
        "X=-delete; find . $X",
        "X=/a find . $X",
        # une variable que le shell réécrit, ou inerte donc réaffectable sans mutation
        "for PWD in a; do find $PWD -name x; done",
        "for LANG in a; do LANG=-delete; find . $LANG; done",
        # les verbes qui parlent au système ne lisent aucune variable, même de boucle
        "for u in a; do systemctl status $u; done",
    ],
)
def test_une_variable_dont_les_valeurs_ne_sont_pas_sures_reste_opaque(command: str) -> None:
    assert _classe(command) is not MutationClass.READ_ONLY


# ── Une forme du shell qui exécute ───────────────────────────────────────────


@pytest.mark.parametrize(
    "command",
    [
        "ls *(e:'rm fichier':)",
        "cat =(rm fichier)",
        "cat <(rm fichier)",
        "echo ${ rm fichier; }",
        "(ls)",
        "ls ; (cd /tmp && ls)",
    ],
)
def test_une_forme_du_shell_qui_execute_n_est_pas_une_lecture(command: str) -> None:
    assert _classe(command) is not MutationClass.READ_ONLY


@pytest.mark.parametrize(
    "command",
    [
        "find . \\\n-delete",
        'find . "-de\\\nlete"',
    ],
)
def test_un_mot_coupe_par_une_fin_de_ligne_ne_se_lit_pas(command: str) -> None:
    assert _classe(command) is not MutationClass.READ_ONLY


# ── Un corps de heredoc n'est une donnée que là où le shell ouvre un heredoc ─


@pytest.mark.parametrize(
    "command",
    [
        "cat <<< EOF\nrm fichier\nEOF",  # une chaîne sur l'entrée : pas de corps
        "cat <<<EOF\ntouch drapeau\nEOF",
        'echo "<<EOF"\nrm fichier\nEOF',  # ``<<`` cité : pas un heredoc
        "echo '<<EOF'\nrm fichier\nEOF",
        "echo \\<<EOF\nrm fichier\nEOF",
        "ls # <<EOF\nrm fichier\nEOF",  # ``<<`` dans un commentaire
    ],
)
def test_ce_qui_suit_un_faux_heredoc_reste_inspecte(command: str) -> None:
    assert _classe(command) is not MutationClass.READ_ONLY


def test_une_chaine_sur_l_entree_ne_cache_pas_une_commande_destructive() -> None:
    facts = classify_tool("Bash", {"command": "cat <<< EOF\nrm -rf build\nEOF"})
    assert facts.mutation is MutationClass.DESTRUCTIVE


@pytest.mark.parametrize(
    "command",
    [
        "cat <<EOF\nrm -rf /\nEOF",
        "cat <<'EOF'\nil s'agit d'une note\nEOF",
        "cat <<EOF\nune ligne\nEOF\ngit status",
        "grep -c x <<< 'a b'",
    ],
)
def test_un_vrai_heredoc_et_une_chaine_sur_l_entree_restent_des_lectures(command: str) -> None:
    assert _classe(command) is MutationClass.READ_ONLY


# ── Le gate « fini » lit le classement du hook, pas le texte du journal ──────

TASK = "bootstrap"


@pytest.fixture
def arme(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Projet gouverné, tâche en cours, gate « fini » armé, un check vert déjà consigné."""
    for var in ("GRIMOIRE_DONE_GATE", "GRIMOIRE_TASK_ID", "GRIMOIRE_ACTOR", "GRIMOIRE_SESSION_ID"):
        monkeypatch.delenv(var, raising=False)
    setup_standard_profile(tmp_path, profile_id="governed", task_id=TASK)
    board = tmp_path / "_grimoire/standard/task-board.yaml"
    yaml = YAML()
    data = yaml.load(board.read_text(encoding="utf-8"))
    for task in data.get("tasks", []):
        if task.get("task_id") == TASK:
            task["status"] = "in_progress"
    stream = io.StringIO()
    yaml.dump(data, stream)
    board.write_text(stream.getvalue(), encoding="utf-8")
    scaffold_task_artifacts(tmp_path, task_id=TASK)
    profile = tmp_path / "_grimoire/standard/standard-profile.yaml"
    profile.write_text(
        profile.read_text(encoding="utf-8").rstrip("\n") + "\noptions:\n  done_gate_enforce_from: '2026-01-01'\n",
        encoding="utf-8",
    )
    append_evidence_event(tmp_path, TASK, build_bash_event("pytest -q", exit_code=0))
    return tmp_path


def _observe(root: Path, command: str) -> dict[str, object]:
    """Le ``PostToolUse`` d'un hôte sans identifiant de session : le journal de preuve seul, pas le compteur."""
    decide_evidence_trace(
        HookInput(
            event=HookEvent.POST_TOOL_USE,
            project_root=root,
            tool_name="Bash",
            tool_input={"command": command},
            tool_response={"exit_code": 0},
        )
    )
    return read_evidence_log(root, TASK)[-1]


def _stop(root: Path) -> object:
    return decide_evidence_gate(HookInput(event=HookEvent.STOP, project_root=root, session_id="s-1"))


def test_une_commande_de_plusieurs_lignes_garde_son_classement_dans_le_journal(arme: Path) -> None:
    """Aplatie sur une ligne par le journal, la seconde commande passait pour un argument de la première."""
    entry = _observe(arme, "ls\ntouch src/app.py")
    assert entry["command"] == "ls touch src/app.py"
    assert entry["read_only"] is False

    decision = _stop(arme)
    assert decision.outcome is Outcome.BLOCK  # type: ignore[attr-defined]
    assert "postérieure au dernier check vert" in decision.reason  # type: ignore[attr-defined]


def test_une_longue_lecture_coupee_par_le_journal_reste_une_lecture(arme: Path) -> None:
    entry = _observe(arme, "cat " + " ".join(f"notes/chapitre-{index:03d}.md" for index in range(40)))
    assert str(entry["command"]).endswith("(tronqué)")
    assert entry["read_only"] is True

    assert _stop(arme).outcome is Outcome.ALLOW  # type: ignore[attr-defined]


def test_une_entree_sans_classement_consigne_est_relue_sur_son_texte(arme: Path) -> None:
    """Journal écrit avant ce champ : une lecture reste une lecture, une écriture une écriture."""
    append_evidence_event(arme, TASK, build_bash_event("git status", exit_code=0))
    assert _stop(arme).outcome is Outcome.ALLOW  # type: ignore[attr-defined]
    append_evidence_event(arme, TASK, build_bash_event("touch src/app.py", exit_code=0))
    assert _stop(arme).outcome is Outcome.BLOCK  # type: ignore[attr-defined]


# ── Garde d'autorité : lire le contenu de la zone par un dossier parent ──────

#: Assemblé : le chemin n'est jamais écrit d'un bloc, comme dans une commande.
_PARENT = "~/." + "grimoire"


@pytest.fixture
def home_reel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    (home / ".grimoire" / "control").mkdir(parents=True)
    (home / ".grimoire" / "other").mkdir()
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.chdir(work)
    return work


@pytest.mark.parametrize(
    "gabarit",
    [
        "grep -r motif {}",
        "grep -Rn motif {}",
        "rg motif {}",
        "cd {} && rg motif",
        "cd {} && grep -r motif .",
        "timeout 5 grep -rn motif {}",
        "bash -c 'grep -r motif {}'",
        "grep -r motif ~",
        "diff -r {} /tmp/vide",
        "tar cf - {}",
        "find {} -type f -exec cat {{}} +",
        "find {} -type f | xargs cat",
    ],
)
def test_une_lecture_recursive_d_un_dossier_parent_de_la_zone_est_refusee_sans_revocation(
    home_reel: Path, gabarit: str
) -> None:
    command = gabarit.format(_PARENT)
    vector = forgery_vector(classify_tool("Bash", {"command": command}))
    assert vector is not None and vector.code == "control-zone", command
    assert vector.revokes is False
    decision = run_decision(
        "grimoire.tool-policy",
        HookInput(
            event=HookEvent.PRE_TOOL_USE,
            project_root=home_reel,
            tool_name="Bash",
            tool_input={"command": command},
            session_id="S1",
        ),
    )
    assert decision.outcome is Outcome.DENY, command


@pytest.mark.parametrize(
    "gabarit",
    [
        "ls {}",
        "ls -R {}",
        "find {} -name '*.json'",
        "du -sh {}",
        "cat {}/other/notes",
        "grep motif {}/other/notes",
        "grep -r motif {}/other",
        "rg motif {}/other",
        "cd {}/other && rg motif",
        "grep -r motif src",
        "rg motif",
    ],
)
def test_une_lecture_qui_n_atteint_pas_le_contenu_de_la_zone_reste_permise(home_reel: Path, gabarit: str) -> None:
    assert forgery_vector(classify_tool("Bash", {"command": gabarit.format(_PARENT)})) is None
