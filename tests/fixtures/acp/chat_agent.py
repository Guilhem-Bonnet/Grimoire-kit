"""Faux agent ACP interactif pour le chat du cockpit : répond aux messages au lieu de rejouer une transcription.

``python chat_agent.py <journal.jsonl>`` consigne chaque ligne reçue du client dans le journal (pour que
le test vérifie ce que l'agent a *vu*) et réagit au texte du prompt :

- « permission » : demande ``session/request_permission`` et raconte le choix reçu ;
- « lent »       : diffuse un morceau puis attend ``session/cancel`` avant de rendre ``cancelled`` ;
- « outils »     : un appel d'outil avec diff, puis un plan ;
- sinon          : répond en markdown, en deux morceaux.

``FAKE_NO_LOAD=1`` n'annonce pas ``loadSession``.
"""

import json
import os
import sys

LOG = sys.argv[1]
# Le client parle UTF-8 : sous Windows les flux par défaut suivent la page de code (cp1252).
sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")


def send(msg: dict) -> None:
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def update(sid: str, body: dict) -> None:
    send({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": sid, "update": body}})


def chunk(sid: str, text: str, kind: str = "agent_message_chunk") -> None:
    update(sid, {"sessionUpdate": kind, "content": {"type": "text", "text": text}})


def read() -> dict | None:
    line = sys.stdin.readline()
    if not line:
        return None
    msg = json.loads(line)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(msg) + "\n")
    return msg


def main() -> int:
    while (msg := read()) is not None:
        method, rid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
        if method == "initialize":
            caps = {} if os.environ.get("FAKE_NO_LOAD") else {"loadSession": True}
            send({"jsonrpc": "2.0", "id": rid, "result": {"protocolVersion": 1, "agentCapabilities": caps}})
        elif method == "session/new":
            send({"jsonrpc": "2.0", "id": rid, "result": {"sessionId": "chat-sess-1"}})
        elif method == "session/load":
            sid = params["sessionId"]
            chunk(sid, "Avant la fermeture : bonjour", "user_message_chunk")
            chunk(sid, "Historique rejoué par l'agent.")
            send({"jsonrpc": "2.0", "id": rid, "result": {}})
        elif method == "session/prompt":
            sid = params["sessionId"]
            text = "".join(p.get("text", "") for p in params["prompt"])
            reason = "end_turn"
            if "permission" in text:
                send({
                    "jsonrpc": "2.0", "id": 900, "method": "session/request_permission",
                    "params": {
                        "sessionId": sid,
                        "toolCall": {"toolCallId": "t-perm", "title": "Écrire notes.md", "kind": "edit"},
                        "options": [
                            {"optionId": "once", "name": "Autoriser", "kind": "allow_once"},
                            {"optionId": "always", "name": "Toujours", "kind": "allow_always"},
                            {"optionId": "no", "name": "Refuser", "kind": "reject_once"},
                        ],
                    },
                })
                answer = read()
                outcome = (answer or {}).get("result", {}).get("outcome", {})
                chunk(sid, f"Permission reçue : {outcome.get('optionId') or outcome.get('outcome')}")
            elif "lent" in text:
                chunk(sid, "Je réfléchis longuement…")
                while (nxt := read()) is not None:
                    if nxt.get("method") == "session/cancel":
                        reason = "cancelled"
                        break
            elif "outils" in text:
                update(sid, {"sessionUpdate": "tool_call", "toolCallId": "t1", "title": "Lire README.md", "kind": "read", "status": "in_progress"})
                update(sid, {
                    "sessionUpdate": "tool_call_update", "toolCallId": "t1", "status": "completed",
                    "content": [{"type": "diff", "path": "README.md", "oldText": "ancien titre", "newText": "nouveau titre"}],
                })
                update(sid, {"sessionUpdate": "plan", "entries": [
                    {"content": "Lire le projet", "priority": "high", "status": "completed"},
                    {"content": "Proposer un plan", "priority": "medium", "status": "pending"},
                ]})
                chunk(sid, "Plan posé.")
            else:
                chunk(sid, "Bonjour, ")
                chunk(sid, "je suis **l'orchestrateur** du projet.\n\n- première ligne\n- seconde ligne")
            send({"jsonrpc": "2.0", "id": rid, "result": {"stopReason": reason}})
    return 0


if __name__ == "__main__":
    sys.exit(main())
