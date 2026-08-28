#!/usr/bin/env python3
"""Génère la référence du système nodal sous ``docs/nodal/reference/``.

Rien de ce que ce script produit n'est écrit à la main, et rien n'est commité :
les pages sont créées à la volée pendant ``mkdocs build`` via ``mkdocs-gen-files``.
Une référence de 78 patterns et 30 contrats recopiée à la main serait périmée
avant la release suivante ; ici la seule façon de changer une page est de
changer la source.

Sources de vérité, dans l'ordre où elles sont lues :

``src/grimoire/tools/blueprint_primitives.py``
    les 7 primitives (``PRIMITIVES``) et la carte des cases de palette
    (``XXL_MAPPING``).
``schemas/blueprint-v1.schema.json`` + ``schemas/blueprint-v1.fr.yaml``
    la structure du fichier ``.blueprint.json``, et sa glose française.
``web/data/catalogue-export.json``
    les 78 patterns, 8 familles, 30 contrats, 52 anti-patterns, 141 relations
    et 50 cas d'usage.

Le script est aussi une porte. En mode ``--check`` il ne rend rien : il vérifie
que chaque champ du schéma a sa glose et réciproquement, et que chaque id du
catalogue est atteignable. Un manque sort non-zéro — donc échoue le build.

Usage :
    python scripts/gen_doc_reference.py --check
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
import unicodedata
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "schemas/blueprint-v1.schema.json"
SCHEMA_FR = ROOT / "schemas/blueprint-v1.fr.yaml"
CATALOGUE = ROOT / "web/data/catalogue-export.json"
PRIMITIVES_MODULE = ROOT / "src/grimoire/tools/blueprint_primitives.py"

BASE = "nodal/reference"

# Les trois contraintes de `ref` dépendantes de `kind` vivent dans un `allOf`,
# hors de l'arbre `properties` : on les nomme explicitement pour qu'elles
# entrent dans la glose comme les autres.
REF_VARIANTS = ("pattern", "extension-node", "composite")

GENERATED_HEADER = (
    "!!! info \"Page générée\"\n"
    "    Cette page est produite au build par `scripts/gen_doc_reference.py`\n"
    "    depuis {source}. La modifier à la main n'aurait aucun effet : éditez\n"
    "    la source.\n"
)


# ─────────────────────────────── chargement ────────────────────────────────


def load_primitives() -> tuple[dict[str, Any], dict[str, Any]]:
    """Importer ``blueprint_primitives`` sans dépendre du paquet installé."""
    spec = importlib.util.spec_from_file_location("_bp_primitives", PRIMITIVES_MODULE)
    if spec is None or spec.loader is None:  # pragma: no cover - chemin figé
        raise RuntimeError(f"module de primitives illisible : {PRIMITIVES_MODULE}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.PRIMITIVES, module.XXL_MAPPING


def load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA.read_text(encoding="utf-8"))


def load_gloss() -> dict[str, dict[str, str]]:
    return YAML(typ="safe").load(SCHEMA_FR.read_text(encoding="utf-8"))


def load_catalogue() -> dict[str, Any]:
    return json.loads(CATALOGUE.read_text(encoding="utf-8"))


# ─────────────────────────── parcours du schéma ────────────────────────────


def walk_schema(schema: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    """Émettre ``(pointeur, nœud)`` pour tout ce qui se documente.

    Trois familles seulement : la racine, chaque entrée de ``$defs``, et tout
    ce qui est atteignable par ``properties`` / ``items``. On ne descend jamais
    dans un ``$ref`` — la cible est déjà émise par ``$defs``.
    """
    yield "/", schema
    yield from _walk_properties(schema, "")
    for name, node in schema.get("$defs", {}).items():
        base = f"/$defs/{name}"
        yield base, node
        yield from _walk_properties(node, base)
        if name == "node":
            for variant in REF_VARIANTS:
                yield f"{base}/ref/{variant}", _ref_variant(node, variant)


def _walk_properties(node: dict[str, Any], prefix: str) -> Iterator[tuple[str, dict[str, Any]]]:
    for key, child in node.get("properties", {}).items():
        pointer = f"{prefix}/properties/{key}"
        yield pointer, child
        if "$ref" in child:
            continue
        yield from _walk_properties(child, pointer)
        items = child.get("items")
        if isinstance(items, dict) and "$ref" not in items:
            yield from _walk_properties(items, f"{pointer}/items")


def _ref_variant(node_def: dict[str, Any], kind: str) -> dict[str, Any]:
    """Récupérer la contrainte de ``ref`` attachée à un ``kind`` donné."""
    for branch in node_def.get("allOf", []):
        condition = branch.get("if", {}).get("properties", {}).get("kind", {})
        if condition.get("const") == kind:
            return branch.get("then", {}).get("properties", {}).get("ref", {})
    raise KeyError(f"aucune contrainte de ref pour kind={kind}")


def check_gloss(schema: dict[str, Any], gloss: dict[str, dict[str, str]]) -> list[str]:
    """Comparer les pointeurs du schéma et ceux de la glose, dans les deux sens."""
    pointers = {pointer for pointer, _ in walk_schema(schema)}
    glossed = set(gloss)
    problems = []
    for missing in sorted(pointers - glossed):
        problems.append(f"champ du schéma sans glose française : {missing}")
    for orphan in sorted(glossed - pointers):
        problems.append(f"glose sans champ correspondant dans le schéma : {orphan}")
    for pointer in sorted(pointers & glossed):
        if not (gloss[pointer] or {}).get("texte", "").strip():
            problems.append(f"glose vide : {pointer}")
    return problems


# ──────────────────────────────── rendu ────────────────────────────────────


def _anchor(text: str) -> str:
    return text.strip().lower()


def family_slug(family: dict[str, Any]) -> str:
    """Dériver le nom de page d'une famille depuis son nom.

    Le champ `slug` du catalogue porte encore la numérotation des répertoires
    du dépôt d'origine (`03-gouvernance-controles`), et deux familles y
    partagent la même valeur — Gouvernance et Modèles vivaient dans le même
    fichier. Un nom de page dérivé du nom de famille est unique, lisible dans
    l'URL, et ne traîne pas une arborescence disparue.
    """
    normalised = unicodedata.normalize("NFKD", family["name"])
    ascii_only = normalised.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", ascii_only)).strip("-")


def pattern_doc_path(family: dict[str, Any], pattern_id: str) -> str:
    """L'emplacement d'un pattern dans le manuel, tel que mkdocs le sert."""
    return f"{BASE}/patterns/{family_slug(family)}/#{_anchor(pattern_id)}"


def _type_of(node: dict[str, Any]) -> str:
    if "enum" in node:
        return " · ".join(f"`{v}`" for v in node["enum"])
    if "const" in node:
        return f"`{node['const']}`"
    if "$ref" in node:
        return f"[`{node['$ref'].rsplit('/', 1)[-1]}`](#{node['$ref'].rsplit('/', 1)[-1].lower()})"
    kind = node.get("type")
    return f"`{kind}`" if kind else "—"


def _constraints(node: dict[str, Any]) -> str:
    bits = []
    if "pattern" in node:
        bits.append(f"motif `{node['pattern']}`")
    for key, label in (("minimum", "min"), ("maximum", "max"), ("minItems", "min. éléments"),
                       ("minLength", "long. min")):
        if key in node:
            bits.append(f"{label} {node[key]}")
    if "default" in node:
        bits.append(f"défaut `{node['default']}`")
    return ", ".join(bits) or "—"


def render_format(schema: dict[str, Any], gloss: dict[str, dict[str, str]]) -> str:
    root_gloss = gloss["/"]
    out = [
        "# Format de fichier `.blueprint.json`",
        "",
        GENERATED_HEADER.format(source="`schemas/blueprint-v1.schema.json`"),
        "",
        root_gloss["texte"],
        "",
        "## Racine",
        "",
        _fields_table(schema, "", gloss, schema.get("required", [])),
        "",
    ]
    order = ["node", "pin", "edge", "gatePolicy", "resiliencePolicy", "evalSuite", "identifier"]
    defs = schema["$defs"]
    for name in order + [n for n in defs if n not in order]:
        node = defs[name]
        pointer = f"/$defs/{name}"
        entry = gloss[pointer]
        title = entry.get("titre") or name
        out += [f"## {title} {{: #{name.lower()} }}", "", entry["texte"], ""]
        if node.get("properties"):
            out += [_fields_table(node, pointer, gloss, node.get("required", [])), ""]
        if name == "node":
            out += ["### Formes de `ref` selon `kind`", ""]
            for variant in REF_VARIANTS:
                ventry = gloss[f"{pointer}/ref/{variant}"]
                out += [f"**{ventry['titre']}** — {ventry['texte']}", ""]
    return "\n".join(out)


def _fields_table(
    node: dict[str, Any], prefix: str, gloss: dict[str, dict[str, str]], required: list[str]
) -> str:
    rows = ["| Champ | Type | Obligation | Contraintes | Rôle |", "| --- | --- | --- | --- | --- |"]
    for key, child in node.get("properties", {}).items():
        pointer = f"{prefix}/properties/{key}"
        text = gloss[pointer]["texte"].replace("\n", " ").strip()
        obligation = "**requis**" if key in required else "facultatif"
        rows.append(
            f"| `{key}` | {_type_of(child)} | {obligation} | {_constraints(child)} | {text} |"
        )
        # Les sous-objets sont rendus dans la même table, préfixés — un blueprint
        # n'imbrique jamais assez profond pour justifier une table par niveau.
        rows += _nested_rows(child, pointer, gloss, key)
    return "\n".join(rows)


def _nested_rows(
    node: dict[str, Any], prefix: str, gloss: dict[str, dict[str, str]], path: str
) -> list[str]:
    rows: list[str] = []
    if "$ref" in node:
        return rows
    for key, child in node.get("properties", {}).items():
        pointer = f"{prefix}/properties/{key}"
        text = gloss[pointer]["texte"].replace("\n", " ").strip()
        req = key in node.get("required", [])
        rows.append(
            f"| `{path}.{key}` | {_type_of(child)} | "
            f"{'**requis**' if req else 'facultatif'} | {_constraints(child)} | {text} |"
        )
        rows += _nested_rows(child, pointer, gloss, f"{path}.{key}")
    items = node.get("items")
    if isinstance(items, dict) and "$ref" not in items:
        rows += _nested_rows(items, f"{prefix}/items", gloss, f"{path}[]")
    return rows


def render_primitive(name: str, spec: dict[str, Any], xxl: dict[str, Any]) -> str:
    cases = sorted(case for case, m in xxl.items() if m["primitive"] == name)
    out = [
        f"# {name}",
        "",
        GENERATED_HEADER.format(source="`src/grimoire/tools/blueprint_primitives.py`"),
        "",
        "| | |",
        "| --- | --- |",
        f"| **Fait du travail** | {'oui' if spec['doesWork'] else 'non'} |",
        f"| **Compile vers** | {spec['compilesTo']} |",
        f"| **Cases de palette** | {len(cases)} |",
        "",
        spec["role"],
        "",
    ]
    if spec["doesWork"]:
        out += [
            "!!! warning \"La seule primitive qui produit\"",
            "    `Unit` est le seul rôle qui consomme des contrats pour en produire.",
            "    Les six autres organisent, contraignent ou désignent — aucun ne",
            "    transforme. Si votre flow n'a pas de `Unit`, il ne fait rien.",
            "",
        ]
    if cases:
        out += ["## Cases de palette portées par cette primitive", "",
                "| Case | Paramètres |", "| --- | --- |"]
        for case in cases:
            params = xxl[case].get("params") or {}
            rendered = ", ".join(f"`{k}` = `{v}`" for k, v in params.items()) or "—"
            out.append(f"| `{case}` | {rendered} |")
        out += ["",
                "Ces cases ne sont pas des types de node : ce sont des configurations",
                f"de `{name}`. Le fichier ne connaît que le rôle.", ""]
    return "\n".join(out)


def render_palette(primitives: dict[str, Any], xxl: dict[str, Any]) -> str:
    out = [
        "# Palette — les cases et leur primitive",
        "",
        GENERATED_HEADER.format(source="`src/grimoire/tools/blueprint_primitives.py`"),
        "",
        f"La palette expose {len(xxl)} cases pour {len(primitives)} primitives. Ce n'est pas un",
        "bestiaire : chaque case est un paramétrage d'un des sept rôles. Comprendre",
        "les sept suffit à comprendre la palette entière.",
        "",
        "| Case | Primitive | Paramètres |",
        "| --- | --- | --- |",
    ]
    for case in sorted(xxl):
        mapping = xxl[case]
        primitive = mapping["primitive"]
        params = mapping.get("params") or {}
        rendered = ", ".join(f"`{k}` = `{v}`" for k, v in params.items()) or "—"
        out.append(f"| `{case}` | [{primitive}]({primitive.lower()}.md) | {rendered} |")
    out.append("")
    return "\n".join(out)


def render_contract(contract: dict[str, Any]) -> str:
    fields = contract.get("fields") or []
    required = [f for f in fields if f.get("obligation") == "required"]
    out = [
        f"# {contract['name']}",
        "",
        GENERATED_HEADER.format(source="`web/data/catalogue-export.json`"),
        "",
        f"Identifiant du contrat : `{contract['id']}`. "
        f"{len(fields)} champs, dont {len(required)} obligatoires.",
        "",
        "Un pin qui porte ce contrat ne peut être relié qu'à un pin qui porte",
        "exactement le même. C'est ce qui rend une edge vérifiable avant toute",
        "exécution.",
        "",
        "| Champ | Obligation | Rôle |",
        "| --- | --- | --- |",
    ]
    for field in fields:
        obligation = "**requis**" if field.get("obligation") == "required" else "facultatif"
        out.append(f"| `{field['name']}` | {obligation} | {field.get('role', '—')} |")
    out.append("")
    return "\n".join(out)


def render_contracts_index(contracts: list[dict[str, Any]]) -> str:
    out = [
        "# Contrats",
        "",
        GENERATED_HEADER.format(source="`web/data/catalogue-export.json`"),
        "",
        f"Les {len(contracts)} contrats sont le système de types du graphe. Un pin en",
        "déclare un ; une edge ne relie que deux pins qui déclarent le même. Une",
        "divergence est une erreur bloquante, relevée à la validation — pas à",
        "l'exécution.",
        "",
        "| Contrat | Id | Champs |",
        "| --- | --- | --- |",
    ]
    for contract in sorted(contracts, key=lambda c: c["id"]):
        n = len(contract.get("fields") or [])
        out.append(f"| [{contract['name']}]({contract['id']}.md) | `{contract['id']}` | {n} |")
    out.append("")
    return "\n".join(out)


def render_family(
    family: dict[str, Any], patterns: list[dict[str, Any]], relations: list[dict[str, Any]]
) -> str:
    out = [
        f"# {family['name']}",
        "",
        GENERATED_HEADER.format(source="`web/data/catalogue-export.json`"),
        "",
        family.get("description", ""),
        "",
        f"{len(patterns)} patterns dans cette famille.",
        "",
    ]
    by_source: dict[str, list[dict[str, Any]]] = {}
    for relation in relations:
        by_source.setdefault(relation["from"], []).append(relation)
    for pattern in patterns:
        pid = pattern["id"]
        out += [
            f"## {pid} — {pattern['name']} {{: #{_anchor(pid)} }}",
            "",
            f"**Intention.** {pattern['intent']}",
            "",
            f"**Problème.** {pattern['problem']}",
            "",
            f"**Solution.** {pattern['solution']}",
            "",
            f"**Maturité.** {pattern.get('maturity', '—')}",
            "",
        ]
        controls = pattern.get("controls") or []
        if controls:
            out += ["**Contrôles.** " + ", ".join(f"`{c}`" for c in controls), ""]
        if pattern.get("antiPattern"):
            out += [f"**À ne pas faire.** {pattern['antiPattern']}", ""]
        links = by_source.get(pid) or []
        if links:
            rendered = ", ".join(
                f"{r['kind']} → `{r['to']}`" + (f" ({r['label']})" if r.get("label") else "")
                for r in links
            )
            out += [f"**Relations.** {rendered}", ""]
    return "\n".join(out)


def render_patterns_index(
    families: list[dict[str, Any]], patterns: list[dict[str, Any]]
) -> str:
    by_family = {f["id"]: f for f in families}
    out = [
        "# Patterns",
        "",
        GENERATED_HEADER.format(source="`web/data/catalogue-export.json`"),
        "",
        f"{len(patterns)} patterns répartis sur {len(families)} familles. Un node de "
        "`kind: pattern`",
        "porte l'id de l'un d'eux dans son `ref`.",
        "",
        "| Id | Pattern | Famille | Maturité |",
        "| --- | --- | --- | --- |",
    ]
    for pattern in sorted(patterns, key=lambda p: p["id"]):
        family = by_family[pattern["family"]]
        link = f"{family_slug(family)}.md#{_anchor(pattern['id'])}"
        out.append(
            f"| `{pattern['id']}` | [{pattern['name']}]({link}) | "
            f"{family['name']} | {pattern.get('maturity', '—')} |"
        )
    out.append("")
    return "\n".join(out)


def render_anti_patterns(
    anti_patterns: list[dict[str, Any]], patterns: list[dict[str, Any]],
    families: list[dict[str, Any]]
) -> str:
    by_id = {p["id"]: p for p in patterns}
    by_family = {f["id"]: f for f in families}
    out = [
        "# Anti-patterns",
        "",
        GENERATED_HEADER.format(source="`web/data/catalogue-export.json`"),
        "",
        f"{len(anti_patterns)} pièges recensés, chacun rattaché aux patterns qui le",
        "corrigent. Un anti-pattern n'est pas une erreur de débutant : c'est ce vers",
        "quoi un flow dérive naturellement quand personne ne l'en empêche.",
        "",
    ]
    for anti in sorted(anti_patterns, key=lambda a: a["id"]):
        out += [
            f"## {anti['name']} {{: #{_anchor(anti['id'])} }}",
            "",
            anti.get("description", ""),
            "",
        ]
        related = [pid for pid in (anti.get("patterns") or []) if pid in by_id]
        if related:
            links = ", ".join(
                f"[`{pid}`](patterns/{family_slug(by_family[by_id[pid]['family']])}.md"
                f"#{_anchor(pid)})"
                for pid in related
            )
            out += [f"**Corrigé par.** {links}", ""]
    return "\n".join(out)


def render_index(
    primitives: dict[str, Any], catalogue: dict[str, Any], xxl: dict[str, Any]
) -> str:
    return "\n".join([
        "# Référence du système nodal",
        "",
        GENERATED_HEADER.format(source="les sources du dépôt"),
        "",
        "Tout ce qu'un blueprint peut contenir, et ce que chaque élément fait.",
        "",
        "| Section | Contenu |",
        "| --- | --- |",
        f"| [Primitives](unit.md) | les {len(primitives)} rôles sémantiques |",
        f"| [Palette](palette.md) | les {len(xxl)} cases et leur primitive |",
        f"| [Contrats](contrats/index.md) | les {len(catalogue['contracts'])} types échangés |",
        f"| [Patterns](patterns/index.md) | les {len(catalogue['patterns'])} patterns du "
        f"catalogue |",
        f"| [Anti-patterns](anti-patterns.md) | les {len(catalogue['antiPatterns'])} dérives "
        f"connues |",
        "| [Format de fichier](format-fichier.md) | la structure de `.blueprint.json` |",
        "",
        f"Catalogue en version `{catalogue['catalogVersion']}`.",
        "",
    ])


def render_summary(
    primitives: dict[str, Any], catalogue: dict[str, Any]
) -> str:
    lines = [
        "* [Vue d'ensemble](index.md)",
        "* Primitives",
    ]
    lines += [f"    * [{name}]({name.lower()}.md)" for name in primitives]
    lines += [
        "* [Palette](palette.md)",
        "* [Contrats](contrats/index.md)",
    ]
    lines += [
        f"    * [{c['name']}](contrats/{c['id']}.md)"
        for c in sorted(catalogue["contracts"], key=lambda c: c["id"])
    ]
    lines.append("* [Patterns](patterns/index.md)")
    lines += [
        f"    * [{f['name']}](patterns/{family_slug(f)}.md)"
        for f in catalogue["families"]
    ]
    lines += ["* [Anti-patterns](anti-patterns.md)", "* [Format de fichier](format-fichier.md)"]
    return "\n".join(lines) + "\n"


# ──────────────────────────────── pilotage ─────────────────────────────────


def build(write: Callable[[str, str], None]) -> list[str]:
    """Rendre toutes les pages. Retourne la liste des chemins produits."""
    primitives, xxl = load_primitives()
    schema = load_schema()
    gloss = load_gloss()
    catalogue = load_catalogue()

    problems = check_gloss(schema, gloss) + check_catalogue(catalogue)
    if problems:
        raise SystemExit(
            "référence du système nodal — la source et la doc ont divergé :\n  - "
            + "\n  - ".join(problems)
        )

    written: list[str] = []

    def emit(path: str, text: str) -> None:
        write(f"{BASE}/{path}", text)
        written.append(f"{BASE}/{path}")

    emit("index.md", render_index(primitives, catalogue, xxl))
    for name, spec in primitives.items():
        emit(f"{name.lower()}.md", render_primitive(name, spec, xxl))
    emit("palette.md", render_palette(primitives, xxl))
    emit("format-fichier.md", render_format(schema, gloss))

    emit("contrats/index.md", render_contracts_index(catalogue["contracts"]))
    for contract in catalogue["contracts"]:
        emit(f"contrats/{contract['id']}.md", render_contract(contract))

    emit("patterns/index.md", render_patterns_index(catalogue["families"], catalogue["patterns"]))
    for family in catalogue["families"]:
        members = [p for p in catalogue["patterns"] if p["family"] == family["id"]]
        emit(f"patterns/{family_slug(family)}.md",
             render_family(family, members, catalogue["relations"]))

    emit("anti-patterns.md",
         render_anti_patterns(catalogue["antiPatterns"], catalogue["patterns"],
                              catalogue["families"]))
    emit("SUMMARY.md", render_summary(primitives, catalogue))
    return written


def check_catalogue(catalogue: dict[str, Any]) -> list[str]:
    """Vérifier que le catalogue est atteignable en entier depuis la référence."""
    problems = []
    families = {f["id"]: f for f in catalogue["families"]}
    slugs = [family_slug(f) for f in catalogue["families"]]
    if len(set(slugs)) != len(slugs):
        problems.append("deux familles produisent le même nom de page — elles se recouvriraient")
    for pattern in catalogue["patterns"]:
        if pattern["family"] not in families:
            problems.append(
                f"pattern `{pattern['id']}` rattaché à une famille inconnue "
                f"`{pattern['family']}` — il n'apparaîtrait sur aucune page"
            )
    pattern_ids = {p["id"] for p in catalogue["patterns"]}
    if len(pattern_ids) != len(catalogue["patterns"]):
        problems.append("deux patterns partagent le même id — les ancres se recouvriraient")
    contract_ids = {c["id"] for c in catalogue["contracts"]}
    if len(contract_ids) != len(catalogue["contracts"]):
        problems.append("deux contrats partagent le même id — les pages se recouvriraient")
    for pattern in catalogue["patterns"]:
        family = families.get(pattern["family"])
        if family is None:
            continue
        expected = pattern_doc_path(family, pattern["id"])
        if pattern.get("docPath") != expected:
            problems.append(
                f"pattern `{pattern['id']}` : docPath vaut "
                f"{pattern.get('docPath')!r}, attendu {expected!r}"
            )
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="vérifier la couverture sans rien écrire")
    parser.add_argument("--out", type=Path,
                        help="écrire les pages dans ce répertoire (mise au point)")
    args = parser.parse_args()

    if args.check:
        problems = check_gloss(load_schema(), load_gloss()) + check_catalogue(load_catalogue())
        for problem in problems:
            print(f"- {problem}", file=sys.stderr)
        if problems:
            print(f"{len(problems)} écart(s) entre la source et la référence", file=sys.stderr)
            return 1
        print("référence du système nodal : source et documentation alignées")
        return 0

    if args.out:
        def to_disk(path: str, text: str) -> None:
            target = args.out / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        written = build(to_disk)
        print(f"{len(written)} pages écrites dans {args.out}")
        return 0

    parser.error("préciser --check ou --out")
    return 2


def _emit_to_mkdocs() -> None:
    """Écrire les pages dans le système de fichiers virtuel de mkdocs."""
    import mkdocs_gen_files

    def to_mkdocs(path: str, text: str) -> None:
        with mkdocs_gen_files.open(path, "w") as handle:
            handle.write(text)

    build(to_mkdocs)


if __name__ == "__main__":
    raise SystemExit(main())

# mkdocs-gen-files exécute ce fichier par `runpy.run_path`, qui pose
# `__name__ = "<run_path>"` — pas `"__main__"`. C'est la seule façon de
# distinguer ce contexte d'un simple import (les tests importent ce module pour
# appeler les vérifications sans rien rendre).
if __name__ == "<run_path>":
    _emit_to_mkdocs()
