#!/usr/bin/env python3
"""Registre de couverture par surface, et cliquet qui nomme l'unité perdue (D-09).

``--cov-fail-under=75`` dit « le code est couvert à 75 % » sans jamais dire
laquelle des surfaces que l'utilisateur touche n'est couverte par rien : une
sous-commande, un hook, un genre de flow ou un hôte peut perdre son dernier
test sans que le pourcentage bouge. Ce script énumère ces surfaces depuis le
code réel, joint les tests qui déclarent les couvrir, et refuse qu'une unité
couverte sur la base cesse de l'être.

Les unités, par classe (``<classe>:<identifiant>``), et le niveau de preuve
minimal qu'un test doit atteindre pour que l'unité compte comme couverte :

- ``cli`` : feuilles de l'arbre Typer, alias masqués exclus ; ``unit``.
- ``genre`` : genres de node de flow (``GENRE_KINDS``) ; ``unit``.
- ``hook`` : décisions de hook (``_DECISION_FUNCS``) ; ``real``. Un hook est
  appelé par l'hôte dans un autre processus, entrée et sortie JSON.
- ``host`` : hôtes qui ont un émetteur (``supported_hosts``) ; ``real``. Un
  émetteur écrit dans les fichiers de l'hôte.

Un test déclare ce qu'il couvre par le marqueur pytest ``covers`` (relevé par
l'AST, sans exécuter le test), à la manière d'un en-tête ``covers:``::

    @pytest.mark.covers("cli:flow run", "genre:fanout", level="integration")

``level`` vaut ``unit`` par défaut. Le niveau retenu est le plus faible de
celui que l'auteur déclare et du plafond que le fichier de test permet :
``real`` si le fichier exécute un sous-processus ou vit sous ``tests/e2e/``,
``integration`` s'il écrit sur un vrai disque (``tmp_path``), ``unit`` sinon.
Déclarer ``real`` dans un fichier sans sous-processus ne vaut donc que
``integration`` ou moins : la déclaration ne gonfle pas la preuve.

Cliquet (``--check --base REF``) : le registre de ``REF`` est reconstruit avec
CE script sur l'arbre de ``REF`` (``git archive``), puis comparé. Une unité
couverte sur la base, encore énumérée ici, et qui ne l'est plus (marqueur
retiré, niveau insuffisant, fichier supprimé) fait échouer, nommée. Une unité
nouvelle sans test est listée, jamais un échec ; une unité supprimée n'est
pas une perte.

Gardes anti-pourrissement (toujours dans ``--check``) :

- chaque classe est non vide (un énumérateur cassé qui renvoie ``[]`` ne doit
  pas rapporter « tout est couvert ») ;
- le décompte énuméré de chaque classe égale un recomptage indépendant fait
  sur les sources (AST ou expression régulière), jamais par le même chemin ;
- l'énumération a bien chargé l'arbre demandé et non un paquet installé ailleurs ;
- chaque unité revendiquée existe, chaque niveau est connu, chaque argument est
  un littéral, et aucun marqueur ne vit hors des chemins que la CI exécute.

Corroboration : une revendication doit laisser une trace lisible dans le
fichier (liste d'arguments ``["flow", "run", ...]`` pour une commande, identifiant
de genre, de décision ou d'hôte comme littéral), faute de quoi elle est refusée.

Limite assumée : le marqueur reste une déclaration. Le script ne prouve pas que
le test exerce l'unité, seulement qu'elle existe, que le fichier la nomme et
qu'il est de niveau suffisant. Le plafond est celui du fichier, pas du test.

Usage::

    python scripts/check-surface-coverage.py                    # résumé
    python scripts/check-surface-coverage.py --list uncovered   # unités sans preuve
    python scripts/check-surface-coverage.py --check            # gardes seules
    python scripts/check-surface-coverage.py --check --base origin/main
    python scripts/check-surface-coverage.py --json             # registre complet
"""

from __future__ import annotations

import argparse
import ast
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

LEVELS = ("unit", "integration", "real")
MIN_LEVEL = {"cli": "unit", "genre": "unit", "hook": "real", "host": "real"}
CLASSES = tuple(MIN_LEVEL)

#: Chemins de tests que la CI exécute (``ci-sdk.yml``, ``ci-validate.yml``,
#: job e2e). Un marqueur ailleurs serait une revendication que rien ne rejoue.
CLAIM_ROOTS = ("tests/unit", "tests/e2e")
CLAIM_ROOT_GLOBS = ("tests/test_*.py",)

_SUBPROCESS_CALLS = frozenset({"run", "Popen", "check_output", "check_call", "call"})
_DISK_FIXTURES = frozenset({"tmp_path", "tmp_path_factory", "tmpdir", "tmpdir_factory"})
_UNIT_ID = re.compile(r"^(?P<cls>[a-z]+):(?P<name>\S.*)$")


@dataclass(frozen=True)
class Claim:
    unit: str
    declared: str
    effective: str
    path: str


@dataclass
class Unit:
    id: str
    cls: str
    min_level: str
    claims: list[Claim] = field(default_factory=list)

    @property
    def best(self) -> str | None:
        ranks = [LEVELS.index(c.effective) for c in self.claims]
        return LEVELS[max(ranks)] if ranks else None

    @property
    def status(self) -> str:
        best = self.best
        if best is None:
            return "uncovered"
        return "covered" if LEVELS.index(best) >= LEVELS.index(self.min_level) else "under-level"


# ── énumération (sous-processus : l'arbre énuméré n'est pas celui qui tourne) ──


def _emit_units() -> dict[str, object]:
    """Énumère les unités du paquet ``grimoire`` importé (appelé en sous-processus)."""
    import typer.main

    import grimoire
    from grimoire.cli.app import app
    from grimoire.flows.blueprint_loader import GENRE_KINDS
    from grimoire.hosts.decisions import _DECISION_FUNCS
    from grimoire.hosts.emitters import supported_hosts

    leaves: list[str] = []

    def walk(command: object, prefix: list[str]) -> None:
        if not hasattr(command, "list_commands"):
            leaves.append(" ".join(prefix))
            return
        children = []
        for name in command.list_commands(None):  # type: ignore[attr-defined]
            sub = command.get_command(None, name)  # type: ignore[attr-defined]
            if sub is not None and not getattr(sub, "hidden", False):
                children.append((name, sub))
        if not children and prefix:
            leaves.append(" ".join(prefix))
        for name, sub in children:
            walk(sub, [*prefix, name])

    walk(typer.main.get_command(app), [])
    return {
        "package": str(Path(grimoire.__file__).resolve()),
        "units": {
            "cli": sorted(leaves),
            "genre": sorted(GENRE_KINDS),
            "hook": sorted(name.removeprefix("grimoire.") for name in _DECISION_FUNCS),
            "host": sorted(h.value.removeprefix("host-") for h in supported_hosts()),
        },
    }


def enumerate_units(root: Path) -> dict[str, list[str]]:
    """Unités de l'arbre *root*, énumérées dans un interpréteur qui n'importe que lui."""
    src = root / "src"
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(src), env.get("PYTHONPATH", "")]).rstrip(os.pathsep)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--emit-units"],
        cwd=root, env=env, capture_output=True, text=True, encoding="utf-8", check=False,
    )
    if proc.returncode != 0:
        raise SystemExit(f"énumération impossible sur {root} :\n{proc.stderr.strip()[-1500:]}")
    payload = json.loads(proc.stdout)
    loaded = Path(payload["package"]).resolve()
    if src.resolve() not in loaded.parents:
        raise SystemExit(
            f"l'énumération a chargé {loaded} au lieu de {src} : "
            "elle aurait mesuré un autre arbre que celui demandé"
        )
    units: dict[str, list[str]] = payload["units"]
    return units


# ── recomptage indépendant (AST et expressions régulières, jamais l'import) ──


def _hidden(call: ast.Call) -> bool:
    return any(
        k.arg == "hidden" and isinstance(k.value, ast.Constant) and k.value.value is True
        for k in call.keywords
    )


def recount(root: Path) -> dict[str, int]:
    """Décompte des mêmes unités, lu dans les sources sans passer par l'énumération."""
    cli = 0
    for path in sorted((root / "src" / "grimoire" / "cli").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call) or _hidden(node):
                continue
            func = node.func
            if (isinstance(func, ast.Attribute) and func.attr == "command") or (
                isinstance(func, ast.Name) and func.id == "LazyCommandSpec"
            ):
                cli += 1
    genres = (root / "src/grimoire/flows/genres.py").read_text(encoding="utf-8")
    decisions = (root / "src/grimoire/hosts/decisions/__init__.py").read_text(encoding="utf-8")
    emitters = (root / "src/grimoire/hosts/emitters/__init__.py").read_text(encoding="utf-8")
    return {
        "cli": cli,
        "genre": len(re.findall(r'^    "[a-z-]+": _execute_\w+,$', genres, re.MULTILINE)),
        "hook": len(re.findall(r'^    "grimoire\.[a-z-]+": \("\w+", "\w+"\),$', decisions, re.MULTILINE)),
        "host": len(re.findall(r"^    HostId\.[A-Z_]+: \w+\(", emitters, re.MULTILINE)),
    }


# ── relevé des revendications (AST des tests) ──


def _is_covers_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "covers"
        and isinstance(func.value, ast.Attribute)
        and func.value.attr == "mark"
    )


def _ceiling(rel: str, tree: ast.Module) -> str:
    """Plus haut niveau de preuve que ce fichier de test peut apporter."""
    parts = Path(rel).parts
    if "e2e" in parts[:2]:
        return "real"
    from_subprocess = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "subprocess"
        for alias in node.names
        if alias.name in _SUBPROCESS_CALLS
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "subprocess"
                and func.attr in _SUBPROCESS_CALLS
            ) or (isinstance(func, ast.Name) and func.id in from_subprocess):
                return "real"
    disk = "integration" in parts[:2]
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            disk = disk or any(a.arg in _DISK_FIXTURES for a in node.args.args + node.args.kwonlyargs)
    return "integration" if disk else "unit"


def _argv_lists(tree: ast.Module) -> list[list[str]]:
    """Mots littéraux d'une liste d'arguments, options écartées, jusqu'au premier non-littéral.

    ``["--output", "json", "flow", "run", str(bp)]`` donne ``["json", "flow", "run"]`` :
    ``--output`` se saute, sa valeur reste, et la commande est une sous-suite contiguë.
    """
    heads: list[list[str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.List):
            head: list[str] = []
            for elt in node.elts:
                if not (isinstance(elt, ast.Constant) and isinstance(elt.value, str)):
                    break
                if not elt.value.startswith("-"):
                    head.append(elt.value)
            if head:
                heads.append(head)
    return heads


def _contains_run(head: list[str], want: list[str]) -> bool:
    return any(head[i : i + len(want)] == want for i in range(len(head) - len(want) + 1))


def _corroborated(unit: str, tree: ast.Module) -> bool:
    """Le fichier nomme-t-il l'unité d'une façon qu'un relecteur retrouve ?"""
    match = _UNIT_ID.match(unit)
    if not match:
        return True  # déjà refusée par la garde de forme
    cls, name = match["cls"], match["name"]
    if cls == "cli":
        want = name.split()
        return any(_contains_run(head, want) for head in _argv_lists(tree))
    wanted = {name, f"grimoire.{name}", f"host-{name}"}
    attr = name.upper().replace("-", "_")
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in wanted:
            return True
        if cls == "host" and isinstance(node, ast.Attribute) and node.attr == attr:
            return True
    return False


def _test_files(root: Path) -> list[Path]:
    found: set[Path] = set()
    for sub in CLAIM_ROOTS:
        found.update((root / sub).rglob("test_*.py"))
    for pattern in CLAIM_ROOT_GLOBS:
        found.update(root.glob(pattern))
    return sorted(found)


def scan_claims(root: Path) -> tuple[list[Claim], list[str]]:
    """Revendications ``covers`` des tests exécutés par la CI, et les erreurs de forme."""
    claims: list[Claim] = []
    errors: list[str] = []
    scanned = {p.resolve() for p in _test_files(root)}
    for path in sorted((root / "tests").rglob("*.py")) if (root / "tests").is_dir() else []:
        rel = path.relative_to(root).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError) as exc:
            errors.append(f"{rel} : illisible, ses revendications ne sont pas relevées ({type(exc).__name__})")
            continue
        calls = [n for n in ast.walk(tree) if _is_covers_call(n)]
        if not calls:
            continue
        if path.resolve() not in scanned:
            errors.append(f"{rel} : marqueur covers hors des chemins que la CI exécute ({', '.join(CLAIM_ROOTS)}, tests/test_*.py)")
            continue
        ceiling = _ceiling(rel, tree)
        for call in calls:
            assert isinstance(call, ast.Call)
            declared = "unit"
            for kw in call.keywords:
                if kw.arg != "level":
                    errors.append(f"{rel}:{call.lineno} : argument nommé inconnu {kw.arg!r} (seul level= existe)")
                elif isinstance(kw.value, ast.Constant) and kw.value.value in LEVELS:
                    declared = str(kw.value.value)
                else:
                    errors.append(f"{rel}:{call.lineno} : level doit être un littéral parmi {', '.join(LEVELS)}")
            effective = LEVELS[min(LEVELS.index(declared), LEVELS.index(ceiling))]
            if not call.args:
                errors.append(f"{rel}:{call.lineno} : covers() sans unité")
            for arg in call.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    claims.append(Claim(arg.value, declared, effective, rel))
                    if not _corroborated(arg.value, tree):
                        errors.append(f"{rel}:{call.lineno} : revendique {arg.value} sans que le fichier la nomme (liste d'arguments ou littéral)")
                else:
                    errors.append(f"{rel}:{call.lineno} : une unité de covers() doit être un littéral texte")
    return claims, errors


# ── registre ──


def build_registry(units: dict[str, list[str]], claims: list[Claim]) -> dict[str, Unit]:
    registry = {
        f"{cls}:{name}": Unit(f"{cls}:{name}", cls, MIN_LEVEL[cls])
        for cls, names in units.items()
        for name in names
    }
    for claim in claims:
        unit = registry.get(claim.unit)
        if unit is not None:
            unit.claims.append(claim)
    return registry


def guard_errors(root: Path, units: dict[str, list[str]], claims: list[Claim]) -> list[str]:
    errors: list[str] = []
    counts = recount(root)
    for cls in CLASSES:
        names = units.get(cls, [])
        if not names:
            errors.append(f"classe {cls} vide : l'énumération a cessé de voir ses unités")
        if len(names) != counts[cls]:
            errors.append(
                f"classe {cls} : {len(names)} unités énumérées, {counts[cls]} au recomptage indépendant des sources"
            )
        if len(set(names)) != len(names):
            errors.append(f"classe {cls} : identifiants en double")
    known = {f"{cls}:{name}" for cls, names in units.items() for name in names}
    for claim in claims:
        match = _UNIT_ID.match(claim.unit)
        if not match or match["cls"] not in MIN_LEVEL:
            errors.append(f"{claim.path} : {claim.unit!r} n'est pas <classe>:<id> ({', '.join(CLASSES)})")
        elif claim.unit not in known:
            errors.append(f"{claim.path} : revendique {claim.unit}, qui n'existe pas (renommée, supprimée ou faute de frappe)")
    return errors


def load_units(root: Path, units_from: Path | None) -> dict[str, list[str]]:
    if units_from is not None:
        data: dict[str, list[str]] = json.loads(units_from.read_text(encoding="utf-8"))
        return data
    return enumerate_units(root)


def registry_for(root: Path, units_from: Path | None) -> tuple[dict[str, Unit], list[str]]:
    units = load_units(root, units_from)
    claims, errors = scan_claims(root)
    if units_from is None:
        errors += guard_errors(root, units, claims)
    else:
        known = {f"{cls}:{name}" for cls, names in units.items() for name in names}
        errors += [f"{c.path} : revendique {c.unit}, qui n'existe pas" for c in claims if c.unit not in known]
    return build_registry(units, claims), errors


# ── cliquet ──


def extract_base(root: Path, ref: str, dest: Path) -> str:
    sha = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--verify", f"{ref}^{{commit}}"],
        capture_output=True, text=True, encoding="utf-8", check=False,
    )
    if sha.returncode != 0:
        raise SystemExit(f"base introuvable : {ref!r} ({sha.stderr.strip()})")
    commit = sha.stdout.strip()
    archive = subprocess.run(
        ["git", "-C", str(root), "archive", "--format=tar", commit, "--", "src", "tests"],
        capture_output=True, check=False,
    )
    if archive.returncode != 0:
        raise SystemExit(f"git archive {commit} a échoué : {archive.stderr.decode('utf-8', 'replace').strip()}")
    with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
        tar.extractall(dest, filter="data")
    return commit


def lost_units(base: dict[str, Unit], head: dict[str, Unit]) -> list[str]:
    lost = []
    for unit_id, before in sorted(base.items()):
        now = head.get(unit_id)
        if before.status != "covered" or now is None or now.status == "covered":
            continue
        was = ", ".join(sorted({c.path for c in before.claims})) or "?"
        lost.append(
            f"{unit_id} : couverte sur la base ({before.best}, par {was}), "
            f"{'sans revendication' if now.status == 'uncovered' else 'niveau ' + str(now.best) + ' < ' + now.min_level} ici"
        )
    return lost


# ── sortie ──


def summary(registry: dict[str, Unit]) -> list[str]:
    lines = []
    for cls in CLASSES:
        mine = [u for u in registry.values() if u.cls == cls]
        by = {s: sum(1 for u in mine if u.status == s) for s in ("covered", "under-level", "uncovered")}
        lines.append(
            f"{cls:6} {len(mine):4} unités (preuve minimale {MIN_LEVEL[cls]}) : "
            f"{by['covered']} couvertes, {by['under-level']} de niveau insuffisant, {by['uncovered']} sans test"
        )
    return lines


def to_json(registry: dict[str, Unit]) -> dict[str, object]:
    return {
        uid: {
            "class": u.cls,
            "min_level": u.min_level,
            "status": u.status,
            "best_level": u.best,
            "tests": sorted({c.path for c in u.claims}),
        }
        for uid, u in sorted(registry.items())
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--check", action="store_true", help="échoue sur une garde rompue (et sur une perte avec --base)")
    parser.add_argument("--base", help="référence git : une unité couverte là-bas ne peut pas être perdue")
    parser.add_argument("--json", action="store_true", help="registre complet sur la sortie standard")
    parser.add_argument("--list", choices=("covered", "under-level", "uncovered"), help="liste les unités d'un état")
    parser.add_argument("--units-from", type=Path, help="(tests) unités lues dans ce JSON au lieu d'être énumérées")
    parser.add_argument("--emit-units", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args.emit_units:
        print(json.dumps(_emit_units()))
        return 0

    root = args.root.resolve()
    registry, errors = registry_for(root, args.units_from)

    if args.json:
        print(json.dumps(to_json(registry), indent=2, ensure_ascii=False))
    else:
        print("\n".join(summary(registry)))
    if args.list:
        for unit in registry.values():
            if unit.status == args.list:
                print(f"{args.list} {unit.id}")

    problems = list(errors)
    if args.base:
        with tempfile.TemporaryDirectory(prefix="surface-base-") as tmp:
            commit = extract_base(root, args.base, Path(tmp))
            base_registry, base_errors = registry_for(Path(tmp), args.units_from)
            print(f"base {args.base} ({commit[:12]}) : {sum(u.status == 'covered' for u in base_registry.values())} unités couvertes")
        for message in base_errors:
            print(f"base : {message}", file=sys.stderr)
        problems += [f"perdue : {m}" for m in lost_units(base_registry, registry)]
        fresh = sorted(uid for uid, u in registry.items() if uid not in base_registry and u.status != "covered")
        if fresh:
            print(f"{len(fresh)} unité(s) nouvelle(s) sans preuve suffisante (listées, pas un échec) : {', '.join(fresh[:10])}{' …' if len(fresh) > 10 else ''}")

    for message in problems:
        print(f"ECHEC {message}", file=sys.stderr)
    return 1 if args.check and problems else 0


if __name__ == "__main__":
    sys.exit(main())
