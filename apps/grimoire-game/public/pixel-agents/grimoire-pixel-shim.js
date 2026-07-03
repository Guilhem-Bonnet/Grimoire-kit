/**
 * Grimoire Pixel Agents shim — remplace la partie "VS Code extension" par un
 * backend 100% client-side. Dispatch des agents Grimoire (9 rôles + sub-agents)
 * dans l'office pixel-agents via postMessage.
 *
 * Mapping :
 *  - Chaque agent Grimoire → un character avec un palette id (0-5 base characters)
 *  - Rooms Grimoire → desks/zones (intake-desk, war-room, workshop, branch-finisher...)
 *  - Activity : démo statique (Scope 3 lira GRIMOIRE_TRACE.jsonl en temps réel)
 */
(function () {
  // Ne PAS mocker acquireVsCodeApi : laisse runtime.ts détecter le mode "browser"
  // pour que browserMock.ts charge les sprites, tiles, furniture automatiquement.
  // Ce shim pousse uniquement les agents Grimoire par-dessus.

  // Roster Grimoire — 9 agents principaux + 3 sub-agents BMB + 1 CIS représentatif
  const GRIMOIRE_ROSTER = [
    { id: 1, folderName: 'grimoire-master', role: 'orchestrator', team: 'core', palette: 0 },
    { id: 2, folderName: 'analyst',         role: 'analyst',      team: 'bmm',  palette: 1 },
    { id: 3, folderName: 'architect',       role: 'architect',    team: 'bmm',  palette: 2 },
    { id: 4, folderName: 'dev',             role: 'coder',        team: 'bmm',  palette: 3 },
    { id: 5, folderName: 'pm',              role: 'pm',           team: 'bmm',  palette: 4 },
    { id: 6, folderName: 'qa',              role: 'reviewer',     team: 'bmm',  palette: 5 },
    { id: 7, folderName: 'sm',              role: 'sm',           team: 'bmm',  palette: 0 },
    { id: 8, folderName: 'tea',             role: 'tester',       team: 'tea',  palette: 1 },
    { id: 9, folderName: 'tech-writer',     role: 'writer',       team: 'bmm',  palette: 2 },
    { id: 10, folderName: 'ux-designer',    role: 'designer',     team: 'bmm',  palette: 3 },
    { id: 11, folderName: 'agent-builder',  role: 'builder',      team: 'bmb',  palette: 4 },
    { id: 12, folderName: 'workflow-builder', role: 'builder',    team: 'bmb',  palette: 5 },
    { id: 13, folderName: 'rodin',          role: 'sparring',     team: 'cis',  palette: 0 }
  ];

  // Démo statuses (seront remplacés par GRIMOIRE_TRACE.jsonl au scope 3)
  const DEMO_STATUSES = [
    { id: 1, status: 'active', tool: 'Task' },          // grimoire-master: orchestre
    { id: 2, status: 'active', tool: 'Read' },          // analyst: lit
    { id: 3, status: 'idle' },                          // architect
    { id: 4, status: 'active', tool: 'Edit' },          // dev: écrit
    { id: 5, status: 'idle' },                          // pm
    { id: 6, status: 'active', tool: 'Bash' },          // qa: commandes
    { id: 7, status: 'idle' },                          // sm
    { id: 8, status: 'active', tool: 'Read' },          // tea
    { id: 9, status: 'waiting' },                       // tech-writer: en attente
    { id: 10, status: 'idle' },                         // ux-designer
    { id: 11, status: 'idle' },                         // agent-builder
    { id: 12, status: 'active', tool: 'Write' },        // workflow-builder
    { id: 13, status: 'idle' }                          // rodin
  ];

  // Intercepte vscode.postMessage pour détecter "webviewReady" et pousser les agents
  const originalPostMessage = window.postMessage.bind(window);
  let webviewReadyReceived = false;
  let agentsDispatched = false;

  const dispatchGrimoireAgents = () => {
    if (agentsDispatched) return;
    agentsDispatched = true;

    const agents = GRIMOIRE_ROSTER.map((r) => r.id);
    const agentMeta = {};
    GRIMOIRE_ROSTER.forEach((r) => {
      agentMeta[r.id] = {
        folderName: r.folderName,
        palette: r.palette,
        isTeammate: false,
        team: r.team,
        role: r.role
      };
    });

    // 1. existingAgents (initial)
    originalPostMessage({ type: 'existingAgents', agents, agentMeta }, '*');

    // 2. agentTeamInfo per agent
    setTimeout(() => {
      GRIMOIRE_ROSTER.forEach((r) => {
        originalPostMessage(
          { type: 'agentTeamInfo', id: r.id, team: r.team, role: r.role },
          '*'
        );
      });

      // 3. statuses démo
      DEMO_STATUSES.forEach((s) => {
        originalPostMessage({ type: 'agentStatus', id: s.id, status: s.status }, '*');
        if (s.tool) {
          originalPostMessage(
            {
              type: 'agentToolStart',
              id: s.id,
              toolId: `grm-demo-${s.id}-${s.tool}`,
              status: `${s.tool}: demo activity`,
              permissionActive: false,
              runInBackground: false
            },
            '*'
          );
        }
      });

      // 4. token usage demo (budget Grimoire)
      GRIMOIRE_ROSTER.forEach((r) => {
        const used = Math.floor(Math.random() * 120000);
        originalPostMessage(
          { type: 'agentTokenUsage', id: r.id, tokens: used, contextUsed: used, contextMax: 200000 },
          '*'
        );
      });
    }, 180);
  };

  // Listener pour webviewReady côté console (fallback)
  window.addEventListener('message', (e) => {
    if (e.data && e.data.type === 'webviewReady' && !webviewReadyReceived) {
      webviewReadyReceived = true;
      dispatchGrimoireAgents();
    }
  });

  // Fallback déclenché si webviewReady ne remonte pas via message loop
  window.addEventListener('DOMContentLoaded', () => {
    setTimeout(() => {
      if (!agentsDispatched) dispatchGrimoireAgents();
    }, 1500);
  });

  // --- Grimoire branding overlay ---
  window.addEventListener('DOMContentLoaded', () => {
    const badge = document.createElement('div');
    badge.innerHTML = '<span style="color:#FF6B3D">◆</span> GRIMOIRE FORGE &middot; PIXEL OFFICE';
    badge.style.cssText = 'position:fixed;bottom:8px;left:14px;font-family:"Geist Mono",Consolas,monospace;font-size:9px;letter-spacing:1.6px;color:#9BA0A8;opacity:.82;pointer-events:none;z-index:9999;text-transform:uppercase;font-weight:500';
    document.body.appendChild(badge);

    const legend = document.createElement('div');
    legend.innerHTML = [
      '<span style="color:#73C991">● active</span>',
      '<span style="color:#FFB84D">◆ waiting</span>',
      '<span style="color:#9BA0A8">○ idle</span>',
      '<span style="color:#FF6B3D">▣ ' + GRIMOIRE_ROSTER.length + ' agents</span>'
    ].join(' &middot; ');
    legend.style.cssText = 'position:fixed;bottom:8px;right:14px;font-family:"Geist Mono",monospace;font-size:9px;letter-spacing:1.2px;opacity:.82;pointer-events:none;z-index:9999;font-weight:500;display:flex;gap:10px';
    document.body.appendChild(legend);
  });
})();
