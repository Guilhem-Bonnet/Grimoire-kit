"""#1233 — la preuve d'écriture seule, sur une cible factice (``/vault``) : jamais la vraie zone de contrôle.

Complète ``test_authority_guard_real_writes.py`` (bout en bout, sous un HOME isolé) par une batterie adverse rapide :
les formes que :mod:`grimoire.hosts.decisions.write_proof` reconnaît comme écritures, et les lectures, mentions et
écritures ailleurs qu'elle ne doit pas prendre pour une écriture sur la cible (répertoire courant compris).
"""

from __future__ import annotations

import pytest

from grimoire.hosts.decisions import write_proof


class _Vault:
    """Cible factice : tout mot qui porte ``/vault``, ou un chemin relatif après un ``cd`` vers elle."""

    def names(self, word: str, cd: tuple[str, ...]) -> bool:
        return "/vault" in word or (not word.startswith("/") and any("/vault" in c for c in cd))

    def carries(self, word: str, cd: tuple[str, ...]) -> bool:
        return word == "/srv"


WRITES = [
    "sed -n \"w /vault/f\" a",
    "sed -ne 'w /vault/f' a",
    "sed --expression='w /vault/f' a",
    "sed -n -e '$w /vault/f' a",
    "sed '1!G;h;$!d;w /vault/f' a",
    "sed -n '/a/,/b/w /vault/f' a",
    "sed -s -n 'W /vault/f' a",
    "sed 'e touch /vault/f' a",
    "sed -i -e 'w /vault/f' a",
    "awk '{print > \"/vault/\" $1}' a",
    "awk 'BEGIN{system(\"touch /vault/f\")}'",
    "awk '{print | \"tee /vault/f\"}' a",
    "awk 'BEGIN { while ((\"cp a /vault/f\" | getline line) > 0) {} }'",
    "awk -v OFS=, -v out=/vault/f '{print $1 > out}' a",
    "awk '{print > out}' out=/vault/f a",
    "perl -e 'open my $fh, \">\", \"/vault/f\"'",
    "perl -e 'open(my $fh, \">>\", $ENV{HOME} . \"/vault\")'",
    "perl -MFile::Copy -e 'copy(\"a\", \"/vault/f\")'",
    "perl -e 'system \"touch\", \"/vault/f\"'",
    "perl -e '`touch /vault/f`'",
    "perl -e 'qx(touch /vault/f)'",
    "perl -e '$f=\"/vault/f\"; open(F, \">$f\")'",
    "perl -e 'my $f = \"/vault/f\"; unlink $f'",
    "perl -e 'mkdir \"/vault/d\"'",
    "perl -e 'sysopen(F, \"/vault/f\", O_WRONLY|O_CREAT)'",
    "node -e \"require('fs').writeFileSync(require('path').join('/vault', 'f'), 'x')\"",
    "node -e \"import('fs').then(fs => fs.writeFileSync('/vault/f', ''))\"",
    "node -e \"require('child_process').execSync('touch /vault/f')\"",
    "node --eval=\"require('fs').rmSync('/vault', {recursive: true})\"",
    "node -pe \"require('fs').writeFileSync('/vault/f','')\"",
    "node -e \"const {writeFileSync} = require('fs'); writeFileSync('/vault/f', '')\"",
    "node -e 'fs.writeFileSync(`${\"/vault\"}/f`, \"\")'",
    "node -e \"require('child_process').spawnSync('cp', ['a', '/vault/f'])\"",
    "ruby -e 'File.open(\"/vault/f\", \"w\")'",
    "ruby -rfileutils -e 'FileUtils.touch \"/vault/f\"'",
    "ruby -e '%x(touch /vault/f)'",
    "ruby -e 'system(\"touch /vault/f\")'",
    "ruby -e 'IO.write(\"/vault/f\", \"x\")'",
    "php -r 'file_put_contents(\"/vault/f\", \"x\");'",
    "php -r '$f = fopen(\"/vault/f\", \"w\");'",
    "lua -e 'io.open(\"/vault/f\", \"w\"):write(\"x\")'",
    "lua -e 'os.execute(\"touch /vault/f\")'",
    "python3 -c \"import fileinput; [print(l) for l in fileinput.input(files=('/vault/f',), inplace=True)]\"",
    "python3 -c \"import sqlite3 as s; s.connect('/vault/db')\"",
    "python3 -c \"from sqlite3 import connect; connect('/vault/db')\"",
    "git clone -- /src /vault/x",
    "git clone /src /vault/x/",
    "git init --bare /vault/r.git",
    "git worktree add --detach /vault/x HEAD",
    "GIT_DIR=/vault/.git git add .",
    "env GIT_DIR=/vault/.git git commit -m x",
    "git --git-dir /vault/.git fetch",
    "tar -C /vault -xf a.tar",
    "tar xvf a.tar --directory /vault",
    "unzip -qo a.zip -d /vault",
    "unzip -d/vault a.zip",
    "7z x a.7z -o/vault",
    "xz -d /vault/f.xz",
    "zstd -o /vault/f a",
    "rtk proxy sed -n 'w /vault/f' a",
    "rtk git clone /src /vault/x",
    "sudo -u root perl -e 'unlink \"/vault/f\"'",
    "bash -c \"awk 'BEGIN{print 1 > \\\"/vault/f\\\"}'\"",
    "echo x | xargs -I{} sed -n 'w /vault/{}' a",
]

NOT_WRITES = [
    "git commit -m \"sed -n 'w /vault/f'\"",
    "echo \"perl -e 'open(F, \\\">/vault/f\\\")'\"",
    "cat > notes.md <<'EOF'\nawk '{print > \"/vault/f\"}'\nEOF",
    "grep -rn \"writeFileSync('/vault/f'\" src",
    "sed -n '/vault/p' notes.md",
    "sed 's/x/\\/vault\\/f/w out.txt' a",
    "sed -n 's/a/b/;w /tmp/x' /vault/f",
    "sed -n 's#w /vault/f#x#p' a",
    "sed 'a\\\nw /vault/f' a",
    "sed 'y/abc/xyz/;w out' /vault/f",
    "awk '$1 > \"/vault/f\"' a",
    "awk '{ if ($1 > \"/vault\") print }' a",
    "awk '{print ($1 > \"/vault/x\")}' a",
    "awk '{print \"/vault/f\" > \"out\"}' a",
    "awk '/vault/ {print > \"out\"}' a",
    "perl -ne 'print if $_ gt \"/vault\"' a",
    "perl -e 'my %h = (path => \"/vault/f\"); print $h{path}'",
    "perl -e 'open(F, \"/vault/f\")'",
    "perl -e 'open(F, \"<\", \"/vault/f\")'",
    "perl -ne 'print if m{/vault/}' f",
    "perl -pe 's{/vault}{/x}g' a",
    "node -e \"fs.existsSync('/vault/f')\"",
    "node -e \"const s = '/vault/f'; console.log(s)\"",
    "node -e \"console.log(require('fs').readFileSync('/vault/f','utf8'))\"",
    "node -e \"fs.copyFileSync('/vault/f', 'out')\"",
    "node -e \"'/vault/f'.replace('a', 'b')\"",
    "ruby -e 'puts File.exist?(\"/vault/f\")'",
    "ruby -e 'File.open(\"/vault/f\", \"r\")'",
    "php -r 'echo file_get_contents(\"/vault/f\");'",
    "lua -e 'io.open(\"/vault/f\")'",
    "lua -e 'print(\"/vault/f\")'",
    "python3 -c \"import logging; logging.getLogger('/vault/f')\"",
    "python3 -c \"import sqlite3; c = sqlite3.connect('a.db'); c.execute('select ?', ('/vault/f',))\"",
    "git clone /vault/repo out",
    "git log --output=out.txt -- /vault",
    "git -C /vault status",
    "git -C /vault diff",
    "git -C /vault log -p",
    "git -C /vault commit -m x",
    "git commit -m x /vault/f",
    "tar -tzf /vault/a.tgz",
    "tar -czf out.tgz /vault",
    "zip -r out.zip /vault",
    "unzip -l /vault/a.zip",
    "scp /vault/f host:/tmp",
    "vim -es -c 'g/vault/p' -c 'q!' notes.md",
    "sqlite3 out.db '.output /tmp/x' 'select * from t where p=\"/vault\"'",
    "sqlite3 /vault/db .tables",
    "ssh-keygen -l -f /vault/k",
    "ed -s notes.md <<< '/vault/p'",
    "gzip -dc /vault/f.gz",
    "bzip2 -t /vault/f",
    "mktemp -p /tmp && ls /vault",
    "uniq /vault/f",
    "patch -i /vault/p.diff a",
    "openssl dgst /vault/f",
    "gh run download 1 -D out && ls /vault",
    "watch -n 1 'cat /vault/f'",
    "su -c 'cat /vault/f'",
    "script -q -c 'cat /vault/f' /dev/null",
    "rtk read /vault/f",
    "rtk grep vault /vault",
    "git -C /vault worktree list",
    "git config --global user.name x",
    "git config -f /vault/f user.name",
    "sed -n '1w /dev/stdout' /vault/f",
    "awk -F'>' '{print $1}' /vault/f",
    "awk 'BEGIN{printf \"a > b\\n\"}' /vault/x",
    "awk '{print $1 > \"/dev/stderr\"}' /vault/f",
    "perl -e 'open(STDOUT, \">\", \"/dev/null\"); print \"/vault\"'",
    "perl -pe 's/a/b/' /vault/f > out",
    "node -e \"process.stdout.write('/vault/f')\"",
    "ruby -e '$stdout.write(\"/vault/f\")'",
    "ruby -e 'File.write(\"out\", File.read(\"/vault/f\"))'",
    "python3 -c \"import tarfile; tarfile.open('/vault/a.tar').extractall()\"",
    "tar -xf /vault/a.tar",
    "cd /vault && tar -tf a.tar",
    "cd /vault && git status",
    "cd /vault && unzip -l a.zip",
    "cd /vault && python3 -c \"import sqlite3; sqlite3.connect(':memory:')\"",
    "cd /vault && gzip -l f.gz",
    "cd /vault && mktemp",
    "cd /vault && zip -sf a.zip",
    "cd /vault && cpio -it < a.cpio",
    "cd /vault && 7z l a.7z",
    "cd /vault && git checkout main",
    "cd /vault && git restore --staged f",
    "cd /vault && git clean -fdn",
    "cd /vault && git stash list",
    "cd /vault && ed -s f <<< 'p'",
    "cd /vault && vim -es -c 'q' f",
    "cd /vault && uniq f",
]
WRITES += [
    "cd /vault && git init",
    "cd /vault && git restore f",
    "cd /vault && tar -xf ../a.tar",
    "cd /vault && unzip ../a.zip",
    "cd /vault && split -l 10 big",
    "cd /vault && git clone /src",
    "cd /vault && sed -n 'w f' a",
    "cd /vault && python3 -c \"import sqlite3; sqlite3.connect('db')\"",
]


@pytest.mark.parametrize("command", WRITES)
def test_ecrit(command: str) -> None:
    assert write_proof.proves_write(command, _Vault()) is True


@pytest.mark.parametrize("command", NOT_WRITES)
def test_n_ecrit_pas(command: str) -> None:
    assert write_proof.proves_write(command, _Vault()) is False
