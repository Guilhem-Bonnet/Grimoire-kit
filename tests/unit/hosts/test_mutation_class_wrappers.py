"""Une lecture enveloppée reste une lecture ; une écriture enveloppée reste une écriture.

Constat (3.84.0, profil ``governed``) : une session qui n'avait fait que lire a été
comptée à plus de 700 « actions d'écriture », et ``Stop`` a refusé chaque arrêt avec
« Clôture hors tâche ». Le ``PostToolUse`` voit la commande telle qu'un autre hook l'a
réécrite : un proxy de terminal préfixe les commandes (``git status`` devient
``rtk git status``, ``cat f`` devient ``rtk read f``), et ``rtk`` n'était pas un verbe
de lecture. Même effet pour ``timeout 30 …``, ``bash -c '…'`` et pour des verbes de
lecture absents de la liste (``journalctl``, ``systemctl status``, ``gh pr checks``,
``gh api`` en GET, ``curl`` en GET, ``pgrep``, ``ss``).

Trois familles de tests :

- chaque lecture, nue puis sous chaque enveloppe, est ``READ_ONLY`` ;
- chaque écriture, nue puis sous chaque enveloppe, ne l'est jamais (règle de sûreté :
  ces tests sont verts avant comme après le correctif, ils gardent la frontière) ;
- de bout en bout, des ``PostToolUse`` de lectures enveloppées laissent le compteur de
  la session à zéro et ``Stop`` n'y trouve rien à refuser ; une seule écriture
  enveloppée le fait refuser comme avant.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from grimoire.bridges.schemas import HostId
from grimoire.core.agentic_standard import setup_standard_profile
from grimoire.hosts.decisions import HookInput, Outcome, classify_tool, run_decision
from grimoire.hosts.decisions.authority_guard import forgery_vector
from grimoire.hosts.events import HookEvent
from grimoire.hosts.runtime import run_hook
from grimoire.policies.schemas import MutationClass
from grimoire.policies.session_state import session_mutations

SESSION = "abcdef01-0000-4000-8000-00000000000a"


def _classe(command: str) -> MutationClass:
    return classify_tool("Bash", {"command": command}).mutation


def _sous_shell(shell: str, script: str) -> str:
    """``bash -c '<script>'`` ; entre guillemets doubles quand le script porte déjà des simples."""
    return f"{shell} -c '{script}'" if "'" not in script else f'{shell} -c "{script}"'


# ── Les formes ───────────────────────────────────────────────────────────────

#: Lectures : chacune doit être ``READ_ONLY`` nue et sous chaque enveloppe.
LECTURES = [
    "git status",
    "git log --oneline -3",
    "git -C /tmp/depot status",
    "git -C /tmp/depot log --oneline -3",
    "ls -la",
    "grep -rn motif src",
    "journalctl -u grimoire-tick --since today -n 50 --no-pager",
    "journalctl --user -u grimoire-tick --cursor=abc -o cat",
    "systemctl status grimoire-tick.timer",
    "systemctl --user is-active grimoire-tick.timer",
    "systemctl is-enabled sshd",
    "systemctl list-timers --all",
    "systemctl list-units --type=service",
    "systemctl show -p ActiveState sshd",
    "systemctl cat sshd",
    "systemctl --no-pager --failed",
    "gh pr view 1204",
    "gh pr list --state open",
    "gh pr checks 1204",
    "gh pr diff 1204",
    "gh pr status",
    "gh issue view 12",
    "gh issue list",
    "gh repo view",
    "gh run view 42",
    "gh run list --limit 5",
    "gh release view v3.84.0",
    "gh release list",
    "gh api repos/o/r/pulls/12",
    "gh api -X GET repos/o/r/pulls",
    "gh api --method GET repos/o/r/pulls --jq length",
    "curl -s https://example.com/health",
    "curl -sSL --connect-timeout 2 https://example.com/api",
    "curl -s -w '%{http_code}' https://example.com/health",
    "curl -fsS --max-time 5 -X GET http://localhost:8080/health",
    "curl -sI https://example.com",
    "pgrep -fa grimoire",
    "ss -ltnp",
]

#: Écritures de la règle de sûreté : jamais ``READ_ONLY``, nues ou enveloppées.
ECRITURES = [
    "ls > sortie.txt",
    "git log >> journal.txt",
    "cat f | tee copie",
    "sed -i s/a/b/ f",
    "sort -o trie f",
    "find . -name x -delete",
    "git commit -m x",
    "git push origin main",
    "git add -A",
    "git checkout main",
    "git merge topic",
    "git rebase main",
    "git reset HEAD~1",
    "git stash",
    "git tag v1",
    "git branch -d topic",
    "git -C /tmp/depot commit -m x",
    "gh pr merge 12 --squash",
    "gh pr create --fill",
    "gh pr close 12",
    "gh pr comment 12 --body ok",
    "gh pr edit 12 --title x",
    "gh api -X POST repos/o/r/issues",
    "gh api -XPOST repos/o/r/issues",
    "gh api --method DELETE repos/o/r/git/refs/heads/x",
    "gh api --method=PATCH repos/o/r/issues/1",
    "gh api repos/o/r/issues -f title=x",
    "gh api repos/o/r/issues -F nombre=1",
    "gh api repos/o/r/issues --raw-field title=x",
    "gh api repos/o/r/issues --field nombre=1",
    "gh api repos/o/r/issues --input corps.json",
    "gh api graphql -f query=x",
    "rm f",
    "rm -rf build",
    "mv a b",
    "cp a b",
    "mkdir d",
    "touch f",
    "python script.py",
    "python3 -c 'print(1)'",
    "python -m pytest",
    "programme-inconnu --lire",
    "curl -o f https://example.com",
    "curl -O https://example.com/f",
    "curl --output f https://example.com",
    "curl -X POST https://example.com",
    "curl -sXPOST https://example.com",
    "curl --request DELETE https://example.com",
    "curl -d a=1 https://example.com",
    "curl --data a=1 https://example.com",
    "curl --data-raw x https://example.com",
    "curl --data-binary @f https://example.com",
    "curl --data-urlencode a=1 https://example.com",
    "curl -F f=@a https://example.com",
    "curl -T f https://example.com",
    "curl --upload-file f https://example.com",
    # Hors de la liste demandée, mais elles écrivent aussi un fichier ou envoient un corps.
    "curl -c bocal https://example.com",
    "curl -D entetes https://example.com",
    "curl --json '{}' https://example.com",
    "curl -K options.txt",
    "curl -s -w '%output{f}' https://example.com",
    "curl --option-inconnue https://example.com",
    # Un en-tête peut changer ce que fait la requête ; un appel authentifié reste évalué et tracé.
    "curl -H 'X-HTTP-Method-Override: DELETE' https://example.com/x",
    "curl -s -H 'Authorization: Bearer jeton' https://example.com/api",
    "curl -u nom:secret https://example.com",
    "curl -b bocal https://example.com",
    "systemctl restart sshd",
    "systemctl stop grimoire-tick.timer",
    "systemctl enable --now grimoire-tick.timer",
    "systemctl daemon-reload",
    "systemctl edit sshd",
    "systemctl -H status restart sshd",  # ``status`` est ici l'hôte de -H
    "journalctl --rotate",
    "journalctl --vacuum-time=2d",
    "journalctl --vacuum-s=1G",  # abréviation acceptée par journalctl
    "journalctl --flush",
    "journalctl -u x --cursor-file=curseur",
    "ss -K dst 10.0.0.1",
    "ss --kill dst 10.0.0.1",
    "ss -D vidage.bin",
    "ss --diag=vidage.bin",
    "pkill -f grimoire",
]

#: Enveloppes transparentes : ``{}`` reçoit la commande portée.
ENVELOPPES = [
    "rtk {}",
    "rtk proxy {}",
    "timeout 30 {}",
    "timeout 30s {}",
    "timeout -k 5 2m {}",
    "timeout --signal=KILL --preserve-status 1.5 {}",
    "env LC_ALL=C {}",
    "env -u GRIMOIRE_TASK_ID {}",
    "env -i LC_ALL=C {}",
    "nice {}",
    "nice -n 10 {}",
    "command {}",
    "command -p {}",
    "time {}",
    "time -p {}",
    # Combinaisons, dont celles que le proxy produit vraiment.
    "LC_ALL=C rtk {}",
    "sudo rtk {}",
    "cd /tmp && rtk {}",
    "rtk {} | head -5",
    "timeout 30 rtk proxy {}",
    "env LC_ALL=C timeout 30 nice {}",
    "rtk git status && rtk {}",
]

#: Enveloppes qui affectent une variable hors de la liste blanche des variables inertes :
#: la commande portée n'est plus une lecture, quelle qu'elle soit. Ces deux formes
#: figuraient parmi les enveloppes transparentes ; une variable quelconque (``FOO``) ou
#: la recherche des exécutables (``PATH``) peut changer ce que la commande portée lance.
ENVELOPPES_A_VARIABLE = [
    "env -i PATH=/usr/bin {}",
    "FOO=1 rtk {}",
]

SHELLS = ["bash", "sh", "zsh"]


def _formes_enveloppees(commands: list[str], enveloppes: list[str] | None = None) -> list[str]:
    formes = [enveloppe.format(command) for command in commands for enveloppe in enveloppes or ENVELOPPES]
    for command in commands:
        formes.extend(_sous_shell(shell, command) for shell in SHELLS)
        formes.append(_sous_shell("bash", f"git status ; {command}"))
        formes.append(_sous_shell("sh", f"{command} && ls -la"))
        formes.append("timeout 60 " + _sous_shell("bash", command))
        formes.append("rtk proxy " + _sous_shell("sh", command))
    return formes


# ── Lectures ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("command", LECTURES)
def test_une_lecture_nue_est_read_only(command: str) -> None:
    assert _classe(command) is MutationClass.READ_ONLY


@pytest.mark.parametrize("command", _formes_enveloppees(LECTURES))
def test_une_lecture_enveloppee_est_read_only(command: str) -> None:
    assert _classe(command) is MutationClass.READ_ONLY


@pytest.mark.parametrize(
    "command",
    [
        # Ce que `rtk rewrite` rend pour cat, head, tail, rg, find, wc, ps, gh, curl (rtk 0.42.4).
        "rtk git status",
        "rtk git status && rtk git log --oneline -3",
        "rtk read notes.md",
        "rtk read notes.md --max-lines 20",
        "rtk read notes.md --tail-lines 5",
        "rtk ls -la",
        "rtk grep -rn motif src",
        "rtk grep motif src",
        "rtk find . -name '*.py'",
        "rtk wc -l notes.md",
        "rtk tree -L 2",
        "rtk diff a b",
        "rtk ps aux",
        "rtk gh pr view 12",
        "rtk gh pr checks 12",
        "rtk gh api repos/o/r/pulls",
        "rtk curl -s https://example.com",
        "rtk systemctl status grimoire-tick.timer",
        "rtk git -C /tmp/depot log",
        "rtk git log | head -5",
        "cd /tmp && rtk ls",
        "LC_ALL=C rtk git status",
        "sudo rtk ls",
    ],
)
def test_les_reecritures_reelles_du_proxy_sont_read_only(command: str) -> None:
    assert _classe(command) is MutationClass.READ_ONLY


@pytest.mark.parametrize(
    "command",
    [enveloppe.format(command) for command in LECTURES for enveloppe in ENVELOPPES_A_VARIABLE],
)
def test_une_lecture_sous_une_affectation_hors_liste_blanche_n_est_plus_read_only(command: str) -> None:
    """Attente corrigée : ces formes étaient attendues en lecture, elles sont des mutations."""
    assert _classe(command) is MutationClass.MUTATION_CONTROLLED


@pytest.mark.parametrize(
    "command",
    [
        "bash -c 'git status ; git log --oneline -3'",
        "sh -c 'cd /tmp && ls -la | head -5'",
        'bash -c "git status && git diff --stat"',
        "bash -c ls",
        "bash -c 'for f in a b; do cat $f; done'",
        "bash -c 'rtk git status ; timeout 5 journalctl -n 3'",
        "bash -c \"sh -c 'git status'\"",  # deux niveaux : encore lu
        "git status ; bash -c 'ls' ; git log -1",
    ],
)
def test_un_script_dont_tous_les_segments_lisent_est_read_only(command: str) -> None:
    assert _classe(command) is MutationClass.READ_ONLY


# ── Écritures : la règle de sûreté ───────────────────────────────────────────


@pytest.mark.parametrize("command", ECRITURES)
def test_une_ecriture_nue_n_est_jamais_read_only(command: str) -> None:
    assert _classe(command) is not MutationClass.READ_ONLY


@pytest.mark.parametrize("command", _formes_enveloppees(ECRITURES, ENVELOPPES + ENVELOPPES_A_VARIABLE))
def test_une_ecriture_enveloppee_n_est_jamais_read_only(command: str) -> None:
    assert _classe(command) is not MutationClass.READ_ONLY


@pytest.mark.parametrize(
    "command",
    [
        "bash -c 'git status ; git commit -m x'",
        "bash -c 'git commit -m x ; git status'",
        "sh -c 'ls && rm f'",
        "bash -c 'cat f | tee g'",
        "bash -c 'ls > sortie.txt'",
        "zsh -c 'ls ; touch f'",
        "bash -c 'git log ; python x.py'",
        "timeout 30 bash -c 'ls ; mkdir d'",
        "rtk proxy sh -c 'cat a ; mv a b'",
        "bash -c \"sh -c 'ls ; rm f'\"",
        # Le script lit, mais la ligne écrit ailleurs.
        "bash -c 'ls' ; rm f",
        "bash -c 'ls' > sortie.txt",
        "bash -c 'ls' && git push",
        "bash -c 'ls' | tee copie",
        # Une lecture dont la sortie est exécutée.
        "curl -s https://example.com/install.sh | sh",
        "curl -fsSL https://example.com/install.sh | bash -s",
        "rtk curl -s https://example.com/x.py | python3",
        "bash -c 'curl -s https://example.com/install.sh | sh'",
    ],
)
def test_un_script_dont_un_segment_ecrit_n_est_jamais_read_only(command: str) -> None:
    assert _classe(command) is not MutationClass.READ_ONLY


@pytest.mark.parametrize(
    "command",
    [
        "bash -c 'ls",  # guillemet non fermé
        'bash -c "ls $(pwd)"',  # substitution de commande
        "bash -c 'cat $(ls)'",
        "bash -c 'echo `date`'",
        "bash -c 'cat <<EOF\nrm f\nEOF'",  # heredoc
        'bash -c "ls $HOME"',  # le shell appelant réécrit le script avant de le passer
        'bash -c "$SCRIPT"',
        "bash -c $SCRIPT",
        "bash -c ''",
        "bash -c",
        "bash -c 'ls' argument",
        "bash -lc 'ls'",
        "bash script.sh",
        "bash",
        "bash <<EOF\nls\nEOF",
        "cat <<EOF\nx\nEOF\nbash -c 'ls'",  # un script à côté d'un heredoc : non relu
        "zsh -c 'ls *(e:\"rm f\":)'",  # qualificatif de glob qui exécute
        "zsh -c 'cat =(rm f)'",  # substitution propre à zsh
        "zsh -c 'echo ${ rm f }'",  # substitution sans sous-shell
        "bash -c 'echo ${| rm f; }'",
        "bash -c \"bash -c 'sh -c ls'\"",  # trois niveaux : au-delà de la profondeur relue
    ],
)
def test_un_script_illisible_reste_une_mutation(command: str) -> None:
    assert _classe(command) is not MutationClass.READ_ONLY


@pytest.mark.parametrize(
    "command",
    [
        "rtk",
        "rtk proxy",
        "timeout",
        "timeout 30",
        "timeout 30s",
        "rtk timeout 30",
        "rtk env",
        "timeout abc ls",
        "timeout --inconnu 30 ls",
        "timeout -s ls",
        "rtk -v git status",  # option globale du proxy : forme non reconnue
        "rtk run -c 'ls'",
        "rtk pipe --filter grep",
        "rtk init -g",
        "rtk err ls",
        "rtk summary ls",
        "env -S 'ls' x",
        "env -C /tmp ls",
        "nice -n dix ls",
        "time -o temps.txt ls",
        # nohup écrit nohup.out dès que la sortie est un terminal : il reste une mutation.
        "nohup ls",
        "nohup git status",
        "rtk nohup git status",
    ],
)
def test_une_enveloppe_vide_ou_non_reconnue_reste_une_mutation(command: str) -> None:
    assert _classe(command) is not MutationClass.READ_ONLY


@pytest.mark.parametrize(
    "command",
    [
        # Les listes blanches de sed, awk, sort, find et git s'appliquent à la commande portée.
        "rtk sed -i s/a/b/ f",
        "timeout 30 sed -i s/a/b/ f",
        "rtk sed -n 's/a/b/w sortie' f",
        "timeout 5 awk '{print > \"sortie\"}' f",
        "rtk proxy awk -i inplace '{print}' f",
        "rtk proxy sort -o sortie f",
        "rtk find . -name x -delete",
        "timeout 9 find . -exec rm {} +",
        "env X=1 git branch -D topic",
        "rtk git diff --output=sortie",
        "nice -n 5 uniq entree sortie",
        # Un drapeau d'écriture écrit entre guillemets reste vu.
        'gh api "-XPOST" repos/o/r/issues',
        'gh api repos/o/r/issues "-f" title=x',
        'curl "-o" f https://example.com',
        'curl -s "--data" a=1 https://example.com',
        'curl -s --max-time "5" -o f https://example.com',
        'systemctl "restart" sshd',
        'journalctl "--rotate"',
        'ss "-K" dst 10.0.0.1',
        "gh api list -X DELETE",
    ],
)
def test_une_ecriture_cachee_par_une_enveloppe_ou_des_guillemets_reste_vue(command: str) -> None:
    assert _classe(command) is not MutationClass.READ_ONLY


@pytest.mark.parametrize(
    "command",
    [
        "cat <<EOF\nbash -c 'ls'\nEOF",
        "cat <<EOF\ntimeout après 30 s\nEOF",
        "cat <<EOF\nrtk\nEOF",
        "cat <<EOF\ncurl -d x https://example.com\nsystemctl restart sshd\nEOF",
        "cat <<'EOF'\ngh api -X POST repos/o/r/issues\nEOF",
    ],
)
def test_un_heredoc_qui_cite_une_enveloppe_ou_une_ecriture_reste_une_lecture(command: str) -> None:
    """Non-régression : le corps d'un heredoc est une donnée, les nouvelles règles ne le lisent pas."""
    assert _classe(command) is MutationClass.READ_ONLY


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf build",
        "rtk rm -rf build",
        "rtk proxy rm -rf build",
        "timeout 30 rm -rf build",
        "env X=1 nice rm -rf build",
        "bash -c 'rm -rf build'",
        "bash -c 'ls ; rm -rf build'",
        "timeout 30 bash -c 'git status ; git reset --hard'",
        "rtk git push --force origin main",
    ],
)
def test_la_classe_destructive_ne_change_pas(command: str) -> None:
    facts = classify_tool("Bash", {"command": command})
    assert facts.mutation is MutationClass.DESTRUCTIVE
    assert facts.destructive_reason


# ── Garde d'autorité : elle lit aussi la classe de mutation ──────────────────
#
# Une lecture enveloppée est maintenant classée lecture. Ce que la garde en fait ne doit
# pas s'ouvrir pour autant : les secrets de la zone de contrôle restent refusés
# (détection de secret, indépendante de la classe), une lecture qui atteint la zone est
# refusée sans révocation comme la même lecture nue (#1173), et une écriture enveloppée
# reste un vecteur.

#: Assemblé : le chemin n'est jamais écrit d'un bloc, comme dans une commande.
_ZONE = "~/." + "grimoire" + "/" + "control"


@pytest.fixture
def home_reel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Un HOME réel dont la zone de contrôle existe, et un répertoire de travail à côté."""
    home = tmp_path / "home"
    (home / ".grimoire" / "control").mkdir(parents=True)
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.chdir(work)
    return work


@pytest.mark.parametrize("secret", ["cockpit-key", "mandates.authority.jsonl"])
@pytest.mark.parametrize(
    "gabarit",
    [
        "cat {}",
        "rtk read {}",
        "rtk read {} --max-lines 5",
        "rtk proxy cat {}",
        "timeout 5 cat {}",
        "env X=1 nice -n 5 head -1 {}",
        "bash -c 'cat {}'",
        'sh -c "cat {}"',
        "zsh -c 'cat {}'",
        "timeout 9 bash -c 'git status ; head -1 {}'",
    ],
)
def test_un_secret_de_la_zone_de_controle_ne_se_lit_pas_mieux_enveloppe(
    home_reel: Path, gabarit: str, secret: str
) -> None:
    command = gabarit.format(f"{_ZONE}/{secret}")
    assert classify_tool("Bash", {"command": command}).secret_target, command
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
        "rtk proxy cp /tmp/x {}",
        "timeout 5 bash -c 'echo x > {}'",
        "bash -c 'cat /tmp/x | tee {}'",
        "rtk sed -i s/a/b/ {}",
        "env X=1 nice mv /tmp/x {}",
    ],
)
def test_une_ecriture_enveloppee_dans_la_zone_de_controle_reste_un_vecteur(home_reel: Path, gabarit: str) -> None:
    command = gabarit.format(f"{_ZONE}/host-contracts.json")
    vector = forgery_vector(classify_tool("Bash", {"command": command}))
    assert vector is not None and vector.code == "control-zone", command


@pytest.mark.parametrize(
    ("gabarit", "cible"),
    [
        ("cat {}", "/host-contracts.json"),
        ("rtk read {}", "/host-contracts.json"),
        ("timeout 5 cat {}", "/host-contracts.json"),
        ("bash -c 'cat {}'", "/host-contracts.json"),
        ("grep -r motif {}", ""),
        ("rtk grep -r motif {}", ""),
        ("bash -c 'grep -r motif {}'", ""),
    ],
)
def test_une_lecture_de_la_zone_de_controle_est_refusee_sans_revocation_enveloppee_comme_nue(
    home_reel: Path, gabarit: str, cible: str
) -> None:
    """#1173 : une lecture qui atteint la zone est refusée sans révoquer, quelle que soit sa classe."""
    facts = classify_tool("Bash", {"command": gabarit.format(_ZONE + cible)})
    assert facts.mutation is MutationClass.READ_ONLY
    vector = forgery_vector(facts)
    assert vector is not None and vector.code == "control-zone"
    assert vector.revokes is False


# ── De bout en bout : le compteur de la session et Stop ──────────────────────


@pytest.fixture
def governed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for var in ("GRIMOIRE_TASK_ID", "GRIMOIRE_ACTOR", "GRIMOIRE_SESSION_ID", "CLAUDE_CODE_SESSION_ID"):
        monkeypatch.delenv(var, raising=False)
    setup_standard_profile(tmp_path, profile_id="governed", task_id="bootstrap")
    return tmp_path


#: Le nom que Claude Code donne à l'événement dans son payload.
_NOM_HOTE = {HookEvent.POST_TOOL_USE: "PostToolUse", HookEvent.STOP: "Stop"}


def _hook(root: Path, event: HookEvent, **extra: Any) -> Any:
    payload = {"session_id": SESSION, "hook_event_name": _NOM_HOTE[event], "cwd": str(root), **extra}
    _, decision, _ = run_hook(payload, host_id=HostId.CLAUDE_CODE_CLI, event=event, project_root=root)
    return decision


def _post_bash(root: Path, command: str) -> None:
    _hook(
        root,
        HookEvent.POST_TOOL_USE,
        tool_name="Bash",
        tool_input={"command": command},
        tool_response={"stdout": "", "exit_code": 0},
    )


#: Une session de lecture telle que le ``PostToolUse`` la reçoit, proxy compris.
SESSION_DE_LECTURE = [
    "rtk git status",
    "rtk git log --oneline -5",
    "rtk read CHANGELOG.md --max-lines 40",
    "rtk grep -rn compteur src",
    "rtk ls -la",
    "cd src && rtk find . -name '*.py'",
    "rtk gh pr view 1204",
    "rtk gh pr checks 1204",
    "rtk gh api repos/o/r/pulls/1204",
    "rtk curl -s http://localhost:8765/health",
    "journalctl --user -u grimoire-tick -n 30 --no-pager",
    "rtk systemctl --user status grimoire-tick.timer",
    "systemctl --user list-timers",
    "timeout 30 rtk proxy git -C /tmp/depot log -1",
    "bash -c 'git status ; git log --oneline -3'",
    "sh -c 'pgrep -fa grimoire ; ss -ltnp'",
    "env LC_ALL=C nice git diff --stat",
]


def test_des_lectures_enveloppees_laissent_le_compteur_a_zero_et_stop_ne_bloque_pas(governed: Path) -> None:
    for command in SESSION_DE_LECTURE:
        _post_bash(governed, command)
    assert session_mutations(governed, SESSION) == 0

    decision = _hook(governed, HookEvent.STOP, stop_hook_active=False)
    assert decision.outcome is Outcome.ALLOW, decision.reason
    assert "blocked_on" not in decision.detail


@pytest.mark.parametrize(
    "ecriture",
    [
        "rtk git commit -m x",
        "timeout 30 touch drapeau",
        "bash -c 'git status ; git add -A'",
        "rtk proxy sed -i s/a/b/ src/app.py",
        "rtk curl -s -o page.html https://example.com",
    ],
)
def test_une_seule_ecriture_enveloppee_fait_bloquer_stop_comme_avant(governed: Path, ecriture: str) -> None:
    for command in SESSION_DE_LECTURE:
        _post_bash(governed, command)
    _post_bash(governed, ecriture)
    assert session_mutations(governed, SESSION) == 1

    decision = _hook(governed, HookEvent.STOP, stop_hook_active=False)
    assert decision.outcome is Outcome.BLOCK
    assert decision.detail["blocked_on"] == "no_active_task"
    assert decision.detail["mutations"] == 1
