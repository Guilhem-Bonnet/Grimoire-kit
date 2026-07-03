#!/usr/bin/env python3
"""Terminal Guard Policy — validates LLM-generated shell commands before execution.

Standalone tool, separate from guardrail-policy.py to stay lightweight.
Loaded only for terminal PreToolUse events. Output: JSON warn or empty.
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass

# Tool names that identify terminal/shell invocations
_TERMINAL_NAMES = frozenset({
    "run_terminal", "terminal", "bash", "shell", "execute_command",
    "run_command", "execute", "run_bash", "run_shell", "computer",
})

# (rule_id, message_fr, guidance_fr, severity)
_RULES = [
    ("quote-double-unbalanced",
     "Guillemets doubles non équilibrés — risque d'argument coupé ou d'injection.",
     "Ferme chaque \" ou utilise des guillemets simples autour des blocs contenant des espaces.",
     "warn"),
    ("quote-single-unbalanced",
     "Guillemets simples non équilibrés — le shell peut attendre de l'input indéfiniment.",
     "Vérifie que chaque ' est fermée. Si la valeur contient ', préfère les doubles guillemets.",
     "warn"),
    ("backtick-unbalanced",
     "Backtick non fermé — le shell interprétera tout ce qui suit comme sous-commande.",
     "Remplace les backticks par $() pour les sous-commandes imbriquées.",
     "warn"),
    ("pipe-depth-excessive",
     "Chaîne de pipes > 4 — complexité élevée, risque de crash terminal VSCode.",
     "Décompose en étapes ou utilise des fichiers temporaires.",
     "warn"),
    ("command-too-long",
     "Commande > 2000 caractères — risque de troncature dans le terminal VSCode.",
     "Découpe en plusieurs commandes ou utilise un script temporaire.",
     "warn"),
    ("find-no-maxdepth",
     "find sans -maxdepth sur '/' ou '.' — scan complet du filesystem possible.",
     "Ajoute -maxdepth 3 ou cible un sous-dossier spécifique.",
     "warn"),
    ("grep-recursive-no-include",
     "grep -r sans --include — scan complet du dossier, peut saturer le terminal VSCode.",
     "Ajoute --include='*.ext' ou cible un dossier précis.",
     "warn"),
    ("unlimited-parallelism",
     "Parallélisme illimité (pytest -n 0, make -j 0, xargs -P 0) — risque de saturation CPU/RAM.",
     "Fixe une limite explicite : -n auto, -j 4, -P 4.",
     "warn"),
    ("background-accumulation",
     "Plus de 2 processus en arrière-plan (&) dans une commande — accumulation non contrôlée.",
     "Préfère wait, une boucle avec jobs -l, ou un task runner dédié.",
     "warn"),
    ("nested-process-substitution",
     "Substitution de processus $() imbriquée ≥ 3 niveaux — difficile à déboguer.",
     "Décompose en variables intermédiaires : VAR=$(cmd); utilise $VAR.",
     "warn"),
]


@dataclass(slots=True)
class Issue:
    rule_id: str
    message: str
    guidance: str
    severity: str


def _count_unescaped(cmd: str, char: str) -> int:
    """Count occurrences of char that are not escaped and not inside the opposing quote type."""
    count = 0
    in_double = in_single = False
    i = 0
    while i < len(cmd):
        c = cmd[i]
        # Inside single-quotes nothing is escaped (bash rule)
        if in_single:
            if c == "'":
                in_single = False
            i += 1
            continue
        if c == '\\' and not in_single:
            i += 2  # skip escaped char
            continue
        if c == '"':
            in_double = not in_double
        elif c == "'" and not in_double:
            in_single = True
        elif c == char and not in_double and not in_single:
            count += 1
        i += 1
    return count


def _pipe_depth(cmd: str) -> int:
    depth = in_double = in_single = 0
    i = 0
    while i < len(cmd):
        c = cmd[i]
        if c == '\\' and not in_single:
            i += 2
            continue
        if c == '"' and not in_single:
            in_double ^= 1
        elif c == "'" and not in_double:
            in_single ^= 1
        elif c == '|' and not in_double and not in_single:
            if i + 1 < len(cmd) and cmd[i + 1] != '|':
                depth += 1
        i += 1
    return depth


def _subst_depth(cmd: str) -> int:
    max_d = d = 0
    i = 0
    while i < len(cmd) - 1:
        if cmd[i] == '$' and cmd[i + 1] == '(':
            d += 1
            max_d = max(max_d, d)
            i += 2
        elif cmd[i] == ')' and d > 0:
            d -= 1
            i += 1
        else:
            i += 1
    return max_d


def _bg_count(cmd: str) -> int:
    # Match & used as backgrounding: preceded by non-& and not followed by &
    # Space before & is optional (e.g. `cmd&` and `cmd &` are both valid)
    return len(re.findall(r'(?<![|&])\s?&(?!&)', cmd))


def _validate(cmd: str) -> list[Issue]:
    issues: list[Issue] = []
    rules = {r[0]: r for r in _RULES}

    def add(rule_id: str) -> None:
        r = rules[rule_id]
        issues.append(Issue(r[0], r[1], r[2], r[3]))

    if _count_unescaped(cmd, '"') % 2:
        add("quote-double-unbalanced")
    if _count_unescaped(cmd, "'") % 2:
        add("quote-single-unbalanced")
    if _count_unescaped(cmd, '`') % 2:
        add("backtick-unbalanced")
    if _pipe_depth(cmd) > 4:
        add("pipe-depth-excessive")
    if len(cmd) > 2000:
        add("command-too-long")
    if re.search(r'\bfind\s+[/.]', cmd) and '-maxdepth' not in cmd:
        add("find-no-maxdepth")
    if re.search(r'\bgrep\s+-[A-Za-z]*r', cmd) and '--include' not in cmd:
        add("grep-recursive-no-include")
    if re.search(r'(?:pytest\s+-n\s*0|-j\s*0|xargs\s+-P\s*0)\b', cmd):
        add("unlimited-parallelism")
    if _bg_count(cmd) > 2:
        add("background-accumulation")
    if _subst_depth(cmd) >= 3:
        add("nested-process-substitution")

    return issues


def _extract_cmd(tool_input: dict) -> str | None:
    for key in ("command", "cmd", "script", "code", "input"):
        val = tool_input.get(key)
        if isinstance(val, str) and val.strip():
            return val
    return None


def main() -> None:
    raw = sys.stdin.read().strip()
    if not raw:
        print("{}")
        return

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        print("{}")
        return

    tool_use = payload.get("toolUse") or payload.get("tool_use") or {}
    tool_name = (tool_use.get("name") or "").lower()

    if not any(t in tool_name for t in _TERMINAL_NAMES):
        print("{}")
        return

    cmd = _extract_cmd(tool_use.get("input") or {})
    if not cmd:
        print("{}")
        return

    issues = _validate(cmd)
    if not issues:
        print("{}")
        return

    blocks = [i for i in issues if i.severity == "block"]
    if blocks:
        first = blocks[0]
        out = {"decision": "block", "message": first.message, "guidance": first.guidance}
    else:
        msgs = " | ".join(i.message for i in issues)
        guidance = "\n".join(f"[{i.rule_id}] {i.guidance}" for i in issues)
        out = {
            "decision": "continue",
            "message": f"⚠ Terminal guard: {msgs}",
            "guidance": guidance,
        }

    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
