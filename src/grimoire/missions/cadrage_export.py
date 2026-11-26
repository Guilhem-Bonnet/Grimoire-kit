"""L'export portable d'un chantier de cadrage (#1342, #1197, décision D13 du portail de connaissance).

Un lot de fichiers Markdown à en-tête YAML (``name`` / ``description`` / ``type``), reliés par des liens relatifs
et un ``index.md`` : la forme de l'Open Knowledge Format v0.1, lisible sans Grimoire. OKF est ici un format
d'**échange** branché sur l'existant, pas un moteur ni un magasin : le lot ne se relit pas dans le kit (pas
d'import), il se montre, se transmet, se vérifie.

Le lot ::

    index.md                  titre, état, date, version du kit, sommaire lié
    phases/<phase>.md         les cinq phases, corps tel quel
    requirements/REQ-nnn.md   une fiche par exigence (validation MOA, tâches, fichiers touchés, recette)
    decisions.md              pistes écartées, questions ouvertes, suggestions tranchées
    manifest.json             grimoire-cadrage-export/v1 : empreinte SHA-256 de chaque fichier et du lot

**Lecture seule sur le projet** : cadrage, ledger, journal d'autorité (via :mod:`.cadrage_trace`) et magasin des
suggestions sont lus, jamais écrits. La seule écriture est celle du dossier de sortie.

**Sécurité** : tout texte passe par ``redact_secrets`` et perd les caractères de contrôle ; un chemin absolu de la
machine (racine du projet, HOME, ``/home/<nom>``) est remplacé ; les chemins de fichiers restent relatifs au dépôt ;
les noms de fichiers du lot viennent d'identifiants validés (phases du kit, ``REQ-<chiffres>``), jamais d'un titre ;
la sortie est confinée (``resolve()`` + ``is_relative_to``), sans lien symbolique, en écriture atomique.

**Déterminisme** : tout est trié, aucun horodatage hors la date passée à :func:`build_export` (``index.md`` et
``manifest.json``). Deux exports du même état, à la même date, sont identiques à l'octet près.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from grimoire.core import atelier_suggestions as store
from grimoire.core.cadrage import DEFAULT_CHANTIER, PHASES, PLACEHOLDER, cadrage_dir
from grimoire.core.cadrage_interview import read_regular
from grimoire.core.cadrage_requirements import REQ_ID, Requirement, read_requirements
from grimoire.core.chantiers import read_chantier
from grimoire.core.fsio import atomic_write
from grimoire.core.regular_file import open_regular
from grimoire.memory.validation import redact_secrets
from grimoire.missions import cadrage_trace as ct
from grimoire.missions import moa_acceptance as moa
from grimoire.missions import requirement_gate as rg

if TYPE_CHECKING:
    from grimoire.missions.service import TaskService

__all__ = [
    "SCHEMA",
    "ExportError",
    "ExportResult",
    "VerifyResult",
    "build_export",
    "scrub",
    "verify_export",
    "write_export",
]

SCHEMA = "grimoire-cadrage-export/v1"
MANIFEST = "manifest.json"
MAX_FILE_BYTES = 262_144
#: Un nom de fichier du lot : jamais dérivé d'un titre, toujours d'un identifiant validé.
_NAME = re.compile(
    r"^(?:index\.md|decisions\.md|phases/(?:brief|brainstorm|comprehension|exigences|cahier-des-charges)\.md"
    r"|requirements/REQ-[0-9]{1,4}\.md)$"
)
_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(?P<text>\S.*)$")
_HOME_PATH = re.compile(r"(?:/home|/Users)/[^\s/\"')\]]+|[A-Za-z]:\\Users\\[^\s\\\"')\]]+")
_FRONT = re.compile(r"\A---\r?\n.*?\r?\n---\r?\n?", re.DOTALL)
_DECIDED = ("acceptee", "refusee")
_DECIDED_LABELS = {"acceptee": "acceptées", "refusee": "refusées"}
_SOURCE_PHASE = {"04-exigences.md": "exigences", "05-cahier-des-charges.md": "cahier-des-charges"}


class ExportError(ValueError):
    """Une sortie refusée (dossier non vide, lien symbolique, date invalide…) : le message dit pourquoi."""


@dataclass(frozen=True, slots=True)
class ExportResult:
    """Le lot construit : ``files`` (chemin relatif -> octets, manifeste compris) et son empreinte globale."""

    chantier: str
    files: dict[str, bytes]
    digest: str
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "chantier": self.chantier,
            "digest": self.digest,
            "files": sorted(self.files),
            "notes": list(self.notes),
        }


@dataclass(frozen=True, slots=True)
class VerifyResult:
    """Le verdict d'une vérification : ``ok`` seulement si chaque fichier du manifeste est intact et rien d'autre."""

    ok: bool
    problems: tuple[str, ...] = ()
    digest: str = ""
    checked: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"schema": SCHEMA, "ok": self.ok, "digest": self.digest, "checked": self.checked,
                "problems": list(self.problems)}


# ── texte sûr ───────────────────────────────────────────────────────────────


def scrub(text: object, root: Path | None = None) -> str:
    """Le texte sans contrôle, caviardé des secrets, sans chemin absolu de la machine ni nom d'utilisateur de chemin."""
    out, _ = redact_secrets(rg.safe_text(text, newlines=True).replace("\r\n", "\n"))
    prefixes: set[str] = set()
    if root is not None:
        prefixes.update({str(root), str(root.resolve())})
    prefixes.add(str(Path.home()))
    for prefix in sorted((p for p in prefixes if len(p) > 1), key=len, reverse=True):
        out = out.replace(prefix, "~" if prefix == str(Path.home()) else ".")
    return _HOME_PATH.sub("~", out)


def _line(text: object, root: Path | None = None) -> str:
    return " ".join(scrub(text, root).split())


def _yaml(value: object) -> str:
    """Un scalaire YAML valide : ``json.dumps`` (guillemets doubles, échappements), sur une seule ligne."""
    return json.dumps(value, ensure_ascii=False)


def _front(fields: dict[str, object]) -> str:
    return "---\n" + "".join(f"{key}: {_yaml(value)}\n" for key, value in fields.items()) + "---\n\n"


def _rel_file(path: str, root: Path) -> str:
    """Un chemin de fichier touché, relatif au dépôt ; un chemin absolu ou sortant est remplacé par son seul nom."""
    text = path.replace("\\", "/")
    if text.startswith("/") or re.match(r"^[A-Za-z]:", text) or ".." in text.split("/"):
        return _line(text.rsplit("/", 1)[-1], root)
    return _line(text, root)


# ── lecture des sources ─────────────────────────────────────────────────────


def _read_phase(project: Path, chantier: str | None, filename: str) -> str | None:
    try:
        return read_regular(cadrage_dir(project, chantier) / filename, limit=MAX_FILE_BYTES)
    except (OSError, ValueError):
        return None


def _brainstorm_items(text: str) -> dict[str, list[str]]:
    """Les puces des sections « Questions ouvertes » et « Écarté (et pourquoi) » ; le gabarit vide est ignoré."""
    wanted = {PHASES[1].sections[1]: "questions", PHASES[1].sections[2]: "ecartes"}
    out: dict[str, list[str]] = {"ecartes": [], "questions": []}
    kind = ""
    for line in text.splitlines():
        if line.startswith("## "):
            kind = wanted.get(line[3:].strip(), "")
            continue
        match = _BULLET.match(line) if kind else None
        if match and not match.group("text").startswith(PLACEHOLDER):
            out[kind].append(match.group("text").strip())
    return out


# ── les fichiers du lot ─────────────────────────────────────────────────────


def _phase_page(phase_id: str, title: str, state: str, text: str, root: Path) -> str:
    body = _FRONT.sub("", text.replace("\r\n", "\n"), count=1).strip("\n")
    head = _front({
        "name": phase_id, "description": _line(title), "type": "phase", "etat": state,
    })
    return head + scrub(body, root) + "\n"


def _mentions(req: ct.RequirementTrace | None, other: Requirement | None, known: set[str]) -> list[str]:
    """Les autres exigences citées dans le texte d'une fiche (titre et critères) : les liens entre fiches."""
    texts = [req.title, *req.criteria] if req else ([other.title, *other.criteria] if other else [])
    own = req.key.rsplit("/", 1)[-1] if req else (other.id if other else "")
    return sorted({m for t in texts for m in REQ_ID.findall(t) if m in known and m != own})


def _requirement_page(
    req_id: str, trace: ct.RequirementTrace | None, plain: Requirement | None, links: list[str],
    phases: set[str], chantier: str, root: Path,
) -> str:
    title = _line(trace.title if trace else (plain.title if plain else ""), root) or req_id
    priority = (trace.priority if trace else (plain.priority if plain else "")) or "non précisée"
    drift = trace.drift if trace else None
    if trace:
        state, validated = trace.stage_label, trace.validated
    else:
        state, validated = "hors plan (hors périmètre ou Won't)", False
    fields: dict[str, object] = {
        "name": req_id, "description": title, "type": "requirement", "chantier": chantier,
        "priorite": priority, "etat": state,
        "validation_moa": "validée" if validated else "non validée",
    }
    if trace and validated:
        fields["valide_par"] = _line(trace.validated_by, root)
        fields["valide_le"] = _line(trace.validated_at, root)
    if drift:
        fields["modifiee_depuis_la_validation"] = True
    out = [_front(fields), f"# {req_id} — {title}\n\n"]
    where = [f"[{p}](../phases/{p}.md)" for p in ("exigences", "cahier-des-charges") if p in phases]
    if where:
        out.append("Phases : " + " · ".join(where) + "\n\n")
    criteria = trace.criteria if trace else (tuple(plain.criteria) if plain else ())
    out.append("## Critères d'acceptation\n\n")
    out.append("".join(f"- {_line(c, root)}\n" for c in criteria) or "_aucun critère dans le cadrage_\n")
    if trace:
        out.append(_validation_block(trace, drift, root))
        out.append(_tasks_block(trace, root))
        out.append(_files_block(trace, root))
        out.append(_recette_block(trace, root))
    if links:
        out.append("\n## Fiches liées\n\n" + "".join(f"- [{r}]({r}.md)\n" for r in links))
    return "".join(out)


def _validation_block(trace: ct.RequirementTrace, drift: ct.Drift | None, root: Path) -> str:
    out = ["\n## Validation MOA\n\n"]
    if trace.validated:
        out.append(f"Validée par {_line(trace.validated_by, root)} le {_line(trace.validated_at, root)}.\n")
    else:
        out.append("Non validée" + (f" : {_line(trace.reason, root)}" if trace.reason else "") + ".\n")
    if drift:
        out.append(
            f"\n**Modifiée depuis la validation** ({_line(drift.validated_at, root)}, "
            f"par {_line(drift.moa, root)}) : {drift.what}.\n"
        )
    return "".join(out)


def _tasks_block(trace: ct.RequirementTrace, root: Path) -> str:
    out = ["\n## Tâches liées\n\n"]
    rows = []
    for task in sorted(trace.tasks, key=lambda t: t.id):
        pr = f" ; PR #{task.pr}" if task.pr else ""
        done = "livrée" if task.delivered else "à faire ou en cours"
        rows.append(f"- `{_line(task.id, root)}` {_line(task.title, root)} — {_line(task.status, root)} ({done}){pr}\n")
    out.append("".join(rows) or "_aucune tâche ne sert cette exigence_\n")
    return "".join(out)


def _files_block(trace: ct.RequirementTrace, root: Path) -> str:
    tests = set(trace.tests)
    names = sorted({_rel_file(f, root) for f in trace.files})
    rows = "".join(f"- `{n}`{' (test)' if n in tests else ''}\n" for n in names)
    return "\n## Fichiers touchés\n\n" + (rows or "_aucun fichier connu_\n")


def _recette_block(trace: ct.RequirementTrace, root: Path) -> str:
    out = ["\n## Recette\n\n"]
    verdict = trace.verdict
    if verdict:
        out.append(f"Dernier verdict : {_line(verdict.get('verdict'), root)}")
        if verdict.get("motif"):
            out.append(f" — {_line(verdict.get('motif'), root)}")
        if verdict.get("ts"):
            out.append(f" ({_line(verdict.get('ts'), root)})")
        out.append("\n")
    else:
        out.append(f"Aucune recette prononcée (état : {_line(moa.STATE_LABELS.get(trace.state, trace.state), root)}).\n")
    for gap in trace.gaps:
        out.append(f"- Trou : {_line(gap.label, root)} — {_line(gap.detail, root)}\n")
    return "".join(out)


def _decisions_page(project: Path, chantier: str | None, root: Path, notes: list[str]) -> str:
    text = _read_phase(project, chantier, PHASES[1].filename) or ""
    items = _brainstorm_items(text)
    out = [_front({"name": "decisions", "description": "Pistes écartées, questions ouvertes et suggestions tranchées",
                   "type": "decisions"}), "# Décisions\n\n## Pistes écartées\n\n"]
    out.append("".join(f"- {_line(t, root)}\n" for t in items["ecartes"]) or "_aucune piste écartée consignée_\n")
    out.append("\n## Questions ouvertes\n\n")
    out.append("".join(f"- {_line(t, root)}\n" for t in items["questions"]) or "_aucune question ouverte consignée_\n")
    suggestions = _suggestions(project, chantier, notes)
    if suggestions is None:
        return "".join(out)
    for etat in _DECIDED:
        rows = sorted((s for s in suggestions if s.etat == etat), key=lambda s: (s.decide_le, s.id))
        out.append(f"\n## Suggestions {_DECIDED_LABELS[etat]}\n\n")
        for s in rows:
            motif = f" Motif de la décision : {_line(s.decide_motif, root)}" if s.decide_motif else ""
            why = f" Motif du dépôt : {_line(s.motif, root)}." if s.motif else ""
            out.append(
                f"- [{_line(s.phase, root)}] {_line(s.texte, root)} (décidée le {_line(s.decide_le, root)}).{why}{motif}\n"
            )
        if not rows:
            out.append("_aucune_\n")
    return "".join(out)


def _suggestions(project: Path, chantier: str | None, notes: list[str]) -> list[store.Suggestion] | None:
    """Les suggestions du magasin ; ``None`` s'il n'existe pas (la section est alors omise)."""
    path = store.store_path(cadrage_dir(project, chantier))
    try:
        if not path.exists() and not path.is_symlink():
            return None
        if path.is_symlink() or not stat.S_ISREG(path.lstat().st_mode):
            notes.append("magasin des suggestions ignoré : ce n'est pas un fichier ordinaire")
            return None
        items, ignored, damaged = store.read(path.parent)
    except (OSError, store.StoreError):
        notes.append("magasin des suggestions illisible : section omise")
        return None
    if damaged is not None or ignored:
        notes.append("magasin des suggestions abîmé : seules les lignes valides sont exportées")
    return items


def _index_page(
    slug: str, titre: str, etat: str, date: str, version: str, pages: dict[str, str], notes: list[str],
    reqs: list[tuple[str, str, str]], root: Path,
) -> str:
    title = _line(titre, root) or slug
    out = [_front({
        "name": slug, "description": f"Cadrage {title} : lot exporté de la spec vivante", "type": "index",
        "chantier": slug, "etat": etat, "genere": date, "version_kit": version, "format": SCHEMA,
    }), f"# {title}\n\n", f"- État du chantier : {_line(etat)}\n", f"- Exporté le : {date}\n",
        f"- Version du kit : {version}\n", f"- Intégrité : `{MANIFEST}` ({SCHEMA})\n\n", "## Sommaire\n\n", "### Phases\n\n"]
    for phase in PHASES:
        if f"phases/{phase.id}.md" in pages:
            out.append(f"- [{_line(phase.title)}](phases/{phase.id}.md)\n")
        else:
            out.append(f"- {_line(phase.title)} — absente\n")
    out.append("\n### Exigences\n\n")
    out.append("".join(f"- [{r}](requirements/{r}.md) — {t} ({s})\n" for r, t, s in reqs) or "_aucune exigence identifiée_\n")
    out.append("\n### Décisions\n\n- [Pistes écartées, questions ouvertes, suggestions](decisions.md)\n")
    if notes:
        out.append("\n## Réserves\n\n" + "".join(f"- {_line(n, root)}\n" for n in notes))
    return "".join(out)


# ── le lot ──────────────────────────────────────────────────────────────────


def _digest(entries: list[dict[str, Any]]) -> str:
    h = hashlib.sha256()
    for entry in entries:
        h.update(f"{entry['path']}\0{entry['sha256']}\n".encode())
    return h.hexdigest()


def build_export(
    root: Path, service: TaskService, chantier: str | None = None, *, date: str | None = None,
    version: str | None = None,
) -> ExportResult:
    """Construit le lot en mémoire. Lecture seule : cadrage, ledger, journal d'autorité, suggestions, git."""
    from grimoire import __version__

    stamp = date or datetime.now(UTC).date().isoformat()
    if not _DATE.match(stamp):
        raise ExportError(f"date invalide : {stamp!r} (AAAA-MM-JJ attendu)")
    project = rg.origin_root(root)
    slug = None if chantier in (None, DEFAULT_CHANTIER) else chantier
    info = read_chantier(project, slug)
    if info.error:
        raise ExportError(f"chantier {info.slug!r} : {info.error}")
    notes: list[str] = []
    pages: dict[str, str] = {}
    for phase in PHASES:
        text = _read_phase(project, slug, phase.filename)
        if text is not None:
            from grimoire.core.cadrage import phase_report

            state = str(phase_report(project, phase, slug)["state"])
            pages[f"phases/{phase.id}.md"] = _phase_page(phase.id, phase.title, state, text, project)
    trace = ct.build_trace(root, service, slug)
    notes.extend(_line(n, project) for n in trace.notes)
    found = {r.id: r for r in read_requirements(project, slug).requirements}
    traced = {t.key.rsplit("/", 1)[-1]: t for t in trace.requirements}
    known = set(found) | set(traced)
    phases = {p.removeprefix("phases/").removesuffix(".md") for p in pages}
    rows: list[tuple[str, str, str]] = []
    for req_id in sorted(known, key=lambda r: (int(r.split("-")[1]), r)):
        t, plain = traced.get(req_id), found.get(req_id)
        links = _mentions(t, plain, known)
        pages[f"requirements/{req_id}.md"] = _requirement_page(req_id, t, plain, links, phases, info.slug, project)
        title = _line(t.title if t else (plain.title if plain else ""), project) or req_id
        rows.append((req_id, title, t.stage_label if t else "hors plan"))
    pages["decisions.md"] = _decisions_page(project, slug, project, notes)
    pages["index.md"] = _index_page(info.slug, info.titre, info.etat, stamp, version or __version__, pages, notes,
                                    rows, project)
    files = {name: text.encode("utf-8") for name, text in pages.items()}
    entries = [
        {"path": n, "sha256": hashlib.sha256(files[n]).hexdigest(), "bytes": len(files[n])} for n in sorted(files)
    ]
    digest = _digest(entries)
    manifest = {
        "schema": SCHEMA, "chantier": info.slug, "generated": stamp, "kit_version": version or __version__,
        "format": "okf/0.1", "files": entries, "digest": digest,
    }
    files[MANIFEST] = (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    return ExportResult(info.slug, files, digest, tuple(notes))


# ── écriture et vérification, confinées ─────────────────────────────────────


def _confined(base: Path, rel: str) -> Path:
    """Le chemin de ``rel`` sous ``base`` : nom validé, confiné après ``resolve()``, jamais à travers un lien."""
    if rel != MANIFEST and not _NAME.match(rel):
        raise ExportError(f"nom de fichier hors du lot : {rel!r}")
    target = (base / rel).resolve()
    if not target.is_relative_to(base):
        raise ExportError(f"{rel} sortirait du dossier de sortie (lien symbolique ?) : refusé")
    if target.is_symlink() or (target.exists() and not stat.S_ISREG(target.lstat().st_mode)):
        raise ExportError(f"{rel} existe et n'est pas un fichier ordinaire : refusé")
    return target


def write_export(result: ExportResult, out: Path, project: Path, *, force: bool = False) -> list[str]:
    """Écrit le lot dans ``out`` (atomique, fichier par fichier). Ne supprime rien : ``force`` n'écrase que les fichiers du lot.

    Refus : ``out`` est un lien symbolique ou un fichier, un dossier non vide sans ``force``, ou un emplacement
    sous le dossier de cadrage du projet (le cadrage reste intact)."""
    if out.is_symlink():
        raise ExportError("le dossier de sortie est un lien symbolique : refusé")
    base = out.resolve()
    if base.is_relative_to((project / "_grimoire" / "cadrage").resolve()):
        raise ExportError("le dossier de sortie ne peut pas être dans le dossier de cadrage du projet")
    if base.exists():
        if not base.is_dir():
            raise ExportError("le dossier de sortie existe et n'est pas un dossier")
        if any(base.iterdir()) and not force:
            raise ExportError("le dossier de sortie n'est pas vide — choisissez un dossier neuf, ou --force")
    targets = {rel: _confined(base, rel) for rel in sorted(result.files, key=lambda r: (r == MANIFEST, r))}
    for rel, target in targets.items():
        atomic_write(target, result.files[rel])
    return list(targets)


def _read_bounded(path: Path) -> bytes | None:
    try:
        with open_regular(path) as handle:
            return handle.read(MAX_FILE_BYTES * 8 + 1)
    except (OSError, ValueError):
        return None


def verify_export(out: Path) -> VerifyResult:
    """Relit ``manifest.json`` et dit si le lot est intact : empreintes, empreinte globale, rien de plus, rien de moins."""
    base = out.resolve()
    if out.is_symlink() or not base.is_dir():
        return VerifyResult(False, ("le dossier du lot est introuvable ou n'est pas un dossier ordinaire",))
    raw = _read_bounded(base / MANIFEST)
    if raw is None:
        return VerifyResult(False, (f"{MANIFEST} introuvable ou illisible",))
    try:
        manifest = json.loads(raw.decode("utf-8"))
        entries = manifest["files"]
        listed = {str(e["path"]): str(e["sha256"]) for e in entries}
        valid = manifest.get("schema") == SCHEMA
    except (ValueError, KeyError, TypeError, AttributeError):
        valid = False
    if not valid:
        return VerifyResult(False, (f"{MANIFEST} invalide (schéma {SCHEMA} attendu)",))
    problems: list[str] = []
    for rel, expected in sorted(listed.items()):
        try:
            target = _confined(base, rel)
        except ExportError as exc:
            problems.append(str(exc))
            continue
        data = _read_bounded(target)
        if data is None:
            problems.append(f"{rel} : manquant ou illisible")
        elif hashlib.sha256(data).hexdigest() != expected:
            problems.append(f"{rel} : altéré (empreinte différente)")
    if _digest([{"path": e["path"], "sha256": e["sha256"]} for e in entries if isinstance(e, dict)]) != manifest.get("digest"):
        problems.append("empreinte globale incohérente avec la liste des fichiers")
    problems.extend(_unlisted(base, set(listed)))
    return VerifyResult(not problems, tuple(problems), str(manifest.get("digest", "")), len(listed))


def _unlisted(base: Path, listed: set[str]) -> list[str]:
    extra: list[str] = []
    for current, dirs, names in os.walk(base, followlinks=False):
        here = Path(current)
        for name in (*dirs, *names):
            path = here / name
            rel = path.relative_to(base).as_posix()
            if path.is_symlink():
                extra.append(f"{rel} : lien symbolique dans le lot")
            elif name in names and rel not in listed and rel != MANIFEST:
                extra.append(f"{rel} : fichier absent du manifeste")
    return sorted(extra)
