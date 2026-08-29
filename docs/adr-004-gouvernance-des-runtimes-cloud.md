# ADR-004 — Grimoire gouverne les runtimes d'agents cloud, il n'en devient pas un

- **Statut** : accepté (2026-08-26)
- **Contexte** : version 3.32, ouverture du chantier de gouvernance cloud

## Contexte

Le kit est sollicité pour « servir une solution IA cloud-native ». Cette formulation
recouvre deux produits incompatibles :

| Option | Ce que ça implique | Verdict |
|---|---|---|
| **Exécuter** des agents contre Bedrock, Gemini Enterprise Agent Platform ou Foundry | Couche d'abstraction modèle, boucle tool-calling, streaming, retry, comptage de tokens, déploiement | Rejeté |
| **Gouverner** des agents exécutés par ces plateformes | Lecture de définitions, gate CI, compilation vers contrôles natifs, audit de traces, preuve signée | Retenu |

Trois faits établis par cartographie du code et de la cible, le 2026-08-26 :

1. **Le kit n'appelle aucun LLM.** Les seules occurrences de fournisseurs dans
   `src/grimoire/` sont des constantes de chaînes (`SUPPORTED_PROVIDER_IDS`,
   `PROVIDER_ALIASES`, `PROVIDER_DEFAULT_MODELS` dans `core/agentic_standard.py`).
   Aucune dépendance à `boto3`, `google-cloud-aiplatform` ou `azure-ai-projects`.
   Les dépendances principales se limitent à `ruamel.yaml`, `typer` et `rich`.
2. **Les runtimes cibles sont des produits à budget d'ingénierie sans commune
   mesure**, et l'un d'eux (AWS Strands) est un SDK open source gratuit atteignant
   le million de téléchargements en moins de quatre mois. Une boucle d'exécution
   maison entrerait en concurrence frontale, sans avantage.
3. **Le terrain de l'autorisation a été partiellement occupé par les fournisseurs,
   celui de la preuve a été déserté.** AgentCore Policy est GA depuis le
   2026-03-03 avec du Cedar en default-deny au Gateway. Symétriquement, AWS Audit
   Manager est passé en maintenance mode : plus d'activation dans un nouveau compte
   depuis le 2026-04-30, aucune nouvelle fonctionnalité ni nouveau framework, et la
   FAQ renvoie explicitement vers des solutions partenaires. Ni Cloud Trace ni
   Application Insights ne produisent d'artefact scellé.

## Décision

1. **Grimoire est une couche design-time, CI et audit autour des runtimes d'agents.
   Il n'exécute jamais d'agent.** Bedrock répond « comment faire tourner un agent ».
   Grimoire répond « de quel droit cet agent fait ça, et puis-je le prouver ».
2. **Aucun SDK cloud n'entre dans les dépendances du kit.** Les définitions d'agents
   et les traces entrent par des fichiers exportés par l'outillage de l'utilisateur
   (`aws`, `gcloud`, `az`, IaC, export OTLP), jamais par un appel réseau depuis le kit.
3. **La compilation ne vise que des primitives stables et ennuyeuses** : Cedar, IAM
   et RBAC, attributs OpenTelemetry, OpenAPI et JSON Schema. Jamais les API d'agents,
   dont aucune n'a dix-huit mois.
4. **`PolicyEngine` n'est jamais câblé dans `RuntimeKernel.mediate_tool`.**
   `check_gates(recipe, pack)` reste une fonction pure de design-time appelée par une
   commande. Le jour où grimoire intercepte un appel d'outil en vol, il perd sa
   position d'auditeur neutre et devient un concurrent d'AgentCore Gateway, d'Agent
   Gateway et de Foundry.

## Conséquences

- Le différenciateur défendable n'est pas l'autorisation — les fournisseurs
  l'occupent — mais **le gate design-time et la preuve portable** : faire échouer un
  pipeline sur la complétude d'une déclaration avant déploiement, et produire un artefact
  vérifiable hors du plan de contrôle qui l'a émis.
- Ce périmètre est étroit et il faut l'assumer comme tel. **Microsoft Agent 365 est GA
  depuis le 2026-05-01**, synchronise son registre avec AWS Bedrock et Google Cloud, et
  vend explicitement de l'« audit-ready evidence ». Il fait de l'inventaire et du cycle de
  vie sur des agents déjà déployés, dans un tenant Microsoft — pas du blocage de pipeline
  ni de la preuve vérifiable hors de son propre plan de contrôle. C'est la seule bande
  qui reste, et elle peut se refermer.
- Le second différenciateur est la **réconciliation entre intention déclarée et
  comportement observé**. Aucun service AWS, GCP ou Azure ne confronte la définition
  approuvée aux traces réelles ; le calcul ne demande que deux surfaces stables. Réserve
  connue : un appel d'outil qui ne passe pas par un Gateway ne produit pas de trace, donc
  le contrôle est aveugle précisément là où le default-deny natif ne couvre pas — d'où
  l'obligation, en P3.4, de rendre un taux de couverture des traces à côté du verdict.
- **Le pari est révocable.** Le plan cible ouvre ses lots les plus coûteux (compilation,
  preuve signée) derrière une porte de validation exigeant qu'un dépôt tiers réel ait
  produit un verdict utile. Si cette porte n'est pas franchie, le chantier s'arrête sans
  perte : les lots d'assainissement du kit n'ont jamais dépendu de cette thèse.
- La **parité entre clouds ne sera jamais promise**. Une définition Foundry expose
  instructions, outils, protocoles et posture réseau ; une définition Gemini
  Enterprise Agent Platform enferme le comportement dans un artefact Python
  sérialisé et n'expose que le périmètre. Toute facette non lisible est rendue
  `not_computable`, jamais verte par défaut.
- La conformité réglementaire n'est **pas** l'argument de vente principal :
  ISO/IEC 42001 ne donne aucune présomption de conformité à l'AI Act, le livrable
  complémentaire prEN 18286 n'est pas publié, aucune norme harmonisée n'est au JOUE,
  les overlays NIST SP 800-53 pour systèmes agentiques ne sont qu'annoncés, et
  l'échéance haut risque de l'Annexe III a glissé au 2027-12-02. L'argument est
  l'ingénierie de la preuve — dette de preuve, incidents, coût de re-run.

## Le schéma des sorties `-o json` entre dans l'API publique

[ADR-002](adr-002-semver-policy.md) déclare publics `grimoire.core.*`, `grimoire.cli.*`
et les options documentées dans `docs/cli-reference.md`, mais reste muet sur la forme des
payloads émis par `-o json`. Une CI tierce qui parse un verdict de gate n'avait donc
aucune garantie SemVer : le schéma pouvait changer en correctif.

**Décision** : les payloads JSON portant une clé `schema` de la forme
`grimoire.<commande>/vN` font partie de l'API publique au sens d'ADR-002. Leur ensemble
de clés de premier niveau est verrouillé par test. Retirer ou renommer une clé est une
rupture majeure ; en ajouter une est mineur, et impose d'incrémenter `N` lorsque la
sémantique d'une clé existante change. Les sorties sans clé `schema` restent hors
périmètre et peuvent changer librement.

Cette décision est la condition d'existence de la posture P1 : un gate qu'un tiers ne
peut pas parser de façon stable n'est pas opposable.

## Alternatives rejetées

- **Devenir un runtime minimal « juste pour le contrôle »** — une boucle
  tool-calling, même réduite, impose de suivre les API modèles de trois fournisseurs
  et de rivaliser avec des SDK gratuits soutenus par des hyperscalers.
- **Abstraire les trois runtimes derrière une API portable** — le dénominateur commun
  réel se réduit à quatre choses : identité et autorisation d'infrastructure,
  enveloppe OTLP et W3C Trace Context, AgentCard A2A v1.0 signée, substrat OCI/IaC/CI.
  Tout le reste (formats de définition, langages de politique, taxonomies de
  guardrails) diverge sans mapping officiel. Ce qui est portable, c'est le modèle de
  gouvernance, pas le runtime.
- **Étendre `HostId` (`bridges/schemas.py`) avec Bedrock, Vertex ou Foundry** —
  cela mélangerait « l'IDE qui m'héberge » et « le runtime que je gouverne » dans une
  enum pilotée par détection de variables d'environnement locales.

## Références

- [Plan cible — gouvernance des agents cloud](https://github.com/Guilhem-Bonnet/Grimoire-kit/blob/main/planning/cloud-agent-governance-target-plan.md) (document de travail, non publié)
- [ADR-001 — Pourquoi l'orchestration n'est pas multi-LLM](adr-001-no-multi-llm.md)
- [ADR-002 — Politique SemVer](adr-002-semver-policy.md)
