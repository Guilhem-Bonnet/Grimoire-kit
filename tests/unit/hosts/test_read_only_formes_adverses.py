"""Relecture adverse du classement en lecture seule : les formes de la même classe qu'il restait à fermer.

Le classement relit les mots résolus et échoue fermé (``test_read_only_resolved_words``).
Ce fichier porte ce qu'une recherche adverse a trouvé ensuite, famille par famille :
une redirection que le motif laissait passer, un rappel d'historique, une expansion qui
affecte ou exécute, un motif de fichiers là où le nombre ou la place des mots compte,
un verbe de lecture qui affecte une variable ou écrit par une option, un proxy qui
confie le verbe à un autre programme, un heredoc reconnu là où le shell n'en ouvre pas
ou fermé là où il ne le ferme pas, et la détection destructive qui lisait les
guillemets autrement que le shell.

Chaque famille a son test rouge avant le correctif, et à côté les lectures courantes
qui doivent le rester. Le dernier test énumère les lectures de tous les jours, nues et
sous le proxy de terminal : c'est la non-régression de l'usage normal.
"""

from __future__ import annotations

import pytest

from grimoire.hosts.decisions import classify_tool
from grimoire.policies.schemas import ActionKind, MutationClass

LECTURE = MutationClass.READ_ONLY
DESTRUCTIVE = MutationClass.DESTRUCTIVE

#: Assemblés : ces formes ne sont jamais écrites d'un bloc hors d'un cas de test.
EFFACE = "rm " + "-rf build"
EFFACE_GIT = "git reset " + "--hard"


def _classe(command: str) -> MutationClass:
    return classify_tool("Bash", {"command": command}).mutation


# ── Une redirection est une redirection, quelle que soit la cible ────────────


@pytest.mark.parametrize(
    "command",
    [
        "echo a >=sortie",
        "cat f >=sortie",
        "ls >>=journal",
        "echo a 2>=journal",
        "sort f >=f",
    ],
)
def test_une_redirection_vers_une_cible_qui_commence_par_un_egal_ecrit(command: str) -> None:
    assert _classe(command) is not LECTURE


# ── Un rappel d'historique réinjecte une ligne passée, opérateurs compris ────


@pytest.mark.parametrize(
    "command",
    [
        "echo !!",
        'echo "!!"',
        "cat !$",
        "ls !*",
        "echo !-2",
        "echo !!:gs/a/b/",
        "grep motif !ls",
        'echo "a !b"',
        "ls ; echo !!",
        "rtk ls !$",
        "bash -c 'echo !!'",
    ],
)
def test_un_rappel_d_historique_n_est_admis_derriere_aucun_verbe(command: str) -> None:
    assert _classe(command) is not LECTURE


@pytest.mark.parametrize(
    "command",
    [
        "find . ! -name '*.pyc'",
        "! grep -q motif f",
        "echo 'a !b'",
        'echo "fini !"',
        "grep -v '^!' f",
        'echo "a != b"',
        "sed '/x/!d' f",
        "git log --format='%h !%s'",
    ],
)
def test_un_point_d_exclamation_qui_ne_rappelle_rien_reste_une_lecture(command: str) -> None:
    assert _classe(command) is LECTURE


# ── Une expansion qui affecte une variable ou exécute en se développant ──────


@pytest.mark.parametrize(
    "command",
    [
        # affectation : la liste blanche des variables inertes ne se contourne pas par une expansion
        "echo ${PATH::=/tmp/x}",
        "echo ${X:=valeur}",
        "echo ${X=valeur}",
        'echo "${X:=valeur}"',
        "ls ${X:=-la} ; ls",
        "echo ${T[1]:=x}",
        # arithmétique à l'ancienne : affecte, et évalue ce que portent les variables
        "echo $[X=1]",
        'echo "$[1+1]"',
        "cat f$[N]",
        # drapeaux d'expansion et opérateur qui développent la VALEUR d'une variable
        "echo ${(e)X}",
        'echo "${(e)X}"',
        "echo ${X@P}",
        'cat "${X@P}"',
        # dans le corps d'un heredoc, que le shell développe avant de le passer
        "cat <<EOF\n${PATH::=/tmp/x}\nEOF\nls",
        "cat <<EOF\n$[X=1]\nEOF",
        "cat <<EOF\n${X@P}\nEOF",
    ],
)
def test_une_expansion_qui_affecte_ou_execute_n_est_pas_une_lecture(command: str) -> None:
    assert _classe(command) is not LECTURE


@pytest.mark.parametrize(
    "command",
    [
        "echo ${X:-defaut}",
        'echo "${X%.txt}"',
        "echo ${#X}",
        "cat ${F}",
        'ls "${DOSSIER}/src"',
        "echo ${X/a/b}",
        'echo "$X" "$Y"',
        "cat <<EOF\n$X ${Y:-z}\nEOF",
    ],
)
def test_une_expansion_qui_ne_fait_que_lire_reste_une_lecture(command: str) -> None:
    assert _classe(command) is LECTURE


# ── Un motif de fichiers devient plusieurs mots, ou une option ───────────────


@pytest.mark.parametrize(
    "command",
    [
        # le nombre de mots compte : le second fichier est la sortie
        "uniq - sortie",
        "sort f | uniq -c - sortie",
        "uniq a*",
        "uniq src/?",
        "xxd a*",
        "xxd vidage[12]",
        # un mot qui commence par un tiret et porte un motif devient l'option que le dossier fournit
        "sort -* f",
        "find . -de*",
        "tree -*",
        "rg --pre* motif",
        "git log --out*",
        "date -*",
        "file -?",
        # la place du mot compte : le script de sed, le programme de awk, la valeur d'une option
        "sed w* f",
        "sed -n p* f",
        "sed -e p f -e w*",
        "awk p* f",
        "awk -v a* '{print}' f",
        # l'argument d'une enveloppe : le second fichier trouvé devient la commande
        "env -u N* ls",
        # la ressource de gh : deux fichiers trouvés font la ressource et son verbe
        "gh r* view",
        "gh pr v* 12",
        # derrière le proxy et dans un script
        "rtk proxy uniq a*",
        "bash -c 'sort -* f'",
    ],
)
def test_un_motif_de_fichiers_n_est_pas_admis_la_ou_il_change_ce_que_fait_la_commande(command: str) -> None:
    assert _classe(command) is not LECTURE


@pytest.mark.parametrize(
    "command",
    [
        "ls *.md",
        "cat src/*.py | head",
        "wc -l src/*.py",
        "grep -n motif src/*.py",
        "rg motif src/*.py",
        "sort data/*.txt | uniq -c",
        "git diff -- src/*.py",
        "git log --oneline src/*.py",
        "sed -n 1,5p src/*.py",
        "awk '{print $1}' journaux/*.log",
        "tree src/*",
        "file src/*",
        "find src/* -name x",
        "find . -name '*.py'",
        "uniq 'a*'",
        "gh pr list --search is:open*",
        "gh pr view 12 --json files --jq '.files[].path'",
    ],
)
def test_un_motif_de_fichiers_en_operande_reste_une_lecture(command: str) -> None:
    assert _classe(command) is LECTURE


# ── Un verbe de lecture qui affecte une variable ou écrit par une option ─────


@pytest.mark.parametrize(
    "command",
    [
        # printf -v affecte une variable du shell : la liste blanche des affectations ne se contourne pas par là
        "printf -v PATH %s /tmp/x",
        "printf -vX a",
        'printf "-v" X a',
        "printf $OPTIONS a",
        "printf -* a",
        "printf -v PATH %s /tmp/x ; ls",
        "for o in a; do printf -v o %s -delete; find . $o; done",
        # awk : fichiers écrits par une option, source incluse, appel indirect
        "awk -d '{print}' f",
        "awk -dvidage '{print}' f",
        "awk --dump-variables=vidage '{print}' f",
        "awk -o '{print}' f",
        "awk --pretty-print=sortie '{print}' f",
        "awk -p '{print}' f",
        "awk --profile=sortie '{print}' f",
        "awk -D '{print}' f",
        "awk -W dump-variables=vidage '{print}' f",
        "gawk -v x=1 -d '{print}' f",
        "awk '@include \"autre\"\n{print}' f",
        "awk 'BEGIN { f = \"sys\" \"tem\"; @f(\"touch drapeau\") }'",
        # git : l'option de sortie est lue par toute sous-commande qui prend des options de diff
        "git blame --output=sortie f",
        "git shortlog --output=sortie",
        "git blame --out sortie f",
        # git branch -l créait une branche avant de lister
        "git branch -l nouvelle",
        # tree : la forme longue de -o
        "tree --output=sortie",
        "tree --out sortie",
    ],
)
def test_un_verbe_de_lecture_qui_affecte_ou_ecrit_par_une_option_est_borne(command: str) -> None:
    assert _classe(command) is not LECTURE


@pytest.mark.parametrize(
    "command",
    [
        "printf '%s\\n' a b",
        "printf -- '-%s\\n' a",
        "printf '%s\\n' $FICHIERS",
        'printf "%d\\n" 3',
        "printf %s a",
        "awk -F: '{print $1}' /etc/passwd",
        "awk -F , -v n=2 '{print $n}' f",
        "awk 'NR==1' f",
        "awk '{print $1}' f | sort | uniq -c",
        "awk -- '{print}' f",
        "gawk --posix '{print NF}' f",
        "tree --noreport -L 2",
        "git blame -L 1,5 f",
        "git shortlog -sn",
        "git branch --list 'feat/*'",
        "sort f | uniq -c",
        "uniq -c -",
    ],
)
def test_leurs_formes_de_lecture_restent_des_lectures(command: str) -> None:
    assert _classe(command) is LECTURE


# ── Le proxy de terminal confie « grep » à un autre programme ────────────────


@pytest.mark.parametrize(
    "command",
    [
        "rtk grep motif . --pre ./filtre",
        "rtk grep --pre=./filtre motif",
        "rtk grep motif --pre-glob '*.pdf' --pre ./filtre",
        "timeout 5 rtk grep motif --hostname-bin ./nom",
        'rtk grep motif "--pre" ./filtre',
        "bash -c 'rtk grep motif --pre ./filtre'",
        "rtk grep motif $OPTIONS",
    ],
)
def test_un_grep_sous_le_proxy_suit_la_regle_du_programme_qui_cherche(command: str) -> None:
    assert _classe(command) is not LECTURE


@pytest.mark.parametrize(
    "command",
    [
        "rtk grep -n motif src",
        'rtk grep "a|b" src -t py',
        "rtk grep -rn 'motif' src tests",
        "rtk grep motif src/*.py",
        "rtk proxy grep -rn motif src",
        "grep -rn motif $DOSSIER",
    ],
)
def test_un_grep_de_lecture_sous_le_proxy_reste_une_lecture(command: str) -> None:
    assert _classe(command) is LECTURE


# ── Une variable de boucle n'est remplacée que sous son nom entier ───────────


@pytest.mark.parametrize(
    "command",
    [
        "for o in a; do find . $oo[1]; done",
        "for o in a; do find . $oo:t; done",
        "for o in a; do sort $o_x f; done",
    ],
)
def test_une_variable_au_nom_plus_long_n_est_pas_la_variable_de_boucle(command: str) -> None:
    assert _classe(command) is not LECTURE


@pytest.mark.parametrize(
    "command",
    [
        # ``fi done`` ferme la boucle : derrière un tube, la variable n'existe plus après elle
        "for o in a; do if true; then ls; fi done | cat; find . $o; for p in b; do ls; done",
        "for o in a; do if true; then ls; fi; done | cat; find . $o; for p in b; do ls; done",
        # une valeur que le shell développe en autre chose que ce qui est écrit
        "for o in =ls; do find . $o; done",
        "for o in ~-; do find . $o; done",
    ],
)
def test_une_variable_de_boucle_n_est_lue_que_dans_le_corps_de_sa_boucle(command: str) -> None:
    assert _classe(command) is not LECTURE


def test_une_boucle_fermee_derriere_un_autre_mot_cle_reste_une_lecture() -> None:
    assert _classe("for f in a b; do if true; then sort $f; fi done") is LECTURE


# ── Un heredoc n'est un heredoc que là où le shell en ouvre et en ferme un ───

#: ``{}`` reçoit la commande que le shell exécute et que le classement prenait pour un corps.
FAUX_HEREDOCS = [
    # l'étiquette continue après le guillemet : le shell attend ``EOF``, pas ``EO``
    "cat <<'EO'F\nEOF\n{}\nEO",
    'cat <<"EO"F\nEOF\n{}\nEO',
    "cat <<EOF-1\nEOF\ncat <<X\nEOF-1\n{}\nX",
    # une ligne de fermeture en retrait ne ferme rien : le corps continue, et ce qu'il cite n'ouvre rien
    "cat <<EOF\n EOF\ncat <<X\nEOF\n{}\nX",
    "cat <<EOF\nEOF \ncat <<X\nEOF\n{}\nX",
    "cat <<-EOF\n EOF\ncat <<X\nEOF\n{}\nX",
    # une étiquette que le classement ne sait pas lire ouvre quand même un heredoc pour le shell
    "cat <<\\EOF\ncat <<X\nEOF\n{}\nX",
    "cat <<E\\OF\ncat <<X\nEOF\n{}\nX",
    "cat <<$E\ncat <<X\n$E\n{}\nX",
]


@pytest.mark.parametrize("gabarit", FAUX_HEREDOCS)
def test_ce_qui_suit_un_heredoc_mal_lu_reste_inspecte(gabarit: str) -> None:
    assert _classe(gabarit.format("touch drapeau")) is not LECTURE


@pytest.mark.parametrize(
    "gabarit",
    [
        *FAUX_HEREDOCS,
        # ``<<`` cité, échappé, dans un commentaire ou dans une chaîne sur l'entrée : pas un heredoc
        "echo '<<EOF'\n{}\nEOF",
        'echo "<<EOF"\n{}\nEOF',
        "echo \\<<EOF\n{}\nEOF",
        "ls # <<EOF\n{}\nEOF",
        "cat <<< EOF\n{}\nEOF",
        # un commentaire dont l'apostrophe cite l'ouverture, pour un shell qui ne lit pas les commentaires
        "echo # '\ncat <<EOF\n'\n{}\nEOF",
    ],
)
def test_une_commande_destructive_derriere_un_faux_heredoc_reste_destructive(gabarit: str) -> None:
    facts = classify_tool("Bash", {"command": gabarit.format(EFFACE)})
    assert facts.mutation is DESTRUCTIVE, gabarit
    assert facts.destructive_reason


@pytest.mark.parametrize(
    "gabarit",
    [
        "echo '<<EOF'\n{}\nEOF",
        "ls # <<EOF\n{}\nEOF",
        "cat <<'EO'F\nEOF\n{}\nEO",
        "cat <<EOF\n EOF\ncat <<X\nEOF\n{}\nX",
    ],
)
def test_une_lecture_de_secret_derriere_un_faux_heredoc_reste_vue(gabarit: str) -> None:
    facts = classify_tool("Bash", {"command": gabarit.format("cat .env")})
    assert facts.kind is ActionKind.SECRET_ACCESS, gabarit
    assert facts.secret_target


@pytest.mark.parametrize(
    "command",
    [
        "cat <<EOF\n" + EFFACE + "\nEOF",
        "cat <<'EOF'\nil s'agit d'une note sur " + EFFACE + "\nEOF",
        "cat <<-EOF\n\tune ligne\n\tEOF",
        "cat <<EOF\nune ligne\nEOF\ngit status",
        "cat <<A <<B\nun\nA\ndeux\nB",
        "cat << EOF\nune ligne\nEOF",
        'cat <<"EOF"\n$X\nEOF',
    ],
)
def test_un_vrai_heredoc_reste_une_donnee(command: str) -> None:
    facts = classify_tool("Bash", {"command": command})
    assert facts.mutation is LECTURE
    assert not facts.destructive_reason


def test_un_heredoc_ecrit_dans_un_fichier_reste_une_ecriture_sans_etre_destructif() -> None:
    facts = classify_tool("Bash", {"command": "cat > notes.md <<'EOF'\n" + EFFACE + "\nEOF"})
    assert facts.mutation is MutationClass.MUTATION_CONTROLLED
    assert not facts.destructive_reason


# ── La détection destructive lit les guillemets comme le shell ───────────────


@pytest.mark.parametrize(
    "gabarit",
    [
        # un guillemet échappé hors guillemets n'ouvre aucun texte cité
        "echo \\' ; {} ; echo \\'",
        "echo a\\'b && {} && echo c\\'d",
        # dans une chaîne ANSI-C, un guillemet échappé ne la ferme pas
        "echo $'\\'' ; {} ; echo \\'",
        "echo $'a\\'b' ; {} ; echo 'c'",
        # un commentaire dont l'apostrophe ouvrirait un texte cité
        "ls # l'état\n{}\n# l'après",
    ],
)
@pytest.mark.parametrize("action", [EFFACE, EFFACE_GIT])
def test_une_commande_destructive_n_est_pas_cachee_par_des_guillemets_mal_lus(gabarit: str, action: str) -> None:
    facts = classify_tool("Bash", {"command": gabarit.format(action)})
    assert facts.mutation is DESTRUCTIVE, gabarit
    assert facts.destructive_reason


@pytest.mark.parametrize(
    "command",
    [
        "git commit -m 'retire " + EFFACE + " du script'",
        'git commit -m "retire ' + EFFACE + ' du script"',
        "echo 'note : " + EFFACE_GIT + "'",
        "grep -rn '" + EFFACE + "' docs/",
    ],
)
def test_une_commande_destructive_citee_reste_une_donnee(command: str) -> None:
    assert not classify_tool("Bash", {"command": command}).destructive_reason


# ── Un verbe ou un drapeau cité reste le verbe ou le drapeau que le shell lance ──


@pytest.mark.parametrize(
    "command",
    [
        "'rm' -rf build",
        '"rm" -rf build',
        'rm "-rf" build',
        "rm '-r' '-f' build",
        "r\\m -rf build",
        'rm -r""f build',
        "r''m -rf build",
        "timeout 5 'rm' -rf build",
        "ls ; 'rm' -rf build > journal",
        'git reset "--hard"',
        '"git" reset --hard',
        "git 'reset' --hard HEAD~1",
        'git push "--force" origin main',
        "git push origin main '-f'",
        "git clean '-fd'",
        'git checkout -- "."',
    ],
)
def test_un_verbe_ou_un_drapeau_cite_reste_destructif(command: str) -> None:
    facts = classify_tool("Bash", {"command": command})
    assert facts.mutation is DESTRUCTIVE, command
    assert facts.destructive_reason


@pytest.mark.parametrize(
    "command",
    [
        # un texte cité qui porte des blancs est une prose, pas un mot de commande
        'git commit -m "retire rm -rf du script"',
        "git commit -m 'git reset --hard est interdit ici'",
        'grep -rn "rm -rf" docs/',
        "echo 'voir git push --force'",
        'gh pr create --title "retire rm -rf" --body "plus de git clean -fd"',
        # un mot cité qui ne suit aucun verbe destructif
        "grep -n '--force' notes.md",
        'git log --grep "reset"',
    ],
)
def test_une_prose_citee_reste_une_donnee(command: str) -> None:
    assert not classify_tool("Bash", {"command": command}).destructive_reason


@pytest.mark.parametrize(
    "command",
    [
        'cat .e"n"v',
        "cat .en\\v",
        "cat '.e'nv",
        'head -3 "."env',
    ],
)
def test_un_nom_de_secret_coupe_par_des_guillemets_reste_une_lecture_de_secret(command: str) -> None:
    facts = classify_tool("Bash", {"command": command})
    assert facts.kind is ActionKind.SECRET_ACCESS, command
    assert facts.secret_target


# ── Non-régression : les lectures de tous les jours restent des lectures ─────

LECTURES_COURANTES = [
    "git status",
    "git status --short",
    "git log --oneline -5",
    "git log --format='%h %s' -n 3 -- src/app.py",
    "git diff",
    "git diff --stat HEAD~1",
    "git diff -- src/app.py",
    "git show HEAD:src/app.py",
    "git show --stat HEAD",
    "git -C sous/dossier status",
    "git --no-pager log -1",
    "ls",
    "ls -la src",
    "cat notes.md",
    "cat a.txt b.txt",
    "grep -rn motif src",
    "grep -E 'a|b' notes.md",
    'grep -n "a\\|b" notes.md',
    "rg motif src",
    "rg -n 'def \\w+' src --glob '*.py'",
    "find . -name '*.py'",
    "find src -type f -newer notes.md",
    "find . -maxdepth 2 -name '*.md' -print",
    "head -20 notes.md",
    "tail -n 50 journal.log",
    "wc -l notes.md",
    "jq '.a.b' donnees.json",
    "jq -r '.[] | .nom' donnees.json",
]

ENVELOPPES_COURANTES = ["{}", "rtk {}", "rtk proxy {}"]


@pytest.mark.parametrize("enveloppe", ENVELOPPES_COURANTES)
@pytest.mark.parametrize("lecture", LECTURES_COURANTES)
def test_les_lectures_courantes_restent_des_lectures_nues_et_sous_le_proxy(lecture: str, enveloppe: str) -> None:
    assert _classe(enveloppe.format(lecture)) is LECTURE


@pytest.mark.parametrize(
    "command",
    [
        # les réécritures réelles du proxy
        "rtk read notes.md",
        "rtk read -n notes.md --max-lines 50",
        "rtk git status",
        "rtk grep -n motif src",
        "rtk find . -name '*.py'",
        "rtk ls -la",
        "rtk wc -l notes.md",
        # tubes, redirections sans fichier, enchaînements
        "git log --oneline | head -5",
        "grep -rn motif src | wc -l",
        "cat notes.md | grep -n motif | head -3",
        "find . -name '*.py' | head -20",
        "ls -la 2>/dev/null",
        "git status 2>&1 | tail -3",
        "cd src && ls && git status",
        "git diff --stat && git log -1",
        "rtk git log --oneline -3 && rtk git status",
        # sous un délai, dans un script, dans une boucle
        "timeout 30 git status",
        "bash -c 'git status && ls'",
        "for f in a.md b.md; do head -3 $f; done",
        "for f in a.md b.md; do wc -l $f; rtk grep -c motif $f; done",
    ],
)
def test_les_formes_composees_courantes_restent_des_lectures(command: str) -> None:
    assert _classe(command) is LECTURE
