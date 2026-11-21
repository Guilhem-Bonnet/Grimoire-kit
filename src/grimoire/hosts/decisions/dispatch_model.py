"""``PreToolUse`` sur ``Agent`` : imposer un modèle au sous-agent quand l'appel l'omet.

La « Politique de dispatch » injectée à chaque ``SessionStart`` (voir
``hosts.emitters.claude_code._dispatch_policy_section``) dit déjà quel modèle
donner à un sous-agent : V0 → ``haiku``, V1 → ``sonnet``, V2 → le modèle de la
session. Ce n'était que du texte. Un appel ``Agent`` sans ``model`` fait tourner
le sous-agent sur le modèle de la session — le plus cher — et la mesure du
2026-11-05 montrait que ``model=`` restait souvent omis.

Cette décision rend la règle mécanique, et seulement là où elle est sûre :

1. ``model`` déjà présent dans l'appel : rien n'est touché, jamais.
2. ``subagent_type: "fork"`` : rien n'est touché (un fork hérite toujours du
   modèle parent, l'hôte ignore ``model``).
3. Politique du projet à ``off`` (``dispatch.impose_model`` dans
   ``_grimoire/standard/orchestration-policy.yaml``) : rien n'est touché.
4. Persona qui déclare son propre ``model:`` (autre que ``inherit``) dans
   ``.claude/agents/<nom>.md`` du projet ou de l'utilisateur, ou agent de plugin
   (``plugin:agent``) : son choix est gardé.
5. Tâche réclamée de classe V0 ou V1 : le ``recommended_model`` de
   :func:`grimoire.missions.dispatch_advice.dispatch_advice`, le même calcul que
   ``task_claim`` — jamais une seconde classification.
   Tâche réclamée de classe V2 : rien n'est imposé (le modèle de la session).
6. Sans classe calculée, une heuristique sur la description de l'appel (voir
   :func:`heuristic_model`) : recherche, lecture ou mesure → ``haiku`` ; revue
   ou analyse → ``sonnet`` ; écriture, jugement fort (audit, sécurité,
   architecture, plan, conception), sujet sensible (garde, hook, politique, CI,
   faille — le vocabulaire de ``missions.sensitive_route``) ou rien de reconnu →
   rien n'est imposé. Une tâche réclamée de classe V0/V1 prime sur cette
   abstention : sa classe décide, même si la description cite une garde.

Le modèle imposé est rendu en ``updatedInput`` (l'entrée complète, l'hôte la
remplace sans fusionner) avec ``permissionDecision: allow`` ; l'agent principal
en est averti par ``additionalContext`` et le journal des traces le note
(étiquette ``dispatch_model:<modèle>``).

GitHub Copilot (#1280, P1-4) : mêmes règles sur ``task``, ``runSubagent`` et
``Agent``/``Task`` ; le palier n'est réécrit que si le projet nomme le modèle
Copilot correspondant (``dispatch.copilot_models``), sinon il est seulement dit
à l'agent. La décision y est marquée consultative (``detail["advisory"]``) : le
rendu Copilot n'affirme aucune permission (voir ``runtime``).

Défensive par construction : le hook est consultatif (sans ``onFailure:
block``), et toute exception ici rend une abstention — l'appel ``Agent`` passe
tel quel. Un bug de cette décision ne doit jamais bloquer une délégation.
"""

from __future__ import annotations

import contextlib
import re
from pathlib import Path
from typing import Any

from grimoire.hosts.decisions._shared import Decision, HookInput
from grimoire.missions.sensitive_route import mentions_sensitive_topic

#: Identifiant de la décision, référencé par l'émetteur Claude Code.
DECISION_ID = "grimoire.dispatch-model"

#: L'outil de délégation de Claude Code : ``Agent`` aujourd'hui, ``Task`` avant 2.1.63.
CLAUDE_DELEGATION_TOOLS = frozenset({"Agent", "Task"})

#: Les outils de délégation de GitHub Copilot (#1280, P1-4) : ``task`` (harness
#: Copilot, rapporté ``Agent`` — ``Task`` accepté — dans une charge PascalCase,
#: référence GitHub) et ``runSubagent`` (harness Local, référence VS Code des
#: sous-agents : un ``model`` explicite y prime sur celui de l'agent).
COPILOT_DELEGATION_TOOLS = frozenset({"task", "runSubagent", *CLAUDE_DELEGATION_TOOLS})

#: Tous les noms de délégation reconnus, quel que soit l'hôte.
DELEGATION_TOOLS = COPILOT_DELEGATION_TOOLS

#: Où l'appel nomme l'agent délégué : ``subagent_type`` (Claude Code) ; les autres
#: clés sont des candidates pour Copilot, dont le schéma des arguments n'est pas
#: documenté (« Tool names and input schemas differ between harnesses »).
_SUBAGENT_KEYS = ("subagent_type", "agent_type", "agentName", "agent_name", "agent")

#: Alias de palier de la politique (ceux de Claude Code) -> clé de réglage Copilot.
#: Copilot nomme ses modèles autrement (``claude-haiku-4.5`` dans son journal de
#: session, fixture ``copilot-harness/subagent-model.json``) et ne documente pas les
#: valeurs que ``task`` accepte : aucune correspondance n'est inventée. Le projet la
#: déclare sous ``dispatch.copilot_models`` ; sans elle, rien n'est réécrit.
COPILOT_MODELS_KEY = "copilot_models"

#: Même règle pour Cursor : le champ ``model`` d'un sous-agent ``.cursor/agents/*.md``
#: prend ``inherit`` ou un identifiant de modèle Cursor (https://cursor.com/docs/context/subagents),
#: qui ne correspond à aucun palier du kit. Le projet déclare la correspondance sous
#: ``dispatch.cursor_models`` ; sans elle, ``model`` n'est pas écrit.
CURSOR_MODELS_KEY = "cursor_models"

#: Le seul ``subagent_type`` qui hérite toujours du modèle parent.
FORK = "fork"

#: Clé de politique : ``dispatch.impose_model`` dans ce fichier.
POLICY_FILE = Path("_grimoire") / "standard" / "orchestration-policy.yaml"

_ON_VALUES = frozenset({"on", "true", "yes", "1", "enabled"})

#: Au-delà, le texte n'est pas lu pour l'heuristique : une borne de temps, pas de sens.
_MAX_HEURISTIC_CHARS = 400

#: Racines de mots, en minuscules et sans accents. Une racine de 4 lettres ou
#: plus matche en préfixe (``analys`` couvre ``analyse``, ``analyser``,
#: ``analysis``) ; plus courte, elle doit être le mot entier.
_WRITE_STEMS = (
    "implement",
    "corrig",
    "fix",
    "modif",
    "ecri",
    "ecrire",
    "write",
    "refacto",
    "cree",
    "creer",
    "create",
    "ajout",
    "add",
    "migr",
    "build",
    "constru",
    "redig",
    "draft",
    "update",
    "supprim",
    "delete",
    "remove",
    "patch",
    "edit",
    "renomm",
    "rename",
    "livr",
    "commit",
    "deploi",
    "deploy",
)
_REVIEW_STEMS = (
    "revue",
    "review",
    "reli",
    "relecture",
    "analys",
    "analyz",
    "evalu",
    "compar",
    "diagnos",
    "investig",
    "enquet",
    "critiq",
    "assess",
    "verifi",
    "verify",
    "juge",
    "judge",
    "arbitr",
)
#: Jugement fort : un audit, une question de sécurité, d'architecture, de plan ou
#: de conception ne descend pas d'un palier sur la foi de trois mots.
_STRONG_JUDGMENT_STEMS = (
    "audit",
    "secu",
    "securit",
    "vulnerab",
    "architect",
    "plan",
    "design",
    "concept",
    "strateg",
)
_RESEARCH_STEMS = (
    "recherch",
    "cherch",
    "search",
    "trouv",
    "find",
    "locali",
    "locate",
    "list",
    "lis",
    "lire",
    "lecture",
    "read",
    "inventa",
    "inventor",
    "mesur",
    "measur",
    "compt",
    "count",
    "explor",
    "lookup",
    "grep",
    "scan",
    "fetch",
    "recupe",
    "resum",
    "summar",
    "extrai",
    "extract",
    "repere",
)

_ACCENTS = str.maketrans("àâäéèêëîïôöùûüç", "aaaeeeeiioouuuc")


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z]+", text.lower().translate(_ACCENTS))


def _hits(words: list[str], stems: tuple[str, ...]) -> bool:
    for word in words:
        for stem in stems:
            if (len(stem) >= 4 and word.startswith(stem)) or word == stem:
                return True
    return False


def heuristic_model(description: str, prompt: str = "") -> str | None:
    """Le modèle que l'heuristique déduit d'un appel ``Agent``, ou ``None``.

    Lit la description (3 à 5 mots, ce que l'appelant dit de la tâche) ; faute de
    description, le début du prompt. Ordre de priorité, du plus prudent au moins
    prudent : un verbe d'écriture, ou un sujet de jugement fort (audit, sécurité,
    architecture, plan, conception) → ``None`` (le jugement de la session) ; un verbe de revue ou d'analyse → ``sonnet`` ; un verbe de
    recherche, de lecture ou de mesure → ``haiku`` ; rien de reconnu → ``None``.
    """
    text = (description or "").strip() or (prompt or "")[:_MAX_HEURISTIC_CHARS]
    words = _words(text[:_MAX_HEURISTIC_CHARS])
    if not words or _hits(words, _WRITE_STEMS) or _hits(words, _STRONG_JUDGMENT_STEMS):
        return None
    if mentions_sensitive_topic(text[:_MAX_HEURISTIC_CHARS]):
        return None
    if _hits(words, _REVIEW_STEMS):
        return "sonnet"
    if _hits(words, _RESEARCH_STEMS):
        return "haiku"
    return None


def impose_model_enabled(project_root: Path) -> bool:
    """``dispatch.impose_model`` du projet : activé par défaut, ``off`` pour désactiver.

    Fichier absent ou sans bloc ``dispatch`` : activé. Valeur reconnue comme
    vraie (``on``, ``true``…) : activé. Toute autre valeur, ou un fichier
    illisible : désactivé — dans le doute, la décision n'impose rien.
    """
    path = project_root / POLICY_FILE
    if not path.is_file():
        return True
    try:
        from ruamel.yaml import YAML

        data = YAML(typ="safe").load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return False
    if not isinstance(data, dict) or "dispatch" not in data:
        return isinstance(data, dict)
    block = data.get("dispatch")
    if not isinstance(block, dict) or "impose_model" not in block:
        return isinstance(block, dict)
    value = block.get("impose_model")
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in _ON_VALUES


def copilot_model_for(project_root: Path, tier: str) -> str:
    """Le nom de modèle Copilot que le projet associe au palier *tier*, ou ``""``.

    Lu sous ``dispatch.copilot_models.<palier>`` de la politique d'orchestration
    (par exemple ``haiku: claude-haiku-4.5``). Absent, vide ou illisible : ``""``,
    et la décision reste consultative.
    """
    return declared_model_for(project_root, COPILOT_MODELS_KEY, tier)


def cursor_model_for(project_root: Path, tier: str) -> str:
    """Le nom de modèle Cursor que le projet associe au palier *tier* (``dispatch.cursor_models``), ou ``""``."""
    return declared_model_for(project_root, CURSOR_MODELS_KEY, tier)


def declared_model_for(project_root: Path, key: str, tier: str) -> str:
    """``dispatch.<key>.<tier>`` de la politique d'orchestration, ou ``""`` (absent, vide, illisible)."""
    path = project_root / POLICY_FILE
    if not path.is_file():
        return ""
    try:
        from ruamel.yaml import YAML

        data = YAML(typ="safe").load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return ""
    block = data.get("dispatch") if isinstance(data, dict) else None
    models = block.get(key) if isinstance(block, dict) else None
    value = models.get(tier) if isinstance(models, dict) else None
    return value.strip() if isinstance(value, str) else ""


def _persona_declares_model(project_root: Path, subagent_type: str, *, copilot: bool = False) -> bool:
    """La persona appelée a-t-elle déjà son modèle, ou ne peut-on pas le savoir ?

    Vrai — donc abstention — pour un agent de plugin (``plugin:agent``, sa
    définition n'est pas lue ici) et pour une définition ``.claude/agents/<nom>.md``
    du projet ou de l'utilisateur qui déclare un ``model:`` autre que ``inherit``.
    Sous Copilot (*copilot*) seulement, aussi ``.github/agents/<nom>.agent.md`` :
    Claude Code ne lit pas ce fichier, son ``model:`` n'y change rien.
    """
    if not subagent_type:
        return False
    if ":" in subagent_type:
        return True
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", subagent_type) or ".." in subagent_type:
        return False
    roots = [project_root]
    with contextlib.suppress(RuntimeError, KeyError, OSError):
        roots.append(Path.home())
    candidates = [root / ".claude" / "agents" / f"{subagent_type}.md" for root in roots]
    if copilot:
        # Agent personnalisé VS Code / Copilot : son `model:` est sa préférence.
        candidates += [root / ".github" / "agents" / f"{subagent_type}.agent.md" for root in roots]
    return any(_definition_declares_model(path) for path in candidates)


def _definition_declares_model(path: Path) -> bool:
    try:
        if not path.is_file():
            return False
        head = path.read_text(encoding="utf-8", errors="replace")[:4000]
    except OSError:
        return False
    if not head.startswith("---"):
        return False
    end = head.find("\n---", 3)
    frontmatter = head[3:end] if end != -1 else ""
    match = re.search(r"(?m)^model:\s*['\"]?([^'\"\s#]*)", frontmatter)
    return bool(match and match.group(1) and match.group(1).lower() != "inherit")


def _class_model(hook: HookInput) -> tuple[str | None, dict[str, Any]]:
    """Le ``recommended_model`` de la tâche réclamée, ou ``None`` (pas de tâche, V2, rien de lisible).

    ``detail["task_class"]`` dit si une classe a été calculée : une tâche V2 en a
    une, sans modèle — l'appelant s'abstient alors au lieu de tenter l'heuristique.
    """
    from grimoire.core.standard_state import is_standard_enrolled, resolve_active_task

    if not is_standard_enrolled(hook.project_root):
        return None, {}
    active = resolve_active_task(hook.project_root, session_id=hook.session_id, agent_id=hook.agent_id)
    if active.is_fallback:
        return None, {}
    from grimoire.missions.dispatch_advice import dispatch_advice
    from grimoire.missions.service import TaskService

    service = TaskService(hook.project_root)
    task = service.ledger.get_task(active.task_id) if service.has_ledger else None
    if task is None:
        return None, {"task_id": active.task_id}
    advice = dispatch_advice(task)
    verifiability = advice.get("verifiability") or {}
    cls = str(verifiability.get("class") or "")
    return advice.get("recommended_model"), {"task_id": active.task_id, "task_class": cls}


def decide_dispatch_model(hook: HookInput) -> Decision:
    """Imposer un modèle à un appel ``Agent`` qui l'omet — voir le module. Ne lève jamais."""
    try:
        return _decide(hook)
    except Exception as exc:
        return Decision(abstain=True, detail={"dispatch_model": "error", "error": f"{type(exc).__name__}: {exc}"})


def _is_copilot(hook: HookInput) -> bool:
    from grimoire.bridges.schemas import HostId

    return hook.host == HostId.GITHUB_COPILOT.value


def _subagent_type(tool_input: dict[str, Any], *, copilot: bool) -> str:
    for key in _SUBAGENT_KEYS if copilot else _SUBAGENT_KEYS[:1]:
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _decide(hook: HookInput) -> Decision:
    copilot = _is_copilot(hook)
    if hook.tool_name not in (COPILOT_DELEGATION_TOOLS if copilot else CLAUDE_DELEGATION_TOOLS):
        return Decision(abstain=True)
    tool_input = hook.tool_input
    if not isinstance(tool_input, dict) or not tool_input:
        return Decision(abstain=True)
    if tool_input.get("model") not in (None, ""):
        return Decision(abstain=True)
    subagent_type = _subagent_type(tool_input, copilot=copilot)
    if subagent_type == FORK:
        return Decision(abstain=True)
    if not impose_model_enabled(hook.project_root):
        return Decision(abstain=True)
    if _persona_declares_model(hook.project_root, subagent_type, copilot=copilot):
        return Decision(abstain=True)

    model, detail = _class_model(hook)
    if not model and detail.get("task_class"):
        # Classe calculée sans modèle (V2) : la politique veut le modèle de la session.
        return Decision(abstain=True)
    source = "class" if model else ""
    if not model:
        description = tool_input.get("description")
        prompt = tool_input.get("prompt")
        model = heuristic_model(
            description if isinstance(description, str) else "",
            prompt if isinstance(prompt, str) else "",
        )
        source = "heuristic" if model else ""
    if not model:
        return Decision(abstain=True)

    why = (
        f"classe {detail.get('task_class') or '?'} de la tâche {detail.get('task_id')}"
        if source == "class"
        else "heuristique sur la description de l'appel"
    )
    if copilot:
        return _copilot_decision(
            hook, tool_input, model, why, {**detail, "dispatch_source": source, "subagent_type": subagent_type}
        )
    context = (
        f"[Grimoire] model={model} imposé au sous-agent ({why}) : l'appel Agent n'avait pas de model. "
        "Passe model= explicitement pour un autre choix ; `dispatch.impose_model: off` dans "
        "_grimoire/standard/orchestration-policy.yaml désactive ce réglage."
    )
    return Decision(
        context=context,
        updated_input={**tool_input, "model": model},
        detail={**detail, "imposed_model": model, "dispatch_source": source, "subagent_type": subagent_type},
    )


def _copilot_decision(
    hook: HookInput, tool_input: dict[str, Any], tier: str, why: str, detail: dict[str, Any]
) -> Decision:
    """Le même palier, rendu pour Copilot : réécrit seulement si le projet a nommé le modèle Copilot.

    Toujours consultatif (``detail["advisory"]``) : le rendu Copilot n'affirme
    aucune permission, il propose l'entrée réécrite (``modifiedArgs`` /
    ``updatedInput`` selon le dialecte, voir ``runtime``).
    """
    from grimoire.hosts.hook_entry import ADVISORY_DETAIL_KEY

    copilot_model = copilot_model_for(hook.project_root, tier)
    base = {**detail, ADVISORY_DETAIL_KEY: True, "recommended_model": tier}
    tool = hook.tool_name
    if not copilot_model:
        return Decision(
            context=(
                f"[Grimoire] palier {tier} recommandé pour ce sous-agent ({why}) : l'appel {tool} n'avait pas de "
                "model, et aucun nom de modèle Copilot n'est associé à ce palier — rien n'est imposé. "
                f"Déclare `dispatch.{COPILOT_MODELS_KEY}.{tier}: <modèle Copilot>` dans "
                "_grimoire/standard/orchestration-policy.yaml pour l'imposer, ou passe model= explicitement."
            ),
            detail={**base, "dispatch_model": f"no_{COPILOT_MODELS_KEY}"},
        )
    return Decision(
        context=(
            f"[Grimoire] model={copilot_model} (palier {tier}) proposé au sous-agent ({why}) : l'appel {tool} "
            "n'avait pas de model. Passe model= explicitement pour un autre choix ; `dispatch.impose_model: off` "
            "dans _grimoire/standard/orchestration-policy.yaml désactive ce réglage."
        ),
        updated_input={**tool_input, "model": copilot_model},
        detail={**base, "imposed_model": copilot_model},
    )
