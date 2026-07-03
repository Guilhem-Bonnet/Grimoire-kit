import { copyFile, mkdir, writeFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';

import { createRuntimeViewsDemoData } from './runtime-views-demo-data';
import { collectObservatorySources } from './observatory-sources';
import { materializeProofSources, type MaterializedProofSource } from './proof-sources';

interface ObservatorySource {
  id: string;
  label: string;
  scope: string;
  url: string;
  available: boolean;
}

interface RuntimeViewsExplorerPayload extends ReturnType<typeof createRuntimeViewsDemoData> {
  observatorySources: readonly ObservatorySource[];
  proofSources: readonly MaterializedProofSource[];
  latestProofRunId: string | null;
}

function escapeHtml(value: string): string {
  return value
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function serializeForHtml(value: unknown): string {
  return JSON.stringify(value)
    .replaceAll('<', '\\u003c')
    .replaceAll('-->', '--\\>');
}

function renderReport(payload: RuntimeViewsExplorerPayload): string {
  const initialScenario = payload.scenarios.find((scenario) => scenario.id === payload.defaultScenarioId) ?? payload.scenarios[0];
  const generatedAt = new Date().toISOString();

  return `<!doctype html>
<html lang="fr">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Grimoire Game Cockpit Shell</title>
    <style>
      :root {
        --bg: #f4ecdf;
        --bg-deep: #eadfcf;
        --panel: rgba(255, 252, 247, 0.88);
        --panel-strong: rgba(255, 250, 242, 0.98);
        --border: rgba(65, 48, 27, 0.14);
        --text: #241d16;
        --muted: #6f6458;
        --accent: #c95f38;
        --teal: #195a68;
        --good: #2f6b41;
        --good-soft: rgba(47, 107, 65, 0.12);
        --warn: #9a5f1f;
        --warn-soft: rgba(154, 95, 31, 0.12);
        --danger: #a1372a;
        --danger-soft: rgba(161, 55, 42, 0.12);
        --shadow: 0 26px 70px rgba(56, 38, 18, 0.12);
        --shadow-soft: 0 14px 36px rgba(56, 38, 18, 0.08);
        --font-body: "IBM Plex Sans", "Segoe UI Variable", "Segoe UI", sans-serif;
        --font-display: "Alegreya Sans", "Trebuchet MS", sans-serif;
        --font-mono: "IBM Plex Mono", "Cascadia Code", monospace;
      }

      * { box-sizing: border-box; }
      html { scroll-behavior: smooth; }

      body {
        margin: 0;
        min-height: 100vh;
        font-family: var(--font-body);
        color: var(--text);
        background:
          radial-gradient(circle at 0% 0%, rgba(25, 90, 104, 0.14), transparent 26%),
          radial-gradient(circle at 100% 0%, rgba(201, 95, 56, 0.18), transparent 22%),
          linear-gradient(180deg, var(--bg) 0%, var(--bg-deep) 100%);
      }

      button,
      input,
      select,
      a {
        font: inherit;
      }

      .shell {
        max-width: 1440px;
        margin: 0 auto;
        padding: 28px 18px 72px;
      }

      .hero {
        display: grid;
        grid-template-columns: minmax(0, 1.35fr) minmax(300px, 0.65fr);
        gap: 20px;
        align-items: stretch;
      }

      .panel {
        border: 1px solid var(--border);
        border-radius: 28px;
        background: var(--panel);
        backdrop-filter: blur(14px);
        box-shadow: var(--shadow);
      }

      .panel-hero {
        padding: 30px;
        position: relative;
        overflow: hidden;
        background:
          linear-gradient(145deg, rgba(255,255,255,0.76), rgba(254,248,240,0.94)),
          linear-gradient(160deg, rgba(201, 95, 56, 0.12), rgba(25, 90, 104, 0.12));
      }

      .panel-hero::after {
        content: '';
        position: absolute;
        inset: auto -12% -28% auto;
        width: 260px;
        height: 260px;
        background: radial-gradient(circle, rgba(201, 95, 56, 0.24), transparent 68%);
        pointer-events: none;
      }

      .panel-soft {
        padding: 22px;
        box-shadow: var(--shadow-soft);
      }

      .panel-block { margin-top: 22px; }

      .eyebrow {
        display: inline-flex;
        align-items: center;
        gap: 8px;
        padding: 8px 12px;
        border-radius: 999px;
        background: rgba(25, 90, 104, 0.08);
        color: var(--teal);
        font-size: 0.78rem;
        font-weight: 800;
        letter-spacing: 0.08em;
        text-transform: uppercase;
      }

      h1, h2, h3, h4, p, pre { margin: 0; }
      h1, h2, h3, h4 { font-family: var(--font-display); }

      h1 {
        margin-top: 18px;
        font-size: clamp(2.5rem, 6vw, 4.75rem);
        line-height: 0.94;
        letter-spacing: -0.05em;
        max-width: 11ch;
      }

      h2 { font-size: 1.55rem; letter-spacing: -0.02em; }
      h3 { font-size: 1.1rem; letter-spacing: -0.02em; }
      h4 {
        font-size: 0.82rem;
        letter-spacing: 0.1em;
        text-transform: uppercase;
        color: var(--muted);
      }

      .lead,
      .muted {
        color: var(--muted);
        line-height: 1.55;
      }

      .lead {
        max-width: 58ch;
        margin-top: 14px;
        font-size: 1.05rem;
      }

      .hero-meta,
      .legend,
      .json-tabs,
      .scenario-tags,
      .stat-pills,
      .toolbar-group,
      .mode-selector {
        display: flex;
        flex-wrap: wrap;
        gap: 10px;
      }

      .hero-meta { margin-top: 24px; }
      .scenario-tags { margin-top: 14px; }
      .stat-pills { margin-top: 14px; }

      .chip,
      .pill,
      .filter-button,
      .scenario-button,
      .tab-button,
      .copy-button,
      .mode-button,
      .source-button,
      .nav-link-button {
        border: 0;
        border-radius: 999px;
        padding: 9px 13px;
        font-size: 0.84rem;
        font-weight: 700;
        line-height: 1;
        text-decoration: none;
      }

      .chip,
      .pill {
        display: inline-flex;
        align-items: center;
        gap: 8px;
        background: rgba(255,255,255,0.72);
        border: 1px solid rgba(65, 48, 27, 0.08);
      }

      .badge-outcome-clear { background: var(--good-soft); color: var(--good); }
      .badge-outcome-attention { background: var(--warn-soft); color: var(--warn); }
      .badge-outcome-blocked { background: var(--danger-soft); color: var(--danger); }

      .summary-grid,
      .stats-grid,
      .scenario-grid,
      .compare-grid,
      .content-grid,
      .card-grid,
      .inspector-grid,
      .observatory-grid,
      .war-room-grid,
      .observer-room-grid,
      .observer-entity-grid,
      .workflow-path-grid,
      .workflow-columns {
        display: grid;
        gap: 16px;
      }

      .summary-grid,
      .stats-grid { grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); }
      .scenario-grid { grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); }
      .compare-grid { grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); }
      .content-grid { grid-template-columns: minmax(0, 1.15fr) minmax(300px, 0.85fr); margin-top: 20px; }
      .card-grid,
      .war-room-grid { grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); }
      .inspector-grid,
      .observatory-grid { grid-template-columns: minmax(0, 0.9fr) minmax(0, 1.1fr); }
      .observer-room-grid,
      .observer-entity-grid,
      .workflow-path-grid,
      .workflow-columns,
      .card-grid.dual-grid { grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); }
      .observer-grid,
      .expert-grid { grid-template-columns: minmax(0, 1.08fr) minmax(320px, 0.92fr); }

      .metric,
      .card,
      .scenario-card,
      .war-room-zone {
        padding: 18px;
        border-radius: 22px;
        border: 1px solid rgba(65, 48, 27, 0.08);
        background: var(--panel-strong);
      }

      .metric span {
        display: block;
        margin-bottom: 8px;
        font-size: 0.76rem;
        letter-spacing: 0.1em;
        text-transform: uppercase;
        color: var(--muted);
      }

      .metric strong { font-size: 1.2rem; }
      .metric small { display: block; margin-top: 6px; color: var(--muted); }

      .toolbar,
      .mode-shell {
        margin-top: 24px;
        display: flex;
        flex-wrap: wrap;
        gap: 12px;
        padding: 14px;
        border-radius: 24px;
        background: rgba(255, 252, 247, 0.78);
        border: 1px solid rgba(65, 48, 27, 0.08);
        backdrop-filter: blur(12px);
        box-shadow: var(--shadow-soft);
      }

      .toolbar {
        position: sticky;
        top: 12px;
        z-index: 10;
      }

      .toolbar-label {
        font-size: 0.78rem;
        text-transform: uppercase;
        letter-spacing: 0.1em;
        color: var(--muted);
      }

      .scenario-button,
      .filter-button,
      .tab-button,
      .copy-button,
      .mode-button,
      .source-button,
      .nav-link-button {
        cursor: pointer;
        background: rgba(255,255,255,0.72);
        color: var(--text);
        border: 1px solid rgba(65, 48, 27, 0.08);
        transition: transform 120ms ease, background 120ms ease, border-color 120ms ease;
      }

      .scenario-button:hover,
      .filter-button:hover,
      .tab-button:hover,
      .copy-button:hover,
      .mode-button:hover,
      .source-button:hover,
      .nav-link-button:hover {
        transform: translateY(-1px);
        border-color: rgba(25, 90, 104, 0.25);
      }

      .scenario-button.is-active,
      .filter-button.is-active,
      .tab-button.is-active,
      .mode-button.is-active,
      .source-button.is-active {
        background: linear-gradient(135deg, rgba(25, 90, 104, 0.12), rgba(201, 95, 56, 0.14));
        border-color: rgba(25, 90, 104, 0.25);
      }

      .scenario-card-header,
      .card-header,
      .row-spread,
      .section-head,
      .json-toolbar,
      .mode-head {
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        gap: 12px;
      }

      .section-head { margin-bottom: 16px; }
      .scenario-card p { margin-top: 10px; }
      .card-subtitle { margin-top: 5px; font-size: 0.92rem; color: var(--muted); }

      .callout {
        padding: 18px;
        border-radius: 22px;
        background: linear-gradient(135deg, rgba(25, 90, 104, 0.08), rgba(201, 95, 56, 0.06));
        border: 1px solid rgba(25, 90, 104, 0.12);
      }

      .timeline {
        display: grid;
        gap: 12px;
      }

      .timeline-item {
        display: grid;
        grid-template-columns: 36px 1fr;
        gap: 14px;
        align-items: start;
      }

      .timeline-marker {
        width: 36px;
        height: 36px;
        border-radius: 50%;
        display: grid;
        place-items: center;
        background: linear-gradient(135deg, rgba(201, 95, 56, 0.14), rgba(25, 90, 104, 0.14));
        color: var(--accent);
        font-weight: 800;
      }

      .issue-list {
        margin-top: 14px;
        padding-left: 18px;
      }

      .issue-list li + li { margin-top: 6px; }

      .json-shell {
        border-radius: 22px;
        overflow: hidden;
        border: 1px solid rgba(65, 48, 27, 0.1);
        background: #201a16;
      }

      .json-toolbar {
        padding: 12px 14px;
        background: rgba(255,255,255,0.06);
        color: #f6e9dd;
      }

      pre {
        margin: 0;
        padding: 18px;
        overflow: auto;
        font-family: var(--font-mono);
        font-size: 0.86rem;
        line-height: 1.55;
        color: #f7f4ef;
      }

      .footer-note {
        margin-top: 26px;
        font-size: 0.9rem;
        color: var(--muted);
      }

      .kbd {
        font-family: var(--font-mono);
        font-size: 0.9em;
        padding: 3px 6px;
        border-radius: 8px;
        background: rgba(255,255,255,0.72);
        border: 1px solid rgba(65, 48, 27, 0.1);
      }

      .mode-section[hidden] { display: none !important; }

      .observatory-frame {
        width: 100%;
        min-height: 72vh;
        border: 1px solid rgba(65, 48, 27, 0.08);
        border-radius: 20px;
        background: rgba(255,255,255,0.62);
      }

      .game-ui-stage {
        position: relative;
        overflow: hidden;
        background: linear-gradient(160deg, rgba(7, 25, 29, 0.97), rgba(15, 40, 48, 0.96));
        color: #f7ead7;
      }

      .game-ui-stage::before {
        content: "";
        position: absolute;
        inset: 0;
        background:
          linear-gradient(90deg, rgba(255,255,255,0.04) 1px, transparent 1px),
          linear-gradient(180deg, rgba(255,255,255,0.04) 1px, transparent 1px),
          radial-gradient(circle at top right, rgba(244, 215, 159, 0.16), transparent 28%);
        background-size: 14px 14px, 14px 14px, auto;
        opacity: 0.38;
        pointer-events: none;
      }

      .game-ui-stage > * {
        position: relative;
        z-index: 1;
      }

      .game-ui-stage .muted,
      .game-ui-stage .metric span,
      .game-ui-stage .metric small,
      .game-ui-stage .toolbar-label {
        color: rgba(247, 234, 215, 0.74);
      }

      .game-ui-stage .metric,
      .game-ui-stage-panel {
        background: rgba(255,255,255,0.08);
        border: 1px solid rgba(255,255,255,0.12);
        color: #f7ead7;
      }

      .game-ui-stage .pill,
      .game-ui-stage .source-button,
      .game-ui-stage .nav-link-button {
        background: rgba(255,255,255,0.08);
        border-color: rgba(255,255,255,0.12);
        color: #f7ead7;
      }

      .game-ui-stage-grid {
        display: grid;
        grid-template-columns: minmax(280px, 0.72fr) minmax(0, 1.28fr);
        gap: 16px;
        align-items: start;
      }

      .game-ui-stage-stack {
        display: grid;
        gap: 14px;
        align-content: start;
      }

      .game-ui-stage-panel {
        padding: 16px;
        border-radius: 20px;
      }

      .game-ui-stage-frame {
        min-height: 560px;
        background: #090d10;
        image-rendering: pixelated;
      }

      .source-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
        gap: 14px;
      }

      .source-card {
        padding: 16px;
        border-radius: 20px;
        border: 1px solid rgba(65, 48, 27, 0.08);
        background: var(--panel-strong);
      }

      .source-card[data-available="false"] {
        opacity: 0.68;
      }

      .war-room-zone {
        min-height: 240px;
      }

      .war-room-zone[data-tone="blocked"] {
        box-shadow: inset 0 0 0 1px rgba(161, 55, 42, 0.18);
      }

      .war-room-zone[data-tone="attention"] {
        box-shadow: inset 0 0 0 1px rgba(154, 95, 31, 0.18);
      }

      .war-room-zone[data-tone="clear"] {
        box-shadow: inset 0 0 0 1px rgba(47, 107, 65, 0.18);
      }

      .war-room-rail {
        display: grid;
        gap: 12px;
      }

      .war-room-rail-card {
        padding: 16px;
        border-radius: 18px;
        background: rgba(255,255,255,0.72);
        border: 1px solid rgba(65, 48, 27, 0.08);
      }

      .panel-block-tight { margin-top: 12px; }

      .subcard.is-focus {
        box-shadow: inset 0 0 0 1px rgba(25, 90, 104, 0.18);
      }

      @media (max-width: 1040px) {
        .hero,
        .content-grid,
        .inspector-grid,
        .observatory-grid,
        .workflow-columns,
        .game-ui-stage-grid {
          grid-template-columns: 1fr;
        }
      }

      @media (max-width: 720px) {
        .shell {
          padding-inline: 14px;
        }

        .panel-hero,
        .panel-soft,
        .card,
        .scenario-card,
        .war-room-zone,
        .source-card {
          padding: 18px;
        }

        .toolbar,
        .section-head,
        .scenario-card-header,
        .card-header,
        .row-spread,
        .json-toolbar,
        .mode-head {
          flex-direction: column;
          align-items: stretch;
        }

        .game-ui-stage-frame {
          min-height: 420px;
        }
      }
    </style>
  </head>
  <body>
    <main class="shell">
      <section class="hero">
        <article class="panel panel-hero">
          <span class="eyebrow">Game UI Shell</span>
          <h1>Un shell web runtime complet, du cockpit au noyau forge</h1>
          <p class="lead">
            Cette coque met en scene la meme causalite runtime a travers douze surfaces web et un panel VS Code hote:
            cockpit, noyau forge, dossier de preuve, game UI, observability, spectator, observer, workflow, expert, observatory supervise, war room tactique, host bridge generique et shell IDE.
          </p>
          <div class="hero-meta">
            <span class="chip">Scenarios: <strong>${String(payload.scenarios.length)}</strong></span>
            <span class="chip">Default: <strong>${escapeHtml(initialScenario?.title ?? 'n/a')}</strong></span>
            <span class="chip">Generated: <strong>${escapeHtml(generatedAt)}</strong></span>
          </div>
        </article>

        <article class="panel panel-soft">
          <div class="section-head">
            <div>
              <h2>Architecture retenue</h2>
              <p class="muted">Un seul produit, douze surfaces runtime et un shell VS Code, aucune seconde source de verite.</p>
            </div>
          </div>
          <div class="summary-grid">
            <div class="metric"><span>Cockpit</span><strong>Main page</strong><small>comprendre, diagnostiquer, commander</small></div>
            <div class="metric"><span>Noyau Forge</span><strong>Control plane</strong><small>triade runtime, contrats, invariants</small></div>
            <div class="metric"><span>Dossier de preuve</span><strong>Evidence hub</strong><small>release gate, packs, artefacts relies</small></div>
            <div class="metric"><span>Game UI</span><strong>Board HUD</strong><small>rooms, agents, lanes, guardrails</small></div>
            <div class="metric"><span>Observability</span><strong>Trace deck</strong><small>timeline, blockers, health, hotspots</small></div>
            <div class="metric"><span>Spectator</span><strong>Shared read-only</strong><small>token, lien, lecture web et VS Code</small></div>
            <div class="metric"><span>Observer</span><strong>Spatial map</strong><small>rooms, entites, handoffs</small></div>
            <div class="metric"><span>Workflow</span><strong>Trace paths</strong><small>steps, decisions, audit</small></div>
            <div class="metric"><span>Expert</span><strong>Deep review</strong><small>preuve, replay, inspection</small></div>
            <div class="metric"><span>Observatory</span><strong>Read-only</strong><small>surface existante supervisee</small></div>
            <div class="metric"><span>War Room</span><strong>Spatial</strong><small>comparer, challenger, expliquer</small></div>
            <div class="metric"><span>Host Bridge</span><strong>Generic dispatch</strong><small>browser, VS Code, hotes externes</small></div>
            <div class="metric"><span>VS Code</span><strong>Host shell</strong><small>webview bridge sur les memes read models</small></div>
          </div>
          <p class="footer-note">Commande de regeneration: <span class="kbd">npm run demo:report</span></p>
        </article>
      </section>

      <section class="mode-shell panel panel-soft panel-block">
        <div class="mode-head">
          <div>
            <h2>Shell de navigation</h2>
            <p class="muted">Choisir la surface active sans dupliquer les read models runtime.</p>
          </div>
          <div id="mode-selector" class="mode-selector"></div>
        </div>
      </section>

      <section id="mode-cockpit" class="mode-section">
        <section class="panel panel-soft panel-block">
          <div class="section-head">
            <div>
              <h2>Catalogue de scenarios</h2>
              <p class="muted">Chaque scenario pousse les memes vues metier, mais avec des garde-fous et niveaux d hygiene differents.</p>
            </div>
          </div>
          <div class="scenario-grid" id="scenario-catalog"></div>
        </section>

        <section class="toolbar">
          <div class="toolbar-group">
            <span class="toolbar-label">Scenario</span>
            <div id="scenario-selector" class="legend"></div>
          </div>
          <div class="toolbar-group">
            <span class="toolbar-label">Visibilite</span>
            <div id="filter-selector" class="legend"></div>
          </div>
          <div class="toolbar-group">
            <span class="toolbar-label">JSON</span>
            <div id="json-selector" class="legend"></div>
          </div>
        </section>

        <section class="content-grid">
          <div>
            <article class="panel panel-soft">
              <div class="section-head">
                <div>
                  <h2 id="scenario-title"></h2>
                  <p id="scenario-description" class="muted"></p>
                </div>
                <span id="scenario-outcome" class="pill"></span>
              </div>
              <div id="scenario-tags" class="scenario-tags"></div>
            </article>

            <article class="panel panel-soft panel-block">
              <div class="section-head">
                <div>
                  <h2>Delta vs release ready</h2>
                  <p class="muted">Comparer le scenario actif avec la base saine pour identifier le vrai residu.</p>
                </div>
              </div>
              <div id="compare-grid" class="compare-grid"></div>
            </article>

            <article class="panel panel-soft panel-block">
              <div class="section-head">
                <div>
                  <h2>Walkthrough</h2>
                  <p class="muted">Resume causalite: carte, provenance, gate de branche.</p>
                </div>
              </div>
              <div id="walkthrough" class="timeline"></div>
            </article>
          </div>

          <div>
            <article class="panel panel-soft">
              <div class="section-head">
                <div>
                  <h2>Vue d ensemble</h2>
                  <p class="muted">Indicateurs clefs du scenario actif.</p>
                </div>
              </div>
              <div id="summary-grid" class="stats-grid"></div>
            </article>

            <article class="panel panel-soft panel-block">
              <div class="section-head">
                <div>
                  <h2>Legende</h2>
                </div>
              </div>
              <div class="legend">
                <span class="pill badge-outcome-clear">clear</span>
                <span class="pill badge-outcome-attention">attention</span>
                <span class="pill badge-outcome-blocked">blocked</span>
              </div>
            </article>
          </div>
        </section>

        <section class="panel panel-soft panel-block">
          <div class="section-head">
            <div>
              <h2>Power Cards View</h2>
              <p class="muted">Persistance runtime/storage, policy, trust et diagnostic d activation.</p>
            </div>
          </div>
          <div id="power-cards-grid" class="card-grid"></div>
        </section>

        <section class="panel panel-soft panel-block">
          <div class="section-head">
            <div>
              <h2>Provenance Compliance View</h2>
              <p class="muted">Conformite fail-closed sur source, licence et attribution.</p>
            </div>
          </div>
          <div id="provenance-grid" class="card-grid"></div>
        </section>

        <section class="panel panel-soft panel-block">
          <div class="section-head">
            <div>
              <h2>Branch Finisher View</h2>
              <p class="muted">Options finales d action et raisons bloquantes reelles.</p>
            </div>
          </div>
          <div id="branch-grid" class="card-grid"></div>
        </section>

        <section class="panel panel-soft panel-block">
          <div class="section-head">
            <div>
              <h2>Inspecteur JSON</h2>
              <p class="muted">Projection brute du scenario actif pour cross-check rapide.</p>
            </div>
          </div>
          <div class="inspector-grid">
            <article class="callout">
              <h3>Ce que tu peux verifier ici</h3>
              <ul class="issue-list">
                <li>les issueCodes et diagnostic des power cards,</li>
                <li>les blockingReasons de provenance,</li>
                <li>les options et allowed du branch finisher.</li>
              </ul>
            </article>
            <div class="json-shell">
              <div class="json-toolbar">
                <div id="json-tabs" class="json-tabs"></div>
                <button id="copy-json" class="copy-button" type="button">Copier JSON</button>
              </div>
              <pre id="json-output"></pre>
            </div>
          </div>
        </section>
      </section>

      <section id="mode-kernel" class="mode-section" hidden>
        <div id="kernel-shell" class="panel-block"></div>
      </section>

      <section id="mode-proofs" class="mode-section" hidden>
        <div id="proofs-shell" class="panel-block"></div>
      </section>

      <section id="mode-game-ui" class="mode-section" hidden>
        <div id="game-ui-shell" class="panel-block"></div>
      </section>

      <section id="mode-observability" class="mode-section" hidden>
        <div id="observability-shell" class="panel-block"></div>
      </section>

      <section id="mode-observer" class="mode-section" hidden>
        <div id="observer-shell" class="panel-block"></div>
      </section>

      <section id="mode-workflow" class="mode-section" hidden>
        <div id="workflow-shell" class="panel-block"></div>
      </section>

      <section id="mode-expert" class="mode-section" hidden>
        <div id="expert-shell" class="panel-block"></div>
      </section>

      <section id="mode-host-bridge" class="mode-section" hidden>
        <div id="host-bridge-shell" class="panel-block"></div>
      </section>

      <section id="mode-vscode" class="mode-section" hidden>
        <div id="vscode-shell" class="panel-block"></div>
      </section>

      <section id="mode-spectator" class="mode-section" hidden>
        <div id="spectator-shell" class="panel-block"></div>
      </section>

      <section id="mode-observatory" class="mode-section" hidden>
        <section class="observatory-grid panel-block">
          <article class="panel panel-soft">
            <div class="section-head">
              <div>
                <h2>Observatory supervise</h2>
                <p class="muted">Version pleine taille du pixel command deck deja injecte dans la Game UI, avec selection de source et ouverture dediee.</p>
              </div>
            </div>
            <div class="source-grid" id="observatory-source-selector"></div>
          </article>

          <article class="panel panel-soft">
            <div class="section-head">
              <div>
                <h2>Mode read-only</h2>
                <p id="observatory-source-status" class="muted"></p>
              </div>
              <a id="observatory-open-link" class="nav-link-button" href="#" target="_blank" rel="noopener noreferrer">Ouvrir seul</a>
            </div>
            <div class="summary-grid">
              <div class="metric"><span>Scope</span><strong>Shared</strong><small>game UI + observatory</small></div>
              <div class="metric"><span>Mode</span><strong>Lecture seule</strong><small>aucune mutation depuis cette surface</small></div>
              <div class="metric"><span>Causalite</span><strong>Partagee</strong><small>meme famille de signaux runtime</small></div>
            </div>
            <article id="observatory-fallback" class="callout panel-block">
              <h3>Fallback prevu</h3>
              <p class="muted">Si l iframe locale ne charge pas dans le navigateur, le lien Ouvrir seul reste la sortie de secours nominale.</p>
            </article>
          </article>
        </section>

        <section class="panel panel-soft panel-block">
          <div class="section-head">
            <div>
              <h2>Iframe supervisee</h2>
              <p class="muted">Integration supervisee du meme Pixel Office que la Game UI, garde en lecture seule pour la revue detaillee.</p>
            </div>
          </div>
          <iframe id="observatory-frame" class="observatory-frame" title="Observatory read-only"></iframe>
        </section>
      </section>

      <section id="mode-war-room" class="mode-section" hidden>
        <section class="content-grid">
          <div>
            <article class="panel panel-soft">
              <div class="section-head">
                <div>
                  <h2 id="war-room-title"></h2>
                  <p id="war-room-subtitle" class="muted"></p>
                </div>
                <span id="war-room-outcome" class="pill"></span>
              </div>
              <div id="war-room-tags" class="scenario-tags"></div>
            </article>

            <article class="panel panel-soft panel-block">
              <div class="section-head">
                <div>
                  <h2>Rail de handoff</h2>
                  <p class="muted">La war room reste explicative: elle raconte les handoffs et les points de friction, elle ne remplace pas le cockpit.</p>
                </div>
              </div>
              <div id="war-room-rail" class="war-room-rail"></div>
            </article>
          </div>

          <div>
            <article class="panel panel-soft">
              <div class="section-head">
                <div>
                  <h2>Resume tactique</h2>
                  <p class="muted">Meme causalite, langage spatial different.</p>
                </div>
              </div>
              <div id="war-room-summary-grid" class="summary-grid"></div>
            </article>

            <article class="panel panel-soft panel-block">
              <div class="section-head">
                <div>
                  <h2>Focus operateur</h2>
                </div>
              </div>
              <div id="war-room-focus-grid" class="summary-grid"></div>
            </article>
          </div>
        </section>

        <section class="panel panel-soft panel-block">
          <div class="section-head">
            <div>
              <h2>Zones de la war room</h2>
              <p class="muted">Chaque zone rejoue un fragment des read models: ops desk, challenge room, compliance library, release gate.</p>
            </div>
          </div>
          <div id="war-room-grid" class="war-room-grid"></div>
        </section>
      </section>
    </main>

    <script id="report-data" type="application/json">${serializeForHtml(payload)}</script>
    <script src="./runtime-views-report-client.js"></script>
  </body>
</html>`;
}

async function main(): Promise<void> {
  const projectRoot = resolve(process.cwd(), '../../..');
  const releaseDir = resolve(process.cwd(), '.release');
  const outputPath = resolve(releaseDir, 'runtime-views-report.html');
  const clientSourcePath = resolve(process.cwd(), 'examples/runtime-views-report-client.js');
  const clientTargetPath = resolve(releaseDir, 'runtime-views-report-client.js');
  const observatorySources = await collectObservatorySources(projectRoot);
  const proofSources = await materializeProofSources(projectRoot, resolve(releaseDir, 'proofs'));
  const payload: RuntimeViewsExplorerPayload = {
    ...createRuntimeViewsDemoData(),
    observatorySources,
    proofSources: proofSources.sources,
    latestProofRunId: proofSources.latestRunId
  };

  await mkdir(dirname(outputPath), { recursive: true });
  await writeFile(outputPath, renderReport(payload), 'utf8');
  await copyFile(clientSourcePath, clientTargetPath);
  console.log(outputPath);
}

await main();