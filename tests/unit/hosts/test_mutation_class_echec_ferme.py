"""Ce que le classement lecture / mutation ne sait pas lire n'est jamais une lecture.

Une commande classée ``READ_ONLY`` saute le moteur de règles, les freins de bail et de
pause, et le compteur d'écritures de la clôture : classer en lecture une commande qui
écrit ou exécute est le seul sens dangereux. Quatre familles de formes que le classement
prenait pour des lectures, fermées par échec fermé :

- les guillemets ANSI-C (``$'…'``) et la chaîne localisée (``$"…"``) : le balayage des
  zones citées ne les modélise pas, et ce qu'il croit cité peut être exécuté ;
- une affectation de variable devant une commande (``NOM=valeur commande``, ``env
  NOM=valeur commande``) : certaines variables font exécuter du code au shell à son
  démarrage ou changent ce que fera un outil de la liste blanche. Seule une courte liste
  de variables inertes laisse la commande en lecture ;
- ``curl`` : seule une URL ``http://`` ou ``https://`` écrite en clair est une lecture ;
- un argument que le shell développe (variable, accolades, motif de fichiers) derrière un
  verbe dont la lecture se décide sur ses arguments : la règle du verbe lit l'argument
  écrit, pas ce qu'il devient.

Les chaînes de ce fichier ne sont jamais exécutées : elles sont passées à
``classify_tool`` et rien d'autre.
"""

from __future__ import annotations

import pytest

from grimoire.hosts.decisions import classify_tool
from grimoire.policies.schemas import MutationClass

LECTURE = MutationClass.READ_ONLY
MUTATION = MutationClass.MUTATION_CONTROLLED


def _classe(command: str) -> MutationClass:
    return classify_tool("Bash", {"command": command}).mutation


#: Enveloppes transparentes : ``{}`` reçoit la commande portée.
ENVELOPPES = [
    "rtk {}",
    "rtk proxy {}",
    "timeout 30 {}",
    "timeout -k 5 2m {}",
    "env {}",
    "env LC_ALL=C {}",
    "env -u NOM {}",
    "nice {}",
    "nice -n 10 {}",
    "command {}",
    "command -p {}",
    "time {}",
    "time -p {}",
    "sudo {}",
    "LC_ALL=C {}",
    "cd /tmp && {}",
    "timeout 30 rtk proxy {}",
]

SHELLS = ["bash", "sh", "zsh"]


# ── Guillemets ANSI-C et chaîne localisée ────────────────────────────────────

#: Un ``\'`` dans une chaîne ANSI-C ne la ferme pas : ce qui suit n'est pas cité.
ANSI_C_PUIS_COMMANDE = "echo $'a\\'' ; mkdir d ; echo \\'"
ANSI_C_PUIS_REDIRECTION = "echo $'a\\'' > f ; echo \\'"

FORMES_ANSI_C = [
    ANSI_C_PUIS_COMMANDE,
    ANSI_C_PUIS_REDIRECTION,
    "echo $'a\\'' | tee f ; echo \\'",
    "echo $'a\\'' && mkdir d && echo \\'",
    # Sans rien derrière : la forme n'est pas modélisée, elle n'est pas une lecture.
    "echo $'a'",
    "grep $'\\t' f",
    "echo a$'b'c",
    "echo 'a'$'b'",
    "echo \"a\"$'b'",
    "echo \\\\$'a'",  # l'antislash est échappé, pas le dollar
    "ls ; echo $'a'",
    "git log --format=$'%h'",
    # Chaîne localisée de bash.
    'echo $"a"',
    'echo a$"b"',
    'echo $"a" ; ls',
    'grep $"a" f',
]


@pytest.mark.parametrize("command", FORMES_ANSI_C)
def test_des_guillemets_ansi_c_ou_une_chaine_localisee_font_une_mutation(command: str) -> None:
    assert _classe(command) is MUTATION


@pytest.mark.parametrize(
    "command",
    [
        enveloppe.format(forme)
        for forme in (ANSI_C_PUIS_COMMANDE, ANSI_C_PUIS_REDIRECTION, "echo $'a'", 'echo $"a"')
        for enveloppe in ENVELOPPES
    ],
)
def test_des_guillemets_ansi_c_sous_une_enveloppe_font_une_mutation(command: str) -> None:
    assert _classe(command) is MUTATION


@pytest.mark.parametrize(
    "command",
    [
        *(f"{shell} -c $'ls'" for shell in SHELLS),
        *(f'{shell} -c $"ls"' for shell in SHELLS),
        *(f"{shell} -c 'echo $\"a\"'" for shell in SHELLS),
        *(f"{shell} -c 'ls ; echo $\"a\"'" for shell in SHELLS),
        *(f"{shell} -c \"echo $'a'\"" for shell in SHELLS),
        "bash -c 'echo $'\"'\"'a'\"'\"''",  # le script, une fois recollé, porte une chaîne ANSI-C
        "timeout 5 bash -c $'ls'",
        "rtk proxy sh -c 'echo $\"a\"'",
        "bash -c 'ls' ; echo $'a'",
        "echo $'a' ; bash -c 'ls'",
        "bash -c \"sh -c 'echo $\\\"a\\\"'\"",
    ],
)
def test_des_guillemets_ansi_c_dans_ou_autour_d_un_script_font_une_mutation(command: str) -> None:
    assert _classe(command) is MUTATION


@pytest.mark.parametrize(
    "command",
    [
        "cat <<EOF\nx\nEOF\n" + ANSI_C_PUIS_COMMANDE,
        "cat <<EOF\nc'est\nEOF\n" + ANSI_C_PUIS_COMMANDE,  # l'apostrophe du corps ne masque pas la suite
        "cat <<'EOF'\nc'est\nEOF\necho $'a'",
        ANSI_C_PUIS_COMMANDE + "\ncat <<EOF\nx\nEOF",
    ],
)
def test_des_guillemets_ansi_c_a_cote_d_un_heredoc_font_une_mutation(command: str) -> None:
    assert _classe(command) is MUTATION


@pytest.mark.parametrize(
    "command",
    [
        # Dans des guillemets simples ou doubles, ``$'`` et ``$"`` sont du texte.
        "grep 'a$' f",
        'grep "a$" f',
        "grep -c 'a$' f | sort",
        "grep -E 'a$|b$' f",
        "echo 'a $' 'b'",
        "echo \"c'est $'a'\"",
        "echo '$\"a\"'",
        "echo '$'",
        'echo "$"',
        "sed -n '/a$/p' f",
        "awk '/a$/' f",
        # Un dollar échappé n'ouvre rien : ce qui suit est une chaîne ordinaire.
        "echo \\$'a'",
        'echo \\$"a"',
        "bash -c 'grep \"a$\" f'",
        "timeout 5 grep 'a$' f",
        "rtk grep 'a$' f",
    ],
)
def test_un_dollar_devant_un_guillemet_a_l_interieur_de_guillemets_reste_un_texte(command: str) -> None:
    assert _classe(command) is LECTURE


# ── Affectation de variable devant une commande ──────────────────────────────

#: Variables qui font exécuter du code au shell porté ou changent ce que fait l'outil
#: porté. La règle est une liste blanche : cette liste n'est qu'un échantillon de ce
#: qu'elle refuse.
AFFECTATIONS_NON_INERTES = [
    # Démarrage d'un shell.
    "BASH_ENV=x",
    "ENV=x",
    "ZDOTDIR=x",
    "HOME=x",
    "PS4=x",
    "SHELLOPTS=xtrace",
    "BASHOPTS=x",
    "PROMPT_COMMAND=x",
    # Configuration d'un outil de la liste blanche.
    "CURL_HOME=x",
    "XDG_CONFIG_HOME=x",
    "GIT_EXTERNAL_DIFF=x",
    "GIT_DIR=x",
    "GIT_CONFIG_GLOBAL=x",
    "GIT_CONFIG_COUNT=1",
    "GIT_SSH_COMMAND=x",
    "GIT_PAGER=x",
    "GIT_PAGER=cat",
    "RIPGREP_CONFIG_PATH=x",
    # Chargeur, recherche des exécutables, afficheurs.
    "LD_PRELOAD=x",
    "LD_LIBRARY_PATH=x",
    "PATH=x",
    "PAGER=x",
    "PAGER=less",
    "GH_PAGER=x",
    "LESSOPEN=x",
    "LESS=x",
    # Interpréteurs.
    "PYTHONSTARTUP=x",
    "PYTHONPATH=x",
    "PERL5OPT=x",
    # Mandataire d'une requête.
    "http_proxy=x",
    "HTTPS_PROXY=x",
    "ALL_PROXY=x",
    # Une variable quelconque : absente de la liste blanche.
    "FOO=1",
    "X=",
    "GRIMOIRE_TASK_ID=x",
    # Un nom proche d'une variable inerte n'est pas inerte.
    "LC=C",
    "lc_all=C",
    "LANGX=C",
    "TERMINFO=x",
    "TZDIR=x",
]

#: Commandes portées : chacune est une lecture sans affectation.
PORTEES = [
    "bash -c 'ls'",
    "sh -c 'ls'",
    "zsh -c 'ls'",
    "curl -s https://example.com",
    "git log",
    "cat f",
]

#: ``{a}`` reçoit l'affectation, ``{c}`` la commande portée.
FORMES_D_AFFECTATION = [
    "{a} {c}",
    "env {a} {c}",
    "env -i {a} {c}",
    "env -u NOM {a} {c}",
    "LC_ALL=C {a} {c}",
    "env {a} LC_ALL=C {c}",
    "timeout 5 env {a} {c}",
    "sudo {a} {c}",
    "nice env {a} {c}",
    "cd /tmp && {a} {c}",
]


@pytest.mark.parametrize("portee", PORTEES)
def test_les_commandes_portees_sont_des_lectures_sans_affectation(portee: str) -> None:
    assert _classe(portee) is LECTURE


@pytest.mark.parametrize("forme", FORMES_D_AFFECTATION)
@pytest.mark.parametrize("portee", PORTEES)
@pytest.mark.parametrize("affectation", AFFECTATIONS_NON_INERTES)
def test_une_affectation_hors_liste_blanche_fait_une_mutation(affectation: str, portee: str, forme: str) -> None:
    assert _classe(forme.format(a=affectation, c=portee)) is MUTATION


#: Variables inertes : langue, fuseau, couleurs, dimensions du terminal, invites et
#: afficheur neutralisés, encodage de Python.
AFFECTATIONS_INERTES = [
    "LC_ALL=C",
    "LC_ALL=C.UTF-8",
    "LC_MESSAGES=C",
    "LC_CTYPE=fr_FR.UTF-8",
    "LC_COLLATE=C",
    "LC_NUMERIC=C",
    "LC_TIME=C",
    "LANG=C",
    "LANGUAGE=fr",
    "TZ=UTC",
    "NO_COLOR=1",
    "CLICOLOR=0",
    "CLICOLOR_FORCE=1",
    "FORCE_COLOR=0",
    "TERM=dumb",
    "COLUMNS=200",
    "LINES=50",
    "PAGER=cat",
    "PAGER=",
    "GH_PAGER=cat",
    "GH_PAGER=",
    "GIT_TERMINAL_PROMPT=0",
    "GH_PROMPT_DISABLED=1",
    "PYTHONIOENCODING=utf-8",
    "PYTHONUTF8=1",
    "PYTHONDONTWRITEBYTECODE=1",
]


@pytest.mark.parametrize("forme", ["{a} {c}", "env {a} {c}", "env -i {a} {c}", "timeout 5 env {a} {c}", "{a} TZ=UTC {c}"])
@pytest.mark.parametrize("portee", PORTEES)
@pytest.mark.parametrize("affectation", AFFECTATIONS_INERTES)
def test_une_affectation_inerte_laisse_la_lecture_en_lecture(affectation: str, portee: str, forme: str) -> None:
    assert _classe(forme.format(a=affectation, c=portee)) is LECTURE


@pytest.mark.parametrize(
    "command",
    [
        'PAGER="cat" git log',
        "PAGER='cat' git log",
        'PAGER="" git log',
        "PAGER='' git log",
        'LC_ALL="C" ls',
        "TZ='Europe/Paris' date",
        "env LC_ALL=C LANG=C TZ=UTC git log",
        "bash -c 'LC_ALL=C git log'",
        "bash -c 'git status ; LC_ALL=C sort f'",
        "for f in a b; do LC_ALL=C sort $f; done",
    ],
)
def test_une_affectation_inerte_entre_guillemets_ou_dans_un_script_reste_une_lecture(command: str) -> None:
    assert _classe(command) is LECTURE


@pytest.mark.parametrize(
    "command",
    [
        # La valeur d'un afficheur n'est inerte que vide ou ``cat``, guillemets retirés.
        'PAGER="less" git log',
        "PAGER='less' git log",
        'PAGER="cat x" git log',
        "PAGER=cat\\ x git log",
        'PAGER=c"at"x git log',
        'PAGER=cat"x" git log',  # ``cat`` sur la surface, ``catx`` pour le shell
        "PAGER=cat' -x' git log",
        "PAGER=$X git log",
        'GH_PAGER="less" gh pr view 1',
        "env PAGER='less' git log",
        # Une valeur entre guillemets ne cache pas le nom.
        'PATH="x" ls',
        "PATH='x' ls",
        'env BASH_ENV="x" bash -c \'ls\'',
        'HOME="x" zsh -c \'ls\'',
        'FOO="a b" cat f',
    ],
)
def test_une_valeur_entre_guillemets_ne_cache_ni_le_nom_ni_la_valeur(command: str) -> None:
    assert _classe(command) is MUTATION


@pytest.mark.parametrize(
    "command",
    [
        # À chaque niveau d'imbrication.
        "timeout 5 env X=1 bash -c 'ls'",
        "rtk proxy env X=1 git log",
        "timeout 5 nice env -u NOM X=1 cat f",
        "bash -c 'PATH=x ls'",
        "sh -c 'FOO=1 cat f'",
        "zsh -c 'HOME=x ls'",
        "bash -c 'git status ; PATH=x ls'",
        "bash -c 'env BASH_ENV=x bash -c ls'",
        "timeout 5 bash -c 'env HOME=x zsh -c ls'",
        "bash -c \"sh -c 'ENV=x ls'\"",
        # Une affectation seule vaut pour les commandes qui la suivent.
        "PATH=x ; ls",
        "PATH=x && ls",
        "PATH=x\nls",
        "HOME=x ; zsh -c 'ls'",
        "ls ; FOO=1",
        "FOO=1",
        "bash -c 'PATH=x ; ls'",
        # Dans une boucle ou derrière un mot-clé.
        "for f in a b; do PATH=x cat $f; done",
        "if PATH=x ls; then ls; fi",
        "! PATH=x ls",
        # Devant chaque segment.
        "ls | PATH=x sort",
        "ls && FOO=1 cat f",
        "git status ; env X=1 git log",
    ],
)
def test_une_affectation_hors_liste_blanche_est_refusee_a_chaque_niveau(command: str) -> None:
    assert _classe(command) is MUTATION


@pytest.mark.parametrize(
    "command",
    [
        "env -i ls",
        "env -i git log",
        "env - ls",
        "env --ignore-environment ls",
        "env -u NOM ls",
        "env -u NOM git log",
        "env -u NOM -u AUTRE cat f",
        "env --unset=NOM ls",
        "env -i LC_ALL=C ls",
        "env -u NOM TZ=UTC date",
        "env -i bash -c 'ls'",
        "env -u NOM curl -s https://example.com",
    ],
)
def test_env_qui_vide_ou_retire_une_variable_devant_une_lecture_reste_une_lecture(command: str) -> None:
    assert _classe(command) is LECTURE


@pytest.mark.parametrize(
    "command",
    [
        "env -u NOM BASH_ENV=x bash -c 'ls'",
        "env -u NOM PATH=x ls",
        "env -i PATH=x ls",
        "env -i HOME=x zsh -c 'ls'",
        "env --unset=NOM FOO=1 cat f",
        "env -u NOM LC_ALL=C CURL_HOME=x curl -s https://example.com",
        "env - GIT_DIR=x git log",
    ],
)
def test_env_qui_retire_une_variable_puis_en_affecte_une_autre_fait_une_mutation(command: str) -> None:
    assert _classe(command) is MUTATION


# ── Schéma d'URL de curl ─────────────────────────────────────────────────────

SCHEMAS_REFUSES = [
    "gopher", "gophers", "telnet", "dict", "ftp", "ftps", "smtp", "smtps", "file", "scp", "sftp",
    "ldap", "ldaps", "mqtt", "tftp", "rtsp", "imap", "imaps", "pop3", "pop3s", "smb", "smbs", "ws", "wss",
]  # fmt: skip

#: ``{u}`` reçoit l'URL.
FORMES_D_URL = [
    "curl -s {u}",
    'curl -s "{u}"',
    "curl -s '{u}'",
    "curl -s --url {u}",
    'curl -s --url "{u}"',
    "curl {u} -s",
    "curl -s https://example.com {u}",
    "curl -s --url https://example.com --url {u}",
    "rtk curl -s {u}",
    "timeout 5 curl -s {u}",
    "bash -c 'curl -s {u}'",
]


@pytest.mark.parametrize("forme", FORMES_D_URL)
@pytest.mark.parametrize("schema", SCHEMAS_REFUSES)
def test_curl_vers_un_schema_autre_que_http_fait_une_mutation(schema: str, forme: str) -> None:
    assert _classe(forme.format(u=f"{schema}://example.com/x")) is MUTATION


@pytest.mark.parametrize("forme", FORMES_D_URL)
@pytest.mark.parametrize(
    "url",
    [
        "GOPHER://example.com/x",
        "File:///x",
        "file:///x",
        "file://localhost/x",
        "httpx://example.com",
        "http:/example.com",
        "http:example.com",
        "https//example.com",
        "://example.com",
    ],
)
def test_curl_vers_un_schema_mal_forme_ou_en_majuscules_fait_une_mutation(url: str, forme: str) -> None:
    assert _classe(forme.format(u=url)) is MUTATION


@pytest.mark.parametrize("forme", FORMES_D_URL)
@pytest.mark.parametrize(
    "url",
    [
        "http://example.com",
        "https://example.com",
        "HTTP://example.com",
        "HTTPS://example.com",
        "HtTpS://Example.COM/x",
        "http://localhost:8080/health",
        "http://127.0.0.1:8765/api/v1/etat",
        "https://example.com/a/b.json?x=1#y",
        "https://api.example.com/depots/o/r/pulls/12",
    ],
)
def test_curl_vers_une_url_http_ou_https_reste_une_lecture(url: str, forme: str) -> None:
    assert _classe(forme.format(u=url)) is LECTURE


@pytest.mark.parametrize("forme", FORMES_D_URL)
@pytest.mark.parametrize(
    "url",
    [
        # Sans schéma, curl devine le protocole d'après le nom d'hôte.
        "example.com",
        "www.example.com",
        "ftp.example.com",
        "dict.example.com/x",
        "localhost:8080/health",
        "127.0.0.1:8765",
        "//example.com/x",
        "/x",
        "x",
    ],
)
def test_curl_vers_une_url_sans_schema_fait_une_mutation(url: str, forme: str) -> None:
    assert _classe(forme.format(u=url)) is MUTATION


@pytest.mark.parametrize(
    "command",
    [
        # Le globbing de curl développe accolades et crochets en plusieurs URL.
        "curl -s 'http://example.com/{a,b}'",
        'curl -s "http://example.com/[1-3]"',
        "curl -s http://example.com/[1-3]",
        "curl -s http://example.com/{a,b}",
        "curl -s 'http://{a,b}.example.com/'",
        "curl -s 'http://[::1]:8080/'",
        "curl -s --url 'http://example.com/{a,b}'",
        "curl -sg 'http://example.com/[1-3]'",  # même avec le globbing coupé
        "curl -s --globoff 'http://example.com/{a}'",
    ],
)
def test_curl_vers_une_url_a_accolades_ou_crochets_fait_une_mutation(command: str) -> None:
    assert _classe(command) is MUTATION


@pytest.mark.parametrize(
    "command",
    [
        # Une valeur portée par une variable ne se lit pas.
        "curl -s $URL",
        'curl -s "$URL"',
        "curl -s ${URL}",
        'curl -s "${URL}/x"',
        'curl -s "https://$HOTE/x"',
        "curl -s https://$HOTE/x",
        "curl -s https://example.com/$CHEMIN",
        'curl -s "https://example.com/$CHEMIN"',
        "curl -s 'https://example.com/$CHEMIN'",  # littéral pour le shell, mais la passe ne le sait pas
        "curl -s --url $URL",
        'curl -s --url "$URL"',
        "curl -s https://example.com/~x/$1",
    ],
)
def test_curl_vers_une_url_portee_par_une_variable_fait_une_mutation(command: str) -> None:
    assert _classe(command) is MUTATION


@pytest.mark.parametrize(
    "command",
    [
        # Un nom devant ``@`` est un identifiant : un appel authentifié reste évalué et tracé.
        "curl -s https://nom@example.com/x",
        'curl -s "https://nom@example.com/x"',
        "curl -s --url https://nom@example.com/x",
        # Ce qui n'est pas une URL entière.
        "curl -s -",
        'curl -s ""',
        'curl -s "https://example.com/a b"',
        "curl -s 'https://example.com/a\\b'",
        "curl -s https://",
        "curl -s https:///x",
        'curl -s --url ""',
    ],
)
def test_curl_vers_une_url_a_identifiant_ou_incomplete_fait_une_mutation(command: str) -> None:
    assert _classe(command) is MUTATION


#: Options qui changent de protocole, d'hôte ou de chemin réseau en cours de route.
OPTIONS_DE_DETOURNEMENT = [
    "--proto all",
    "--proto =gopher",
    "--proto-redir all",
    "--proto-default ftp",
    "--connect-to example.com:443:autre.example:443",
    "--resolve example.com:443:127.0.0.1",
    "-x http://mandataire.example:3128",
    "--proxy http://mandataire.example:3128",
    "--proxy1.0 mandataire.example:3128",
    "--preproxy socks5://mandataire.example:1080",
    "--socks4 mandataire.example:1080",
    "--socks4a mandataire.example:1080",
    "--socks5 mandataire.example:1080",
    "--socks5-hostname mandataire.example:1080",
    "--unix-socket /tmp/prise",
    "--abstract-unix-socket prise",
    "--location-trusted",
    "--doh-url https://example.com/dns",
    "--interface eth0",
    "--dns-servers 127.0.0.1",
    "--haproxy-protocol",
    "--request-target /x",
    "--path-as-is",
    "-q",
    "--disable",
]


@pytest.mark.parametrize(
    "forme",
    [
        "curl -s {o} https://example.com",
        "curl -s https://example.com {o}",
        "rtk curl -s {o} https://example.com",
        "bash -c 'curl -s {o} https://example.com'",
    ],
)
@pytest.mark.parametrize("option", OPTIONS_DE_DETOURNEMENT)
def test_curl_avec_une_option_de_detournement_fait_une_mutation(option: str, forme: str) -> None:
    assert _classe(forme.format(o=option)) is MUTATION


@pytest.mark.parametrize(
    "command",
    [
        'curl -s "--resolve" example.com:443:127.0.0.1 https://example.com',
        "curl -s --resolve 'example.com:443:127.0.0.1' https://example.com",
        "curl -s --resolve=example.com:443:127.0.0.1 https://example.com",
        "curl -s --reso example.com:443:127.0.0.1 https://example.com",  # abréviation acceptée par curl
        "curl -s --pro all https://example.com",
        'curl -s "-x" http://mandataire.example:3128 https://example.com',
        "curl -sx http://mandataire.example:3128 https://example.com",
        "curl -sLx http://mandataire.example:3128 https://example.com",
    ],
)
def test_curl_avec_une_option_de_detournement_citee_ou_abregee_fait_une_mutation(command: str) -> None:
    assert _classe(command) is MUTATION


@pytest.mark.parametrize(
    "command",
    [
        # ``-L`` reste une lecture : sans ``--proto-redir`` ni ``--location-trusted`` (refusés
        # plus haut), curl borne lui-même les protocoles d'une redirection.
        "curl -sL https://example.com",
        "curl -s --location https://example.com",
        "curl -sSL --max-redirs 3 --connect-timeout 2 https://example.com/api",
        "curl -fsSL --retry 2 --max-time 5 -X GET https://example.com/x",
        "curl -sI https://example.com",
        "curl -s -w '%{http_code}' https://example.com/health",
        "curl -s --cacert ca.pem https://example.com",
        "curl -s",
    ],
)
def test_curl_en_get_anonyme_vers_http_reste_une_lecture(command: str) -> None:
    assert _classe(command) is LECTURE


# ── La classe destructive ne change pas ──────────────────────────────────────


@pytest.mark.parametrize(
    "command",
    [
        "PATH=x rm -rf build",
        "env BASH_ENV=x bash -c 'rm -rf build'",
        "LC_ALL=C rm -rf build",
        "curl -s gopher://example.com/x ; rm -rf build",
        "curl -s https://example.com ; git reset --hard",
        "echo $'a' ; rm -rf build",
        "echo $'a' ; git push --force origin main",
        'echo $"a" && git clean -fd',
    ],
)
def test_la_classe_destructive_ne_change_pas(command: str) -> None:
    facts = classify_tool("Bash", {"command": command})
    assert facts.mutation is MutationClass.DESTRUCTIVE
    assert facts.destructive_reason


# ── Arguments que le shell développe ─────────────────────────────────────────
#
# ``curl``, ``journalctl``, ``systemctl``, ``ss`` et ``gh api`` ne sont des lectures que
# selon leurs arguments, lus tels qu'ils sont écrits. Le shell, lui, développe un argument
# avant de lancer la commande : une variable (que le découpage en mots scinde), des
# accolades, un motif de fichiers. L'argument développé peut être une option que la règle
# du verbe refuse. Pour ces verbes, un argument que le shell développe n'est pas lu.

#: ``{}`` reçoit l'argument développé, à une place où un argument écrit en clair se lit.
VERBES_A_ARGUMENTS = [
    "curl -s -m {} https://example.com",
    "curl -s https://example.com -w {}",
    "journalctl -n 5 {}",
    "journalctl {} -n 5",
    "systemctl status {}",
    "systemctl {} status x",
    "ss -ltn {}",
    "gh api repos/o/r/pulls {}",
    "gh api {} repos/o/r/pulls",
]

ARGUMENTS_DEVELOPPES = [
    # Variable : sa valeur n'est pas connue, et hors guillemets elle se scinde en mots.
    "$X",
    "${X}",
    '"$X"',
    '"${X}"',
    "a$X",
    "$1",
    "$@",
    # Accolades.
    "{a,b}",
    "{1..3}",
    "a{b,c}",
    '{"a",b}',
    "{a,'b'}",
    "{a,b,c}d",
    # Motif de fichiers.
    "*",
    "a*",
    "[ab]",
    "??",
    "a?",
]


@pytest.mark.parametrize("forme", VERBES_A_ARGUMENTS)
@pytest.mark.parametrize("argument", ARGUMENTS_DEVELOPPES)
def test_un_argument_que_le_shell_developpe_fait_une_mutation(argument: str, forme: str) -> None:
    assert _classe(forme.format(argument)) is MUTATION


@pytest.mark.parametrize(
    "command",
    [
        # La variable d'une boucle porte ce que la liste lui donne.
        "for o in a b; do journalctl $o; done",
        "for o in a; do gh api $o repos/o/r/pulls; done",
        "for o in 5; do curl -s -m $o https://example.com; done",
        "for o in a; do systemctl status $o; done",
        "for o in a; do ss $o; done",
        # Sous une enveloppe et dans un script.
        "rtk journalctl $X",
        "timeout 5 ss {a,b}",
        "env LC_ALL=C systemctl status {a,b}",
        "bash -c 'journalctl $X'",
        "sh -c 'gh api {a,b} repos/o/r/pulls'",
        "bash -c 'for o in a; do journalctl $o; done'",
        # Quelle que soit la graphie de la sous-commande et du point d'entrée.
        "gh api view {a,b}",
        "gh 'api' repos/o/r/pulls {a,b}",
        'gh "api" view {a,b}',
    ],
)
def test_un_argument_developpe_est_refuse_dans_une_boucle_une_enveloppe_ou_un_script(command: str) -> None:
    assert _classe(command) is MUTATION


@pytest.mark.parametrize(
    "command",
    [
        # Entre guillemets, accolades et motifs sont des données.
        "gh api repos/o/r/pulls --jq '.[] | {a, b}'",
        "gh api repos/o/r/pulls --jq '.[0].a'",
        "systemctl list-units 'ssh*'",
        'journalctl -u x -g "a[bc]"',
        "journalctl -u x --since '1 hour ago'",
        "ss -ltn 'sport = :22'",
        "curl -s -w '%{http_code} %{time_total}' https://example.com",
        # Des accolades sans virgule ni intervalle ne se développent pas.
        "gh api repos/{owner}/{repo}/pulls",
        "curl -s -w %{http_code} https://example.com",
        # Un point d'interrogation derrière une barre oblique : un chemin, jamais une option.
        "gh api repos/o/r/pulls?per_page=5",
        "curl -s https://example.com/a?b=1",
        "systemctl show -p ActiveState,SubState x",
    ],
)
def test_un_argument_ecrit_en_clair_reste_une_lecture(command: str) -> None:
    assert _classe(command) is LECTURE


@pytest.mark.parametrize(
    "command",
    [
        # Un dollar entre guillemets simples est un texte pour le shell, mais les mots
        # résolus ne disent pas quels guillemets le portaient : refusé aussi.
        "journalctl -u x -g 'a$'",
        "gh api repos/o/r/pulls --jq '.[] | select(.a == $b)'",
    ],
)
def test_un_dollar_cite_dans_un_argument_de_ces_verbes_est_refuse_aussi(command: str) -> None:
    assert _classe(command) is MUTATION


@pytest.mark.parametrize(
    "command",
    [
        "gh pr view $PR",
        "gh pr list --author $USER",
        "gh issue view $1",
        "gh run view $ID",
    ],
)
def test_gh_hors_gh_api_garde_sa_lecture_d_avant(command: str) -> None:
    assert _classe(command) is LECTURE
