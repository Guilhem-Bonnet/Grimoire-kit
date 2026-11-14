"""#1233 — la preuve d'écriture reconnaît d'autres écritures réelles dans la zone de contrôle.

Depuis #1227, nommer la zone de contrôle est refusé, et seule une écriture *prouvée* révoque le mandat. Des
écritures réelles n'étaient pas reconnues comme telles : refusées, elles ne révoquaient pas. Ce fichier fige
celles que la preuve reconnaît désormais, et ce qu'elle continue de ne pas prendre pour une écriture.

Rien ici n'ouvre un chemin d'autorisation : chaque commande reste refusée ; seule la révocation s'étend. Une
lecture, une mention, un texte qui cite la zone restent refusés sans révoquer.

Tous les tests travaillent sous ``tmp_path`` avec un HOME isolé : la zone est celle que rend
``control_key_path().parent`` sous ce HOME, jamais celle du poste.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

import pytest

from grimoire.hosts.decisions import HookInput, Outcome, run_decision
from grimoire.hosts.decisions import authority_guard as ag
from grimoire.hosts.events import HookEvent
from grimoire.missions.mandate import MandateRequest, MandateStore, control_key_path
from grimoire.missions.schemas import ORIGIN_HUMAN

PROOF, JOURNAL, KEY = ag.CONTROL_ZONE_FILES
SPELLINGS = ("absolu", "tilde", "variable-home")


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Un HOME de test qui porte la zone ; le répertoire courant est un dossier de travail ordinaire."""
    root = tmp_path / "home"
    work = tmp_path / "work"
    work.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(root))
    monkeypatch.setenv("USERPROFILE", str(root))
    monkeypatch.chdir(work)
    for var in ("GRIMOIRE_TASK_ID", "GRIMOIRE_SESSION_ID", "CLAUDE_CODE_SESSION_ID", "CLAUDE_PROJECT_DIR"):
        monkeypatch.delenv(var, raising=False)
    zone = control_key_path().parent
    assert zone.is_relative_to(tmp_path), "la zone de test doit vivre sous tmp_path, jamais sous le vrai HOME"
    zone.mkdir(parents=True)
    return root


@pytest.fixture
def zone(home: Path) -> PurePosixPath:
    """La zone de contrôle du HOME de test, telle qu'une commande l'écrit."""
    return PurePosixPath(control_key_path().parent.as_posix())


def _spell(template: str, zone: PurePosixPath, spelling: str = "absolu") -> str:
    """*template* où ``{Z}`` devient le chemin de la zone, écrit selon *spelling*."""
    relative = control_key_path().parent.relative_to(Path.home()).as_posix()
    path = {"absolu": str(zone), "tilde": f"~/{relative}", "variable-home": f"$HOME/{relative}"}[spelling]
    return template.replace("{Z}", path)


def _project(root: Path) -> Path:
    """Un projet Grimoire qui porte un mandat actif."""
    (root / "_grimoire" / "standard").mkdir(parents=True, exist_ok=True)
    MandateStore(root).grant(
        MandateRequest.build(("accept",), ("implementation",), 5, "+1h"), origin=ORIGIN_HUMAN, via="test"
    )
    assert MandateStore(root).state().active
    return root


def _pre(root: Path, command: str):  # type: ignore[no-untyped-def]
    return run_decision(
        "grimoire.tool-policy",
        HookInput(
            event=HookEvent.PRE_TOOL_USE,
            project_root=root,
            tool_name="Bash",
            tool_input={"command": command},
            session_id="S1",
        ),
    )


def _assert_refused_and_revoked(proj: Path, command: str) -> None:
    decision = _pre(proj, command)
    assert decision.outcome is Outcome.DENY, command
    assert decision.detail["authority_vector"] == "control-zone", command
    assert decision.detail["authority_revokes"] is True, command
    assert not MandateStore(proj).state().active, command


def _assert_refused_not_revoked(proj: Path, command: str) -> None:
    decision = _pre(proj, command)
    assert decision.outcome is Outcome.DENY, command
    assert decision.detail["authority_vector"] == "control-zone", command
    assert decision.detail["authority_revokes"] is False, command
    assert MandateStore(proj).state().active, command
    assert "rien n'a été révoqué" in decision.reason, command


# ── Les formes de l'issue ────────────────────────────────────────────────────────────────────────

FORMES_DE_L_ISSUE = {
    # sed : les commandes qui écrivent un fichier
    "sed-w": "sed -n 'w {Z}/f' notes.md",
    "sed-W": "sed -n 'W {Z}/f' notes.md",
    "sed-w-derriere-une-adresse": "sed -n '1,3w {Z}/f' notes.md",
    "sed-w-derriere-un-motif": "sed -n '/x/w {Z}/f' notes.md",
    "sed-drapeau-w-de-s": "sed -n 's/a/b/w {Z}/f' notes.md",
    "sed-drapeau-gw-de-s": "sed 's/a/b/gw {Z}/f' notes.md",
    "sed-w-par-e": "sed -n -e p -e 'w {Z}/f' notes.md",
    "sed-w-dans-un-bloc": "sed -n '/x/{p;w {Z}/f\n}' notes.md",
    # awk : une sortie redirigée dans le programme
    "awk-print-redirige": "awk 'BEGIN{print 1 > \"{Z}/f\"}'",
    "awk-print-ajoute": "awk '{print $0 >> \"{Z}/f\"}' notes.md",
    "awk-printf-redirige": "awk '{printf \"%s\\n\", $1 > \"{Z}/f\"}' notes.md",
    "awk-fichier-dans-une-variable": "awk -v sortie={Z}/f '{print > sortie}' notes.md",
    "awk-fichier-affecte-dans-le-programme": "awk 'BEGIN{f=\"{Z}/f\"} {print > f}' notes.md",
    "gawk": "gawk '{print > \"{Z}/f\"}' notes.md",
    # perl et node qui ouvrent en écriture
    "perl-open-deux-arguments": "perl -e 'open(F, \">>{Z}/f\")'",
    "perl-open-trois-arguments": "perl -e 'open(my $f, \">\", \"{Z}/f\") or die'",
    "perl-open-sans-parentheses": "perl -e 'open F, \">{Z}/f\"; print F 1'",
    "perl-unlink": "perl -e 'unlink \"{Z}/f\"'",
    "node-writefilesync": "node -e \"require('fs').writeFileSync('{Z}/f', 'x')\"",
    "node-opensync": "node -e \"require('fs').openSync('{Z}/f', 'a')\"",
    "node-appendfile": "node -e \"const fs = require('fs'); fs.appendFileSync('{Z}/f', 'x')\"",
    "node-variable": "node -e \"const p = '{Z}/f'; require('fs').writeFileSync(p, 'x')\"",
    "node-eval": "node --eval \"require('fs').rmSync('{Z}/f')\"",
    # python : fileinput en place, journal sur fichier, base sqlite
    "python-fileinput": "python3 -c \"import fileinput; fileinput.input('{Z}/f', inplace=True)\"",
    "python-fileinput-files": "python3 -c \"import fileinput\nfor l in fileinput.input(files=['{Z}/f'], inplace=1): print(l)\"",
    "python-fileinput-classe": "python3 -c \"import fileinput; fileinput.FileInput('{Z}/f', inplace=True)\"",
    "python-logging": "python3 -c \"import logging; logging.FileHandler('{Z}/f')\"",
    "python-logging-importe": "python3 -c \"from logging import FileHandler; FileHandler('{Z}/f')\"",
    "python-logging-rotatif": "python3 -c \"import logging.handlers as h; h.RotatingFileHandler('{Z}/f')\"",
    "python-logging-basicconfig": "python3 -c \"import logging; logging.basicConfig(filename='{Z}/f')\"",
    "python-sqlite": "python3 -c \"import sqlite3; sqlite3.connect('{Z}/f')\"",
    "python-sqlite-database": "python3 -c \"import sqlite3; sqlite3.connect(database='{Z}/f').execute('create table t(x)')\"",
    # git : la destination d'un clone, d'un dépôt créé, d'un arbre de travail
    "git-clone": "git clone /src {Z}/x",
    "git-clone-options": "git clone --depth 1 -b main https://example.com/r.git {Z}/x",
    "git-init": "git init {Z}/x",
    "git-init-q": "git init -q --initial-branch main {Z}",
    "git-worktree-add": "git worktree add {Z}/x",
    "git-worktree-add-branche": "git worktree add -b essai {Z}/x origin/main",
    "git-c-worktree-add": "git -C /depot worktree add {Z}/x",
}
ENVELOPPES = {
    "rtk": "rtk ",
    "rtk-proxy": "rtk proxy ",
    "env": "env ",
    "env-variable": "env LC_ALL=C ",
    "sudo": "sudo ",
    "sudo-u": "sudo -u root ",
    "affectation": "VAR=x ",
    "timeout": "timeout 5 ",
    "empilees": "sudo env VAR=x timeout 5 ",
}


@pytest.mark.parametrize("spelling", SPELLINGS)
@pytest.mark.parametrize("template", FORMES_DE_L_ISSUE.values(), ids=FORMES_DE_L_ISSUE.keys())
def test_une_forme_de_l_issue_est_refusee_et_revoque(tmp_path: Path, zone: PurePosixPath, template: str, spelling: str) -> None:
    _assert_refused_and_revoked(_project(tmp_path / "proj"), _spell(template, zone, spelling))


@pytest.mark.parametrize("enveloppe", ENVELOPPES.values(), ids=ENVELOPPES.keys())
@pytest.mark.parametrize("template", FORMES_DE_L_ISSUE.values(), ids=FORMES_DE_L_ISSUE.keys())
def test_une_forme_de_l_issue_sous_une_enveloppe_est_refusee_et_revoque(
    tmp_path: Path, zone: PurePosixPath, template: str, enveloppe: str
) -> None:
    _assert_refused_and_revoked(_project(tmp_path / "proj"), enveloppe + _spell(template, zone))


def test_un_chemin_relatif_qui_aboutit_dans_la_zone_est_prouve(tmp_path: Path, zone: PurePosixPath) -> None:
    commands = (
        f"cd {zone} && sed -n 'w f' /etc/hostname",
        f"cd {zone} && awk 'BEGIN{{print 1 > \"f\"}}'",
        f"cd {zone} && perl -e 'open(F, \">f\")'",
        f"cd {zone} && node -e \"require('fs').writeFileSync('f', 'x')\"",
        f"cd {zone} && python3 -c \"import sqlite3; sqlite3.connect('f')\"",
        f"cd {zone} && git init",
        f"cd {zone.parent} && git clone /src {zone.name}/x",
        f"cd {zone.parent} && git worktree add ./{zone.name}/x",
        f"git -C {zone} init",
    )
    for index, command in enumerate(commands):
        _assert_refused_and_revoked(_project(tmp_path / f"proj{index}"), command)


def test_une_destination_atteinte_par_un_lien_est_prouvee(tmp_path: Path, zone: PurePosixPath) -> None:
    """Le lien est résolu quand il est un mot de la commande (pas quand il est cité dans un programme)."""
    (tmp_path / "alias").symlink_to(control_key_path().parent)
    alias = (tmp_path / "alias").as_posix()
    commands = (f"git clone /src {alias}/x", f"git init {alias}", f"tar -xf a.tar -C {alias}", f"unzip a.zip -d {alias}")
    for index, command in enumerate(commands):
        _assert_refused_and_revoked(_project(tmp_path / f"proj{index}"), command)


# ── Les formes de la même classe, trouvées en cherchant ──────────────────────────────────────────

AUTRES_ECRITURES = {
    # autres langages à option d'exécution en ligne
    "ruby-file-write": "ruby -e 'File.write(\"{Z}/f\", \"x\")'",
    "ruby-file-open": "ruby -e 'File.open(\"{Z}/f\", \"w\") { |f| f.puts 1 }'",
    "ruby-fileutils": "ruby -rfileutils -e 'FileUtils.rm_rf(\"{Z}\")'",
    "php-file-put-contents": "php -r 'file_put_contents(\"{Z}/f\", \"x\");'",
    "php-fopen": "php -r 'fopen(\"{Z}/f\", \"a\");'",
    "lua-io-open": "lua -e 'io.open(\"{Z}/f\", \"w\")'",
    "deno-eval": "deno eval \"Deno.writeTextFileSync('{Z}/f', 'x')\"",
    "bun-e": "bun -e \"require('fs').writeFileSync('{Z}/f', 'x')\"",
    "node-p": "node -p \"require('fs').mkdirSync('{Z}/d')\"",
    "node-renomme": "node -e \"require('fs').renameSync('forge', '{Z}/f')\"",
    "node-copie": "node -e \"require('fs').copyFileSync('forge', '{Z}/f')\"",
    "node-lance-une-commande": "node -e \"require('child_process').execSync('cp forge {Z}/f')\"",
    "perl-en-place": "perl -pi -e 's/a/b/' {Z}/f",
    "perl-en-place-suffixe": "perl -i.bak -pe 's/a/b/' {Z}/f",
    "perl-lance-une-commande": "perl -e 'system(\"cp forge {Z}/f\")'",
    "perl-renomme": "perl -e 'rename \"forge\", \"{Z}/f\"'",
    "ruby-en-place": "ruby -i -pe '$_.upcase!' {Z}/f",
    "awk-lance-une-commande": "awk 'BEGIN{system(\"cp forge {Z}/f\")}'",
    "awk-en-place": "gawk -i inplace '{print}' {Z}/f",
    # python hors de la liste d'origine
    "python-os-open": "python3 -c \"import os; os.open('{Z}/f', os.O_WRONLY | os.O_CREAT)\"",
    "python-zipfile": "python3 -c \"import zipfile; zipfile.ZipFile('{Z}/a.zip', 'w')\"",
    "python-tarfile-extrait": "python3 -c \"import tarfile; tarfile.open('a.tar').extractall('{Z}')\"",
    "python-shutil-unpack": "python3 -c \"import shutil; shutil.unpack_archive('a.zip', '{Z}')\"",
    "python-shelve": "python3 -c \"import shelve; shelve.open('{Z}/f')\"",
    "python-os-symlink": "python3 -c \"import os; os.symlink('forge', '{Z}/f')\"",
    # archives : extraction vers un dossier, archive créée dans la zone, compression en place
    "tar-extrait": "tar -xf a.tar -C {Z}",
    "tar-extrait-agglutine": "tar xzf a.tgz -C {Z}",
    "tar-extrait-directory": "tar --extract --file a.tar --directory={Z}",
    "tar-cree-dans-la-zone": "tar -cf {Z}/a.tar src",
    "unzip-d": "unzip -o a.zip -d {Z}",
    "zip-dans-la-zone": "zip -r {Z}/a.zip src",
    "sept-zip-extrait": "7z x a.7z -o{Z}",
    "gzip-en-place": "gzip {Z}/f",
    "gunzip-en-place": "gunzip {Z}/f.gz",
    "xz-en-place": "xz -9 {Z}/f",
    "cpio-vers-la-zone": "cpio -pdm {Z}",
    # copie et synchronisation
    "scp": "scp hote:forge {Z}/f",
    "git-archive-sortie": "git archive -o {Z}/a.tar HEAD",
    "git-format-patch": "git format-patch -o {Z} HEAD~1",
    "git-bundle": "git bundle create {Z}/b HEAD",
    "git-diff-sortie": "git diff --output={Z}/f",
    "gh-telecharge": "gh run download 12 -D {Z}",
    # fichiers spéciaux, fichiers temporaires, découpage
    "mkfifo": "mkfifo {Z}/p",
    "mktemp-p": "mktemp -p {Z}",
    "mktemp-gabarit": "mktemp {Z}/tmp.XXXXXX",
    "split": "split -l 10 notes.md {Z}/part-",
    "fallocate": "fallocate -l 1K {Z}/f",
    # éditeurs en mode script
    "ed": "ed -s {Z}/f <<'EOF'\n1d\nw\nq\nEOF",
    "ex": "ex -s -c '1d' -c 'wq' {Z}/f",
    "vim": "vim -es -c ':%s/a/b/' -c ':wq' {Z}/f",
    "vim-plus": "vim -es '+%s/a/b/' '+x' {Z}/f",
    "vim-ecrit-ailleurs": "vim -es -c 'w {Z}/f' -c 'q' notes.md",
    # outils qui écrivent le fichier donné à une option
    "patch": "patch {Z}/f correctif.diff",
    "patch-o": "patch -o {Z}/f notes.md correctif.diff",
    "openssl-out": "openssl rand -out {Z}/f 32",
    "ssh-keygen": "ssh-keygen -q -N '' -f {Z}/cle",
    "sqlite3-qui-ecrit": "sqlite3 {Z}/f 'create table t(x)'",
    "uniq-sortie": "uniq notes.md {Z}/f",
    "shuf-o": "shuf -o {Z}/f notes.md",
    # dépôt git ou dossier de travail forcé dans la zone
    "git-c-clean": "git -C {Z} clean -fdx",
    "git-c-checkout-chemin": "git -C {Z} checkout -- .",
    "git-c-clone-sans-destination": "git -C {Z} clone /src/depot.git",
    "git-git-dir": "git --git-dir={Z}/.git add notes.md",
    "git-git-dir-commit": "git --git-dir {Z}/.git commit -m x",
    "git-work-tree-variable": "GIT_WORK_TREE={Z} git checkout -- .",
    "git-git-dir-exporte": "export GIT_DIR={Z}/.git; git commit -m x",
    "git-rm": "git rm -r {Z}/f",
    "git-worktree-remove": "git worktree remove {Z}/x",
    "git-work-tree": "git --work-tree {Z} checkout -- .",
    "git-dir-par-variable": "GIT_DIR={Z}/.git git commit -m x",
    "git-config-fichier": "git config -f {Z}/f user.name x",
    "git-mv": "git mv notes.md {Z}/f",
    # redirection ou écriture dans une sous-commande
    "find-exec-sh": "find . -name x -exec sh -c 'echo x > {Z}/f' \\;",
    "find-exec-cp": "find . -name forge -exec cp {} {Z}/f \\;",
    "su-c": "su -c 'echo x > {Z}/f'",
    "watch": "watch -n 1 'date > {Z}/f'",
    "script-c": "script -q -c 'cp forge {Z}/f' /dev/null",
    "git-rebase-exec": "git rebase -x 'touch {Z}/f' HEAD~1",
}


@pytest.mark.parametrize("template", AUTRES_ECRITURES.values(), ids=AUTRES_ECRITURES.keys())
def test_une_autre_ecriture_reelle_est_refusee_et_revoque(tmp_path: Path, zone: PurePosixPath, template: str) -> None:
    _assert_refused_and_revoked(_project(tmp_path / "proj"), _spell(template, zone))


@pytest.mark.parametrize("enveloppe", ["rtk ", "rtk proxy ", "env ", "sudo "])
@pytest.mark.parametrize(
    "template", ["tee {Z}/f", "cp forge {Z}/f", "tar -xf a.tar -C {Z}", "ruby -e 'File.write(\"{Z}/f\", 1)'"]
)
def test_une_ecriture_sous_le_proxy_de_terminal_est_prouvee(tmp_path: Path, zone: PurePosixPath, template: str, enveloppe: str) -> None:
    _assert_refused_and_revoked(_project(tmp_path / "proj"), enveloppe + _spell(template, zone))


# ── Ce qui reste refusé sans révoquer : lectures, mentions, écritures ailleurs ───────────────────

LECTURES = {
    # les mêmes programmes, quand ils ne font que lire la zone
    "sed-p": f"sed -n '/w/p' {{Z}}/{PROOF}",
    "sed-s-qui-cite-w": f"sed -n 's/w x/y/p' {{Z}}/{PROOF}",
    "sed-w-ailleurs": f"sed -n 'w ailleurs.txt' {{Z}}/{PROOF}",
    "awk-print": f"awk '{{print $1}}' {{Z}}/{JOURNAL}",
    "awk-comparaison": f"awk '$1 > \"x\"' {{Z}}/{JOURNAL}",
    "awk-ecrit-ailleurs": f"awk '{{print > \"ailleurs.txt\"}}' {{Z}}/{JOURNAL}",
    "perl-n": f"perl -ne 'print if /x/' {{Z}}/{JOURNAL}",
    "perl-p-sans-i": f"perl -pe 's/a/b/' {{Z}}/{JOURNAL}",
    "perl-open-lecture": f"perl -e 'open(F, \"<{{Z}}/{PROOF}\"); print <F>'",
    "perl-open-lecture-trois-arguments": f"perl -e 'open(my $f, \"<\", \"{{Z}}/{PROOF}\"); print <$f>'",
    "node-lit": f"node -e \"console.log(require('fs').readFileSync('{{Z}}/{PROOF}', 'utf8'))\"",
    "node-ouvre-en-lecture": f"node -e \"require('fs').openSync('{{Z}}/{PROOF}', 'r')\"",
    "node-copie-hors-de-la-zone": f"node -e \"require('fs').copyFileSync('{{Z}}/{PROOF}', 'ailleurs.json')\"",
    "ruby-lit": f"ruby -e 'puts File.read(\"{{Z}}/{PROOF}\")'",
    "ruby-ouvre-en-lecture": f"ruby -e 'File.open(\"{{Z}}/{PROOF}\") {{ |f| puts f.read }}'",
    "php-lit": f"php -r 'echo file_get_contents(\"{{Z}}/{PROOF}\");'",
    "python-fileinput-sans-en-place": f"python3 -c \"import fileinput\nfor l in fileinput.input('{{Z}}/{JOURNAL}'): print(l)\"",
    "python-fileinput-en-place-faux": f"python3 -c \"import fileinput; fileinput.input('{{Z}}/{JOURNAL}', inplace=False)\"",
    "python-sqlite-lecture-seule": f"python3 -c \"import sqlite3; sqlite3.connect('file:{{Z}}/{PROOF}?mode=ro', uri=True)\"",
    "python-zipfile-lecture": "python3 -c \"import zipfile; zipfile.ZipFile('{Z}/a.zip').namelist()\"",
    "python-os-open-lecture": f"python3 -c \"import os; os.open('{{Z}}/{PROOF}', os.O_RDONLY)\"",
    "python-archive-lue-dans-la-zone": "python3 -c \"import tarfile; tarfile.open('{Z}/a.tar').extractall('ailleurs')\"",
    "git-c-status": "git -C {Z} status",
    "git-c-log": "git -C {Z} log --oneline -3",
    "git-clone-depuis-la-zone": "git clone {Z}/depot ailleurs",
    "git-commit-f": f"git commit -F {{Z}}/{PROOF}",
    "git-diff-entre-fichiers": f"git diff --no-index {{Z}}/{PROOF} ailleurs.json",
    "git-config-lecture": "git config -f {Z}/f --get user.name",
    "git-dir-par-variable-lecture": "GIT_DIR={Z}/.git git log -1",
    "tar-cree-depuis-la-zone": "tar -cf ailleurs.tar -C {Z} .",
    "tar-liste": "tar -tf {Z}/a.tar",
    "tar-extrait-ailleurs": "tar -xf {Z}/a.tar -C ailleurs",
    "unzip-liste": "unzip -l {Z}/a.zip",
    "unzip-ailleurs": "unzip {Z}/a.zip -d ailleurs",
    "gzip-vers-la-sortie": f"gzip -c {{Z}}/{JOURNAL}",
    "zcat": "zcat {Z}/f.gz",
    "scp-hors-de-la-zone": f"scp {{Z}}/{PROOF} hote:ailleurs",
    "vim-sans-ecriture": f"vim -es -c '%print' -c 'q!' {{Z}}/{PROOF}",
    "ed-sans-ecriture": f"ed -s {{Z}}/{PROOF} <<'EOF'\n1p\nq\nEOF",
    "sqlite3-qui-lit": "sqlite3 {Z}/f 'select 1'",
    "uniq-lit": f"uniq -c {{Z}}/{JOURNAL}",
    "patch-lu-dans-la-zone": "patch notes.md {Z}/correctif.diff",
    "openssl-lit": f"openssl dgst -sha256 {{Z}}/{PROOF}",
    "mktemp-ailleurs": "mktemp -p ailleurs && ls {Z}",
    # écritures que la ligne de commande ne prouve pas : refusées, sans révoquer
    "git-c-commit-depot-inconnu": "git -C {Z} commit -m x",
    "git-work-tree-commit": "git --work-tree {Z} commit -m x",
    "git-rm-cached": "git rm --cached {Z}/f",
    "git-mv-a-blanc": "git mv -n {Z}/a ailleurs",
    "git-clean-a-blanc": "git -C {Z} clean -n",
    "git-checkout-branche": "git -C {Z} checkout main",
    "git-config-liste": "git config -f {Z}/f --list",
    "gh-repo-clone-option-inconnue": "gh repo clone a/b {Z}/x --description x",
    "perl-pi-sans-boucle": f"perl -i -e 'print 1' {{Z}}/{JOURNAL}",
    "sed-sandbox": "sed --sandbox -n 'w {Z}/f' notes.md",
    "node-mode-calcule": "node -e \"const m = process.argv[1]; require('fs').openSync('{Z}/f', m)\"",
    "awk-vers-un-champ": "awk '{print > $1}' {Z}/liste",
    "python-journal-differe": "python3 -c \"import logging; logging.FileHandler('{Z}/f', delay=True)\"",
    "python-dbm-lecture": "python3 -c \"import dbm; dbm.open('{Z}/f')\"",
    "tar-vers-la-sortie": "tar -xOf a.tar -C {Z}",
    "unzip-vers-la-sortie": "unzip -p a.zip -d {Z}",
    "mktemp-a-blanc": "mktemp -u -p {Z}",
    "ssh-keygen-empreinte": "ssh-keygen -l -f {Z}/cle",
    "patch-a-blanc": "patch --dry-run {Z}/f correctif.diff",
}
MENTIONS = {
    "sed-qui-cite-w-et-la-zone-dans-un-remplacement": "sed -i 's#ici#w {Z}/f#' notes.md",
    "sed-a-qui-cite-la-zone": "sed '1a w {Z}/f' notes.md",
    "awk-qui-affiche-le-chemin": "awk 'BEGIN{print \"{Z}/f\"}'",
    "awk-qui-ecrit-ailleurs-le-chemin": "awk 'BEGIN{print \"{Z}/f\" > \"ailleurs.txt\"}'",
    "perl-qui-affiche-le-chemin": "perl -e 'print \"{Z}/f\\n\"'",
    "perl-qui-ecrit-ailleurs-le-chemin": "perl -e 'open(F, \">ailleurs.txt\"); print F \"{Z}/f\"'",
    "node-qui-affiche-le-chemin": "node -e \"console.log('{Z}/f')\"",
    "node-qui-ecrit-ailleurs-le-chemin": "node -e \"require('fs').writeFileSync('ailleurs.txt', '{Z}/f')\"",
    "ruby-qui-affiche-le-chemin": "ruby -e 'puts \"{Z}/f\"'",
    "ruby-qui-ecrit-ailleurs-le-chemin": "ruby -e 'File.write(\"ailleurs.txt\", \"{Z}/f\")'",
    "php-qui-affiche-le-chemin": "php -r 'echo \"{Z}/f\";'",
    "python-journal-ailleurs-qui-cite": "python3 -c \"import logging; logging.FileHandler('ailleurs.log'); print('{Z}/f')\"",
    "python-sqlite-ailleurs-qui-cite": "python3 -c \"import sqlite3; sqlite3.connect('ailleurs.db').execute('select ?', ('{Z}/f',))\"",
    "git-clone-dont-la-branche-cite": "git clone -b {Z} /src ailleurs",
    "git-log-qui-cherche-le-chemin": "git log --grep '{Z}' --oneline",
    "git-init-ailleurs-en-citant": "git init ailleurs # {Z}",
    "tar-qui-exclut-le-chemin": "tar -xf a.tar -C ailleurs --exclude {Z}",
    "heredoc-de-documentation": "cat > notes.md <<'EOF'\nsed -n 'w {Z}/f' ; git clone /src {Z}/x\nEOF",
    "message-de-commit-par-heredoc": "git commit -q -F - <<'EOF'\nfix: git init {Z}/x n'est plus possible\nEOF",
    "echo-d-une-commande": "echo \"git clone /src {Z}/x\"",
    "corps-de-pr-par-substitution-qui-cite-des-ecritures": (
        "gh pr create --title t --body \"$(cat <<'EOF'\ngit clone /src {Z}/x\nawk '{print > \"{Z}/f\"}'\n"
        "perl -e 'unlink \"{Z}/f\"'\nEOF\n)\""
    ),
    "python-qui-affiche-une-recette": "python3 -c \"print(\\\"logging.FileHandler('{Z}/f')\\\")\"",
    "node-qui-affiche-une-recette": "node -e \"console.log(\\\"fs.writeFileSync('{Z}/f')\\\")\"",
    "vim-qui-cherche-le-chemin": "vim -es -c '/{Z}' -c 'q' notes.md",
}


@pytest.mark.parametrize("spelling", SPELLINGS)
@pytest.mark.parametrize("template", LECTURES.values(), ids=LECTURES.keys())
def test_une_lecture_par_ces_programmes_est_refusee_sans_revoquer(
    tmp_path: Path, zone: PurePosixPath, template: str, spelling: str
) -> None:
    _assert_refused_not_revoked(_project(tmp_path / "proj"), _spell(template, zone, spelling))


@pytest.mark.parametrize("template", MENTIONS.values(), ids=MENTIONS.keys())
def test_une_mention_par_ces_programmes_est_refusee_sans_revoquer(tmp_path: Path, zone: PurePosixPath, template: str) -> None:
    _assert_refused_not_revoked(_project(tmp_path / "proj"), _spell(template, zone))


@pytest.mark.parametrize(
    "template",
    [
        'git commit -m "fix(hosts): git init {Z}/x et sed w {Z}/f sont reconnus"',
        "git tag -a v1 -m 'git clone /src {Z}/x'",
        'gh pr create --title t --body "perl -e open {Z}/f ; node writeFileSync {Z}/f"',
        "gh issue comment 12 --body 'tar -xf a.tar -C {Z}'",
    ],
)
def test_la_simple_mention_dans_une_option_de_texte_passe_toujours(tmp_path: Path, zone: PurePosixPath, template: str) -> None:
    """La preuve d'écriture ne sert jamais à refuser ce que la liste blanche de la simple mention laisse passer."""
    proj = _project(tmp_path / "proj")
    decision = _pre(proj, _spell(template, zone))
    assert decision.detail.get("authority_vector") != "control-zone", template
    assert MandateStore(proj).state().active


@pytest.mark.parametrize(
    "command",
    [
        "sed -n 'w sortie.txt' notes.md",
        "awk '{print > \"sortie.txt\"}' notes.md",
        "perl -e 'open(F, \">sortie.txt\")'",
        "node -e \"require('fs').writeFileSync('sortie.txt', 'x')\"",
        "python3 -c \"import sqlite3; sqlite3.connect('base.db')\"",
        "git clone /src copie",
        "git init depot",
        "git worktree add ../arbre",
        "tar -xf a.tar -C sortie",
    ],
)
def test_une_ecriture_hors_de_la_zone_n_est_pas_concernee(tmp_path: Path, command: str) -> None:
    proj = _project(tmp_path / "proj")
    decision = _pre(proj, command)
    assert "authority_vector" not in decision.detail, command
    assert MandateStore(proj).state().active
