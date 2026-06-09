# Guide Observatory API

Le mode `serve` de l'Observatory expose une API HTTP locale pour consulter, comparer et modifier la configuration des agents en direct.

## Demarrage du serveur

Depuis la racine de `grimoire-kit`:

```bash
python3 framework/tools/observatory.py serve --host 127.0.0.1 --port 8082
```

Mode avec message de commit obligatoire:

```bash
python3 framework/tools/observatory.py serve --host 127.0.0.1 --port 8082 --commit-required
```

Mode lecture seule (aucune mutation autorisee):

```bash
python3 framework/tools/observatory.py serve --host 127.0.0.1 --port 8082 --read-only
```

## Endpoints disponibles

### `GET /api/agent-config`

Retourne l'etat courant de la configuration agent:

```json
{
  "ok": true,
  "commit_required": false,
  "read_only": false,
  "config": {
    "version": 3,
    "agents": {
      "grimoire-master": {
        "description": "...",
        "tools": ["read", "edit"],
        "mode": "orchestrator"
      }
    }
  },
  "backups": []
}
```

### `POST /api/agent-config/diff`

Calcule le diff de patch sans ecriture.

```bash
curl -sS -X POST http://127.0.0.1:8082/api/agent-config/diff \
  -H 'Content-Type: application/json' \
  -d '{"agent_id":"grimoire-master","candidate":{"description":"Nouvelle description"}}'
```

### `POST /api/agent-config/apply`

Applique le patch sur le JSON de configuration.

```bash
curl -sS -X POST http://127.0.0.1:8082/api/agent-config/apply \
  -H 'Content-Type: application/json' \
  -d '{"agent_id":"grimoire-master","candidate":{"description":"Nouvelle description"},"commit_message":"Ajustement local"}'
```

L'API accepte aussi `patch` et `message` comme alias de compatibilite.

### `POST /api/agent-config/rollback`

Restaure la sauvegarde immediate precedente.

```bash
curl -sS -X POST http://127.0.0.1:8082/api/agent-config/rollback \
  -H 'Content-Type: application/json' \
  -d '{"agent_id":"grimoire-master"}'
```

## Gestion des erreurs API

- JSON invalide: `400`
- Endpoint inconnu: `404`
- `agent_id` manquant: `400`
- Rollback sans backup: `404`
- `apply` ou `rollback` en mode read-only: `403`

## Journal d'audit

Chaque requete `diff`, `apply` et `rollback` ecrit une ligne JSON dans:

- `_grimoire-output/.agent-config-audit.jsonl`

Exemple de lecture rapide:

```bash
tail -n 20 _grimoire-output/.agent-config-audit.jsonl
```

Le journal capture notamment:

- horodatage UTC
- action (`diff`, `apply`, `rollback`)
- statut (`ok` ou `error`)
- version globale avant/apres
- champs modifies
- metadonnees (backup utilise, erreur, mode read-only)

## Workflow recommande

1. Lire l'etat courant via `GET /api/agent-config`.
2. Previsualiser via `POST /api/agent-config/diff`.
3. Appliquer via `POST /api/agent-config/apply`.
4. En cas de probleme, annuler via `POST /api/agent-config/rollback`.
5. Verifier la trace dans le journal d'audit.
