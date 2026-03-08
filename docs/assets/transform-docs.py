#!/usr/bin/env python3
"""Transform all markdown docs to match README design system.

Applies:
1. SVG icons on H1 and H2 headers
2. SVG dividers between major sections
3. Emoji removal (outside code blocks)
4. Navigation breadcrumb at top
"""

import os
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
ICONS_DIR = PROJECT_ROOT / "docs" / "assets" / "icons"

# --- Icon assignment for H1 (by filename) ---
FILE_H1_ICON = {
    "concepts.md": "lightbulb",
    "getting-started.md": "bolt",
    "onboarding.md": "handshake",
    "vscode-setup.md": "wrench",
    "troubleshooting.md": "microscope",
    "creating-agents.md": "team",
    "archetype-guide.md": "puzzle",
    "memory-system.md": "brain",
    "workflow-design-patterns.md": "workflow",
    "workflow-taxonomy.md": "workflow",
    "adr-001-no-multi-llm.md": "clipboard",
    "CONTRIBUTING.md": "handshake",
    "CHANGELOG.md": "chart",
    "agent-base.md": "hexagon",
    "agent-base-compact.md": "hexagon",
    "agent2agent.md": "network",
    "agent-mesh-network.md": "network",
    "agent-relationship-graph.md": "network",
    "agent-rules.md": "clipboard",
    "grimoire-trace.md": "microscope",
    "cc-reference.md": "seal",
    "context-router.md": "boomerang",
    "cross-validation-trust.md": "seal",
    "event-log-shared-state.md": "workflow",
    "honest-uncertainty-protocol.md": "shield-pulse",
    "hybrid-parallelism-engine.md": "cognition",
    "orchestrator-gateway.md": "boomerang",
    "productive-conflict-engine.md": "cognition",
    "question-escalation-chain.md": "lightbulb",
    "selective-huddle-protocol.md": "team",
    "grimoire-mcp-server.md": "server",
    "boomerang-orchestration.md": "boomerang",
    "incident-response.md": "shield-pulse",
    "repo-map-generator.md": "folder-tree",
    "state-checkpoint.md": "branch",
    "subagent-orchestration.md": "team",
    "workflow-status.md": "workflow",
    "vectus.md": "brain",
}

# --- Keyword → icon for H2 headers (first match wins) ---
H2_KEYWORD_MAP = [
    # Specific multi-word first
    ("quick start", "bolt"),
    ("getting started", "bolt"),
    ("premier jour", "bolt"),
    ("premier pas", "bolt"),
    ("première semaine", "bolt"),
    ("premier mois", "bolt"),
    ("vue d'ensemble", "grimoire"),
    ("self-healing", "resilience"),
    ("anti-fragil", "resilience"),
    ("cross-validat", "seal"),
    ("repo map", "folder-tree"),
    ("completion contract", "seal"),
    ("mise en garde", "shield-pulse"),
    ("structure du projet", "folder-tree"),
    ("structure créée", "folder-tree"),
    ("design pattern", "workflow"),
    ("intent analy", "cognition"),
    ("prompt enrich", "cognition"),
    ("result aggregat", "cognition"),
    ("agent relation", "network"),
    ("plan/act", "cognition"),
    # Single keywords
    ("démarrage", "bolt"),
    ("installation", "bolt"),
    ("installer", "bolt"),
    ("prérequis", "clipboard"),
    ("prerequis", "clipboard"),
    ("pourquoi", "grimoire"),
    ("overview", "grimoire"),
    ("architecture", "temple"),
    ("arborescence", "folder-tree"),
    ("feature", "sparkle"),
    ("fonctionnalit", "sparkle"),
    ("ajouté", "sparkle"),
    ("nouveaut", "sparkle"),
    ("troubleshoot", "wrench"),
    ("diagnostic", "microscope"),
    ("debug", "microscope"),
    ("symptôme", "microscope"),
    ("résolution", "microscope"),
    ("cause", "microscope"),
    ("test", "flask"),
    ("smoke", "flask"),
    ("validation", "seal"),
    ("vérifié", "seal"),
    ("vérifier", "seal"),
    ("certif", "seal"),
    ("qualité", "seal"),
    ("changelog", "chart"),
    ("historique", "chart"),
    ("performance", "chart"),
    ("benchmark", "chart"),
    ("métrique", "chart"),
    ("score", "chart"),
    ("statistique", "chart"),
    ("contribu", "handshake"),
    ("bienvenue", "handshake"),
    ("archétype", "puzzle"),
    ("archetype", "puzzle"),
    ("composant", "puzzle"),
    ("workflow", "workflow"),
    ("processus", "workflow"),
    ("pipeline", "workflow"),
    ("pattern", "workflow"),
    ("boucle", "workflow"),
    ("orchestrat", "boomerang"),
    ("gateway", "boomerang"),
    ("boomerang", "boomerang"),
    ("dispatch", "boomerang"),
    ("routing", "boomerang"),
    ("router", "boomerang"),
    ("agent", "team"),
    ("persona", "team"),
    ("équipe", "team"),
    ("team", "team"),
    ("huddle", "team"),
    ("subagent", "team"),
    ("mémoire", "brain"),
    ("memory", "brain"),
    ("contexte", "brain"),
    ("context", "brain"),
    ("embedding", "brain"),
    ("qdrant", "brain"),
    ("sémantique", "brain"),
    ("cogni", "cognition"),
    ("raisonnement", "cognition"),
    ("thinking", "cognition"),
    ("uncertain", "cognition"),
    ("incertitude", "cognition"),
    ("paralleli", "cognition"),
    ("hybrid", "cognition"),
    ("dna", "dna"),
    ("évolution", "dna"),
    ("mutation", "dna"),
    ("conflit", "cognition"),
    ("adversarial", "cognition"),
    ("débat", "cognition"),
    ("challenger", "cognition"),
    ("productif", "cognition"),
    ("sécurité", "shield-pulse"),
    ("security", "shield-pulse"),
    ("trust", "shield-pulse"),
    ("honest", "shield-pulse"),
    ("incident", "shield-pulse"),
    ("response", "shield-pulse"),
    ("immune", "resilience"),
    ("résilience", "resilience"),
    ("config", "wrench"),
    ("setup", "wrench"),
    ("setting", "wrench"),
    ("paramètre", "wrench"),
    ("modifier", "wrench"),
    ("outil", "wrench"),
    ("tool", "wrench"),
    ("option", "wrench"),
    ("cli", "wrench"),
    ("commande", "wrench"),
    ("protocole", "hexagon"),
    ("protocol", "hexagon"),
    ("règle", "clipboard"),
    ("format", "clipboard"),
    ("convention", "clipboard"),
    ("référence", "clipboard"),
    ("reference", "clipboard"),
    ("adr", "clipboard"),
    ("décision", "clipboard"),
    ("decision", "clipboard"),
    ("registr", "folder-tree"),
    ("mcp", "server"),
    ("serveur", "server"),
    ("server", "server"),
    ("api", "plug"),
    ("extension", "plug"),
    ("plugin", "plug"),
    ("intégration", "integration"),
    ("réseau", "network"),
    ("network", "network"),
    ("mesh", "network"),
    ("session", "moon"),
    ("checkpoint", "branch"),
    ("version", "branch"),
    ("corrigé", "wrench"),
    ("fix", "wrench"),
    ("concept", "lightbulb"),
    ("principe", "lightbulb"),
    ("pilier", "lightbulb"),
    ("question", "lightbulb"),
    ("faq", "lightbulb"),
    ("inspir", "lightbulb"),
    ("astuce", "lightbulb"),
    ("conseil", "lightbulb"),
    ("exemple", "rocket"),
    ("example", "rocket"),
    ("demo", "rocket"),
    ("usage", "rocket"),
    ("innovation", "flask"),
    ("r&d", "flask"),
    ("incubat", "flask"),
    ("expérim", "flask"),
    ("log", "clipboard"),
    ("trace", "microscope"),
    ("state", "brain"),
    ("event", "workflow"),
    ("mode", "cognition"),
    ("module", "puzzle"),
    ("sortie", "chart"),
    ("output", "chart"),
    ("résultat", "chart"),
]

# --- Emoji replacement map ---
EMOJI_REPLACEMENTS = {
    "✅": "&#x2713;",
    "❌": "&#x2717;",
    "✔": "&#x2713;",
    "☐": "&#x2610;",
    "⚠️": "**Attention**",
    "⚠": "**Attention**",
    "➡️": "→",
    "➡": "→",
    "❗": "—",
    "❓": "?",
}

# Emojis to simply remove (decorative, not semantic)
EMOJI_REMOVE_PATTERN = re.compile(
    r"[\U0001F300-\U0001F5FF"  # Misc Symbols and Pictographs
    r"\U0001F600-\U0001F64F"   # Emoticons
    r"\U0001F680-\U0001F6FF"   # Transport and Map
    r"\U0001F700-\U0001F77F"   # Alchemical
    r"\U0001F780-\U0001F7FF"   # Geometric Extended
    r"\U0001F800-\U0001F8FF"   # Supplemental Arrows-C
    r"\U0001F900-\U0001F9FF"   # Supplemental Symbols
    r"\U0001FA00-\U0001FA6F"   # Chess
    r"\U0001FA70-\U0001FAFF"   # Symbols Extended-A
    r"\u2600-\u26FF"           # Misc symbols
    r"\u2700-\u27BF"           # Dingbats
    r"\u2300-\u23FF"           # Misc Technical
    r"\uFE0F"                  # Variation Selector
    r"]+"
)


def compute_rel_path(from_file: Path, to_file: Path) -> str:
    """Compute relative path from one file to another."""
    return os.path.relpath(to_file, from_file.parent)


def icon_tag(from_file: Path, icon_name: str, size: int = 28) -> str:
    """Generate <img> tag for an icon."""
    icon_path = ICONS_DIR / f"{icon_name}.svg"
    rel = compute_rel_path(from_file, icon_path)
    return f'<img src="{rel}" width="{size}" height="{size}" alt="">'


def divider_tag(from_file: Path) -> str:
    """Generate divider <img> tag."""
    divider_path = PROJECT_ROOT / "docs" / "assets" / "divider.svg"
    rel = compute_rel_path(from_file, divider_path)
    return f'<img src="{rel}" width="100%" alt="">'


def pick_h2_icon(title: str) -> str:
    """Pick icon for an H2 header based on keyword matching."""
    title_lower = title.lower()
    for keyword, icon in H2_KEYWORD_MAP:
        if keyword in title_lower:
            return icon
    return "hexagon"


def pick_h1_icon(filepath: Path) -> str:
    """Pick icon for H1 based on filename."""
    name = filepath.name
    return FILE_H1_ICON.get(name, "grimoire")


def strip_leading_emoji(text: str) -> str:
    """Remove leading emoji from a header title."""
    # Remove leading emojis + variation selectors + spaces
    cleaned = text.lstrip()
    while cleaned:
        ch = cleaned[0]
        cp = ord(ch)
        if (
            0x1F300 <= cp <= 0x1FAFF
            or 0x2600 <= cp <= 0x27BF
            or 0x2300 <= cp <= 0x23FF
            or cp == 0xFE0F
        ):
            cleaned = cleaned[1:].lstrip()
        else:
            break
    return cleaned


def has_icon_already(line: str) -> bool:
    """Check if a header line already has an <img> icon."""
    return "<img " in line and "svg" in line


def readme_link(from_file: Path) -> str:
    """Generate a link back to README."""
    readme_path = PROJECT_ROOT / "README.md"
    rel = compute_rel_path(from_file, readme_path)
    return rel


def process_markdown(filepath: Path) -> str:
    """Process a single markdown file."""
    content = filepath.read_text(encoding="utf-8")
    lines = content.split("\n")
    result = []
    in_code_block = False
    h1_found = False
    prev_was_divider_or_blank = False
    h2_count = 0

    for i, line in enumerate(lines):
        # Track code blocks
        if line.strip().startswith("```"):
            in_code_block = not in_code_block
            result.append(line)
            continue

        if in_code_block:
            result.append(line)
            continue

        # --- Process headers ---
        h1_match = re.match(r"^(# )(.+)$", line)
        h2_match = re.match(r"^(## )(.+)$", line)

        if h1_match and not h1_found and not has_icon_already(line):
            h1_found = True
            title = strip_leading_emoji(h1_match.group(2))
            icon_name = pick_h1_icon(filepath)
            tag = icon_tag(filepath, icon_name, 32)
            result.append(f"# {tag} {title}")
            continue

        if h2_match and not has_icon_already(line):
            h2_count += 1
            title = strip_leading_emoji(h2_match.group(2))
            icon_name = pick_h2_icon(title)
            tag = icon_tag(filepath, icon_name)

            # Add divider before H2 (except the first one right after H1)
            if h2_count > 1:
                # Remove trailing blank lines before inserting divider
                while result and result[-1].strip() == "":
                    result.pop()
                # Check if we already have a --- that we'll be replacing
                if result and result[-1].strip() == "---":
                    result.pop()
                # Remove more trailing blanks
                while result and result[-1].strip() == "":
                    result.pop()
                result.append("")
                result.append(divider_tag(filepath))
                result.append("")

            result.append(f"## {tag} {title}")
            continue

        # Replace standalone --- with divider (only between sections)
        if line.strip() == "---":
            # Check if this is between sections (has content before and after)
            # We'll skip it here since we add dividers before H2 headers
            # Only keep --- if it's right after H1 (as a subtitle separator)
            if h2_count == 0 and h1_found:
                # This is the first --- after H1, keep as visual separator
                # but replace with divider
                while result and result[-1].strip() == "":
                    result.pop()
                result.append("")
                result.append(divider_tag(filepath))
                result.append("")
            # Otherwise skip (we add dividers before H2 already)
            continue

        # --- Replace emojis in non-code text ---
        processed_line = line
        for emoji, replacement in EMOJI_REPLACEMENTS.items():
            processed_line = processed_line.replace(emoji, replacement)
        # Remove remaining decorative emojis
        processed_line = EMOJI_REMOVE_PATTERN.sub("", processed_line)
        # Clean up double spaces left by emoji removal
        processed_line = re.sub(r"  +", " ", processed_line)
        # Clean up ` — ` or ` | ` at start of cells where emoji was removed
        processed_line = re.sub(r"\| +\| ", "| ", processed_line)

        result.append(processed_line)

    output = "\n".join(result)

    # Clean up excessive blank lines (max 2 consecutive)
    output = re.sub(r"\n{4,}", "\n\n\n", output)

    return output


def add_nav_header(content: str, filepath: Path) -> str:
    """Add navigation breadcrumb at top of doc."""
    readme_rel = readme_link(filepath)
    
    # Don't add nav to CHANGELOG (it's a reference doc)
    if filepath.name == "CHANGELOG.md":
        return content

    # Build breadcrumb based on location
    parts = filepath.relative_to(PROJECT_ROOT).parts
    
    if filepath.name == "CONTRIBUTING.md":
        nav = f'<p align="right"><a href="{readme_rel}">README</a></p>\n\n'
    elif "docs" in parts:
        nav = f'<p align="right"><a href="{readme_rel}">README</a></p>\n\n'
    elif "framework" in parts:
        docs_rel = compute_rel_path(filepath, PROJECT_ROOT / "docs")
        nav = f'<p align="right"><a href="{readme_rel}">README</a> · <a href="{docs_rel}">Docs</a></p>\n\n'
    elif "archetypes" in parts:
        nav = f'<p align="right"><a href="{readme_rel}">README</a></p>\n\n'
    elif "examples" in parts:
        nav = f'<p align="right"><a href="{readme_rel}">README</a></p>\n\n'
    else:
        nav = f'<p align="right"><a href="{readme_rel}">README</a></p>\n\n'

    return nav + content


def get_target_files() -> list[Path]:
    """Get all markdown files to process."""
    files = []
    for pattern in [
        "CONTRIBUTING.md",
        "CHANGELOG.md",
        "docs/*.md",
        "framework/*.md",
        "framework/mcp/*.md",
        "framework/tools/README.md",
        "framework/copilot-extension/README.md",
        "framework/registry/README.md",
        "framework/sessions/README.md",
        "framework/workflows/*.md",
        "archetypes/fix-loop/README.md",
        "archetypes/features/vector-memory/vectus.md",
        "examples/*/README.md",
    ]:
        found = list(PROJECT_ROOT.glob(pattern))
        files.extend(found)

    # Exclude already-processed README.md and template files
    files = [
        f
        for f in files
        if f.name != "README.md" or f.parent != PROJECT_ROOT
    ]
    # Exclude template files
    files = [f for f in files if not f.name.endswith(".tpl.md")]
    # Exclude workflow-status which is a template
    files = [f for f in files if "workflow-status" not in f.name]

    return sorted(set(files))


def main():
    files = get_target_files()
    print(f"Processing {len(files)} files...\n")

    for filepath in files:
        rel = filepath.relative_to(PROJECT_ROOT)
        try:
            original = filepath.read_text(encoding="utf-8")
            transformed = process_markdown(filepath)
            transformed = add_nav_header(transformed, filepath)

            if transformed != original:
                filepath.write_text(transformed, encoding="utf-8")
                # Count changes
                orig_lines = set(original.split("\n"))
                new_lines = set(transformed.split("\n"))
                changed = len(new_lines - orig_lines)
                print(f"  &#x2713; {rel} ({changed} lines changed)")
            else:
                print(f"  - {rel} (no changes)")
        except Exception as e:
            print(f"  ERROR {rel}: {e}")

    print(f"\nDone. {len(files)} files processed.")


if __name__ == "__main__":
    main()
