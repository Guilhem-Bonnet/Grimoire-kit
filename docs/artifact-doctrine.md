<p align="right"><a href="../README.md">README</a></p>

# <img src="../assets/icons/lightbulb.svg" width="32" height="32" alt=""> Doctrine — quand créer un artefact, et quand s'en abstenir

Ce document répond à **quand**. Il complète [Créer un agent](creating-agents.md), qui répond à
**comment** — il ne le remplace pas. Les mêmes questions valent pour un skill ou un prompt, même
si leurs gabarits vivent ailleurs (`extensions/*/artifacts/skills/`, `extensions/*/artifacts/prompts/`).

Neuf agents fantômes référencés par des manifestes d'équipe qu'aucun archétype ne livrait, un
mécanisme d'artefacts éphémères mort sans un seul usage, un outil d'ajout qui n'écrivait aucun
fichier : trois échecs distincts, une seule cause commune — rien ne disait quand un artefact
méritait d'exister. Cette doctrine comble ce vide.

<img src="../assets/divider.svg" width="100%" alt="">

## <img src="../assets/icons/lightbulb.svg" width="28" height="28" alt=""> Le critère qui discrimine les trois types

Une seule question : **la décision peut-elle être écrite à l'avance, et qui invoque ?**

| Type | La décision | Qui invoque |
|---|---|---|
| **Skill** | Écrite d'avance, séquence stable | Le contexte — personne n'arbitre à l'exécution, on suit |
| **Prompt** | Écrite d'avance, avec un livrable nommé | Une personne |
| **Agent** | Impossible à écrire d'avance — jugement requis sur des cas non énumérés | Le dispatch, au cas par cas |

Si la réponse tient dans un skill ou un prompt, ne créez pas d'agent : c'est le chemin le plus
cher pour le même résultat.

## <img src="../assets/icons/lightbulb.svg" width="28" height="28" alt=""> La règle qui borne le nombre d'agents

**Un agent doit avoir une frontière d'outils qui lui est propre.** À accès et droits identiques à
ceux du généraliste, ce n'est pas un spécialiste, c'est un ton de voix. Les neuf agents fantômes
échouaient exactement là : un organigramme humain — architecte, développeur, testeur — plaqué sur
un outil, sans aucune frontière distincte.

Le nombre d'agents est donc borné par le nombre de frontières d'outils réellement différentes, pas
par le nombre de métiers imaginables. C'est pourquoi le gabarit d'agent (voir plus bas) porte
`tool_boundary` comme champ obligatoire : chemins, commandes ou surfaces auxquelles l'agent a accès,
distincts de ceux du généraliste ou des agents voisins.

## <img src="../assets/icons/lightbulb.svg" width="28" height="28" alt=""> Les deux tests, avant de créer quoi que ce soit

1. **Si je le retire, qu'est-ce qui casse ?** Réponse « rien, on ferait pareil à la main » :
   l'artefact ne doit pas exister. Ce test seul aurait arrêté les trois échecs cités en
   introduction.
2. **Quel est le moins cher qui fait le travail ?** Un prompt ne coûte rien tant qu'on ne
   l'invoque pas ; un skill coûte quelques lignes lues quand le contexte correspond ; un agent
   coûte un contexte entier, un dispatch, et le risque de diverger. On prend le moins cher qui
   répond, jamais le plus prestigieux.

## <img src="../assets/icons/lightbulb.svg" width="28" height="28" alt=""> Ce qui rend la règle vérifiable : la clause d'emploi

**Tout artefact déclare quand l'employer et quand ne pas l'employer.** Un artefact sans clause
d'emploi ne pourra jamais être retiré, faute de pouvoir dire s'il a servi. C'est le chaînon entre
l'instrumentation du choix d'agent et une future règle contre les artefacts morts.

Concrètement, les gabarits déclarent la clause comme des champs de frontmatter obligatoires :

```yaml
use_when: "Situation précise où invoquer cet artefact."
dont_use_when: "Cas hors périmètre, avec le nom de l'artefact compétent si possible."
tools: "read, edit, execute — la frontière grossière (agents uniquement)."
tool_boundary: "Frontière fine — chemins, commandes, surfaces (agents uniquement)."
```

- `use_when` et `dont_use_when` sont obligatoires pour les trois types (agent, skill, prompt).
- `tools` et `tool_boundary` sont obligatoires pour les agents uniquement — c'est la règle qui
  borne leur nombre, elle n'a pas de sens pour un skill ou un prompt qui n'ont pas de frontière
  d'outils propre à déclarer. `tools` dit la capacité (peut-il éditer, exécuter ?), `tool_boundary`
  dit le périmètre (quels fichiers, quelles commandes). Un agent qui hérite des mêmes capacités que
  le généraliste sans périmètre distinct n'a pas de frontière propre — c'est un candidat au retrait,
  pas un champ à remplir par défaut.

Un artefact qui ne sait pas répondre à `dont_use_when` est un candidat au retrait, pas un
artefact à documenter de force : voir le test des deux questions ci-dessus.

## <img src="../assets/icons/lightbulb.svg" width="28" height="28" alt=""> Décisions déjà prises

- Un artefact est **proposé puis validé**, jamais créé d'office : un système qui crée seul sur un
  projet que personne ne regarde reproduit ce qu'on vient de nettoyer.
- Le socle initial reste **par archétype**, comme le kit le fait déjà, mais assumé explicitement
  plutôt que subi.
- **Un skill appartient à un agent par défaut** (décision du 2026-09-10, issue #372). Les skills
  sont émis au niveau du projet : chaque tour de session paie leur description — environ soixante
  tokens par skill — qu'ils servent ou non, et ce coût croît linéairement avec chaque skill ajouté.
  Un skill attaché à un agent (`skills:` dans son frontmatter) ne coûte que sur les tours où cet
  agent travaille : son corps, multiplié par les seuls tours de cet agent, jamais par ceux de la
  session entière. Un skill ne redevient transversal que si l'instrumentation montre qu'au moins
  deux agents le mobilisent réellement — avant #372, tout était transversal par défaut et rien
  n'appartenait à personne : dix skills livrés, zéro rattaché. Les prompts restent transversaux :
  ils ne se chargent qu'à l'invocation, leur rattachement n'a pas d'enjeu économique.
  Voir `tests/unit/test_skill_attachment_cost.py` pour la mesure chiffrée (tokens par tour, avec
  et sans attachement) et `AgentSpec.fingerprint()` / `duplicate_agent_fingerprints()`
  (`src/grimoire/hosts/surface.py`) pour la garde qui interdit à deux agents de déclarer le même
  faisceau outils + contexte + skills.

## <img src="../assets/icons/lightbulb.svg" width="28" height="28" alt=""> Vérification

`tests/unit/test_artifact_employment_clause.py` échoue si un agent livré par le kit
(`archetypes/*/agents/*.md`) ne déclare pas `use_when`, `dont_use_when` et `tool_boundary`. La
garde ne couvre pour l'instant que les agents ; son extension aux skills et prompts livrés
(`extensions/*/artifacts/{skills,prompts}/`) reste à faire — voir la discussion de l'issue #368.

<img src="../assets/divider.svg" width="100%" alt="">

Voir aussi : [Créer un agent](creating-agents.md) (le comment), [Taxonomie des workflows](workflow-taxonomy.md).
