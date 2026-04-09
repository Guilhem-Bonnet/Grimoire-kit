# Grimoire Kit {: .gp-hero-brand }

## L'agentic OS qui empêche les projets IA de retomber à zéro {: .gp-hero-title }

Une session cadre. La suivante exécute. La troisième vérifie. Rien ne retombe à zéro entre les trois.
{: .gp-hero-lead }

Pour les builders, leads et équipes qui veulent une continuité réelle, pas juste une bonne session.
{: .gp-hero-audience }

[Voir un workflow vivant](workflow-design-patterns.md)
[Entrer dans le Quick Start](getting-started.md)
[Explorer l'architecture](concepts.md)
{: .gp-hero-actions }

<!-- markdownlint-disable MD033 -->

<div class="gp-hero-stage" aria-hidden="true">
    <div class="gp-hero-stage__orbit">
        <span>Intent</span>
        <span>Dispatch</span>
        <span class="gp-hero-stage__node--proof">Proof</span>
        <span>Replay</span>
    </div>
    <div class="gp-hero-stage__timeline">
        <article>
            <p>Session 01</p>
            <strong>Cadre</strong>
        </article>
        <article>
            <p>Session 02</p>
            <strong>Execution</strong>
        </article>
        <article>
            <p>Session 03</p>
            <strong>Verification</strong>
        </article>
    </div>
</div>

[Orchestration et intent routing](concepts.md)
[Evidence-first et Completion Contract](workflow-design-patterns.md)
[Mémoire projet et session](memory-system.md)
[Workflows et artefacts](workflow-taxonomy.md)
{: .gp-hero-signals }

<div class="gp-site-rail" aria-label="Navigation de la présentation">
    <span class="gp-site-rail__label">Parcours</span>
    <div class="gp-site-rail__items">
        <a href="../">Accueil</a>
        <a href="../signaux/">Signaux</a>
        <a href="../workflow-design-patterns/">Workflows</a>
        <a href="../getting-started/">Quick Start</a>
        <a href="../concepts/">Concepts</a>
    </div>
</div>

{{ grimoire_signals_presentation }}

!!! onepager "Mouvement I — Le coût caché"
    **Le problème n'est pas que l'IA réponde mal. Le problème, c'est que le projet oublie.**

    Quand le contexte, les décisions et la validation restent prisonniers du chat, la reprise suivante reconsomme du temps humain au lieu de produire.

    - le rebrief devient une taxe,
    - le même assistant prétend tout faire,
    - le "done" arrive avant la preuve,
    - la session suivante hérite d'indices, pas d'un état.

!!! onepager "Mouvement II — La bascule"
    **Avant Grimoire, la session improvise. Avec Grimoire, le projet reprend.**

    <div class="gp-contrast-grid">
        <section class="gp-contrast-card gp-contrast-card--before">
            <p class="gp-diagram-label">Sans système</p>
            <ul>
                <li>Contexte réexpliqué à chaque reprise</li>
                <li>Un seul assistant pour cadrer, coder, vérifier et conclure</li>
                <li>Validation déclarative sans Definition of Done stable</li>
                <li>Handoffs implicites et erreurs rediscutées plus tard</li>
            </ul>
        </section>
        <section class="gp-contrast-card gp-contrast-card--after">
            <p class="gp-diagram-label">Avec Grimoire</p>
            <ul>
                <li>Intention cadrée avant exécution</li>
                <li>Rôles spécialisés et délégations observables</li>
                <li>Evidence-first, Challenger et Completion Contract</li>
                <li>Mémoire, décisions et artefacts réutilisables</li>
            </ul>
        </section>
    </div>

!!! onepager "Mouvement III — La machine"
    **Grimoire montre le moteur au lieu de le cacher.**

    <div class="gp-pillars">
        <article class="gp-pillar">
            <p class="gp-pillar-kicker">Orchestration</p>
            <h3>Le bon mode, le bon rôle</h3>
            <p>La demande part vers le bon spécialiste sans perdre son cadre.</p>
        </article>
        <article class="gp-pillar">
            <p class="gp-pillar-kicker">Roles</p>
            <h3>Le fixeur n'est pas son challenger</h3>
            <p>Chaque rôle porte une responsabilité lisible au lieu d'une compétence floue.</p>
        </article>
        <article class="gp-pillar">
            <p class="gp-pillar-kicker">Workflows</p>
            <h3>Les garde-fous sont dans la boucle</h3>
            <p>Les phases, escalades et vérifications sont codées dans le système.</p>
        </article>
        <article class="gp-pillar">
            <p class="gp-pillar-kicker">Mémoire</p>
            <h3>Ce qui a été appris reste</h3>
            <p>Décisions, contradictions et learnings restent utilisables à la reprise.</p>
        </article>
    </div>

    <div class="gp-machine-preview">
        <figure class="gp-machine-preview__frame">
            <p class="gp-machine-preview__eyebrow">Aperçu visuel</p>
            <img src="../assets/presentation/pixel-office-reference.jpg" alt="Vue pixel art d'un observatoire avec postes de travail, war room et agents visibles." loading="lazy" decoding="async">
            <figcaption>Un observatoire lisible où rooms, rôles, mémoire et contexte restent visibles sans quitter l'IDE.</figcaption>
        </figure>
        <aside class="gp-machine-preview__aside">
            <p class="gp-diagram-label">Repères runtime</p>
            <ul class="gp-machine-preview__rooms">
                <li><img src="../assets/presentation/room-war.png" alt="" loading="lazy" decoding="async"> War room</li>
                <li><img src="../assets/presentation/room-dev.png" alt="" loading="lazy" decoding="async"> Build room</li>
                <li><span class="gp-machine-preview__badge">Replay</span> Timeline et état rejouable</li>
            </ul>
            <div class="gp-machine-preview__agents">
                <article>
                    <div class="gp-sprite gp-sprite--archivist" aria-hidden="true"></div>
                    <strong>Archiviste</strong>
                    <p>Les décisions et la mémoire projet restent visibles et récupérables.</p>
                </article>
                <article>
                    <div class="gp-sprite gp-sprite--ember" aria-hidden="true"></div>
                    <strong>Operator Ember</strong>
                    <p>Le replay continue au lieu de casser au changement de session.</p>
                </article>
            </div>
        </aside>
    </div>

!!! onepager "Mouvement IV — Le fil"
    **Le fil d'un besoin ne casse plus entre la demande initiale et la reprise suivante.**

    <ol class="gp-flow-rail">
        <li>
            <strong>Intention</strong>
            <span>Le besoin arrive avec objectif, contrainte et contexte initial.</span>
            <em>Sortie: cadrage clair</em>
        </li>
        <li>
            <strong>Cadrage</strong>
            <span>DoD, sévérité, surface d'impact et preuves attendues sont posées avant le code.</span>
            <em>Sortie: contrat de validation</em>
        </li>
        <li>
            <strong>Dispatch</strong>
            <span>Le bon rôle prend la main: analyst, dev, qa, challenger ou orchestrateur.</span>
            <em>Sortie: délégation explicite</em>
        </li>
        <li>
            <strong>Execution</strong>
            <span>Le travail suit un workflow borné plutôt qu'une suite de prompts opportunistes.</span>
            <em>Sortie: implémentation traçable</em>
        </li>
        <li>
            <strong>Verification</strong>
            <span>Evidence-first, Challenger et circuit-breaker testent le résultat au lieu de le déclarer.</span>
            <em>Sortie: confiance opérationnelle</em>
        </li>
        <li>
            <strong>Reprise</strong>
            <span>La session suivante retrouve artefacts, décisions, learnings et état de projet.</span>
            <em>Sortie: continuité réelle</em>
        </li>
    </ol>

    <p class="gp-flow-caption">Cadrage → exécution spécialisée → evidence-first → challenger → mémoire.</p>

!!! onepager "Mouvement V — La preuve"
    **Grimoire gagne sa crédibilité en se contredisant, en se vérifiant et en bornant ses propres sorties.**

    <div class="gp-trust-loop">
        <article>
            <p class="gp-diagram-label">Contract</p>
            <h3>Completion Contract</h3>
            <p>Pas de "fini" sans checks, sans preuves et sans clôture vérifiable.</p>
        </article>
        <article>
            <p class="gp-diagram-label">Proof</p>
            <h3>Evidence-first</h3>
            <p>Les commandes, sorties et verdicts s'attachent au flux au lieu de rester implicites.</p>
        </article>
        <article class="gp-trust-loop__core">
            <p>Confiance</p>
            <strong>DoD + Challenger + Circuit-Breaker</strong>
        </article>
        <article>
            <p class="gp-diagram-label">Challenge</p>
            <h3>Adversarial review</h3>
            <p>Le système veut casser un fix avant de l'accepter.</p>
        </article>
        <article>
            <p class="gp-diagram-label">Guardrails</p>
            <h3>Escalade et boucles bornées</h3>
            <p>Quand la root cause bouge ou que les itérations s'emballent, le workflow s'arrête et requalifie.</p>
        </article>
    </div>

!!! onepager "Mouvement VI — Ce qui survit"
    **Une bonne session laisse une mémoire, pas une impression.**

    <div class="gp-memory-stack">
        <article>
            <p class="gp-diagram-label">Mémoire de session</p>
            <h3>Ce qui permet de reprendre vite</h3>
            <p>État courant, arbitrages, prochain geste.</p>
        </article>
        <article>
            <p class="gp-diagram-label">Mémoire projet</p>
            <h3>Ce qui permet de reprendre juste</h3>
            <p>Décisions, contradictions, conventions et learnings qui survivent à la session.</p>
        </article>
        <article>
            <p class="gp-diagram-label">Artefacts</p>
            <h3>Ce qui permet de reprendre avec preuves</h3>
            <p>Rapports, handoffs, traces et preuves que l'on peut rejouer.</p>
        </article>
    </div>

    <ul class="gp-artifact-strip">
        <li>Routing</li>
        <li>Contracts</li>
        <li>Workflow map</li>
        <li>Decision logs</li>
        <li>Failure museum</li>
        <li>Replay traces</li>
    </ul>

!!! onepager "Mouvement VII — Salle de contrôle"
    **Grimoire n'ajoute pas un chatbot de plus dans l'IDE. Il installe une salle de contrôle.**

    War room pour orchestrer. Rooms pour spécialiser. Timeline pour rejouer. Mémoire pour tenir l'état. Le Game UI sert à voir le travail, pas à le décorer.

    <div class="gp-agentic-map">
    <span>Intent</span>
    <span>Router</span>
    <span>Rooms</span>
    <span class="gp-agentic-map__node--proof">Proof</span>
    <span>Replay</span>
    </div>

    <div class="gp-os-grid">
    <article>
    <p class="gp-diagram-label">System of record</p>
    <h3>La session devient un état rejouable</h3>
    <p>Snapshots, sequence ids et replay remplacent le "re-explique-moi" permanent.</p>
    </article>
    <article>
    <p class="gp-diagram-label">Team of Teams</p>
    <h3>Les rôles prennent place dans des rooms</h3>
    <p>War room, build room, qa room et design room rendent visibles délégation et charge.</p>
    </article>
    <article>
    <p class="gp-diagram-label">Explainability</p>
    <h3>Les preuves deviennent un écran de contrôle</h3>
    <p>Tool calls, workflow steps et décisions deviennent lisibles sans lecture forensique.</p>
    </article>
    </div>

    <div class="gp-observatory">
    <figure class="gp-observatory-frame">
    <div class="gp-observatory-screen">
    <div class="gp-observatory-hud">
    <span>war room</span>
    <span>build room</span>
    <span>mode spectateur</span>
    </div>
    <img src="../assets/presentation/pixel-office-reference.jpg" alt="Prototype pixel art d'un observatoire agentique avec open space, salle de réunion et postes de travail." loading="lazy" decoding="async">
    </div>
    <figcaption>Prototype d'observatoire: positions, rooms et densité de travail deviennent visibles au premier regard.</figcaption>
    <div class="gp-room-strip">
    <span><img src="../assets/presentation/room-war.png" alt="" loading="lazy" decoding="async"> Orchestrateur</span>
    <span><img src="../assets/presentation/room-dev.png" alt="" loading="lazy" decoding="async"> Agents de build</span>
    <span>Replay timeline</span>
    </div>
    </figure>
    <div class="gp-observatory-stack">
    <div class="gp-dialogue-stage">
    <article class="gp-agent-card">
    <p class="gp-agent-bubble">Décision enregistrée. Le contexte ne retombe plus.</p>
    <div class="gp-sprite gp-sprite--archivist" aria-hidden="true"></div>
    <h3>Archiviste</h3>
    </article>
    <article class="gp-agent-card">
    <p class="gp-agent-bubble gp-agent-bubble--accent">Replay synchronisé. La reprise reste continue.</p>
    <div class="gp-sprite gp-sprite--ember" aria-hidden="true"></div>
    <h3>Operator Ember</h3>
    </article>
    </div>
    <div class="gp-runtime-lenses">
    <article>
    <p class="gp-diagram-label">Observatoire</p>
    <h3>Board live</h3>
    <p>Agents actifs, charge et colonnes de tâches restent lisibles au même endroit.</p>
    </article>
    <article>
    <p class="gp-diagram-label">Replay</p>
    <h3>Timeline et reconnexion</h3>
    <p>Handshake et curseur de séquence reconstruisent l'état depuis les événements.</p>
    </article>
    <article>
    <p class="gp-diagram-label">Contrats</p>
    <h3>Runtimes lisibles</h3>
    <p>Transitions, status, workflow steps et tool calls laissent une trace exploitable.</p>
    </article>
    </div>
    </div>
    </div>

    <p class="gp-final-kicker">Entre par un besoin. Ressors avec un système qui tient.</p>

    <div class="gp-final-actions">
    <a class="gp-final-actions__link gp-final-actions__link--primary" href="../workflow-design-patterns/">Lancer un premier workflow</a>
    <a class="gp-final-actions__link gp-final-actions__link--primary" href="../getting-started/">Démarrer sans setup long</a>
    <a class="gp-final-actions__link" href="../concepts/">Explorer la carte des concepts</a>
    <a class="gp-final-actions__link" href="../memory-system/">Ouvrir la mémoire projet</a>
    </div>

<!-- markdownlint-enable MD033 -->
