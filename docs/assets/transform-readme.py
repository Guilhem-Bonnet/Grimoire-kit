#!/usr/bin/env python3
"""Transform README.md: replace all emojis with custom SVG icon references.

This script reads README.md, replaces every emoji with the appropriate
custom icon from the design system, and writes the result back.
"""

import re

ICON_BASE = "docs/assets/icons"

def icon(name, size=28):
    """Inline icon IMG tag."""
    return f'<img src="{ICON_BASE}/{name}.svg" width="{size}" height="{size}" alt="">'


def icon_center(name, size=36):
    """Centered icon for feature cards."""
    return f'<img src="{ICON_BASE}/{name}.svg" width="{size}" height="{size}" alt="">'


# Section header emoji → icon mapping
SECTION_MAP = {
    "🔮": "grimoire",
    "⚡": "bolt",
    "🏛": "temple",
    "✨": "sparkle",
    "🧩": "puzzle",
    "🔧": "wrench",
    "🔌": "plug",
    "🧪": "flask",
    "📊": "chart",
    "📁": "folder-tree",
    "🧠": "brain",
    "🚀": "rocket",
    "📋": "clipboard",
    "🤝": "handshake",
}

# Feature card emoji → icon mapping
FEATURE_MAP = {
    "🏢": "team",
    "🧠": "brain",
    "🔒": "seal",
    "🪃": "boomerang",
    "🌿": "branch",
    "🔀": "lightbulb",
    "🛡️": "shield-pulse",
    "🛡": "shield-pulse",
    "🧬": "dna",
    "🐜": "network",
    "🌙": "moon",
    "🔌": "plug",
    "🔬": "microscope",
}

# Tool accordion emoji → icon mapping
TOOL_CAT_MAP = {
    "🧠": "cognition",
    "🧬": "dna",
    "🛡️": "resilience",
    "🛡": "resilience",
    "🌀": "workflow",
    "🐜": "network",
    "🔌": "integration",
}

# Advanced features table emoji map
ADV_FEATURE_MAP = {
    "🏛️": "temple",
    "🏛": "temple",
    "🛡️": "shield-pulse",
    "🛡": "shield-pulse",
    "🧠": "brain",
    "📦": "plug",
    "🪞": "hexagon",
    "🌀": "workflow",
    "⏪": "branch",
    "🧬": "dna",
    "⛓️": "network",
    "⛓": "network",
    "🔁": "rocket",
    "🧭": "lightbulb",
    "🎵": "sparkle",
    "📊": "chart",
}

# Archetype emoji → icon mapping
ARCHETYPE_MAP = {
    "🗺️": "hexagon",
    "🗺": "hexagon",
    "🌐": "plug",
    "⚙️": "wrench",
    "⚙": "wrench",
    "🧬": "dna",
    "🎨": "sparkle",
    "🔧": "wrench",
    "🔁": "workflow",
    "📦": "puzzle",
}

# Comparison table row emoji → icon mapping
COMP_MAP = {
    "🏠": "rocket",
    "🏢": "team",
    "📄": "seal",
    "🧠": "brain",
    "🌿": "branch",
    "🪃": "boomerang",
    "🔌": "plug",
    "🔁": "workflow",
    "🧬": "dna",
    "🔀": "lightbulb",
    "🏛️": "temple",
    "🏛": "temple",
    "🛡️": "shield-pulse",
    "🛡": "shield-pulse",
}


def transform(text):
    lines = text.split("\n")
    result = []
    in_mermaid = False
    in_tree_code = False

    for i, line in enumerate(lines):
        original = line

        # Track fenced code blocks
        if line.strip().startswith("```mermaid"):
            in_mermaid = True
            result.append(line)
            continue
        if in_mermaid:
            if line.strip() == "```":
                in_mermaid = False
            else:
                # Remove emojis from Mermaid labels
                line = re.sub(r'[\U0001F300-\U0001F9FF\U00002600-\U000026FF\U00002700-\U000027BF\U0001F600-\U0001F64F\U0001FA00-\U0001FAFF\U0000FE0F]+\s?', '', line)
            result.append(line)
            continue

        # Tree structure in code block — remove decorative emojis
        if line.startswith("├── 📜") or line.startswith("├── 📋") or line.startswith("├── 🔧") or line.startswith("├── 🧩") or line.startswith("├── 📚") or line.startswith("├── 🧪") or line.startswith("└── 📦"):
            line = re.sub(r'[\U0001F300-\U0001F9FF\U00002600-\U000026FF\U00002700-\U000027BF\U0001F600-\U0001F64F\U0001FA00-\U0001FAFF\U0000FE0F]+\s?', '', line)
            result.append(line)
            continue

        # Section headers: ## EMOJI Title → ## <img> Title
        m = re.match(r'^(#{2,3})\s*([\U0001F300-\U0001F9FF\U00002600-\U000026FF\U00002700-\U000027BF\U0001F600-\U0001F64F\U0001FA00-\U0001FAFF\U0000FE0F]+)\s+(.+)$', line)
        if m:
            level, emoji_chars, title = m.group(1), m.group(2), m.group(3)
            # Find best icon from section or feature maps
            emoji_clean = emoji_chars.replace('\uFE0F', '')
            icon_name = None
            for e, name in {**SECTION_MAP, **FEATURE_MAP}.items():
                if emoji_clean.startswith(e.replace('\uFE0F', '')):
                    icon_name = name
                    break
            if icon_name:
                sz = 28 if level == "##" else 24
                result.append(f'{level} {icon(icon_name, sz)} {title}')
            else:
                # Fallback: strip emoji
                result.append(f'{level} {title}')
            continue

        # Feature grid: ### EMOJI Title (inside <td>)
        if line.strip().startswith("### ") and any(e in line for e in FEATURE_MAP):
            for emoji, icon_name in sorted(FEATURE_MAP.items(), key=lambda x: -len(x[0])):
                if emoji in line:
                    line = line.replace(f"### {emoji} ", f"### {title_clean(emoji, line)} ")
                    line = re.sub(r'### [\U0001F300-\U0001F9FF\U00002600-\U000026FF\U00002700-\U000027BF\U0001F600-\U0001F64F\U0001FA00-\U0001FAFF\U0000FE0F]+\s*', f'### ', line)
                    break
            result.append(line)
            continue

        # Accordion summary headers
        if "<summary>" in line and "<b>" in line:
            for emoji, icon_name in sorted({**TOOL_CAT_MAP, **SECTION_MAP}.items(), key=lambda x: -len(x[0])):
                if emoji in line:
                    line = line.replace(emoji + " ", f'{icon(icon_name, 18)} ')
                    line = line.replace(emoji, f'{icon(icon_name, 18)} ')
                    break
            result.append(line)
            continue

        # Comparison table rows: replace ❌/✅ and row label emojis
        if line.startswith("<tr><td>") and ("❌" in line or "✅" in line):
            # Replace marker emojis
            line = line.replace("✅", "✓")
            line = line.replace("❌", "✗")
            # Remove row label emojis
            line = re.sub(r'<td>[\U0001F300-\U0001F9FF\U00002600-\U000026FF\U00002700-\U000027BF\U0001F600-\U0001F64F\U0001FA00-\U0001FAFF\U0000FE0F]+\s*', '<td>', line)
            result.append(line)
            continue

        # Advanced features table rows: | EMOJI **Name** | Desc |
        if line.startswith("| ") and "**" in line:
            for emoji in sorted(ADV_FEATURE_MAP.keys(), key=lambda x: -len(x)):
                if emoji in line:
                    line = line.replace(emoji + " ", "")
                    line = line.replace(emoji, "")
                    break
            # Also catch any remaining emojis in table rows
            line = re.sub(r'[\U0001F300-\U0001F9FF\U00002600-\U000026FF\U00002700-\U000027BF\U0001F600-\U0001F64F\U0001FA00-\U0001FAFF\U0000FE0F]+\s?', '', line)
            result.append(line)
            continue

        # Archetype names: **EMOJI name**
        if line.strip().startswith("**") and any(e in line for e in ARCHETYPE_MAP):
            for emoji in sorted(ARCHETYPE_MAP.keys(), key=lambda x: -len(x)):
                if emoji in line:
                    line = line.replace(emoji + " ", "")
                    line = line.replace(emoji, "")
                    break
            result.append(line)
            continue

        # Inline list items with emojis: - EMOJI text
        if re.match(r'^- [\U0001F300-\U0001F9FF\U00002600-\U000026FF\U00002700-\U000027BF\U0001F600-\U0001F64F\U0001FA00-\U0001FAFF]', line):
            line = re.sub(r'^- [\U0001F300-\U0001F9FF\U00002600-\U000026FF\U00002700-\U000027BF\U0001F600-\U0001F64F\U0001FA00-\U0001FAFF\U0000FE0F]+\s*', '- ', line)
            result.append(line)
            continue

        # Tip callout: > **💡 text
        if "💡" in line:
            line = line.replace("💡 ", "")
            result.append(line)
            continue

        # Detail summary with emoji: 🔽 or 📖
        if "🔽" in line or "📖" in line:
            line = line.replace("🔽 ", "").replace("📖 ", "")
            result.append(line)
            continue

        # Footer: Made with 🔮
        if "🔮" in line:
            line = line.replace("🔮", "Grimoire")
            result.append(line)
            continue

        # Catch any remaining emojis on non-code lines
        if not line.startswith("```") and not line.startswith("    ") and not line.startswith("|"):
            if re.search(r'[\U0001F300-\U0001F9FF\U00002600-\U000026FF\U00002700-\U000027BF\U0001F600-\U0001F64F\U0001FA00-\U0001FAFF]', line):
                # Generic strip
                line = re.sub(r'[\U0001F300-\U0001F9FF\U00002600-\U000026FF\U00002700-\U000027BF\U0001F600-\U0001F64F\U0001FA00-\U0001FAFF\U0000FE0F]+\s?', '', line)

        result.append(line)

    return "\n".join(result)


def title_clean(emoji, line):
    """Extract clean title from a line with emoji."""
    return ""


def add_feature_icons(text):
    """Post-process: add centered icons above feature card titles in the grid."""
    # Pattern: <td align="center" width="33%">\n\n### Title
    # Add icon img between td and ###

    feature_title_icons = {
        "Team of Teams": "team",
        "Mémoire Sémantique": "brain",
        "Completion Contract": "seal",
        "Boomerang Orchestration": "boomerang",
        "Session Branching": "branch",
        "Plan / Act / Think": "lightbulb",
        "Self-Healing": "shield-pulse",
        "Agent Darwinism": "dna",
        "Stigmergy": "network",
        "Dream Mode": "moon",
        "MCP Server": "plug",
        "R&D Engine v2.1": "microscope",
    }

    for title, icon_name in feature_title_icons.items():
        # Find "### Title" in feature grid and prepend icon
        old = f"### {title}"
        new = f"{icon_center(icon_name, 36)}\n\n### {title}"
        text = text.replace(old, new, 1)

    return text


def main():
    with open("README.md", "r") as f:
        content = f.read()

    # Phase 1: Transform emojis
    content = transform(content)

    # Phase 2: Add feature card icons
    content = add_feature_icons(content)

    with open("README.md", "w") as f:
        f.write(content)

    # Count remaining emojis
    remaining = len(re.findall(r'[\U0001F300-\U0001F9FF\U00002600-\U000026FF\U00002700-\U000027BF\U0001F600-\U0001F64F\U0001FA00-\U0001FAFF]', content))
    print(f"[OK] README.md transformed")
    print(f"     Remaining emoji characters: {remaining}")


if __name__ == "__main__":
    main()
