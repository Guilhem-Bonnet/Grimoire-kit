from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from html import escape
from pathlib import Path
import posixpath
from typing import Any

from mkdocs.config.defaults import MkDocsConfig
from mkdocs.exceptions import PluginError
from mkdocs.structure.files import File, Files
from mkdocs.structure.pages import Page
from ruamel.yaml import YAML

_MONTHS_FR = {
    1: "janvier",
    2: "février",
    3: "mars",
    4: "avril",
    5: "mai",
    6: "juin",
    7: "juillet",
    8: "août",
    9: "septembre",
    10: "octobre",
    11: "novembre",
    12: "décembre",
}
_REGISTRY_FILE = Path("mkdocs_data/signals.yml")
_SIGNALS: tuple["SignalEntry", ...] = ()
_FILES_BY_SRC_URI: dict[str, File] = {}


@dataclass(frozen=True, slots=True)
class SignalEntry:
    title: str
    path: str
    date: date
    kicker: str
    summary: str
    featured: bool
    proof: bool


def on_config(config: MkDocsConfig) -> MkDocsConfig:
    config_root = Path(config.config_file_path).resolve().parent
    registry_path = config_root / _REGISTRY_FILE

    if not registry_path.is_file():
        raise PluginError(f"Signal registry not found: {registry_path}")

    global _SIGNALS
    _SIGNALS = _load_signals(registry_path, Path(config.docs_dir))
    return config


def on_files(files: Files, config: MkDocsConfig) -> Files:
    del config
    global _FILES_BY_SRC_URI
    _FILES_BY_SRC_URI = {file.src_uri: file for file in files}

    missing_files = [entry.path for entry in _SIGNALS if entry.path not in _FILES_BY_SRC_URI]
    if missing_files:
        missing_list = ", ".join(sorted(missing_files))
        raise PluginError(f"Signals registry references missing documentation files: {missing_list}")

    return files


def on_page_markdown(markdown: str, page: Page, config: MkDocsConfig, files: Files) -> str:
    del config, files

    replacements = {
        "{{ grimoire_signals_home }}": _render_home_signals(page),
        "{{ grimoire_signals_featured }}": _render_signal_blocks(page, featured_only=True),
        "{{ grimoire_signals_archive }}": _render_signal_blocks(page, featured_only=False),
        "{{ grimoire_signals_presentation }}": _render_presentation_strip(page),
    }

    for placeholder, content in replacements.items():
        if placeholder in markdown:
            markdown = markdown.replace(placeholder, content)

    return markdown


def _load_signals(registry_path: Path, docs_dir: Path) -> tuple[SignalEntry, ...]:
    yaml = YAML(typ="safe")
    raw_data = yaml.load(registry_path.read_text(encoding="utf-8")) or {}
    raw_signals = raw_data.get("signals")

    if not isinstance(raw_signals, list) or not raw_signals:
        raise PluginError(f"Signals registry must define a non-empty 'signals' list: {registry_path}")

    entries: list[SignalEntry] = []

    for index, raw_signal in enumerate(raw_signals, start=1):
        if not isinstance(raw_signal, dict):
            raise PluginError(f"Signals registry entry #{index} must be a mapping")

        title = _read_required_string(raw_signal, "title", index)
        path = _read_required_string(raw_signal, "path", index)
        kicker = _read_required_string(raw_signal, "kicker", index)
        summary = _read_required_string(raw_signal, "summary", index)
        signal_date = _parse_date(raw_signal.get("date"), index)
        featured = bool(raw_signal.get("featured", False))
        proof = bool(raw_signal.get("proof", False))

        if not (docs_dir / path).is_file():
            raise PluginError(f"Signals registry entry '{title}' points to a missing file: {path}")

        entries.append(
            SignalEntry(
                title=title,
                path=path,
                date=signal_date,
                kicker=kicker,
                summary=summary,
                featured=featured,
                proof=proof,
            )
        )

    sorted_entries = sorted(entries, key=lambda entry: (entry.date, entry.title.casefold()), reverse=True)
    return tuple(sorted_entries)


def _read_required_string(raw_signal: dict[str, Any], key: str, index: int) -> str:
    value = raw_signal.get(key)
    if not isinstance(value, str) or not value.strip():
        raise PluginError(f"Signals registry entry #{index} must define a non-empty '{key}' string")
    return value.strip()


def _parse_date(value: Any, index: int) -> date:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise PluginError(f"Signals registry entry #{index} has an invalid ISO date: {value}") from exc
    raise PluginError(f"Signals registry entry #{index} must define a 'date' field in ISO format")


def _render_home_signals(page: Page) -> str:
    featured_entries = [entry for entry in _SIGNALS if entry.featured][:4]
    lines = [
        f"- [{entry.title}]({_relative_source_path(page.file.src_uri, entry.path)}) — {entry.kicker}"
        for entry in featured_entries
    ]
    lines.extend(
        [
            "",
            f"[Voir tous les signaux]({_relative_source_path(page.file.src_uri, 'signaux.md')})",
        ]
    )
    return "\n".join(lines)


def _render_signal_blocks(page: Page, *, featured_only: bool) -> str:
    selected_entries = [entry for entry in _SIGNALS if entry.featured is featured_only]
    if not selected_entries:
        return "_Aucun signal supplémentaire pour le moment._"

    blocks: list[str] = []
    for entry in selected_entries:
        link = _relative_source_path(page.file.src_uri, entry.path)
        meta_parts = [_format_date(entry.date), entry.kicker]
        blocks.extend(
            [
                f"### [{entry.title}]({link})",
                "",
                f"*{' · '.join(meta_parts)}*",
                "",
                entry.summary,
                "",
            ]
        )

    return "\n".join(blocks).strip()


def _render_presentation_strip(page: Page) -> str:
    featured_entries = [entry for entry in _SIGNALS if entry.featured][:4]
    cards = [
        _render_presentation_card(page, entry)
        for entry in featured_entries
    ]
    return "\n".join(["<div class=\"gp-news-strip\">", *cards, "</div>"])


def _render_presentation_card(page: Page, entry: SignalEntry) -> str:
    href = _relative_route(page.file.url, _target_file(entry.path).url)
    classes = "gp-news-strip__card"
    if entry.proof:
        classes += " gp-news-strip__card--proof"

    meta = escape(f"Ajout · {entry.kicker}")
    title = escape(entry.title)
    return f'    <a class="{classes}" href="{href}"><span>{meta}</span><strong>{title}</strong></a>'


def _relative_source_path(current_src_uri: str, target_src_uri: str) -> str:
    current_dir = posixpath.dirname(current_src_uri) or "."
    return posixpath.relpath(target_src_uri, current_dir)


def _relative_route(current_url: str, target_url: str) -> str:
    current_dir = current_url if current_url.endswith("/") else posixpath.dirname(current_url)
    current_dir = current_dir or "."
    relative_url = posixpath.relpath(target_url, current_dir)
    if target_url.endswith("/") and not relative_url.endswith("/"):
        relative_url += "/"
    return relative_url


def _target_file(src_uri: str) -> File:
    target_file = _FILES_BY_SRC_URI.get(src_uri)
    if target_file is None:
        raise PluginError(f"Signals registry target not found in MkDocs files list: {src_uri}")
    return target_file


def _format_date(value: date) -> str:
    return f"{value.day} {_MONTHS_FR[value.month]} {value.year}"