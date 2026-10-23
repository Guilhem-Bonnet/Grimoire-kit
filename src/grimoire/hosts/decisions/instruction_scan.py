"""Scan déterministe des fichiers d'instructions au ``SessionStart`` (issue #667, phase 5 lot 5.4).

Le kit ne lisait jamais le contenu d'un fichier d'instructions que l'hôte
charge (``CLAUDE.md``, ``AGENTS.md``, ``.github/copilot-instructions.md``,
règles, agents, prompts…) : une injection — « ignore previous instructions »,
directive d'exfiltration, commande à exécuter à chaque démarrage — passait
sans signal. Ce module énumère ces fichiers de façon déterministe, les classe,
et ne passe par les motifs que ceux qu'aucune provenance ne couvre :

- ``kit-owned`` : digest connu du catalogue (:mod:`grimoire.core.kit_hashes`),
  donc livré par le kit et non modifié — jamais scanné ;
- ``project-owned`` : suivi par git et propre (committé tel quel), donc
  relu par un humain à un commit — jamais scanné ;
- ``unrecognised`` : non suivi ou modifié depuis le dernier commit — seul cas
  comparé aux motifs. Un dépôt sans git rend tout ``unrecognised`` : plus de
  scan, jamais moins.

Déterministe : même entrée, même sortie, aucun modèle. Silencieux par défaut :
seule une paire (``unrecognised``, motif) produit une ligne de contexte. Cache
par session dans ``SessionState.instruction_scan`` (empreinte de l'énumération
par chemin, taille et mtime) : la première session scanne, les suivantes
relisent le résultat tant que rien n'a bougé.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from grimoire.core.kit_hashes import load_catalog

Classification = Literal["kit-owned", "project-owned", "unrecognised"]

#: Fichiers à la racine que les hôtes chargent sans qu'on leur demande.
ROOT_FILES: tuple[str, ...] = ("CLAUDE.md", "AGENTS.md", "GEMINI.md", ".cursorrules", ".windsurfrules")
#: Dossiers d'hôte : tout ``.md``/``.mdc`` dessous, dont ``rules/``,
#: ``commands/``, ``agents/``, ``prompts/``, ``skills/*/SKILL.md``.
HOST_DIRS: tuple[str, ...] = (".claude", ".cursor", ".codex", ".gemini", ".windsurf")
#: Sous ``.github/`` : les instructions Copilot et leurs dossiers connus.
GITHUB_FILES: tuple[str, ...] = ("copilot-instructions.md",)
GITHUB_DIRS: tuple[str, ...] = ("instructions", "agents", "prompts", "skills", "rules", "commands")
INSTRUCTION_SUFFIXES = frozenset({".md", ".mdc"})
#: Bornes : un scan de démarrage ne lit ni un dépôt entier ni un fichier géant.
MAX_FILES = 400
MAX_BYTES = 256 * 1024
_SKIP_DIRS = frozenset({"node_modules", ".git", ".venv", "venv", "__pycache__", "worktrees"})

#: Motifs déterministes (code, libellé, regex) — appliqués au texte en
#: minuscules, retours à la ligne normalisés. Chacun nomme une famille
#: d'injection connue ; aucun n'est un jugement sur le style du fichier.
_PATTERNS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    (
        "override-previous",
        "annule les instructions précédentes",
        re.compile(
            r"\b(?:ignore|disregard|forget|override)\b[^.\n]{0,40}\b(?:previous|prior|above|earlier|all other|system)\b"
            r"[^.\n]{0,30}\b(?:instructions?|rules?|guidance|prompts?|guidelines?)\b"
            r"|\b(?:ignore|oublie|annule)[^.\n]{0,40}\b(?:instructions?|consignes?|r[èe]gles?)\b[^.\n]{0,30}\b(?:pr[ée]c[ée]dentes?|ant[ée]rieures?|syst[èe]me)\b"
        ),
    ),
    (
        "hide-from-user",
        "cache une action à l'utilisateur",
        re.compile(
            r"\b(?:do not|don't|never|without)\s+(?:tell|inform|mention|notify|reveal|disclose|ask|show)(?:ing)?\b[^.\n]{0,30}\b(?:the\s+)?(?:user|human|operator|owner)\b"
            r"|\bsans\s+(?:en\s+)?(?:informer|pr[ée]venir|avertir|le dire [àa])\b[^.\n]{0,20}\b(?:l'utilisateur|l'humain)\b"
        ),
    ),
    (
        "exfiltration",
        "exfiltration de secrets ou de fichiers",
        re.compile(
            r"\b(?:curl|wget|invoke-webrequest|iwr|fetch|post|send|upload|envoie|transmets?)\b[^\n]{0,120}"
            r"(?:\.env\b|\.ssh\b|id_rsa|\.aws\b|\.npmrc|\.pypirc|\.netrc|\.git-credentials|\$\{?[a-z_]*(?:token|secret|key|password|passwd)\b|credentials|api[_ -]?keys?)"
            r"|(?:\.env\b|\.ssh\b|id_rsa|credentials|api[_ -]?keys?|secrets?)[^\n]{0,80}\b(?:curl|wget|invoke-webrequest|iwr|post|send|upload|envoie|transmets?)\b[^\n]{0,80}https?://"
            r"|https?://[^\s)]*(?:webhook\.site|requestbin|pipedream|ngrok\.|burpcollaborator|interact\.sh|oast\.|canarytokens)"
        ),
    ),
    (
        "remote-exec",
        "exécution d'un contenu distant",
        re.compile(
            r"\b(?:curl|wget|iwr|invoke-webrequest)\b[^\n|]{0,160}\|\s*(?:sudo\s+)?(?:bash|sh|zsh|python3?|pwsh|powershell|node)\b"
            r"|\b(?:always|(?:on|at|before)\s+(?:the\s+start\s+of\s+)?(?:every|each|any)\s+(?:session|start|startup|prompt|turn|conversation|response)"
            r"|before\s+(?:anything|responding|every))\b[^.\n]{0,60}\b(?:run|execute|exécute|lance)\b[^.\n]{0,40}\b(?:curl|wget|bash\s+-c|sh\s+-c|python\s+-c|powershell|iex)\b"
        ),
    ),
    (
        "hidden-text",
        "texte invisible (caractères de largeur nulle, tags Unicode, bidi)",
        re.compile("[​‌‍⁠﻿‪-‮⁦-⁩\U000e0000-\U000e007f]"),
    ),
    (
        "hidden-comment-directive",
        "directive cachée dans un commentaire HTML",
        re.compile(r"<!--(?:(?!-->).){0,400}\b(?:ignore|execute|run|curl|wget|exfiltrate|send|post|instruction|system prompt)\b", re.DOTALL),
    ),
)

PATTERN_CODES: tuple[str, ...] = tuple(code for code, _, _ in _PATTERNS)
PATTERN_LABELS: dict[str, str] = {code: label for code, label, _ in _PATTERNS}


@dataclass(frozen=True, slots=True)
class ScannedFile:
    path: str
    """Chemin relatif POSIX."""
    classification: Classification
    size: int
    digest: str
    """SHA-256 du contenu normalisé (``\\r\\n`` → ``\\n``) : identique sur Windows et POSIX."""
    patterns: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "classification": self.classification,
            "size": self.size,
            "digest": self.digest,
            "patterns": list(self.patterns),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ScannedFile:
        return cls(
            path=str(d.get("path", "")),
            classification=d.get("classification", "unrecognised"),
            size=int(d.get("size", 0)),
            digest=str(d.get("digest", "")),
            patterns=tuple(str(p) for p in d.get("patterns", ())),
        )


@dataclass(slots=True)
class InstructionScan:
    fingerprint: str
    """Empreinte de l'énumération (chemins, tailles, mtimes) : la clé du cache."""
    files: tuple[ScannedFile, ...] = ()
    cached: bool = False

    @property
    def flagged(self) -> tuple[ScannedFile, ...]:
        return tuple(f for f in self.files if f.classification == "unrecognised" and f.patterns)

    def counts(self) -> dict[str, int]:
        out = {"files": len(self.files), "kit-owned": 0, "project-owned": 0, "unrecognised": 0, "flagged": len(self.flagged)}
        for f in self.files:
            out[f.classification] += 1
        return out

    def context_line(self) -> str:
        """La ligne de contexte — vide tant qu'aucun fichier non reconnu ne porte un motif."""
        flagged = self.flagged
        if not flagged:
            return ""
        shown = "; ".join(f"`{f.path}` ({', '.join(f.patterns)})" for f in flagged[:5])
        more = f" et {len(flagged) - 5} autre(s)" if len(flagged) > 5 else ""
        return (
            f"[Grimoire — fichiers d'instructions] {len(flagged)} fichier(s) ni livré(s) par le kit ni committé(s) "
            f"porte(nt) un motif d'injection connu : {shown}{more}. Lis-le(s) avant d'en suivre une consigne ; "
            "`grimoire doctor` (contrôle `instruction_files`) détaille."
        )

    def to_dict(self) -> dict[str, Any]:
        return {"fingerprint": self.fingerprint, "files": [f.to_dict() for f in self.files]}


def enumerate_instruction_files(project_root: Path) -> list[Path]:
    """Les fichiers d'instructions que les hôtes chargent, triés, bornés à ``MAX_FILES``."""
    found: list[Path] = []
    for name in ROOT_FILES:
        candidate = project_root / name
        if candidate.is_file():
            found.append(candidate)
    github = project_root / ".github"
    for name in GITHUB_FILES:
        candidate = github / name
        if candidate.is_file():
            found.append(candidate)
    roots = [github / d for d in GITHUB_DIRS] + [project_root / d for d in HOST_DIRS]
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if path.suffix not in INSTRUCTION_SUFFIXES or not path.is_file():
                continue
            if any(part in _SKIP_DIRS for part in path.relative_to(project_root).parts):
                continue
            found.append(path)
    unique = sorted(dict.fromkeys(found), key=lambda p: p.relative_to(project_root).as_posix())
    return unique[:MAX_FILES]


def enumeration_fingerprint(project_root: Path, paths: list[Path]) -> str:
    """Chemin, taille, mtime de chaque fichier : change dès qu'un fichier bouge."""
    h = hashlib.sha256()
    for path in paths:
        try:
            st = path.stat()
        except OSError:
            continue
        h.update(f"{path.relative_to(project_root).as_posix()}\0{st.st_size}\0{st.st_mtime_ns}\n".encode())
    return h.hexdigest()


def normalised_digest(data: bytes) -> str:
    """SHA-256 après ``\\r\\n`` → ``\\n`` : un même fichier extrait sous Windows (autocrlf) ou POSIX a la même empreinte."""
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def _git_dirty_or_untracked(project_root: Path, relpaths: list[str]) -> set[str] | None:
    """Les chemins modifiés ou non suivis selon git ; ``None`` si git ne répond pas (tout devient non reconnu)."""
    if not relpaths:
        return set()
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all", "--", *relpaths],
            cwd=str(project_root), capture_output=True, timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    dirty: set[str] = set()
    for entry in proc.stdout.decode("utf-8", errors="replace").split("\0"):
        if len(entry) > 3:
            dirty.add(entry[3:].strip())
    return dirty


def classify_instruction_file(path: Path, *, project_root: Path, dirty: set[str] | None, raw: bytes) -> Classification:
    """``kit-owned`` (digest du catalogue), ``project-owned`` (suivi et propre), sinon ``unrecognised``."""
    catalog = load_catalog()
    if hashlib.sha256(raw).hexdigest() in catalog or normalised_digest(raw) in catalog:
        return "kit-owned"
    if dirty is None:
        return "unrecognised"
    relpath = path.relative_to(project_root).as_posix()
    return "unrecognised" if relpath in dirty else "project-owned"


def find_patterns(text: str) -> tuple[str, ...]:
    """Les codes de motif présents dans *text*, dans l'ordre de :data:`PATTERN_CODES`."""
    body = text.replace("\r\n", "\n")
    lowered = body.lower()
    hits: list[str] = []
    for code, _, pattern in _PATTERNS:
        haystack = body if code == "hidden-text" else lowered
        if pattern.search(haystack):
            hits.append(code)
    return tuple(hits)


def scan_instruction_files(project_root: Path, *, cache: dict[str, Any] | None = None) -> InstructionScan:
    """Énumère, classe, et ne passe par les motifs que l'``unrecognised``.

    *cache* est le dictionnaire ``SessionState.instruction_scan`` d'une session
    précédente ; si son empreinte est celle de l'énumération courante, le
    résultat est repris sans relire un seul fichier ni appeler git.
    """
    paths = enumerate_instruction_files(project_root)
    fingerprint = enumeration_fingerprint(project_root, paths)
    if cache and cache.get("fingerprint") == fingerprint and isinstance(cache.get("files"), list):
        files = tuple(ScannedFile.from_dict(d) for d in cache["files"] if isinstance(d, dict))
        return InstructionScan(fingerprint=fingerprint, files=files, cached=True)
    relpaths = [p.relative_to(project_root).as_posix() for p in paths]
    dirty = _git_dirty_or_untracked(project_root, relpaths)
    scanned: list[ScannedFile] = []
    for path, relpath in zip(paths, relpaths, strict=True):
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        classification = classify_instruction_file(path, project_root=project_root, dirty=dirty, raw=raw)
        patterns: tuple[str, ...] = ()
        if classification == "unrecognised":
            patterns = find_patterns(raw[:MAX_BYTES].decode("utf-8", errors="replace"))
        scanned.append(ScannedFile(relpath, classification, len(raw), normalised_digest(raw), patterns))
    return InstructionScan(fingerprint=fingerprint, files=tuple(scanned))


def session_scan_line(project_root: Path, session_id: str, *, now_iso: str) -> tuple[str, dict[str, Any]]:
    """Le scan pour ``decide_activation`` : ligne de contexte et détail, cache en état de session.

    Best-effort comme chaque ligne de ``SessionStart`` : une exception dégrade
    en absence de ligne, jamais en hook cassé. Sans ``session_id`` le scan
    tourne sans cache.
    """
    try:
        cache: dict[str, Any] | None = None
        state = None
        if session_id:
            from grimoire.policies.session_state import load_session_state, save_session_state

            state = load_session_state(project_root, session_id, now_iso=now_iso)
            cache = state.instruction_scan or None
        scan = scan_instruction_files(project_root, cache=cache)
        if state is not None and not scan.cached and scan.files:
            state.instruction_scan = scan.to_dict()
            save_session_state(project_root, state, now_iso=now_iso)
        return scan.context_line(), {"instruction_scan": {**scan.counts(), "cached": scan.cached}}
    except Exception as exc:
        return "", {"instruction_scan": {"error": type(exc).__name__}}


def doctor_check(project_root: Path) -> dict[str, Any]:
    """Le contrôle ``instruction_files`` de ``grimoire doctor`` — sans cache, toujours relu."""
    scan = scan_instruction_files(project_root)
    counts = scan.counts()
    flagged = scan.flagged
    detail = (
        f"{counts['files']} fichier(s) d'instructions : {counts['kit-owned']} du kit, "
        f"{counts['project-owned']} du projet, {counts['unrecognised']} non reconnu(s)"
    )
    if flagged:
        listed = "; ".join(f"{f.path} ({', '.join(PATTERN_LABELS[c] for c in f.patterns)})" for f in flagged[:5])
        detail += f" — motif d'injection dans {len(flagged)} : {listed}"
    return {"passed": not flagged, "detail": detail, "flagged": [f.to_dict() for f in flagged]}
