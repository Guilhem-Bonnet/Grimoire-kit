# Template: network-troubleshoot

> Diagnostic réseau structuré pour l'infrastructure homelab.
> Utilisable par Forge (Docker networks, LXC), Helm (K3s services), Hawk (probes Blackbox).

## Structure

```
{{agent_alias}} diagnostique un problème réseau {{network_layer}}.

RAISONNEMENT :
1. SYMPTÔME : décrire le comportement observé.
   - Quoi ne fonctionne pas ? (service, endpoint, connectivité)
   - Depuis quand ? (changement récent ?)
   - Impact : {{impact_scope}}

2. COUCHE PAR COUCHE (bottom-up) :
   a) L3 — Connectivité IP :
      - `ping {{target_ip}}`
      - `ip route get {{target_ip}}`
      - Vérifier VLAN/bridge réseau Proxmox (192.168.2.0/24)
   b) L4 — Ports & Firewall :
      - `ss -tlnp | grep {{port}}`
      - `iptables -L -n` / `nft list ruleset`
      - LXC : vérifier les features réseau dans config Proxmox
   c) L7 — Application :
      - `curl -sf {{endpoint}} -o /dev/null -w '%{http_code}'`
      - DNS : `dig {{domain}} @{{dns_server}}`
      - TLS : `openssl s_client -connect {{host}}:443 -servername {{domain}}`

3. INFRA SPÉCIFIQUE :
   {{#if traefik}}
   - Traefik : vérifier les routers/middlewares — `curl http://localhost:8080/api/http/routers`
   - Labels Docker : `docker inspect {{container}} | jq '.[0].Config.Labels'`
   {{/if}}
   {{#if wireguard}}
   - WireGuard : `wg show`, vérifier allowed-ips et endpoint
   - Kill-switch gluetun : vérifier que le trafic passe bien par le VPN
   {{/if}}
   {{#if k8s}}
   - K3s : `kubectl get svc,ep -A | grep {{service}}`
   - CoreDNS : `kubectl exec -it deploy/coredns -n kube-system -- nslookup {{service}}`
   {{/if}}

4. CAUSE ROOT : identifier et documenter.
5. CORRIGER : appliquer le fix.
6. VALIDER : confirmer la résolution avec la même commande que le symptôme.

Résumer : "Cause: {{root_cause}} — Fix: {{fix_applied}} — Validé: {{validation_result}}".
```

## Variables

| Variable | Description | Exemple |
|----------|-------------|---------|
| `agent_alias` | Nom court de l'agent | Forge, Helm |
| `network_layer` | Couche réseau suspectée | L3/L4/L7, DNS, TLS, routing |
| `target_ip` | IP cible | 192.168.2.22, 192.168.2.50 |
| `port` | Port ciblé | 443, 3001, 9090 |
| `endpoint` | URL à tester | https://grafana.home.lab/api/health |
| `domain` | Domaine DNS | grafana.home.lab, jellyfin.home.lab |
| `dns_server` | Serveur DNS | 192.168.2.1, 1.1.1.1 |
| `impact_scope` | Périmètre d'impact | un service, tout le VLAN, externe |
| `traefik` | Implique Traefik ? (bool) | true/false |
| `wireguard` | Implique WireGuard ? (bool) | true/false |
| `k8s` | Implique K3s ? (bool) | true/false |

## Prompts utilisant ce template

| Prompt ID | Agent | network_layer | contexte |
|-----------|-------|---------------|----------|
| docker-ops (network) | Forge | L4/L7 | Docker bridge, port mapping |
| troubleshoot-ops (net) | Helm | L7/DNS | K3s service, ingress |
| blackbox-probes | Hawk | L7 | Probe HTTP/TCP/ICMP |
