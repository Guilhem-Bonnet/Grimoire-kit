"""Un heredoc ne s'ouvre que dans une commande simple ; une boucle n'affecte pas n'importe quelle variable.

Retouches après relecture adverse du classement en lecture seule :

- un ``<<`` écrit à l'intérieur d'une expansion, d'une expression arithmétique, d'un test
  ou d'un indice n'ouvre aucun heredoc pour le shell. Le balayage en ouvrait un, et
  retirait du code jugé les lignes qui suivent : il ne les voyait plus, ni pour le
  classement, ni pour la détection destructive, ni pour celle des secrets ;
- une expansion dont l'indice ou le décalage affecte une variable, et une boucle dont la
  variable est une variable que le shell ou un outil lit, ne sont pas des lectures ;
- trois formes de tous les jours redeviennent des lectures : un motif de fichiers en
  valeur d'une option longue derrière une recherche, une variable de boucle suivie de
  deux-points derrière ``git``, une boucle sur un motif à préfixe dont la variable est
  citée.
"""

from __future__ import annotations

import pytest

from grimoire.hosts.decisions import classify_tool, command_surface
from grimoire.policies.schemas import ActionKind, MutationClass

LECTURE = MutationClass.READ_ONLY
DESTRUCTIVE = MutationClass.DESTRUCTIVE

#: Assemblé : jamais écrit d'un bloc hors d'un cas de test.
EFFACE = "rm " + "-rf build"


def _classe(command: str) -> MutationClass:
    return classify_tool("Bash", {"command": command}).mutation


# ── Un « heredoc » là où le shell n'en ouvre pas ─────────────────────────────

#: Première ligne : un ``<<mot`` dans un contexte qui n'est pas une commande simple.
#: La ligne suivante est exécutée par le shell ; la troisième porte ``mot``.
FAUSSES_OUVERTURES = [
    # expansion de paramètre, chaque opérateur
    "echo ${a:-<<mot }",
    "echo ${a-<<mot }",
    "echo ${a+<<mot }",
    "echo ${a#<<mot }",
    "echo ${a%<<mot }",
    "echo ${a/<<mot }",
    "echo ${a?<<mot }",
    # texte avant, blanc après, étiquette citée, tabulations, tabulation avant la fermeture
    "echo ${a:-x <<mot }",
    "echo ${a:-<< mot }",
    "echo ${a:-<<'mot' }",
    'echo ${a:-<<"mot" }',
    "echo ${a:-<<-mot }",
    "echo ${a:-<<mot\t}",
    "echo ${a:-{x} <<mot }",
    # entre guillemets doubles, avec ou sans guillemets imbriqués
    'echo "${a:-<<mot }"',
    'echo "${a:-"} <<mot "x"}"',
    "echo \"$(echo ')' \" <<mot \")\"",
    # arithmétique, test, indice
    "echo $(( 1 <<mot ))",
    "echo $((1<<mot ))",
    'echo "$(( 1 <<mot ))"',
    "echo $[ 1 <<mot ]",
    "(( a <<mot ))",
    "[[ a <<mot ]]",
    "a[1 <<mot ]=1",
    "echo ${a[1 <<mot ]}",
    "let a=1<<mot",
    "let a=1 <<mot",
    # substitution à l'ancienne, guillemets que le balayage ne décode pas
    "echo `echo x <<mot `",
    "echo $'a\\' <<mot \\''",
]

#: Deuxième ligne : ce que le shell exécute.
ECRITURES = [
    "touch drapeau",
    "sed -i s/a/b/ f",
    "awk 'BEGIN{system(\"touch drapeau\")}'",
    "PATH=x ls",
    "cat <(touch drapeau)",
]


@pytest.mark.parametrize("ecriture", ECRITURES)
@pytest.mark.parametrize("ouverture", FAUSSES_OUVERTURES)
def test_ce_qui_suit_une_fausse_ouverture_reste_juge(ouverture: str, ecriture: str) -> None:
    assert _classe(f"{ouverture}\n{ecriture}\nmot") is not LECTURE


@pytest.mark.parametrize("ouverture", FAUSSES_OUVERTURES)
def test_une_commande_destructive_derriere_une_fausse_ouverture_est_vue(ouverture: str) -> None:
    facts = classify_tool("Bash", {"command": f"{ouverture}\n{EFFACE}\nmot"})
    assert facts.mutation is DESTRUCTIVE, ouverture
    assert facts.destructive_reason


@pytest.mark.parametrize("ouverture", FAUSSES_OUVERTURES)
def test_une_lecture_de_secret_derriere_une_fausse_ouverture_est_vue(ouverture: str) -> None:
    facts = classify_tool("Bash", {"command": f"{ouverture}\ncat .env\nmot"})
    assert facts.kind is ActionKind.SECRET_ACCESS, ouverture


@pytest.mark.parametrize("ouverture", FAUSSES_OUVERTURES)
def test_la_surface_lue_par_les_freins_garde_ce_qui_suit_une_fausse_ouverture(ouverture: str) -> None:
    assert "touch drapeau" in command_surface(f"{ouverture}\ntouch drapeau\nmot")


@pytest.mark.parametrize(
    "command",
    [
        # une substitution exécutée entre guillemets doubles n'est pas une donnée
        'echo "$(' + EFFACE + ')"',
        'echo "`' + EFFACE + '`"',
        # le corps d'un heredoc à étiquette nue est développé avant d'être passé
        "cat <<EOF\n$(" + EFFACE + ")\nEOF",
    ],
)
def test_une_commande_destructive_dans_une_substitution_est_vue(command: str) -> None:
    facts = classify_tool("Bash", {"command": command})
    assert facts.mutation is DESTRUCTIVE, command


@pytest.mark.parametrize(
    "command",
    [
        "cat <<EOF\nune ligne\nEOF",
        "echo ${a:-b} ; cat <<EOF\nune ligne\nEOF",
        'echo "$X" "${Y}" ; cat <<EOF\nune ligne\nEOF',
        "ls [ab]* ; cat <<EOF\nune ligne\nEOF",
        "cat <<EOF | grep -c ligne\nune ligne\nEOF",
        "grep -c x <<< 'a << b'",
        'echo "a << b"',
    ],
)
def test_un_heredoc_dans_une_commande_simple_reste_une_lecture(command: str) -> None:
    assert _classe(command) is LECTURE


@pytest.mark.parametrize(
    "command",
    [
        # la forme courante d'un message de commit ou d'un corps de PR : une donnée
        "git commit -m \"$(cat <<'EOF'\nretire " + EFFACE + " du script\n\nil s'agit d'un \"nettoyage\"\nEOF\n)\"",
        'git commit -m "$(cat <<EOF\nretire ' + EFFACE + " du script\nEOF\n)\"",
        "gh pr create --title \"nettoyage\" --body \"$(cat <<'EOF'\nplus de " + EFFACE + "\nEOF\n)\"",
        "git commit -F - <<'EOF'\nretire " + EFFACE + "\nEOF",
        'cat > "${DOSSIER}/notes.md" <<\'EOF\'\nne jamais lancer ' + EFFACE + "\nEOF",
    ],
)
def test_un_message_ecrit_par_heredoc_reste_une_donnee(command: str) -> None:
    facts = classify_tool("Bash", {"command": command})
    assert facts.mutation is MutationClass.MUTATION_CONTROLLED, command
    assert not facts.destructive_reason


# ── Une ligne que le shell refuse n'ouvre pas de heredoc ─────────────────────
#
# Dans un terminal interactif, une ligne mal formée est rejetée seule : son heredoc
# n'est jamais ouvert, et les lignes suivantes sont lues comme des commandes. Un corps
# n'est donc retiré que derrière une ligne dont la forme est sûre : une suite de
# commandes simples, sans mot réservé, sans commande vide, sans redirection sans cible,
# sans rappel d'historique, sans parenthèse.

LIGNES_REFUSEES = [
    "echo a ;; cat <<mot",
    "cat <<mot ; ;",
    "| cat <<mot",
    "cat a && && cat <<mot",
    "cat a | | cat <<mot",
    "cat <<mot >",
    "cat <<mot 2>",
    "cat < <<mot",
    "do cat <<mot",
    "done <<mot",
    "then cat <<mot",
    "fi ; cat <<mot",
    "} ; cat <<mot",
    "for x in a\ncat <<mot",
    "for x in <<mot ; do ls; done",
    "select x in <<mot ; do ls; done",
    "f() cat <<mot",
    "cat ) <<mot",
    "echo !rien <<mot",
    'echo "!rien" <<mot',
    'echo "$(a ;; b)" <<mot',
    "echo $(a ;; b) <<mot",
    "cat <<mot <<",
]


@pytest.mark.parametrize("ecriture", ["touch drapeau", "PATH=x ls"])
@pytest.mark.parametrize("ligne", LIGNES_REFUSEES)
def test_ce_qui_suit_une_ligne_refusee_reste_juge(ligne: str, ecriture: str) -> None:
    assert _classe(f"{ligne}\n{ecriture}\nmot") is not LECTURE


@pytest.mark.parametrize("ligne", LIGNES_REFUSEES)
def test_une_commande_destructive_derriere_une_ligne_refusee_est_vue(ligne: str) -> None:
    facts = classify_tool("Bash", {"command": f"{ligne}\n{EFFACE}\nmot"})
    assert facts.mutation is DESTRUCTIVE, ligne


@pytest.mark.parametrize(
    "command",
    [
        "cd docs && cat <<EOF\nune ligne\nEOF",
        "cat <<EOF | grep -c ligne | head -1\nune ligne\nEOF",
        "cat <<EOF ; ls\nune ligne\nEOF",
        "cat <<EOF 2>/dev/null\nune ligne\nEOF\ngit status ; ls",
        "LC_ALL=C sort <<EOF\nb\na\nEOF",
    ],
)
def test_un_heredoc_derriere_une_suite_de_commandes_simples_reste_une_lecture(command: str) -> None:
    assert _classe(command) is LECTURE


# ── Une expansion dont l'indice ou le décalage affecte une variable ──────────


@pytest.mark.parametrize(
    "command",
    [
        "echo ${a[IFS=1]}",
        'echo "${a[IFS=1]}"',
        "echo ${x:IFS=1:1}",
        'echo "${x:1:IFS=2}"',
        "echo ${a[i++]}",
        "echo ${x:n--}",
        "echo ${@:i=2}",
        "echo ${#a[IFS=1]}",
        "echo ${a[b[IFS=1]]}",
        'cat "${f:(IFS=1)}"',
        "echo ${a[IFS=1]} ; for o in a1-delete; do find . $o; done",
    ],
)
def test_une_affectation_dans_un_indice_ou_un_decalage_n_est_pas_une_lecture(command: str) -> None:
    assert _classe(command) is not LECTURE


@pytest.mark.parametrize(
    "command",
    [
        "echo ${a[1]}",
        'echo "${a[@]}"',
        "echo ${x:1:2}",
        'echo "${x: -3}"',
        "echo ${x:-a=b}",
        "echo ${#a[@]}",
    ],
)
def test_un_indice_ou_un_decalage_qui_ne_fait_que_lire_reste_une_lecture(command: str) -> None:
    assert _classe(command) is LECTURE


# ── La variable d'une boucle est une affectation ─────────────────────────────


@pytest.mark.parametrize(
    "command",
    [
        "for PATH in /tmp/x; do ls; done",
        "for HOME in /tmp/x; do ls; done",
        "for GIT_DIR in a b; do git status; done",
        "for IFS in a; do ls; done",
        "for PAGER in x; do git log; done",
        "for LD_PRELOAD in x; do ls; done",
        "for BASH_ENV in x; do bash -c 'ls'; done",
        # zsh lie des tableaux en minuscules aux variables du shell
        "for path in /tmp/x; do ls; done",
        "for cdpath in /tmp/x; do cd y; done",
        "for fpath in /tmp/x; do ls; done",
        "for mapfile in x; do ls; done",
        "for aliases in x; do ls; done",
        "for functions in x; do ls; done",
        "for commands in x; do ls; done",
        "for options in x; do ls; done",
        "for nameddirs in x; do ls; done",
        "for histchars in x; do ls; done",
        "for watch in x; do ls; done",
        "for module_path in x; do ls; done",
        "for http_proxy in x; do curl -s https://example.com; done",
        # deux variables, ou une forme qui n'est pas « for NOM in … »
        "for a PATH in x y; do ls; done",
        "bash -c 'for PATH in /tmp/x; do ls; done'",
    ],
)
def test_une_boucle_sur_une_variable_sensible_n_est_pas_une_lecture(command: str) -> None:
    assert _classe(command) is not LECTURE


@pytest.mark.parametrize(
    "command",
    [
        "for f in a b; do cat $f; done",
        "for rev in a b; do git log -1 $rev; done",
        "for i in 1 2 3; do echo $i; done",
        "for LANG in C fr_FR.UTF-8; do date; done",
        "for f; do cat $f; done",
    ],
)
def test_une_boucle_sur_une_variable_ordinaire_reste_une_lecture(command: str) -> None:
    assert _classe(command) is LECTURE


# ── Un motif de fichiers en valeur d'une option longue ───────────────────────

ENVELOPPES = ["{}", "rtk {}", "rtk proxy {}"]


@pytest.mark.parametrize("enveloppe", ENVELOPPES)
@pytest.mark.parametrize(
    "recherche",
    [
        "grep -rn motif src --include=*.py",
        "grep -rn motif --include=*.py src tests | head -20",
        'grep -rn "a\\|b" web --include=*.js -l',
        "grep -rn motif docs/ --include=*.md 2>/dev/null | head",
        "grep -rn motif --include=*.py src/cli* src/*.py",
        "rg -n motif --glob=*.py src",
    ],
)
def test_un_motif_en_valeur_d_option_derriere_une_recherche_reste_une_lecture(recherche: str, enveloppe: str) -> None:
    assert _classe(enveloppe.format(recherche)) is LECTURE


@pytest.mark.parametrize(
    "command",
    [
        # le motif peut changer le NOM de l'option
        "rtk grep motif --inc*=x",
        "rtk grep motif --pre*",
        "rtk grep motif -*",
        "rg motif --*=x",
        # l'option elle-même n'est pas admise, quelle que soit sa valeur
        "rtk grep motif --pre=./filtre*",
        "rg motif --hostname-bin=./n*",
        "sort --output=sortie* f",
        "git diff --output=sortie*",
        # hors recherche, la valeur d'une option reste lue telle qu'elle est écrite
        "uniq --skip-fields=* f",
        "sed --expression=p* f",
        "date --date=*",
    ],
)
def test_un_motif_qui_peut_changer_l_option_reste_refuse(command: str) -> None:
    assert _classe(command) is not LECTURE


# ── Une variable de boucle suivie de deux-points : entre accolades seulement ─
#
# ``$nom:suite`` : zsh lit ``:suite`` comme des modificateurs de la valeur. Ceux qui
# résolvent un chemin puis en gardent la fin rendent le nom que porte un lien du dossier
# courant — un mot qui peut commencer par un tiret. Seule ``${nom}:suite`` est remplacée.


@pytest.mark.parametrize(
    "command",
    [
        "for rev in 59575bc29 ec37d0808 origin/main; do echo \"== $rev\"; git show ${rev}:src/x.py | wc -l; done",
        "for c in 0753d01 020ba30; do echo $c; git show ${c}:tools/o.py | grep -n 'def ' | cut -c1-90; done",
        "for rev in a1 b2; do git show ${rev}:docs/mon-fichier.md | head -3; done",
        "for rev in HEAD~1 HEAD~2; do git log -1 --format=%h $rev; done",
        # deux boucles qui se suivent sur le même nom
        'for n in 1 2; do sed -n "${n}p" f; done && for n in 3 4; do sed -n "${n}p" g; done',
        # des valeurs citées, écrites en clair
        "for p in 'max-turns' 'fork-session'; do grep -c -- \"--$p\" notes.md; done",
    ],
)
def test_une_variable_de_boucle_derriere_git_reste_une_lecture(command: str) -> None:
    assert _classe(command) is LECTURE


@pytest.mark.parametrize(
    "command",
    [
        # sans accolades, la suite du mot est lue par zsh comme des modificateurs de la valeur
        "for rev in 59575bc29 origin/main; do git show $rev:src/x.py | wc -l; done",
        "for c in 0753d01 020ba30; do git show $c:tools/o.py; done",
        "for f in lien; do git log $f:A:t; done",
        "for f in lien; do git log $f:P:t; done",
        "for f in a; do sort $f:s/a/-o/ x; done",
        "for f in a; do git show $f:s/a/--output=x/; done",
        "for f in a-b; do git show $f:x; done",
        "for f in a; do git show $f:x-y; done",
        "for f in a; do sed -n $f:p f; done",
        "for f in a; do uniq $f:e; done",
        'for f in a; do git show "$f:e"-o; done',
        "for f in show; do git $f:e HEAD; done",
        # la même variable portée par deux boucles imbriquées
        "for f in a; do for f in b; do find . $f; done; find . $f; done",
        # une valeur citée qui n'est pas écrite en clair
        "for o in '-delete'; do find . $o; done",
        "for o in 'a b'; do find . $o; done",
        'for o in "$X"; do find . $o; done',
    ],
)
def test_une_variable_de_boucle_modifiable_reste_refusee(command: str) -> None:
    assert _classe(command) is not LECTURE


# ── Une boucle sur un motif à préfixe, variable citée ────────────────────────


@pytest.mark.parametrize(
    "command",
    [
        'for f in dossier/ok-*.md; do echo "== $f"; sed -n 1,3p "$f"; done',
        'for f in src/*.py; do wc -l "$f"; rg -c motif "$f"; done',
        'for f in notes/a?.md journal.md; do sort "${f}" | head -3; done',
        'for d in src/*/; do find "$d" -name x; done',
        'for f in src/*.py; do git log -1 --format=%h -- "$f"; done',
    ],
)
def test_une_boucle_sur_un_motif_a_prefixe_variable_citee_reste_une_lecture(command: str) -> None:
    assert _classe(command) is LECTURE


@pytest.mark.parametrize(
    "command",
    [
        # sans guillemets, un nom de fichier qui porte un blanc se redécoupe
        "for f in dossier/ok-*.md; do sed -n 1,3p $f; done",
        "for f in src/*.py; do sort $f; done",
        # sans préfixe, ou préfixe en tiret : le nom trouvé peut être une option
        'for f in *.md; do sort "$f"; done',
        'for f in -*; do sort "$f"; done',
        'for f in ?x; do find "$f"; done',
        # là où la place ou le nombre des mots décide
        'for s in s*a*b*; do sed -n "$s" f; done',
        'for p in p*; do awk "$p" f; done',
        'for f in d/*; do xxd "$f" "$f.hex"; done',
        'for u in https://example.com/*; do curl -s "$u"; done',
        # motif que ce classement ne sait pas lire
        'for f in d/{a,b}*; do sort "$f"; done',
        'for f in d/*(.); do sort "$f"; done',
    ],
)
def test_une_boucle_sur_un_motif_reste_refusee_hors_de_ce_cas(command: str) -> None:
    assert _classe(command) is not LECTURE


# ── Une substitution échappée entre guillemets doubles est un texte ──────────


@pytest.mark.parametrize(
    "command",
    [
        'grep -n "^- fix(hosts): la \\`classe\\` de lecture\\|^- feat" CHANGELOG.md | cut -c1-60',
        'grep -h "Lis \\`notes" docs/*.md | head -5',
        'echo "prix : \\$(5)"',
    ],
)
def test_une_substitution_echappee_entre_guillemets_reste_un_texte(command: str) -> None:
    assert _classe(command) is LECTURE


# ── La détection destructive ne perd rien de ce que main signalait ───────────
#
# Le drapeau d'une poussée forcée est cherché jusqu'à la fin de la ligne. Borner la
# recherche à « sa » commande demande de savoir où la commande finit, ce que le texte
# brut ne dit pas : un séparateur échappé, dans une expansion, une substitution ou une
# accolade ne finit rien, et une redirection d'entrée par descripteur non plus. Le motif
# reste donc celui de main ; un drapeau pris à la commande suivante est un faux
# « destructif », donc un échec fermé.

POUSSE = "git push"

#: Le motif de main, recopié : ce qu'il trouve sur la surface historique doit rester signalé.
MOTIF_DE_MAIN = (
    r"\bgit\s+push\b.*(?:^|\s)"
    r"(--force|--force-with-lease(?:=\S+)?|--force-if-includes|-[a-z]*f[a-z]*)"
    r"(?=\s|$)"
)

POUSSEES_FORCEES = [
    POUSSE + " origin HEAD:x --force",
    POUSSE + " -f origin HEAD:x",
    "git status && " + POUSSE + " origin HEAD:x --force-with-lease",
    "ls ; " + POUSSE + " --force",
    POUSSE + " origin HEAD:x --force ; ls",
    POUSSE + ' origin "HEAD:x" -f',
    POUSSE + " origin HEAD:x 2>&1 --force",
    POUSSE + " origin HEAD:x &>/dev/null -f",
    # une redirection d'entrée par descripteur avant le drapeau
    POUSSE + " origin HEAD:x <&0 -f",
    POUSSE + " origin HEAD:x <&- --force",
    POUSSE + " origin HEAD:x <&3 -f",
    POUSSE + " origin HEAD:x 0<&1 --force",
    # un séparateur échappé dans un argument
    POUSSE + " origin a\\;b --force",
    POUSSE + " origin HEAD:x \\& -f",
    POUSSE + " origin HEAD:x \\| -f",
    # un séparateur dans une accolade, une expansion, une substitution
    POUSSE + " origin {a,b\\;c} --force",
    POUSSE + " origin HEAD:x ${x:-;} --force",
    POUSSE + " origin HEAD:x ${x//a/|} -f",
    POUSSE + " origin HEAD:x ${x:-&} -f",
    POUSSE + " origin $(echo;) --force",
    POUSSE + " origin HEAD:x `a;b` --force",
    POUSSE + " origin HEAD:x 2>&1| tee x --force",
]

#: Le drapeau appartient à la commande suivante : main le signale quand même, la tête aussi.
DRAPEAUX_D_UNE_AUTRE_COMMANDE = [
    POUSSE + " origin HEAD:x ; pgrep -f boucle",
    POUSSE + " origin HEAD:x && tail -f journal.log",
    POUSSE + " origin HEAD:x | grep -f motifs.txt",
    POUSSE + " origin HEAD:x & ls -f",
]

SANS_DRAPEAU = [
    POUSSE + " origin HEAD:x",
    POUSSE + " -u origin feat/rejeu-lot-f-2026",
    POUSSE + " origin HEAD:x\nls -f",
    "git commit -m 'pas de " + POUSSE + " --force ici'",
]


@pytest.mark.parametrize("command", POUSSEES_FORCEES)
def test_une_poussee_forcee_reste_destructive(command: str) -> None:
    assert classify_tool("Bash", {"command": command}).mutation is DESTRUCTIVE


@pytest.mark.parametrize("command", [*POUSSEES_FORCEES, *DRAPEAUX_D_UNE_AUTRE_COMMANDE, *SANS_DRAPEAU])
def test_ce_que_le_motif_de_main_signale_la_tete_le_signale(command: str) -> None:
    """Propriété : la détection de la tête est un sur-ensemble de celle de main."""
    import re

    if re.search(MOTIF_DE_MAIN, command_surface(command), flags=re.IGNORECASE):
        assert classify_tool("Bash", {"command": command}).mutation is DESTRUCTIVE


@pytest.mark.parametrize("command", SANS_DRAPEAU)
def test_une_poussee_sans_drapeau_n_est_pas_destructive(command: str) -> None:
    assert not classify_tool("Bash", {"command": command}).destructive_reason


# ── Un message écrit par heredoc n'est une donnée que pour qui ne prend que du texte ──
#
# ``"$(cat <<'EOF' … EOF)"`` : le corps est le message d'un commit, le corps d'une PR, un
# texte affiché — ou le script d'un interpréteur, le nom d'un fichier à lire. Il n'est
# retiré de ce qui inspecte la ligne que derrière une commande connue pour ne prendre que
# du texte.

#: ``{}`` reçoit le corps du message.
MESSAGE = "\"$(cat <<'EOF'\n{}\nEOF\n)\""

CONSOMMATEURS_DE_TEXTE = [
    "git commit -m " + MESSAGE,
    "git commit -q --amend -m " + MESSAGE,
    "git -C sous/dossier commit -m " + MESSAGE,
    "git tag -a v1 -m " + MESSAGE,
    "git notes add -m " + MESSAGE,
    "rtk git commit -m " + MESSAGE,
    "rtk proxy git commit -m " + MESSAGE,
    "git add notes.md && git commit -m " + MESSAGE + " && git status",
    'gh pr create --title "nettoyage" --body ' + MESSAGE,
    "gh pr comment 12 --body " + MESSAGE,
    "gh issue create --title t --body " + MESSAGE,
    "gh release create v1 --notes " + MESSAGE,
    "echo " + MESSAGE,
    "printf '%s\\n' " + MESSAGE + " > notes.md",
]

INTERPRETEURS = [
    "bash -c " + MESSAGE,
    "sh -c " + MESSAGE,
    "zsh -c " + MESSAGE,
    "eval " + MESSAGE,
    "ssh hote " + MESSAGE,
    "timeout 5 bash -c " + MESSAGE,
    "env X=1 sh -c " + MESSAGE,
    # un texte affiché puis confié à un interpréteur
    "echo " + MESSAGE + " | bash",
    "printf %s " + MESSAGE + " | sh",
    "echo " + MESSAGE + " > >(sh)",
    # une commande qui n'est pas dans la liste
    "xargs -I% " + MESSAGE,
    "git -c core.editor=true commit -m " + MESSAGE,
    "gh codespace ssh -- " + MESSAGE,
    "gh alias set x " + MESSAGE,
    "X=1 git commit -m " + MESSAGE,
]


@pytest.mark.parametrize("gabarit", CONSOMMATEURS_DE_TEXTE)
@pytest.mark.parametrize("corps", [EFFACE, POUSSE + " --force", "plus de `" + EFFACE + '` ni de "' + EFFACE + '"'])
def test_le_message_d_une_commande_qui_ne_prend_que_du_texte_reste_une_donnee(gabarit: str, corps: str) -> None:
    assert not classify_tool("Bash", {"command": gabarit.format(corps)}).destructive_reason, gabarit


@pytest.mark.parametrize("gabarit", INTERPRETEURS)
@pytest.mark.parametrize("corps", [EFFACE, POUSSE + " --force"])
def test_le_corps_confie_a_un_interpreteur_reste_inspecte(gabarit: str, corps: str) -> None:
    facts = classify_tool("Bash", {"command": gabarit.format(corps)})
    assert facts.mutation is DESTRUCTIVE, gabarit


def test_le_corps_confie_a_python_reste_inspecte() -> None:
    facts = classify_tool("Bash", {"command": ("python3 -c " + MESSAGE).format("import shutil; shutil.rmtree('/x')")})
    assert facts.mutation is DESTRUCTIVE


@pytest.mark.parametrize(
    "gabarit",
    [
        "cat " + MESSAGE,
        "head -3 " + MESSAGE,
        "grep -c x " + MESSAGE,
        "bash -c " + MESSAGE,
    ],
)
def test_le_nom_d_un_secret_ecrit_par_heredoc_reste_vu(gabarit: str) -> None:
    facts = classify_tool("Bash", {"command": gabarit.format(".env")})
    assert facts.kind is ActionKind.SECRET_ACCESS, gabarit


@pytest.mark.parametrize("gabarit", [*CONSOMMATEURS_DE_TEXTE, *INTERPRETEURS[:7]])
def test_la_surface_lue_par_les_freins_suit_la_meme_regle(gabarit: str) -> None:
    """Derrière ce qui exécute son argument (``bash -c``, ``eval``, ``ssh``…), le corps reste sur la surface."""
    surface = command_surface(gabarit.format("touch drapeau"))
    assert ("touch drapeau" not in surface) is (gabarit in CONSOMMATEURS_DE_TEXTE), gabarit
