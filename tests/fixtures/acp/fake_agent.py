"""Faux agent ACP : rejoue une transcription du côté de l'agent, sur stdio.

``python fake_agent.py <X.transcript.json>`` : pour chaque entrée ``out`` (client → agent) il lit
une ligne et vérifie méthode, id, résultat et les paramètres ``sessionId``/``prompt`` ; pour
chaque entrée ``in`` (agent → client) il écrit le message. Un écart sort en code 3 avec la
raison sur stderr — le test qui l'attend échoue alors sur « l'agent s'est arrêté ».
"""

import json
import sys


def main() -> int:
    with open(sys.argv[1], encoding="utf-8") as fh:
        transcript = json.load(fh)
    for i, entry in enumerate(transcript):
        if entry.get("agent_sees") is False or entry["dir"] == "close":
            continue
        if entry["dir"] == "in":
            sys.stdout.write(json.dumps(entry["msg"]) + "\n")
            sys.stdout.flush()
            continue
        line = sys.stdin.readline()
        if not line:
            print(f"entrée {i} : stdin fermé, {entry['msg'].get('method', 'réponse')} attendu", file=sys.stderr)
            return 4
        got, want = json.loads(line), entry["msg"]
        for key in ("method", "id", "result"):
            if key in want and got.get(key) != want[key]:
                print(f"entrée {i} : {key} {got.get(key)!r} != {want[key]!r}", file=sys.stderr)
                return 3
        for key in ("sessionId", "prompt"):
            if key in want.get("params", {}) and got.get("params", {}).get(key) != want["params"][key]:
                print(f"entrée {i} : params.{key} différent", file=sys.stderr)
                return 3
    sys.stdin.read()  # le client ferme stdin : fin propre
    return 0


if __name__ == "__main__":
    sys.exit(main())
