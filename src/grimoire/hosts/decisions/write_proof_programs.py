"""Preuve d'écriture des programmes dont la cible écrite se lit dans une grammaire propre (#1233).

Partie de :mod:`.write_proof` : même contrat, aucun refus ni aucune autorisation décidés ici. Chaque fonction ne
rend vrai que pour une forme dont l'écriture sur la cible est prouvée par sa ligne de commande ; une option
inconnue là où elle changerait le sens, une lecture, une liste, une sortie standard rendent faux.

- ``git`` : destination de ``clone``, dossier de ``init``, chemin de ``worktree add``/``move``/``remove`` (relatifs
  aux ``cd`` lus et aux ``-C``) ; un dossier git forcé (``--git-dir``, ``GIT_DIR``) sous une sous-commande qui
  écrit le dépôt, un arbre de travail forcé (``--work-tree``, ``GIT_WORK_TREE``) sous une sous-commande qui écrit
  l'arbre ; ``config`` qui règle une valeur dans un fichier de la cible ; ``rm``/``mv``/``clean``/``checkout --``/
  ``restore`` sur un chemin de la cible ; ``archive -o``, ``format-patch -o``, ``bundle create``, ``--output=`` ;
  les commandes que lancent ``rebase -x``, ``submodule foreach``, ``bisect run`` ;
- archives : extraction par ``tar``/``unzip``/``7z``/``cpio`` vers la cible (``-C``, ``-d``, ``-o``, répertoire
  courant après un ``cd``), archive créée dans la cible (``tar -c``, ``zip``, ``7z a``) ; compresseurs en place
  (``gzip``, ``xz``, ``zstd``…) sauf vers la sortie standard ;
- ``scp`` vers un chemin local de la cible, ``mktemp``, ``split``, ``csplit``, ``uniq`` (sortie), ``patch``,
  ``openssl -out``, ``ssh-keygen -f``, ``sqlite3`` qui écrit sa base ou un fichier de sortie, ``script``, les
  éditeurs en mode script (``ed``, ``ex``, ``vim``…) qui écrivent, ``gh`` qui télécharge ou clone vers la cible ;
- les commandes lancées par ``su -c``, ``runuser -c``, ``watch``, ``script -c``.
"""

from __future__ import annotations

import re
from itertools import pairwise
from typing import TYPE_CHECKING

from grimoire.hosts.decisions.read_only_arguments import _writes_to_file

if TYPE_CHECKING:
    from collections.abc import Callable

    from grimoire.hosts.decisions.write_proof_languages import Host

__all__ = ["program_writes"]


def _scan(
    args: list[str], valued: frozenset[str], *, attached: str = ""
) -> tuple[list[str], list[tuple[str, str]], list[str]]:
    """``(opérandes, [(option, valeur)…], drapeaux)`` de *args*.

    *valued* : options à valeur (``-o v``, ``-ov`` si la lettre est dans *attached*, ``--opt=v``, ``--opt v``).
    Les drapeaux courts groupés (``-xzf``) sont rendus lettre à lettre ; ``--`` termine les options.
    """
    operands: list[str] = []
    values: list[tuple[str, str]] = []
    flags: list[str] = []
    i = 0
    while i < len(args):
        arg = args[i]
        i += 1
        if arg == "--":
            operands.extend(args[i:])
            break
        if arg.startswith("--"):
            name, eq, value = arg.partition("=")
            if eq:
                values.append((name, value))
            elif name in valued and i < len(args):
                values.append((name, args[i]))
                i += 1
            else:
                flags.append(name)
        elif arg.startswith("-") and len(arg) > 1:
            if arg in valued:
                values.append((arg, args[i] if i < len(args) else ""))
                i += 1
                continue
            for k, letter in enumerate(arg[1:], start=1):
                option = "-" + letter
                if option in valued:
                    value = arg[k + 1 :]
                    if not value and letter not in attached and i < len(args):
                        value, i = args[i], i + 1
                    values.append((option, value))
                    break
                flags.append(option)
        else:
            operands.append(arg)
    return operands, values, flags


def _values(values: list[tuple[str, str]], *names: str) -> list[str]:
    return [value for name, value in values if name in names]


# ── git ─────────────────────────────────────────────────────────────────────────────────────────────────────────

_GIT_GLOBAL_VALUED = frozenset(
    {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env", "--super-prefix", "--attr-source",
     "--list-cmds"}
)
#: Sous-commandes qui écrivent le dépôt (le dossier git) dans toutes leurs formes.
_GIT_DIR_WRITERS = frozenset(
    {"add", "commit", "rm", "mv", "reset", "merge", "rebase", "cherry-pick", "revert", "pull", "fetch", "am",
     "update-ref", "update-index", "gc", "prune", "repack", "init", "checkout", "switch", "commit-tree", "write-tree",
     "read-tree", "pack-refs", "fast-import", "filter-branch"}
)
#: Sous-commandes qui écrivent l'arbre de travail dans toutes leurs formes.
_GIT_TREE_WRITERS = frozenset(
    {"checkout", "switch", "merge", "rebase", "cherry-pick", "revert", "pull", "am", "checkout-index",
     "sparse-checkout"}
)
_GIT_STASH_WRITERS = frozenset({"push", "save", "pop", "apply", "drop", "clear", "store", "branch"})
_CLONE_VALUED = frozenset(
    {"-o", "--origin", "-b", "--branch", "-u", "--upload-pack", "--reference", "--reference-if-able",
     "--separate-git-dir", "--depth", "--shallow-since", "--shallow-exclude", "-c", "--config", "--server-option",
     "-j", "--jobs", "--filter", "--bundle-uri", "--template", "--ref-format", "--revision"}
)
_INIT_VALUED = frozenset({"--template", "--separate-git-dir", "--object-format", "--ref-format", "-b",
                          "--initial-branch"})
_CONFIG_VALUED = frozenset({"-f", "--file", "--blob", "--type", "--default", "--comment", "--value"})
_CONFIG_READS = frozenset(
    {"--get", "--get-all", "--get-regexp", "--get-urlmatch", "-l", "--list", "--get-color", "--get-colorbool",
     "-e", "--edit"}
)
_CONFIG_WRITES = frozenset({"--unset", "--unset-all", "--add", "--replace-all", "--rename-section",
                            "--remove-section"})
_MV_FLAGS = frozenset({"-f", "--force", "-k", "-v", "--verbose", "--sparse"})
_RM_FLAGS = frozenset({"-f", "--force", "-r", "-q", "--quiet", "--ignore-unmatch", "--sparse"})


def _git(args: list[str], host: Host) -> bool:
    dirs: list[str] = []
    git_dir = host.variable("GIT_DIR") or ""
    work_tree = host.variable("GIT_WORK_TREE") or ""
    i = 0
    while i < len(args) and args[i].startswith("-"):
        arg = args[i]
        name, eq, value = arg.partition("=")
        if name in _GIT_GLOBAL_VALUED and not eq:
            value = args[i + 1] if i + 1 < len(args) else ""
            i += 1
        i += 1
        if name == "-C" and value:
            dirs.append(value)
        elif name == "--git-dir":
            git_dir = value
        elif name == "--work-tree":
            work_tree = value
    if i >= len(args):
        return False
    sub, rest = args[i], args[i + 1 :]
    where = tuple(dirs)

    def hit(word: str) -> bool:
        return bool(word) and host.hit(word, where)

    if git_dir and hit(git_dir) and (sub in _GIT_DIR_WRITERS or _stash_writes(sub, rest)):
        return True
    if work_tree and hit(work_tree) and (sub in _GIT_TREE_WRITERS or _stash_writes(sub, rest)):
        return True
    if any(_writes_to_file(a) for a in rest):
        outputs = [a.split("=", 1)[1] for a in rest if _writes_to_file(a) and "=" in a]
        outputs += [b for a, b in pairwise(rest) if _writes_to_file(a) and "=" not in a]
        if any(hit(o) for o in outputs):
            return True
    handler = _GIT_SUBCOMMANDS.get(sub)
    return handler is not None and handler(rest, host, hit, git_dir)


def _stash_writes(sub: str, rest: list[str]) -> bool:
    if sub != "stash":
        return False
    operands = [a for a in rest if not a.startswith("-")]
    return not operands or operands[0] in _GIT_STASH_WRITERS


def _git_clone(rest: list[str], _host: Host, hit: Callable[[str], bool], _git_dir: str) -> bool:
    operands, values, flags = _scan(rest, _CLONE_VALUED)
    if any(hit(v) for v in _values(values, "--separate-git-dir")):
        return True
    if len(operands) >= 2:
        return hit(operands[1])
    if operands:
        name = re.split(r"[/:]", operands[0].rstrip("/"))[-1].removesuffix(".git")
        return hit((name + ".git") if "--bare" in flags or "--mirror" in flags else name)
    return False


def _git_init(rest: list[str], _host: Host, hit: Callable[[str], bool], git_dir: str) -> bool:
    operands, values, _flags = _scan(rest, _INIT_VALUED)
    targets = [operands[0] if operands else ".", *_values(values, "--separate-git-dir")]
    return any(hit(t) for t in targets) or bool(git_dir and hit(git_dir))


def _git_worktree(rest: list[str], _host: Host, hit: Callable[[str], bool], _git_dir: str) -> bool:
    if not rest:
        return False
    operands, _opts, _flags = _scan(rest[1:], frozenset({"-b", "-B", "--reason"}))
    if rest[0] == "add":
        return bool(operands) and hit(operands[0])
    if rest[0] in ("move", "remove"):
        return any(hit(o) for o in operands[:2])
    return False


def _git_config(rest: list[str], _host: Host, hit: Callable[[str], bool], git_dir: str) -> bool:
    operands, values, flags = _scan(rest, _CONFIG_VALUED)
    files = _values(values, "-f", "--file")
    target = files[-1] if files else (git_dir.rstrip("/") + "/config" if git_dir else "")
    if not target or not hit(target):
        return False
    if any(f in _CONFIG_READS for f in flags) or (operands[:1] and operands[0] in ("get", "list", "edit")):
        return False
    if operands[:1] and operands[0] in ("set", "unset", "rename-section", "remove-section"):
        return True
    return any(f in _CONFIG_WRITES for f in flags) or len(operands) >= 2


def _git_paths(rest: list[str], _host: Host, hit: Callable[[str], bool], known: frozenset[str]) -> bool:
    """``git mv``/``git rm`` : un chemin de la cible ; une option inconnue ou ``-n`` : pas de preuve."""
    operands, values, flags = _scan(rest, frozenset({"--pathspec-from-file"}))
    if values or any(f not in known for f in flags):
        return False
    return any(hit(o) for o in operands)


def _git_clean(rest: list[str], _host: Host, hit: Callable[[str], bool], _git_dir: str) -> bool:
    operands, _opts, flags = _scan(rest, frozenset({"-e", "--exclude"}))
    if not ({"-f", "--force"} & set(flags)) or {"-n", "--dry-run", "-i", "--interactive"} & set(flags):
        return False
    return any(hit(o) for o in operands or ["."])


def _git_checkout(rest: list[str], _host: Host, hit: Callable[[str], bool], _git_dir: str) -> bool:
    return "--" in rest and any(hit(p) for p in rest[rest.index("--") + 1 :])


def _git_restore(rest: list[str], _host: Host, hit: Callable[[str], bool], _git_dir: str) -> bool:
    operands, _opts, flags = _scan(rest, frozenset({"-s", "--source", "--pathspec-from-file"}))
    staged_only = bool({"-S", "--staged"} & set(flags)) and not ({"-W", "--worktree"} & set(flags))
    return not staged_only and any(hit(o) for o in operands)


def _git_output(rest: list[str], _host: Host, hit: Callable[[str], bool], _git_dir: str) -> bool:
    _operands, values, _flags = _scan(rest, frozenset({"-o", "--output", "--output-directory"}))
    return any(hit(v) for v in _values(values, "-o", "--output", "--output-directory"))


def _git_bundle(rest: list[str], _host: Host, hit: Callable[[str], bool], _git_dir: str) -> bool:
    operands = [a for a in rest[1:] if not a.startswith("-")]
    return rest[:1] == ["create"] and bool(operands) and hit(operands[0])


def _git_rebase(rest: list[str], host: Host, _hit: Callable[[str], bool], _git_dir: str) -> bool:
    _operands, values, _flags = _scan(rest, frozenset({"-x", "--exec", "-s", "--strategy", "-X", "--onto"}))
    return any(host.run_script(v) for v in _values(values, "-x", "--exec"))


def _git_submodule(rest: list[str], host: Host, _hit: Callable[[str], bool], _git_dir: str) -> bool:
    if "foreach" not in rest:
        return False
    command = [a for a in rest[rest.index("foreach") + 1 :] if a != "--recursive"]
    return bool(command) and host.run_script(" ".join(command))


def _git_bisect(rest: list[str], host: Host, _hit: Callable[[str], bool], _git_dir: str) -> bool:
    return rest[:1] == ["run"] and len(rest) > 1 and host.run_words(rest[1:])


_GIT_SUBCOMMANDS: dict[str, Callable[[list[str], Host, Callable[[str], bool], str], bool]] = {
    "clone": _git_clone,
    "init": _git_init,
    "worktree": _git_worktree,
    "config": _git_config,
    "mv": lambda r, h, f, _g: _git_paths(r, h, f, _MV_FLAGS),
    "rm": lambda r, h, f, _g: _git_paths(r, h, f, _RM_FLAGS),
    "clean": _git_clean,
    "checkout": _git_checkout,
    "restore": _git_restore,
    "archive": _git_output,
    "format-patch": _git_output,
    "bundle": _git_bundle,
    "rebase": _git_rebase,
    "submodule": _git_submodule,
    "bisect": _git_bisect,
}


# ── Archives et compresseurs ────────────────────────────────────────────────────────────────────────────────────

_TAR_VALUED = frozenset(
    {"-f", "--file", "-C", "--directory", "-b", "--blocking-factor", "-T", "--files-from", "-X", "--exclude-from",
     "-K", "--starting-file", "-N", "--newer", "--after-date", "-V", "--label", "-L", "--tape-length", "-g",
     "--listed-incremental", "-F", "--info-script", "--new-volume-script", "-H", "--format", "--exclude",
     "--transform", "--xform", "--owner", "--group", "--mode", "--mtime", "--record-size",
     "--use-compress-program", "-I", "--to-command", "--rsh-command", "--checkpoint-action", "--suffix",
     "--strip-components", "--index-file"}
)
_TAR_WRITE_MODES = frozenset({"-c", "--create", "-r", "--append", "-u", "--update", "-A", "--catenate",
                              "--concatenate", "--delete"})
_TAR_EXTRACT = frozenset({"-x", "--extract", "--get"})


def _tar(args: list[str], host: Host) -> bool:
    if args and not args[0].startswith("-"):  # forme ancienne : ``tar xzf a.tgz`` — lettres groupées sans tiret
        letters, rest = args[0], args[1:]
        expanded: list[str] = []
        for letter in letters:
            expanded.append("-" + letter)
            if "-" + letter in _TAR_VALUED and rest:
                expanded.append(rest.pop(0))
        args = expanded + rest
    _operands, values, flags = _scan(args, _TAR_VALUED)
    names = set(flags) | {name for name, _ in values}
    if any(host.hit(v) for v in _values(values, "--index-file", "-g", "--listed-incremental")):
        return True
    if names & _TAR_WRITE_MODES:
        return any(f != "-" and host.hit(f) for f in _values(values, "-f", "--file"))
    if names & _TAR_EXTRACT and not ({"-O", "--to-stdout"} & names):
        return any(host.hit(d) for d in _values(values, "-C", "--directory") or ["."])
    return False


def _unzip(args: list[str], host: Host) -> bool:
    _operands, values, flags = _scan(args, frozenset({"-d", "-P", "-I", "-O"}))
    if {"-l", "-t", "-v", "-p", "-c", "-z", "-Z"} & set(flags):
        return False
    return any(host.hit(d) for d in _values(values, "-d") or ["."])


def _zip(args: list[str], host: Host) -> bool:
    operands, values, flags = _scan(args, frozenset({"-b", "-t", "--tt", "-n", "-P", "-s", "-Z", "-O",
                                                    "--out", "-x", "-i"}))
    outputs = _values(values, "-O", "--out")
    if {"-sf", "-T"} & set(args) or "-T" in flags:  # ``-sf`` liste, ``-T`` teste
        return False
    archive = outputs[-1] if outputs else (operands[0] if operands else "")
    return archive not in ("", "-") and host.hit(archive)


def _seven_zip(args: list[str], host: Host) -> bool:
    if not args:
        return False
    command, rest = args[0], args[1:]
    operands = [a for a in rest if not a.startswith("-")]
    if command in ("a", "u", "d", "rn"):
        return bool(operands) and host.hit(operands[0])
    if command in ("x", "e"):
        outputs = [a[2:] for a in rest if a.startswith("-o")]
        return any(host.hit(o) for o in outputs or ["."])
    return False


_COMPRESSORS = frozenset(
    {"gzip", "gunzip", "bzip2", "bunzip2", "xz", "unxz", "lzma", "unlzma", "zstd", "unzstd", "lz4", "unlz4", "lzip",
     "pigz", "unpigz", "pbzip2", "lzop", "compress", "uncompress", "brotli"}
)
_COMPRESSOR_VALUED = frozenset({"-S", "--suffix", "-o", "--output", "-D", "-T", "--threads", "-M", "--memlimit",
                                "-F", "--format", "-C", "--check", "-b", "-p", "--processes"})


def _compressor(args: list[str], host: Host) -> bool:
    operands, values, flags = _scan(args, _COMPRESSOR_VALUED)
    if {"-c", "--stdout", "--to-stdout", "-t", "--test", "-l", "--list"} & set(flags):
        return False
    return any(host.hit(o) for o in _values(values, "-o", "--output")) or any(
        o != "-" and host.hit(o) for o in operands
    )


def _cpio(args: list[str], host: Host) -> bool:
    operands, values, flags = _scan(args, frozenset({"-F", "--file", "-O", "-I", "-H", "--format", "-D",
                                                    "--directory", "-E", "-M", "-R", "-C"}))
    if "-p" in flags or "--pass-through" in flags:
        return bool(operands) and host.hit(operands[0])
    if "-t" in flags or "--list" in flags:
        return False
    if "-i" in flags or "--extract" in flags:
        return any(host.hit(d) for d in _values(values, "-D", "--directory") or ["."])
    if "-o" in flags or "--create" in flags:
        return any(host.hit(f) for f in _values(values, "-O", "-F", "--file"))
    return False


# ── Copie, fichiers temporaires, découpage, outils à fichier de sortie ──────────────────────────────────────────


def _scp(args: list[str], host: Host) -> bool:
    operands, _opts, _flags = _scan(args, frozenset({"-P", "-i", "-o", "-c", "-F", "-l", "-S", "-J", "-D", "-X"}))
    if len(operands) < 2:
        return False
    destination = operands[-1]
    remote = re.match(r"^(?:scp://|[^/]*:)", destination) and not re.match(r"^[A-Za-z]:[\\/]", destination)
    return not remote and host.hit(destination)


def _mktemp(args: list[str], host: Host) -> bool:
    operands, values, flags = _scan(args, frozenset({"-p", "--suffix"}))
    if "-u" in flags or "--dry-run" in flags:
        return False
    folders = _values(values, "-p", "--tmpdir")
    template = operands[0] if operands else ""
    if template and "/" in template and host.hit(template):
        return True
    return any(host.hit(folder) or (template and host.hit(folder.rstrip("/") + "/" + template)) for folder in folders)


def _second_operand(valued: frozenset[str], default: str = "") -> Callable[[list[str], Host], bool]:
    """``split``/``uniq`` : le second opérande est écrit (préfixe des morceaux, fichier de sortie).

    *default* : ce qui est écrit sans second opérande (``split`` écrit ``xaa``… dans le répertoire courant).
    """

    def check(args: list[str], host: Host) -> bool:
        operands, _opts, _flags = _scan(args, valued)
        written = operands[1] if len(operands) >= 2 else default
        return bool(written) and host.hit(written)

    return check


def _csplit(args: list[str], host: Host) -> bool:
    _operands, values, _flags = _scan(args, frozenset({"-f", "--prefix", "-b", "--suffix-format", "-n", "--digits"}))
    return any(host.hit(v) for v in _values(values, "-f", "--prefix"))


def _patch(args: list[str], host: Host) -> bool:
    operands, values, flags = _scan(
        args,
        frozenset({"-p", "--strip", "-i", "--input", "-o", "--output", "-r", "--reject-file", "-d", "--directory",
                   "-D", "--ifdef", "-F", "--fuzz", "-V", "--version-control", "-B", "--prefix", "-Y",
                   "--basename-prefix", "-z", "--suffix", "-g", "--get"}),
    )
    if "--dry-run" in flags:
        return False
    where = tuple(_values(values, "-d", "--directory"))
    outputs = _values(values, "-o", "--output", "-r", "--reject-file")
    if any(o != "-" and host.hit(o, where) for o in outputs):
        return True
    return not _values(values, "-o", "--output") and bool(operands) and host.hit(operands[0], where)


def _openssl(args: list[str], host: Host) -> bool:
    return any(a in ("-out", "-keyout") and host.hit(b) for a, b in pairwise(args))


_KEYGEN_VALUED = frozenset("-" + letter for letter in "fbtCNPmZaOnVzIDEFGJjKMrSsTWwY")
_KEYGEN_READS = frozenset("-" + letter for letter in "lyeBLFQDir")


def _ssh_keygen(args: list[str], host: Host) -> bool:
    _operands, values, flags = _scan(args, _KEYGEN_VALUED)
    names = set(flags) | {name for name, _ in values}
    return not (names & _KEYGEN_READS) and any(host.hit(v) for v in _values(values, "-f"))


_SQL_WRITES = re.compile(
    r"(?i)\b(?:create|insert|update|delete|drop|alter|replace|vacuum|reindex|attach)\b|\bpragma\s+\w+\s*=|^\s*\.(?:import|restore|load)\b",
    re.MULTILINE,
)
_SQLITE_OUTPUTS = re.compile(r"(?im)^\s*\.(?:output|once|save|backup|excel)\s+(?:-\S+\s+)*(\S+)")


def _sqlite3(args: list[str], host: Host, stdin: str | None) -> bool:
    valued = frozenset({"-cmd", "-init", "-separator", "-newline", "-nullvalue", "-vfs", "-mmap", "-maxsize",
                        "-heap", "-lookaside", "-pagecache"})
    operands: list[str] = []
    commands: list[str] = []
    readonly = False
    i = 0
    while i < len(args):
        arg = args[i]
        i += 1
        option = "-" + arg.lstrip("-") if arg.startswith("-") else ""
        if option in valued:
            commands.extend([args[i]] if option == "-cmd" and i < len(args) else [])
            i += 1
        elif option:
            readonly = readonly or option == "-readonly"
        else:
            operands.append(arg)
    sql = "\n".join([*commands, *operands[1:], stdin or ""])
    if any(host.hit(m.group(1).strip("'\"")) for m in _SQLITE_OUTPUTS.finditer(sql)):
        return True
    return bool(operands) and not readonly and bool(_SQL_WRITES.search(sql)) and host.hit(operands[0])


_ED_WRITE = re.compile(r"^\s*(?:[0-9.,$;+\-'a-vx-z]+\s*)?[wW]q?(?:\s+(\S.*))?$")
_EX_WRITE = re.compile(
    r"^[:\s]*(?:[0-9.,$%'<>+\-]+\s*)?(?:w|write|wq|x|xit|exit|wa|wall|wqa|wqall|xa|xall|up|update|sav|saveas)!?"
    r"(?:\s+(?:>>\s*)?(\S.*))?$"
)
_EDITORS = frozenset({"ex", "vi", "vim", "nvim", "view", "gvim", "vim.basic", "vim.tiny"})
_EDITOR_VALUED = frozenset({"-S", "-u", "-U", "-i", "-T", "-w", "-W", "-t", "--listen", "--servername", "--cmd"})


def _ed(args: list[str], host: Host, stdin: str | None) -> bool:
    operands, _opts, _flags = _scan(args, frozenset({"-p", "--prompt"}))
    if stdin is None:
        return False
    for line in stdin.splitlines():
        write = _ED_WRITE.match(line)
        if write and any(host.hit(f) for f in ([write.group(1)] if write.group(1) else operands)):
            return True
    return False


def _editor(args: list[str], host: Host, stdin: str | None) -> bool:
    commands: list[str] = []
    files: list[str] = []
    silent = False
    i = 0
    while i < len(args):
        arg = args[i]
        i += 1
        if arg == "-c" and i < len(args):
            commands.append(args[i])
            i += 1
        elif arg.startswith("+"):
            commands.append(arg[1:])
        elif arg in _EDITOR_VALUED:
            i += 1
        elif arg.startswith("-"):
            silent = silent or (not arg.startswith("--") and "s" in arg[1:])
        else:
            files.append(arg)
    if silent and stdin is not None:
        commands.extend(stdin.splitlines())
    for command in commands:
        for part in command.split("|"):
            write = _EX_WRITE.match(part)
            if write and any(host.hit(f) for f in ([write.group(1).strip()] if write.group(1) else files)):
                return True
    return False


def _script_command(args: list[str], host: Host) -> bool:
    """``script [-c cmd] [fichier]`` : la commande lancée, le journal écrit."""
    operands, values, _flags = _scan(
        args,
        frozenset({"-c", "--command", "-E", "--echo", "-I", "--log-in", "-O", "--log-out", "-B", "--log-io", "-T",
                   "--log-timing", "-m", "--logging-format", "-o", "--output-limit"}),
    )
    if any(host.run_script(c) for c in _values(values, "-c", "--command")):
        return True
    logs = _values(values, "-I", "--log-in", "-O", "--log-out", "-B", "--log-io", "-T", "--log-timing")
    return any(host.hit(f) for f in [*logs, *operands[:1]])


def _su(args: list[str], host: Host) -> bool:
    _operands, values, _flags = _scan(args, frozenset({"-c", "--command", "-s", "--shell", "-g", "--group", "-G",
                                                      "--supp-group", "-u", "--user", "-w",
                                                      "--whitelist-environment"}))
    return any(host.run_script(c) for c in _values(values, "-c", "--command"))


def _watch(args: list[str], host: Host) -> bool:
    i = 0
    direct = False
    while i < len(args) and args[i].startswith("-"):
        direct = direct or args[i] in ("-x", "--exec")
        i += 2 if args[i] in ("-n", "--interval", "-q", "--equexit") else 1
    rest = args[i:]
    if not rest:
        return False
    return host.run_words(rest) if direct else host.run_script(" ".join(rest))


def _gh(args: list[str], host: Host) -> bool:
    if args[:2] in (["run", "download"], ["release", "download"]):
        _operands, values, _flags = _scan(args[2:], frozenset({"-D", "--dir", "-O", "--output", "-n", "--name",
                                                               "-p", "--pattern", "-A", "--archive", "-R", "--repo"}))
        return any(host.hit(v) for v in _values(values, "-D", "--dir", "-O", "--output"))
    if args[:2] in (["repo", "clone"], ["gist", "clone"]):
        rest = args[2:]
        own = rest[: rest.index("--")] if "--" in rest else rest
        operands, values, flags = _scan(own, frozenset({"-u", "--upstream-remote-name"}))
        if flags or any(name not in ("-u", "--upstream-remote-name") for name, _ in values):
            return False  # option inconnue : gh refuse la commande, rien n'est écrit
        if len(operands) >= 2:
            return host.hit(operands[1])
        return bool(operands) and host.hit(re.split(r"[/:]", operands[0].rstrip("/"))[-1].removesuffix(".git"))
    return False


_PROGRAMS: dict[str, Callable[[list[str], Host], bool]] = {
    "git": _git,
    "tar": _tar,
    "bsdtar": _tar,
    "unzip": _unzip,
    "zip": _zip,
    "7z": _seven_zip,
    "7za": _seven_zip,
    "7zr": _seven_zip,
    "cpio": _cpio,
    "scp": _scp,
    "mktemp": _mktemp,
    "split": _second_operand(
        frozenset({"-a", "--suffix-length", "-b", "--bytes", "-C", "--line-bytes", "-l", "--lines", "-n", "--number",
                   "-t", "--separator", "--additional-suffix", "--filter"}),
        default="x",
    ),
    "uniq": _second_operand(frozenset({"-f", "--skip-fields", "-s", "--skip-chars", "-w", "--check-chars"})),
    "csplit": _csplit,
    "patch": _patch,
    "openssl": _openssl,
    "ssh-keygen": _ssh_keygen,
    "script": _script_command,
    "su": _su,
    "runuser": _su,
    "watch": _watch,
    "gh": _gh,
}
_WITH_STDIN: dict[str, Callable[[list[str], Host, str | None], bool]] = {
    "sqlite3": _sqlite3,
    "ed": _ed,
    **dict.fromkeys(_EDITORS, _editor),
}


def program_writes(program: str, args: list[str], host: Host, stdin: str | None) -> bool:
    """Vrai si *program* *args* (entrée littérale *stdin*) écrit, de façon prouvée, sur la cible."""
    if program in _COMPRESSORS:
        return _compressor(args, host)
    handler = _PROGRAMS.get(program)
    if handler is not None:
        return handler(args, host)
    reader = _WITH_STDIN.get(program)
    return reader is not None and reader(args, host, stdin)
