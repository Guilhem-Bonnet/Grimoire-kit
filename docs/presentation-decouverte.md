# Grimoire Kit {: .gp-hero-brand }

## L'agentic OS qui empeche les projets IA de retomber a zero {: .gp-hero-title }

Une session cadre. La suivante execute. La troisieme verifie. Rien ne retombe a zero entre les trois.
{: .gp-hero-lead }

Pour les builders, leads et equipes qui veulent une continuite reelle, pas juste une bonne session.
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
[Memoire projet et session](memory-system.md)
[Workflows et artefacts](workflow-taxonomy.md)
{: .gp-hero-signals }

<div class="gp-site-rail" aria-label="Navigation presentation">
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

!!! onepager "Mouvement I — Le cout cache"
    **Le probleme n'est pas que l'IA reponde mal. Le probleme, c'est que le projet oublie.**

    Quand le contexte, les decisions et la validation restent prisonniers du chat, la reprise suivante reconsomme du temps humain au lieu de produire.

    - le rebrief devient une taxe,
    - le meme assistant pretend tout faire,
    - le "done" arrive avant la preuve,
    - la session suivante herite d'indices, pas d'un etat.

!!! onepager "Mouvement II — La bascule"
    **Avant Grimoire, la session improvise. Avec Grimoire, le projet reprend.**

    <div class="gp-contrast-grid">
        <section class="gp-contrast-card gp-contrast-card--before">
            <p class="gp-diagram-label">Sans systeme</p>
            <ul>
                <li>Contexte reexplique a chaque reprise</li>
                <li>Un seul assistant pour cadrer, coder, verifier et conclure</li>
                <li>Validation declarative sans Definition of Done stable</li>
                <li>Handoffs implicites et erreurs rediscutees plus tard</li>
            </ul>
        </section>
        <section class="gp-contrast-card gp-contrast-card--after">
            <p class="gp-diagram-label">Avec Grimoire</p>
            <ul>
                <li>Intention cadree avant execution</li>
                <li>Roles specialises et delegations observables</li>
                <li>Evidence-first, Challenger et Completion Contract</li>
                <li>Memoire, decisions et artefacts reutilisables</li>
            </ul>
        </section>
    </div>

!!! onepager "Mouvement III — La machine"
    **Grimoire montre le moteur au lieu de le cacher.**

    <div class="gp-pillars">
        <article class="gp-pillar">
            <p class="gp-pillar-kicker">Orchestration</p>
            <h3>Le bon mode, le bon role</h3>
            <p>La demande part vers le bon specialiste sans perdre son cadre.</p>
        </article>
        <article class="gp-pillar">
            <p class="gp-pillar-kicker">Roles</p>
            <h3>Le fixeur n'est pas son challenger</h3>
            <p>Chaque role porte une responsabilite lisible au lieu d'une competence floue.</p>
        </article>
        <article class="gp-pillar">
            <p class="gp-pillar-kicker">Workflows</p>
            <h3>Les garde-fous sont dans la boucle</h3>
            <p>Les phases, escalades et verifications sont codees dans le systeme.</p>
        </article>
        <article class="gp-pillar">
            <p class="gp-pillar-kicker">Memoire</p>
            <h3>Ce qui a ete appris reste</h3>
            <p>Decisions, contradictions et learnings restent utilisables a la reprise.</p>
        </article>
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
            <span>DoD, severite, surface d'impact et preuves attendues sont poses avant le code.</span>
            <em>Sortie: contrat de validation</em>
        </li>
        <li>
            <strong>Dispatch</strong>
            <span>Le bon role prend la main: analyst, dev, qa, challenger ou orchestrateur.</span>
            <em>Sortie: delegation explicite</em>
        </li>
        <li>
            <strong>Execution</strong>
            <span>Le travail suit un workflow borne plutot qu'une suite de prompts opportunistes.</span>
            <em>Sortie: implementation tracable</em>
        </li>
        <li>
            <strong>Verification</strong>
            <span>Evidence-first, Challenger et circuit-breaker testent le resultat au lieu de le declarer.</span>
            <em>Sortie: confiance operationnelle</em>
        </li>
        <li>
            <strong>Reprise</strong>
            <span>La session suivante retrouve artefacts, decisions, learnings et etat de projet.</span>
            <em>Sortie: continuite reelle</em>
        </li>
    </ol>

    <p class="gp-flow-caption">Cadrage -> execution specialisee -> evidence-first -> challenger -> memoire.</p>

!!! onepager "Mouvement V — La preuve"
    **Grimoire gagne sa credibilite en se contredisant, en se verifiant et en bornant ses propres sorties.**

    <div class="gp-trust-loop">
        <article>
            <p class="gp-diagram-label">Contract</p>
            <h3>Completion Contract</h3>
            <p>Pas de "fini" sans checks, sans preuves et sans cloture verificable.</p>
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
            <p>Le systeme veut casser un fix avant de l'accepter.</p>
        </article>
        <article>
            <p class="gp-diagram-label">Guardrails</p>
            <h3>Escalade et boucles bornees</h3>
            <p>Quand la root cause bouge ou que les iterations s'emballent, le workflow s'arrete et requalifie.</p>
        </article>
    </div>

!!! onepager "Mouvement VI — Ce qui survit"
    **Une bonne session laisse une memoire, pas une impression.**

    <div class="gp-memory-stack">
        <article>
            <p class="gp-diagram-label">Memoire de session</p>
            <h3>Ce qui permet de reprendre vite</h3>
            <p>Etat courant, arbitrages, prochain geste.</p>
        </article>
        <article>
            <p class="gp-diagram-label">Memoire projet</p>
            <h3>Ce qui permet de reprendre juste</h3>
            <p>Decisions, contradictions, conventions et learnings qui survivent a la session.</p>
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

!!! onepager "Mouvement VII — Salle de controle"
    **Grimoire n'ajoute pas un chatbot de plus dans l'IDE. Il installe une salle de controle.**

    War room pour orchestrer. Rooms pour specialiser. Timeline pour rejouer. Memoire pour tenir l'etat. Le Game UI sert a voir le travail, pas a le decorer.

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
    <h3>La session devient un etat rejouable</h3>
    <p>Snapshots, sequence ids et replay remplacent le "re-explique-moi" permanent.</p>
    </article>
    <article>
    <p class="gp-diagram-label">Team of Teams</p>
    <h3>Les roles prennent place dans des rooms</h3>
    <p>War room, build room, qa room et design room rendent visibles delegation et charge.</p>
    </article>
    <article>
    <p class="gp-diagram-label">Explainability</p>
    <h3>Les preuves deviennent un ecran de controle</h3>
    <p>Tool calls, workflow steps et decisions deviennent lisibles sans lecture forensee.</p>
    </article>
    </div>

    <div class="gp-observatory">
    <figure class="gp-observatory-frame">
    <div class="gp-observatory-screen">
    <div class="gp-observatory-hud">
    <span>war room</span>
    <span>build room</span>
    <span>spectator mode</span>
    </div>
    <img src="../assets/presentation/pixel-office-reference.jpg" alt="Prototype pixel art d'un observatoire agentique avec open space, salle de reunion et postes de travail." loading="lazy" decoding="async">
    </div>
    <figcaption>Prototype d'observatoire: positions, rooms et densite de travail deviennent visibles au premier regard.</figcaption>
    <div class="gp-room-strip">
    <span><img src="../assets/presentation/room-war.png" alt="" loading="lazy" decoding="async"> Orchestrateur</span>
    <span><img src="../assets/presentation/room-dev.png" alt="" loading="lazy" decoding="async"> Agents de build</span>
    <span>Replay timeline</span>
    </div>
    </figure>
    <div class="gp-observatory-stack">
    <div class="gp-dialogue-stage">
    <article class="gp-agent-card">
    <p class="gp-agent-bubble">Decision enregistree. Le contexte ne retombe plus.</p>
    <div class="gp-sprite gp-sprite--archivist" aria-hidden="true"></div>
    <h3>Archiviste</h3>
    </article>
    <article class="gp-agent-card">
    <p class="gp-agent-bubble gp-agent-bubble--accent">Replay synchronise. La reprise reste continue.</p>
    <div class="gp-sprite gp-sprite--ember" aria-hidden="true"></div>
    <h3>Operator Ember</h3>
    </article>
    </div>
    <div class="gp-runtime-lenses">
    <article>
    <p class="gp-diagram-label">Observatoire</p>
    <h3>Board live</h3>
    <p>Agents actifs, charge et colonnes de taches restent lisibles au meme endroit.</p>
    </article>
    <article>
    <p class="gp-diagram-label">Replay</p>
    <h3>Timeline et reconnexion</h3>
    <p>Handshake et curseur de sequence reconstruisent l'etat depuis les evenements.</p>
    </article>
    <article>
    <p class="gp-diagram-label">Contrats</p>
    <h3>Runtimes lisibles</h3>
    <p>Transitions, status, workflow steps et tool calls laissent une trace exploitable.</p>
    </article>
    </div>
    </div>
    </div>

    <p class="gp-final-kicker">Entre par un besoin. Ressors avec un systeme qui tient.</p>

    <div class="gp-final-actions">
    <a class="gp-final-actions__link gp-final-actions__link--primary" href="../workflow-design-patterns/">Lancer un premier workflow</a>
    <a class="gp-final-actions__link gp-final-actions__link--primary" href="../getting-started/">Demarrer sans setup long</a>
    <a class="gp-final-actions__link" href="../concepts/">Explorer la carte des concepts</a>
    <a class="gp-final-actions__link" href="../memory-system/">Ouvrir la memoire projet</a>
    </div>

<!-- markdownlint-enable MD033 -->
