"""Faux agent ACP de l'assistant de l'Atelier : fait ce que le skill `grimoire-cadrage` demande, sans modèle.

``python atelier_agent.py <journal.jsonl>``, lancé dans la racine du projet. Il consigne chaque ligne reçue dans le
journal (le test vérifie ce que l'agent a *vu*) et agit comme l'agent réel le ferait par les outils du kit :

- consigne « Applique le skill grimoire-cadrage » : ouvre l'entretien et pose la première question ;
- « Je ne sais pas répondre » : consigne l'hypothèse (``--unknown``), pose la suivante ;
- « est sans objet » : écarte l'angle (``--skip``), pose la suivante ;
- « Challenge le bloc <id> » : dépose une suggestion (``atelier_suggest``), jamais une écriture du document ;
- « lent » : attend ``session/cancel`` (Pause) ;
- sinon, si le contexte de l'Atelier est là : la réponse de l'humain est consignée, puis la question suivante.
"""

import json
import re
import sys
from pathlib import Path

LOG = sys.argv[1]
sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")


def send(msg: dict) -> None:
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def chunk(sid: str, text: str) -> None:
    send({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": sid, "update": {
        "sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": text}}}})


def read() -> dict | None:
    line = sys.stdin.readline()
    if not line:
        return None
    msg = json.loads(line)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(msg) + "\n")
    return msg


def next_question(root: Path, slug: str) -> str:
    from grimoire.core.cadrage_interview import ANGLES, interview_plan

    plan = interview_plan(root, slug)
    pending = plan["open_required"]
    if not pending:
        return "Le cadrage de cette phase est complet."
    angle = next(a for a in ANGLES if a.id == pending[0])
    return angle.seeds[0]


def act(text: str, rest: str) -> str:
    from grimoire.core import cadrage_interview as interview

    root = Path.cwd()
    slug = re.search(r"^chantier: (\S+)$", text, re.MULTILINE)
    if slug is None:
        return "Bonjour, je suis l'orchestrateur."
    name = slug.group(1)
    if "Challenge le bloc" in text:
        from grimoire.tools import workspace_atelier_suggestions as sugg

        block = re.search(r"Challenge le bloc (\S+) de ce chantier sous la posture « (\w+) »", text)
        sugg.suggest(str(root), name, block.group(1), "Version plus simple proposée par le faux agent.", "Plus court.", block.group(2))
        return "J'ai déposé une suggestion sous ce bloc."
    if "Applique le skill grimoire-cadrage" in text or "Reprends l'entretien" in text:
        return "Voici ma première question.\n\n" + next_question(root, name)
    plan = interview.interview_plan(root, name)
    named = re.search(r"sur l'angle (\S+?)[ ,.]", text)  # les gestes « Je ne sais pas » et « Sans objet » nomment l'angle affiché
    angle = named.group(1) if named else (plan["open_required"][0] if plan["open_required"] else "utilisateurs")
    if "Je ne sais pas répondre" in text:
        interview.answer(root, name, angle, "", unknown=True)
    elif "est sans objet" in text:
        interview.answer(root, name, angle, "sans objet", skip=True)
    else:
        interview.answer(root, name, angle, rest.strip())
    return "Noté.\n\n" + next_question(root, name)


def main() -> int:
    while (msg := read()) is not None:
        method, rid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": rid, "result": {"protocolVersion": 1, "agentCapabilities": {}}})
        elif method == "session/new":
            send({"jsonrpc": "2.0", "id": rid, "result": {"sessionId": "atelier-sess-1"}})
        elif method == "session/prompt":
            sid = params["sessionId"]
            text = "".join(p.get("text", "") for p in params["prompt"])
            reason = "end_turn"
            if text.startswith("lent"):
                chunk(sid, "Je réfléchis longuement…")
                while (nxt := read()) is not None:
                    if nxt.get("method") == "session/cancel":
                        reason = "cancelled"
                        break
            else:
                chunk(sid, act(text, text.split("\n\n[Contexte", 1)[0]))
            send({"jsonrpc": "2.0", "id": rid, "result": {"stopReason": reason}})
    return 0


if __name__ == "__main__":
    sys.exit(main())
