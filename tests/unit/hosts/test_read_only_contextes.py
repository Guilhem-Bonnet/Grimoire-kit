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


# ── Une variable de boucle suivie de deux-points derrière git ────────────────


@pytest.mark.parametrize(
    "command",
    [
        "for rev in 59575bc29 ec37d0808 origin/main; do echo \"== $rev\"; git show $rev:src/x.py | wc -l; done",
        "for c in 0753d01 020ba30; do echo $c; git show $c:tools/o.py | grep -n 'def ' | cut -c1-90; done",
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
        # un modificateur de zsh peut remplacer un morceau de la valeur par ce qui est écrit derrière
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
