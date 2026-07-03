import { createConfigUpdate } from '../src/contracts/events';
import {
  authorizeClientEvent,
  createAuthorizationAuditEntry,
  type AuthContext
} from '../src/server/auth/rbac';
import { CommandGateway } from '../src/server/control-plane/command-gateway';
import type { BranchFinisherView } from '../src/state/branch-finisher-view';
import { createCollaborationView, type CollaborationView } from '../src/state/collaboration-view';
import { createExpertCockpitView, type ExpertCockpitView } from '../src/state/expert-cockpit-view';
import {
  createGenericHostBridgeView,
  type GenericHostBridgeView
} from '../src/state/generic-host-bridge-view';
import { createRuntimeGameUiView, type RuntimeGameUiView } from '../src/state/runtime-game-ui-view';
import {
  createRuntimeObservabilitySurfaceView,
  type RuntimeObservabilitySurfaceView
} from '../src/state/runtime-observability-surface-view';
import type { PowerCardsView } from '../src/state/power-cards-view';
import type { ProvenanceComplianceView } from '../src/state/provenance-compliance-view';
import { createBranchFinisherView } from '../src/state/branch-finisher-view';
import { createPowerCardsView } from '../src/state/power-cards-view';
import { createProvenanceComplianceView } from '../src/state/provenance-compliance-view';
import {
  createRuntimeCockpitView,
  type RuntimeCockpitView
} from '../src/state/runtime-cockpit-view';
import { createRuntimeKernelView, type RuntimeKernelView } from '../src/state/runtime-kernel-view';
import { createMissionBoardView, type MissionBoardView } from '../src/state/mission-board-view';
import {
  createRuntimeDashboardView,
  type RuntimeDashboardControlPlaneState
} from '../src/state/runtime-dashboard-view';
import { createRuntimeObserverView, type RuntimeObserverView } from '../src/state/runtime-observer-view';
import {
  createRuntimeProofDossierView,
  type RuntimeProofDossierView
} from '../src/state/runtime-proof-dossier-view';
import {
  createSpectatorSurfaceView,
  type SpectatorSurfaceView
} from '../src/state/spectator-surface-view';
import { createVsCodePanelView, type VsCodePanelView } from '../src/state/vscode-panel-view';
import type { GameState, WorkflowStepLogEntry } from '../src/state/game-state';
import {
  createWorkflowVisualizationView,
  type WorkflowVisualizationView
} from '../src/state/workflow-visualization-view';

type ScenarioOutcome = 'clear' | 'attention' | 'blocked';

interface RuntimeScenarioWebViews {
  collaborationView: CollaborationView;
  cockpitView: RuntimeCockpitView;
  gameUiView: RuntimeGameUiView;
  kernelView: RuntimeKernelView;
  missionBoardView: MissionBoardView;
  observabilityView: RuntimeObservabilitySurfaceView;
  observerView: RuntimeObserverView;
  proofDossierView: RuntimeProofDossierView;
  workflowView: WorkflowVisualizationView;
  expertView: ExpertCockpitView;
  genericHostBridgeView: GenericHostBridgeView;
  spectatorView: SpectatorSurfaceView;
  vscodePanelView: VsCodePanelView;
}

interface RuntimeScenarioSpectatorShare {
  commandId: string;
  principalId: string;
  tokenId: string;
  issuedAt: string;
  expiresAt: string | null;
  shareQuery: string;
}

export interface RuntimeViewsScenario {
  id: string;
  title: string;
  description: string;
  outcome: ScenarioOutcome;
  tags: readonly string[];
  walkthrough: readonly string[];
  state: GameState;
  controlPlane: RuntimeDashboardControlPlaneState;
  powerCardsView: PowerCardsView;
  provenanceView: ProvenanceComplianceView;
  branchFinisherView: BranchFinisherView;
  spectatorShare: RuntimeScenarioSpectatorShare;
  webViews: RuntimeScenarioWebViews;
}

export interface RuntimeViewsDemoData {
  scenarios: readonly RuntimeViewsScenario[];
  defaultScenarioId: string;
}

interface BaseStateOptions {
  branch: string;
  lastSequenceId: number;
  scenarioId: string;
  outcome: ScenarioOutcome;
  workflowSteps?: readonly WorkflowStepLogEntry[];
}

const DEFAULT_PROJECT_ID = 'grimoire-game-web';
const DEFAULT_NODE_ID = 'node-web-1';
const DEFAULT_WORKER_ID = 'worker-orchestrator-1';
const DEFAULT_WORKTREE_ID = 'wt-grimoire-game-web';
const ORCHESTRATOR_AUTH: AuthContext = {
  principalId: 'orch-1',
  role: 'orchestrator'
};
const DASHBOARD_UI_OPTIONS = {
  maxTasksPerLane: 4,
  maxAttentionItems: 8,
  maxEvidencePacks: 3,
  maxTimelinePoints: 8,
  maxVerificationItemsPerLane: 3
} as const;

function createBaseState(options: BaseStateOptions): GameState {
  const runId = createRunId(options.scenarioId);
  const reviewVerdictByOutcome = {
    clear: 'pass',
    attention: 'warn',
    blocked: 'fail'
  } as const;
  const invocationDecisionByOutcome = {
    clear: 'ALLOW',
    attention: 'DEGRADE',
    blocked: 'DENY'
  } as const;
  const hostTrustByOutcome = {
    clear: 'trusted',
    attention: 'restricted',
    blocked: 'review'
  } as const;
  const hostConnectionByOutcome = {
    clear: 'online',
    attention: 'stale',
    blocked: 'degraded'
  } as const;

  return {
    protocolVersion: 'v1',
    lastSequenceId: options.lastSequenceId,
    hydratedAt: '2026-04-12T01:30:00.000Z',
    agents: {
      'orch-1': {
        id: 'orch-1',
        name: 'Orchestrator',
        role: 'orchestrator',
        status: 'working',
        roomId: 'war-room',
        position: { x: 4, y: 4 },
        lastTool: 'run_task'
      },
      'qa-1': {
        id: 'qa-1',
        name: 'Compliance Bot',
        role: 'agent',
        status: 'working',
        roomId: 'library',
        position: { x: 7, y: 3 },
        parentId: 'orch-1',
        lastTool: 'review_artifact'
      }
    },
    tasks: {
      'task-power': {
        id: 'task-power',
        title: 'Activate power cards',
        status: 'review',
        priority: 'high',
        kind: 'ops',
        assigneeId: 'orch-1'
      },
      'task-provenance': {
        id: 'task-provenance',
        title: 'Audit provenance bundle',
        status: 'in_progress',
        priority: 'critical',
        kind: 'security',
        assigneeId: 'qa-1'
      },
      'task-release': {
        id: 'task-release',
        title: 'Prepare branch finish decision',
        status: 'todo',
        priority: 'medium',
        kind: 'ops',
        dependencyIds: ['task-power', 'task-provenance']
      }
    },
    config: {
      'live.connection.status': options.outcome === 'blocked' ? 'stale' : 'live',
      'live.connection.found': true,
      'live.connection.path': '.generated/runtime/live-session.jsonl',
      'live.connection.parsedLineCount': 42,
      'live.connection.lastDataAt': '2026-04-12T02:31:10.000Z',
      'live.connection.scannedAt': '2026-04-12T02:31:12.000Z',
      'live.connection.staleAfterMs': 5000,
      'live.connection.ageMs': options.outcome === 'attention' ? 7800 : 1200,
      'live.connection.byAgent': {
        'orch-1': {
          status: 'live',
          found: true,
          path: '.generated/runtime/orch-1.jsonl',
          parsedLineCount: 20,
          lastDataAt: '2026-04-12T02:31:10.000Z',
          scannedAt: '2026-04-12T02:31:12.000Z',
          staleAfterMs: 5000,
          ageMs: 1200
        },
        'qa-1': {
          status: options.outcome === 'clear' ? 'live' : 'stale',
          found: true,
          path: '.generated/runtime/qa-1.jsonl',
          parsedLineCount: 16,
          lastDataAt: '2026-04-12T02:30:58.000Z',
          scannedAt: '2026-04-12T02:31:12.000Z',
          staleAfterMs: 5000,
          ageMs: options.outcome === 'clear' ? 900 : 6200
        }
      }
    },
    recentToolCalls: [
      {
        tool: 'run_task',
        params: {
          task_id: 'task-power',
          path: 'grimoire-kit/apps/grimoire-game/.release/runtime-views-report.html'
        },
        sourceEventType: 'verification_artifact',
        traceId: 'trace-runtime-web',
        sequenceId: options.lastSequenceId - 8,
        timestamp: '2026-04-12T02:30:12.000Z',
        agentId: 'orch-1'
      },
      {
        tool: 'graph_update',
        params: {
          task_id: 'task-provenance',
          edge: 'orch-1->qa-1',
          strength_before: 0.35,
          strength_after: 0.82
        },
        sourceEventType: 'graph_update',
        traceId: 'trace-runtime-web',
        sequenceId: options.lastSequenceId - 7,
        timestamp: '2026-04-12T02:30:18.000Z',
        agentId: 'orch-1'
      },
      {
        tool: 'review_artifact',
        params: {
          task_id: 'task-provenance',
          path: 'grimoire-kit/apps/grimoire-game/.release/cockpit-app/index.html'
        },
        sourceEventType: 'review_artifact',
        traceId: 'trace-runtime-web',
        sequenceId: options.lastSequenceId - 6,
        timestamp: '2026-04-12T02:30:24.000Z',
        agentId: 'qa-1'
      }
    ],
    recentWorkflowSteps: [
      {
        step: 'Route runtime web audit',
        detail: 'Unify cockpit, observatory and branch finish signals in a single web shell.',
        sourceEventType: 'routing',
        traceId: 'trace-runtime-web',
        taskId: 'task-power',
        metadata: {
          correlationId: `corr:${runId}`,
          requestId: `req:${runId}`,
          intent: 'runtime web parity',
          missionPack: {
            objective: 'Keep the same runtime truth readable from browser, VS Code and external hosts.',
            scope: [
              'src/state/host-bridge-view.ts',
              'src/state/host-handoff-view.ts',
              'src/bridge/vscode-webview-bridge.ts'
            ],
            canonicalSources: [
              'src/state/host-bridge-view.ts',
              'src/state/host-handoff-view.ts',
              'src/bridge/vscode-webview-bridge.ts'
            ],
            constraints: ['repo-first', 'preview-before-commit', 'policy://host-bridge/default-v1'],
            expectedOutput: 'generic host bridge packet',
            expectedProof: [
              `verify://runtime-host-bridge/${options.scenarioId}`,
              `artifact://review/${options.scenarioId}/host-bridge`
            ],
            mode: 'preview'
          }
        },
        sequenceId: options.lastSequenceId - 10,
        timestamp: '2026-04-12T02:30:00.000Z',
        agentId: 'orch-1'
      },
      {
        step: 'Inspect runtime web posture',
        detail: 'Capture power card persistence drift and provenance readiness.',
        sourceEventType: 'investigation_step',
        traceId: 'trace-runtime-web',
        taskId: 'task-power',
        metadata: {
          correlationId: `corr:${runId}`,
          requestId: `req:${runId}`,
          actionId: 'action:runtime-web-audit'
        },
        sequenceId: options.lastSequenceId - 9,
        timestamp: '2026-04-12T02:30:06.000Z',
        agentId: 'orch-1'
      },
      {
        step: 'Handoff provenance validation',
        detail: 'Compliance Bot reviews the provenance lane before release decision.',
        sourceEventType: 'task_handoff',
        traceId: 'trace-runtime-web',
        taskId: 'task-provenance',
        metadata: {
          correlationId: `corr:${runId}`,
          requestId: `req:${runId}`
        },
        sequenceId: options.lastSequenceId - 5,
        timestamp: '2026-04-12T02:30:28.000Z',
        agentId: 'qa-1'
      },
      {
        step: `Verification gate ${options.outcome === 'clear' ? 'PASS' : 'FAIL'}`,
        detail: `${options.scenarioId}: verify runtime web proof bundle.`,
        sourceEventType: 'verification_gate',
        traceId: 'trace-runtime-web',
        taskId: 'task-power',
        metadata: {
          correlationId: `corr:${runId}`,
          requestId: `req:${runId}`,
          actionId: 'action:runtime-web-audit',
          verificationRef: `verify://runtime-web/${options.scenarioId}`,
          controlsExecuted: ['policy:runtime-web', 'review:provenance'],
          evidenceRefs: [
            'artifact://runtime/cockpit-shell',
            'tests://vitest/runtime-web',
            'log://runtime/audit'
          ],
          typedEvidenceRefs: [
            { kind: 'artifact', ref: 'artifact://runtime/cockpit-shell' },
            { kind: 'test', ref: 'tests://vitest/runtime-web' },
            { kind: 'log', ref: 'log://runtime/audit' }
          ],
          verdict: options.outcome === 'clear' ? 'PASS' : 'FAIL',
          unmetControls: options.outcome === 'clear' ? [] : ['proof:attribution']
        },
        sequenceId: options.lastSequenceId - 2,
        timestamp: '2026-04-12T02:30:48.000Z',
        agentId: 'qa-1'
      },
      {
        step: 'Branch finisher options updated',
        detail: options.branch,
        sourceEventType: 'branch_finish_options',
        traceId: 'trace-runtime-web',
        taskId: 'task-release',
        metadata: {
          branch: options.branch,
          testsPassed: true,
          allowedOptions: ['merge', 'pr', 'keep', 'discard'],
          correlationId: `corr:${runId}`,
          requestId: `req:${runId}`
        },
        sequenceId: options.lastSequenceId - 1,
        timestamp: '2026-04-12T02:30:54.000Z',
        agentId: 'orch-1'
      },
      ...(options.workflowSteps ?? [])
    ],
    hostBindings: {
      'host-vscode-panel': {
        sequenceId: options.lastSequenceId - 6,
        timestamp: '2026-04-12T02:30:30.000Z',
        binding: {
          hostId: 'host-vscode-panel',
          hostType: 'ide',
          displayName: 'VS Code Panel Bridge',
          authMode: 'session',
          connectionState: options.outcome === 'blocked' ? 'degraded' : 'online',
          trustStatus: 'trusted',
          scopes: ['fs', 'network'],
          capabilityManifestRef: 'manifest://host-vscode-panel',
          sourceOfTruth: 'secondary',
          lastSeenAt: '2026-04-12T02:30:57.000Z'
        },
        manifest: {
          manifestId: 'manifest://host-vscode-panel',
          hostId: 'host-vscode-panel',
          routines: ['panel-sync', 'focus-navigation'],
          toolProviders: ['vscode-webview'],
          reviewChannels: ['other'],
          contextSources: ['selection', 'session_context'],
          permissionMode: 'prompt',
          supportsStreaming: true,
          supportsReviewImport: false,
          supportsContextImport: true,
          supportsPreviewCommit: false
        },
        reason: 'VS Code mirrors the same read models through the host bridge.'
      },
      'host-copilot-web': {
        sequenceId: options.lastSequenceId - 4,
        timestamp: '2026-04-12T02:30:34.000Z',
        binding: {
          hostId: 'host-copilot-web',
          hostType: 'copilot',
          displayName: 'Copilot Web Bridge',
          authMode: 'session',
          connectionState: hostConnectionByOutcome[options.outcome],
          trustStatus: hostTrustByOutcome[options.outcome],
          scopes: ['fs', 'exec', 'network'],
          capabilityManifestRef: 'manifest://host-copilot-web',
          sourceOfTruth: 'secondary',
          lastSeenAt: '2026-04-12T02:30:56.000Z'
        },
        manifest: {
          manifestId: 'manifest://host-copilot-web',
          hostId: 'host-copilot-web',
          routines: ['runtime-audit', 'proof-sync'],
          toolProviders: ['vscode', 'terminal'],
          reviewChannels: ['github_pr_comment'],
          contextSources: ['memory', 'selection', 'session_context'],
          permissionMode: 'hybrid',
          supportsStreaming: true,
          supportsReviewImport: true,
          supportsContextImport: true,
          supportsPreviewCommit: true
        },
        reason: options.outcome === 'clear' ? 'Host healthy.' : 'Host under supervision.'
      },
      'host-claude-code': {
        sequenceId: options.lastSequenceId - 3,
        timestamp: '2026-04-12T02:30:40.000Z',
        binding: {
          hostId: 'host-claude-code',
          hostType: 'claude',
          displayName: 'Claude Code Bridge',
          authMode: 'token',
          connectionState:
            options.outcome === 'clear' ? 'online' : options.outcome === 'attention' ? 'degraded' : 'degraded',
          trustStatus: options.outcome === 'clear' ? 'review' : options.outcome === 'attention' ? 'restricted' : 'review',
          scopes: ['fs', 'network'],
          capabilityManifestRef: 'manifest://host-claude-code',
          sourceOfTruth: 'secondary',
          lastSeenAt: '2026-04-12T02:30:58.000Z'
        },
        manifest: {
          manifestId: 'manifest://host-claude-code',
          hostId: 'host-claude-code',
          routines: ['review.runtime', 'context.recap'],
          toolProviders: ['claude-cli'],
          reviewChannels: ['claude_review'],
          contextSources: ['selection', 'memory', 'session_context'],
          permissionMode: 'policy',
          supportsStreaming: true,
          supportsReviewImport: true,
          supportsContextImport: true,
          supportsPreviewCommit: false
        },
        reason: options.outcome === 'clear' ? 'Claude stays review-first even when healthy.' : 'Claude connector degraded under review.'
      },
      'host-mcp-runtime': {
        sequenceId: options.lastSequenceId - 2,
        timestamp: '2026-04-12T02:30:44.000Z',
        binding: {
          hostId: 'host-mcp-runtime',
          hostType: 'mcp',
          displayName: 'MCP Runtime Bridge',
          authMode: 'delegated',
          connectionState: options.outcome === 'blocked' ? 'offline' : options.outcome === 'attention' ? 'stale' : 'online',
          trustStatus: options.outcome === 'clear' ? 'trusted' : 'restricted',
          scopes: ['network', 'exec'],
          capabilityManifestRef: 'manifest://host-mcp-runtime',
          sourceOfTruth: 'secondary',
          lastSeenAt: options.outcome === 'blocked' ? '2026-04-12T02:30:11.000Z' : '2026-04-12T02:30:59.000Z'
        },
        manifest: {
          manifestId: 'manifest://host-mcp-runtime',
          hostId: 'host-mcp-runtime',
          routines: ['tool-handoff', 'context-ingest'],
          toolProviders: ['github-mcp', 'grimoire-mcp'],
          reviewChannels: ['mcp_review'],
          contextSources: ['session_context', 'review_summary'],
          permissionMode: 'hybrid',
          supportsStreaming: true,
          supportsReviewImport: true,
          supportsContextImport: true,
          supportsPreviewCommit: true
        },
        reason: options.outcome === 'clear' ? 'MCP relay aligned with host bridge policy.' : 'MCP relay stays degraded until the proof chain is complete.'
      }
    },
    recentHostInvocationDecisions: [
      {
        sequenceId: options.lastSequenceId - 4,
        timestamp: '2026-04-12T02:30:36.000Z',
        envelope: {
          envelopeId: `envelope://${options.scenarioId}/runtime-web-audit`,
          hostId: 'host-copilot-web',
          actionKind: 'review_import',
          mode: 'validate',
          correlationId: `corr:${runId}`,
          idempotencyKey: `idemp:${options.scenarioId}:runtime-web-audit`,
          traceId: 'trace-runtime-web',
          taskId: 'task-power',
          requestedScopes: ['fs', 'exec'],
          payload: {
            branch: options.branch,
            scenarioId: options.scenarioId
          },
          evidencePolicy: 'strict'
        },
        decision: invocationDecisionByOutcome[options.outcome],
        reason:
          options.outcome === 'clear'
            ? 'Review import accepted with complete proof chain.'
            : options.outcome === 'attention'
              ? 'Review import degraded while provenance drift remains under audit.'
              : 'Review import denied until provenance blockers are resolved.',
        meta: {
          traceId: 'trace-runtime-web',
          taskId: 'task-power',
          correlationId: `corr:${runId}`,
          hostId: 'host-copilot-web',
          policyRef: 'policy://runtime-web-review'
        }
      },
      {
        sequenceId: options.lastSequenceId - 3,
        timestamp: '2026-04-12T02:30:43.000Z',
        envelope: {
          envelopeId: `envelope://${options.scenarioId}/claude-runtime-review`,
          hostId: 'host-claude-code',
          actionKind: 'routine',
          mode: 'preview',
          correlationId: `corr:${runId}:claude`,
          idempotencyKey: `idemp:${options.scenarioId}:claude-runtime-review`,
          traceId: 'trace-runtime-web',
          taskId: 'task-power',
          requestedScopes: ['fs'],
          payload: {
            routine: 'review.runtime',
            scenarioId: options.scenarioId
          },
          evidencePolicy: 'basic'
        },
        decision: options.outcome === 'clear' ? 'ALLOW' : options.outcome === 'attention' ? 'PROMPT' : 'DEGRADE',
        reason:
          options.outcome === 'clear'
            ? 'Claude preview routine is allowed because the packet remains preview-only.'
            : options.outcome === 'attention'
              ? 'Claude preview routine requires an explicit prompt while the connector is restricted.'
              : 'Claude preview routine degrades to read-only until the host stabilises.',
        meta: {
          traceId: 'trace-runtime-web',
          taskId: 'task-power',
          correlationId: `corr:${runId}:claude`,
          hostId: 'host-claude-code',
          policyRef: 'policy://host-bridge/default-v1'
        }
      },
      {
        sequenceId: options.lastSequenceId - 2,
        timestamp: '2026-04-12T02:30:46.000Z',
        envelope: {
          envelopeId: `envelope://${options.scenarioId}/mcp-context-import`,
          hostId: 'host-mcp-runtime',
          actionKind: 'context_import',
          mode: 'read',
          correlationId: `corr:${runId}:mcp`,
          idempotencyKey: `idemp:${options.scenarioId}:mcp-context-import`,
          traceId: 'trace-runtime-web',
          taskId: 'task-provenance',
          requestedScopes: ['network'],
          payload: {
            source: 'review_summary',
            scenarioId: options.scenarioId
          },
          evidencePolicy: 'basic'
        },
        decision: options.outcome === 'clear' ? 'ALLOW' : 'DEGRADE',
        reason:
          options.outcome === 'clear'
            ? 'MCP context import is allowed in read mode.'
            : 'MCP context import degrades to stale read diagnostics while the host is not fully healthy.',
        meta: {
          traceId: 'trace-runtime-web',
          taskId: 'task-provenance',
          correlationId: `corr:${runId}:mcp`,
          hostId: 'host-mcp-runtime',
          policyRef: 'policy://host-bridge/default-v1'
        }
      }
    ],
    recentHostContextEntries: [
      {
        sequenceId: options.lastSequenceId - 3,
        timestamp: '2026-04-12T02:30:42.000Z',
        entry: {
          entryId: `context://${options.scenarioId}/runtime-web`,
          hostId: 'host-copilot-web',
          sourceType: 'session_context',
          visibility: 'shared',
          confidence: 8,
          importedAt: '2026-04-12T02:30:42.000Z',
          ttlSeconds: 3600,
          contentRef: `memory://runtime-web/${options.scenarioId}`,
          trustStatus: options.outcome === 'clear' ? 'trusted' : 'review'
        },
        meta: {
          traceId: 'trace-runtime-web',
          taskId: 'task-power',
          correlationId: `corr:${runId}`,
          hostId: 'host-copilot-web'
        }
      },
      {
        sequenceId: options.lastSequenceId - 2,
        timestamp: '2026-04-12T02:30:47.000Z',
        entry: {
          entryId: `context://${options.scenarioId}/proof-summary`,
          hostId: 'host-mcp-runtime',
          sourceType: 'review_summary',
          visibility: 'shared',
          confidence: options.outcome === 'clear' ? 9 : 6,
          importedAt: '2026-04-12T02:30:47.000Z',
          ttlSeconds: 1800,
          contentRef: `artifact://review/${options.scenarioId}/host-bridge`,
          trustStatus: options.outcome === 'clear' ? 'trusted' : 'review'
        },
        meta: {
          traceId: 'trace-runtime-web',
          taskId: 'task-provenance',
          correlationId: `corr:${runId}:mcp`,
          hostId: 'host-mcp-runtime'
        }
      }
    ],
    recentHostReviews: [
      {
        sequenceId: options.lastSequenceId - 2,
        timestamp: '2026-04-12T02:30:50.000Z',
        review: {
          reviewId: `review://${options.scenarioId}/runtime-web`,
          hostId: 'host-copilot-web',
          sourceType: 'copilot_review',
          subjectRef: `branch://${options.branch}`,
          verdict: reviewVerdictByOutcome[options.outcome],
          findings: [
            {
              id: `finding://${options.scenarioId}/runtime-web`,
              severity:
                options.outcome === 'clear'
                  ? 'info'
                  : options.outcome === 'attention'
                    ? 'medium'
                    : 'critical',
              message:
                options.outcome === 'clear'
                  ? 'Runtime web proof bundle is internally consistent.'
                  : options.outcome === 'attention'
                    ? 'Runtime/storage drift requires follow-up before merge.'
                    : 'Attribution and trust blockers still prevent a safe release.',
              resolutionStatus: options.outcome === 'clear' ? 'resolved' : 'open'
            }
          ],
          linkedEvidenceRefs: ['artifact://runtime/cockpit-shell', 'log://runtime/audit'],
          importedAt: '2026-04-12T02:30:50.000Z',
          traceId: 'trace-runtime-web',
          taskId: 'task-power'
        },
        meta: {
          traceId: 'trace-runtime-web',
          taskId: 'task-power',
          correlationId: `corr:${runId}`,
          hostId: 'host-copilot-web'
        }
      },
      {
        sequenceId: options.lastSequenceId - 1,
        timestamp: '2026-04-12T02:30:52.000Z',
        review: {
          reviewId: `review://${options.scenarioId}/claude-runtime`,
          hostId: 'host-claude-code',
          sourceType: 'claude_review',
          subjectRef: 'task:task-power',
          verdict: options.outcome === 'clear' ? 'pass' : options.outcome === 'attention' ? 'warn' : 'comment',
          findings: [
            {
              id: `finding://${options.scenarioId}/claude-runtime`,
              severity: options.outcome === 'clear' ? 'info' : 'medium',
              message:
                options.outcome === 'clear'
                  ? 'The host bridge packet is consistent across browser and IDE surfaces.'
                  : options.outcome === 'attention'
                    ? 'The host bridge packet remains readable but still needs a human prompt before dispatch.'
                    : 'The host bridge packet stays readable, but dispatch is intentionally suppressed while blockers remain.',
              resolutionStatus: options.outcome === 'clear' ? 'resolved' : 'acknowledged'
            }
          ],
          linkedEvidenceRefs: [
            `verify://runtime-host-bridge/${options.scenarioId}`,
            `artifact://review/${options.scenarioId}/host-bridge`
          ],
          importedAt: '2026-04-12T02:30:52.000Z',
          traceId: 'trace-runtime-web',
          taskId: 'task-power'
        },
        meta: {
          traceId: 'trace-runtime-web',
          taskId: 'task-power',
          correlationId: `corr:${runId}:claude`,
          hostId: 'host-claude-code'
        }
      }
    ],
    lastErrors:
      options.outcome === 'blocked'
        ? [
            {
              version: 'v1',
              type: 'ERROR',
              sequenceId: options.lastSequenceId,
              timestamp: '2026-04-12T02:31:00.000Z',
              code: 'RUNTIME_WEB_RELEASE_BLOCKED',
              message: 'Runtime web release gate is blocked by unresolved provenance controls.',
              correlationId: `corr:${runId}`,
              retryable: false
            }
          ]
        : []
  };
}

function createScenarioState(
  baseState: GameState,
  powerCardsConfig: GameState['config'],
  provenanceConfig: GameState['config']
): GameState {
  return {
    ...baseState,
    config: {
      ...powerCardsConfig,
      ...provenanceConfig
    }
  };
}

function createControlPlaneState(
  scenarioId: string,
  branch: string,
  lastSequenceId: number,
  outcome: ScenarioOutcome
): RuntimeDashboardControlPlaneState {
  const runId = createRunId(scenarioId);
  const generatedAt = '2026-04-12T02:31:12.000Z';
  const nodeStatus = outcome === 'attention' ? 'stale' : 'live';
  const leaseStatus = outcome === 'blocked' ? 'expired' : 'active';

  return {
    projectRegistry: {
      registryVersion: 'control-plane-v1',
      generatedAt,
      activeProject: {
        protocolVersion: 'v1',
        projectId: DEFAULT_PROJECT_ID,
        runId,
        firstEventAt: '2026-04-12T02:30:00.000Z',
        lastEventAt: generatedAt,
        firstSequenceId: Math.max(1, lastSequenceId - 10),
        lastSequenceId,
        eventCount: 8,
        lastMessageId: `runtime-web:${scenarioId}:${lastSequenceId}`,
        lastCorrelationId: `corr:${runId}`,
        traceId: 'trace-runtime-web',
        taskId: 'task-power',
        nodeId: DEFAULT_NODE_ID,
        workerId: DEFAULT_WORKER_ID,
        leaseId: `lease://${scenarioId}/task-power`,
        worktreeId: DEFAULT_WORKTREE_ID,
        nodeIds: [DEFAULT_NODE_ID],
        workerIds: [DEFAULT_WORKER_ID],
        leaseIds: [`lease://${scenarioId}/task-power`],
        worktreeIds: [DEFAULT_WORKTREE_ID],
        channels: ['runtime', 'session'],
        messageTypes: ['workflow.step', 'verification.gate', 'task.update']
      }
    },
    nodeRegistry: {
      registryVersion: 'node-registry-v1',
      generatedAt,
      projectId: DEFAULT_PROJECT_ID,
      runId,
      nodes: [
        {
          protocolVersion: 'v1',
          projectId: DEFAULT_PROJECT_ID,
          runId,
          nodeId: DEFAULT_NODE_ID,
          firstSeenAt: '2026-04-12T02:30:00.000Z',
          lastSeenAt: outcome === 'attention' ? '2026-04-12T02:31:02.000Z' : '2026-04-12T02:31:11.000Z',
          firstSequenceId: Math.max(1, lastSequenceId - 10),
          lastSequenceId,
          messageCount: 8,
          staleAfterMs: 5000,
          offlineAfterMs: 30000,
          ageMs: outcome === 'attention' ? 10100 : 1000,
          status: nodeStatus,
          traceId: 'trace-runtime-web',
          taskId: 'task-power',
          leaseId: `lease://${scenarioId}/task-power`,
          worktreeId: DEFAULT_WORKTREE_ID,
          capabilityTags: ['runtime-ui', 'verification'],
          workerIds: [DEFAULT_WORKER_ID],
          workers: [
            {
              workerId: DEFAULT_WORKER_ID,
              firstSeenAt: '2026-04-12T02:30:00.000Z',
              lastSeenAt: outcome === 'attention' ? '2026-04-12T02:31:02.000Z' : '2026-04-12T02:31:11.000Z',
              firstSequenceId: Math.max(1, lastSequenceId - 10),
              lastSequenceId,
              messageCount: 8,
              traceId: 'trace-runtime-web',
              taskId: 'task-power',
              leaseId: `lease://${scenarioId}/task-power`,
              worktreeId: DEFAULT_WORKTREE_ID
            }
          ],
          channels: ['runtime', 'session'],
          messageTypes: ['workflow.step', 'verification.gate', 'task.update']
        }
      ],
      summary: {
        nodeCount: 1,
        liveNodeCount: nodeStatus === 'live' ? 1 : 0,
        staleNodeCount: nodeStatus === 'stale' ? 1 : 0,
        offlineNodeCount: 0,
        workerCount: 1
      }
    },
    leaseStore: {
      registryVersion: 'lease-store-v1',
      generatedAt,
      projectId: DEFAULT_PROJECT_ID,
      runId,
      leases: [
        {
          protocolVersion: 'v1',
          projectId: DEFAULT_PROJECT_ID,
          runId,
          leaseId: `lease://${scenarioId}/task-power`,
          taskId: 'task-power',
          nodeId: DEFAULT_NODE_ID,
          workerId: DEFAULT_WORKER_ID,
          worktreeId: DEFAULT_WORKTREE_ID,
          branch,
          claimedAt: '2026-04-12T02:30:20.000Z',
          lastRenewedAt: outcome === 'blocked' ? '2026-04-12T02:30:24.000Z' : '2026-04-12T02:31:08.000Z',
          expiresAt: outcome === 'blocked' ? '2026-04-12T02:30:34.000Z' : '2026-04-12T02:31:38.000Z',
          ttlMs: 30000,
          ageMs: outcome === 'blocked' ? 38000 : 900,
          status: leaseStatus,
          messageCount: 5,
          lastSequenceId,
          traceId: 'trace-runtime-web',
          channels: ['runtime'],
          messageTypes: ['lease.claim', 'verification.gate']
        }
      ],
      summary: {
        leaseCount: 1,
        activeLeaseCount: leaseStatus === 'active' ? 1 : 0,
        expiredLeaseCount: leaseStatus === 'expired' ? 1 : 0
      }
    }
  };
}

function createSpectatorShareQuery(scenarioId: string, tokenId: string): string {
  return `?mode=spectator&scenario=${encodeURIComponent(scenarioId)}&token=${encodeURIComponent(tokenId)}`;
}

function createSpectatorArtifacts(
  scenarioId: string,
  dashboard: ReturnType<typeof createRuntimeDashboardView>
): {
  spectatorView: SpectatorSurfaceView;
  spectatorShare: RuntimeScenarioSpectatorShare;
} {
  const gateway = new CommandGateway();
  const shareResult = gateway.execute(
    {
      commandId: `cmd-share-${scenarioId}`,
      type: 'spectator.share',
      idempotencyKey: `share-${scenarioId}`
    },
    ORCHESTRATOR_AUTH
  );

  if (shareResult.issuedToken === undefined) {
    throw new Error(`Unable to create spectator token for scenario ${scenarioId}.`);
  }

  const spectatorAuth = gateway.getTokenRegistry().authenticate(shareResult.issuedToken.token);
  const forbiddenEvent = createConfigUpdate(
    `req-${scenarioId}-spectator`,
    'hud.theme',
    'paper',
    `cfg-${scenarioId}-spectator`
  );
  const authDecision = authorizeClientEvent(spectatorAuth, forbiddenEvent);
  const authAudit = createAuthorizationAuditEntry(
    spectatorAuth,
    forbiddenEvent,
    authDecision,
    '2026-04-12T03:00:00.000Z'
  );

  gateway.execute(
    {
      commandId: `cmd-${scenarioId}-spectator-mutation`,
      type: 'node.set_maintenance',
      idempotencyKey: `node-maint-${scenarioId}`,
      nodeId: DEFAULT_NODE_ID
    },
    spectatorAuth
  );

  return {
    spectatorView: createSpectatorSurfaceView(dashboard, spectatorAuth, {
      authorizationAudit: [authAudit],
      commandAudit: gateway.getAuditLog()
    }),
    spectatorShare: {
      commandId: shareResult.commandId,
      principalId: spectatorAuth.principalId,
      tokenId: shareResult.issuedToken.token,
      issuedAt: shareResult.issuedToken.issuedAt,
      expiresAt: shareResult.issuedToken.expiresAt ?? null,
      shareQuery: createSpectatorShareQuery(scenarioId, shareResult.issuedToken.token)
    }
  };
}

function buildScenarioViews(
  scenarioId: string,
  state: GameState,
  controlPlane: RuntimeDashboardControlPlaneState
) {
  const dashboard = createRuntimeDashboardView(state, { observability: {} }, controlPlane);
  const collaborationView = createCollaborationView(state);
  const cockpitView = createRuntimeCockpitView(dashboard, DASHBOARD_UI_OPTIONS);
  const spectatorArtifacts = createSpectatorArtifacts(scenarioId, dashboard);
  const workflowView = createWorkflowVisualizationView(state, {
    includeCompleted: true,
    maxAuditEntries: 8,
    taskId: 'task-power',
    traceId: 'trace-runtime-web'
  });

  return {
    controlPlane,
    powerCardsView: createPowerCardsView(state),
    provenanceView: createProvenanceComplianceView(state),
    branchFinisherView: createBranchFinisherView(state),
    spectatorShare: spectatorArtifacts.spectatorShare,
    webViews: {
      collaborationView,
      cockpitView,
      gameUiView: createRuntimeGameUiView(dashboard, DASHBOARD_UI_OPTIONS),
      kernelView: createRuntimeKernelView(dashboard, DASHBOARD_UI_OPTIONS),
      missionBoardView: createMissionBoardView(dashboard),
      observabilityView: createRuntimeObservabilitySurfaceView(dashboard),
      genericHostBridgeView: createGenericHostBridgeView(dashboard),
      observerView: createRuntimeObserverView(dashboard, collaborationView, DASHBOARD_UI_OPTIONS),
      proofDossierView: createRuntimeProofDossierView(dashboard),
      workflowView,
      spectatorView: spectatorArtifacts.spectatorView,
      vscodePanelView: createVsCodePanelView(dashboard),
      expertView: createExpertCockpitView(state, {
        dashboard,
        taskId: workflowView.focus.taskId ?? 'task-power',
        traceId: workflowView.focus.traceId ?? 'trace-runtime-web',
        targetAgentId: 'orch-1',
        dashboardUiOptions: DASHBOARD_UI_OPTIONS
      })
    }
  };
}

function createRunId(scenarioId: string): string {
  return `run:${scenarioId}`;
}

function createBlockedGuardrailsScenario(): RuntimeViewsScenario {
  const baseState = createBaseState({
    scenarioId: 'blocked-guardrails',
    branch: 'feature/provenance-clean',
    outcome: 'blocked',
    lastSequenceId: 34,
    workflowSteps: [
      {
        step: 'Power card activation applied',
        detail: 'Host review relay enabled.',
        sourceEventType: 'power_card_activation',
        traceId: 'power-001',
        taskId: 'task-power',
        metadata: {
          powerCardId: 'power-card.host-review',
          enabled: true,
          allowed: true,
          actorId: 'orch-1'
        },
        sequenceId: 20,
        timestamp: '2026-04-12T01:00:20.000Z',
        agentId: 'orch-1'
      },
      {
        step: 'Power card activation blocked',
        detail: 'blocked trust',
        sourceEventType: 'power_card_activation',
        traceId: 'power-002',
        taskId: 'task-power',
        metadata: {
          powerCardId: 'power-card.branch-guard',
          enabled: true,
          allowed: false,
          reason: 'Power card Branch Guard is blocked by trust status blocked.',
          actorId: 'orch-1'
        },
        sequenceId: 33,
        timestamp: '2026-04-12T01:31:00.000Z',
        agentId: 'orch-1'
      }
    ]
  });

  const state = createScenarioState(
    baseState,
    {
      'powerCards.runtimeSnapshot': {
        'power-card.host-review': { enabled: true },
        'power-card.branch-guard': { enabled: false }
      },
      'powerCards.storageSnapshot': {
        'power-card.host-review': { enabled: true },
        'power-card.branch-guard': { enabled: false }
      },
      'powerCards.cardGovernance': {
        'power-card.host-review': {
          origin: 'runtime_adapter',
          requiredPolicy: 'surface_scoped',
          trustStatus: 'trusted',
          riskClass: 'high'
        },
        'power-card.branch-guard': {
          origin: 'runtime_ui',
          requiredPolicy: 'elevated',
          trustStatus: 'blocked',
          riskClass: 'critical'
        }
      }
    },
    {
      'provenanceRegistry.snapshot': {
        'plugin.host-review': {
          kind: 'plugin',
          label: 'Host Review Relay',
          sourceRef: 'repo://plugins/host-review',
          licenseId: 'MIT',
          attributionRequired: false,
          attributionRefs: []
        },
        'asset.hero-banner': {
          kind: 'asset',
          label: 'Hero Banner',
          sourceRef: 'asset://hero-banner/source',
          licenseId: 'CC-BY-4.0',
          attributionRequired: true,
          attributionRefs: []
        }
      }
    }
  );
  const controlPlane = createControlPlaneState('blocked-guardrails', 'feature/provenance-clean', state.lastSequenceId, 'blocked');

  return {
    id: 'blocked-guardrails',
    title: 'Guardrails bloquants',
    description: 'Une carte est rejetee par le trust et une attribution manquante bloque la fin de branche.',
    outcome: 'blocked',
    tags: ['trust blocked', 'missing attribution', 'merge blocked'],
    walkthrough: [
      'Host Review Relay reste actif et persiste correctement.',
      'Branch Guard tente une activation, mais le trust le rejette immediatement.',
      'Hero Banner manque de bundle d attribution, donc merge et pr sont refuses.'
    ],
    state,
    ...buildScenarioViews('blocked-guardrails', state, controlPlane)
  };
}

function createReleaseReadyScenario(): RuntimeViewsScenario {
  const baseState = createBaseState({
    scenarioId: 'release-ready',
    branch: 'release/ready-runtime',
    outcome: 'clear',
    lastSequenceId: 52,
    workflowSteps: [
      {
        step: 'Power card activation applied',
        detail: 'Host review relay enabled.',
        sourceEventType: 'power_card_activation',
        traceId: 'power-101',
        taskId: 'task-power',
        metadata: {
          powerCardId: 'power-card.host-review',
          enabled: true,
          allowed: true,
          actorId: 'orch-1'
        },
        sequenceId: 50,
        timestamp: '2026-04-12T02:10:00.000Z',
        agentId: 'orch-1'
      },
      {
        step: 'Power card activation applied',
        detail: 'Branch guard enabled.',
        sourceEventType: 'power_card_activation',
        traceId: 'power-102',
        taskId: 'task-power',
        metadata: {
          powerCardId: 'power-card.branch-guard',
          enabled: true,
          allowed: true,
          actorId: 'orch-1'
        },
        sequenceId: 51,
        timestamp: '2026-04-12T02:11:00.000Z',
        agentId: 'orch-1'
      }
    ]
  });

  const state = createScenarioState(
    baseState,
    {
      'powerCards.runtimeSnapshot': {
        'power-card.host-review': { enabled: true },
        'power-card.branch-guard': { enabled: true }
      },
      'powerCards.storageSnapshot': {
        'power-card.host-review': { enabled: true },
        'power-card.branch-guard': { enabled: true }
      },
      'powerCards.cardGovernance': {
        'power-card.host-review': {
          origin: 'runtime_adapter',
          requiredPolicy: 'surface_scoped',
          trustStatus: 'trusted',
          riskClass: 'high'
        },
        'power-card.branch-guard': {
          origin: 'runtime_ui',
          requiredPolicy: 'elevated',
          trustStatus: 'trusted',
          riskClass: 'critical'
        }
      }
    },
    {
      'provenanceRegistry.snapshot': {
        'plugin.host-review': {
          kind: 'plugin',
          label: 'Host Review Relay',
          sourceRef: 'repo://plugins/host-review',
          licenseId: 'MIT',
          attributionRequired: false,
          attributionRefs: []
        },
        'asset.hero-banner': {
          kind: 'asset',
          label: 'Hero Banner',
          sourceRef: 'asset://hero-banner/source',
          licenseId: 'CC-BY-4.0',
          attributionRequired: true,
          attributionRefs: ['artifact://attribution/hero-banner']
        },
        'asset.room-kit': {
          kind: 'asset',
          label: 'Room Kit',
          sourceRef: 'asset://room-kit/source',
          licenseId: 'CC0-1.0',
          attributionRequired: false,
          attributionRefs: []
        }
      }
    }
  );
  const taskPower = state.tasks['task-power'];
  if (taskPower !== undefined) {
    state.tasks['task-power'] = { ...taskPower, status: 'done' };
  }
  const taskProvenance = state.tasks['task-provenance'];
  if (taskProvenance !== undefined) {
    state.tasks['task-provenance'] = { ...taskProvenance, status: 'done' };
  }
  const taskRelease = state.tasks['task-release'];
  if (taskRelease !== undefined) {
    state.tasks['task-release'] = { ...taskRelease, status: 'review', assigneeId: 'orch-1' };
  }
  const controlPlane = createControlPlaneState('release-ready', 'release/ready-runtime', state.lastSequenceId, 'clear');

  return {
    id: 'release-ready',
    title: 'Release ready',
    description: 'Toutes les cartes sont synchronisees, la provenance est complete, merge et pr redeviennent autorises.',
    outcome: 'clear',
    tags: ['all clear', 'trusted', 'merge allowed'],
    walkthrough: [
      'Les deux cartes de pouvoir sont alignees entre runtime et stockage.',
      'Chaque entree de provenance declare source, licence et attribution si necessaire.',
      'Le branch finisher repasse en vert et laisse merge et pr disponibles.'
    ],
    state,
    ...buildScenarioViews('release-ready', state, controlPlane)
  };
}

function createDriftAndComplianceScenario(): RuntimeViewsScenario {
  const baseState = createBaseState({
    scenarioId: 'drift-and-compliance',
    branch: 'hotfix/drift-audit',
    outcome: 'attention',
    lastSequenceId: 64,
    workflowSteps: [
      {
        step: 'Power card activation applied',
        detail: 'Host review relay enabled in runtime only.',
        sourceEventType: 'power_card_activation',
        traceId: 'power-201',
        taskId: 'task-power',
        metadata: {
          powerCardId: 'power-card.host-review',
          enabled: true,
          allowed: true,
          actorId: 'orch-1'
        },
        sequenceId: 61,
        timestamp: '2026-04-12T02:30:00.000Z',
        agentId: 'orch-1'
      },
      {
        step: 'Power card activation applied',
        detail: 'Branch guard enabled after policy review.',
        sourceEventType: 'power_card_activation',
        traceId: 'power-202',
        taskId: 'task-power',
        metadata: {
          powerCardId: 'power-card.branch-guard',
          enabled: true,
          allowed: true,
          actorId: 'orch-1'
        },
        sequenceId: 62,
        timestamp: '2026-04-12T02:31:00.000Z',
        agentId: 'orch-1'
      }
    ]
  });

  const state = createScenarioState(
    baseState,
    {
      'powerCards.runtimeSnapshot': {
        'power-card.host-review': { enabled: true },
        'power-card.branch-guard': { enabled: true }
      },
      'powerCards.storageSnapshot': {
        'power-card.host-review': { enabled: false },
        'power-card.branch-guard': { enabled: true }
      },
      'powerCards.cardGovernance': {
        'power-card.host-review': {
          origin: 'runtime_adapter',
          requiredPolicy: 'surface_scoped',
          trustStatus: 'trusted',
          riskClass: 'high'
        },
        'power-card.branch-guard': {
          origin: 'runtime_ui',
          requiredPolicy: 'elevated',
          trustStatus: 'trusted',
          riskClass: 'critical'
        }
      }
    },
    {
      'provenanceRegistry.snapshot': {
        'plugin.host-review': {
          kind: 'plugin',
          label: 'Host Review Relay',
          sourceRef: 'repo://plugins/host-review',
          attributionRequired: false,
          attributionRefs: []
        },
        'asset.untracked-room': {
          kind: 'asset',
          label: 'Untracked Room Kit',
          licenseId: 'CC-BY-4.0',
          attributionRequired: true,
          attributionRefs: ['artifact://attribution/untracked-room']
        },
        'asset.hero-banner': {
          kind: 'asset',
          label: 'Hero Banner',
          sourceRef: 'asset://hero-banner/source',
          licenseId: 'CC-BY-4.0',
          attributionRequired: true,
          attributionRefs: ['artifact://attribution/hero-banner']
        }
      }
    }
  );
  const driftPowerTask = state.tasks['task-power'];
  if (driftPowerTask !== undefined) {
    state.tasks['task-power'] = {
      ...driftPowerTask,
      status: 'review',
      blockedReason: 'Runtime and storage diverge.'
    };
  }
  const driftProvenanceTask = state.tasks['task-provenance'];
  if (driftProvenanceTask !== undefined) {
    state.tasks['task-provenance'] = { ...driftProvenanceTask, status: 'review' };
  }
  const controlPlane = createControlPlaneState('drift-and-compliance', 'hotfix/drift-audit', state.lastSequenceId, 'attention');

  return {
    id: 'drift-and-compliance',
    title: 'Drift et hygiene compliance',
    description: 'Un drift runtime/storage apparait sur une carte, pendant que la provenance detecte source et licence manquantes.',
    outcome: 'attention',
    tags: ['runtime drift', 'missing license', 'missing source'],
    walkthrough: [
      'Host Review Relay diverge entre runtime et stockage, ce qui force une investigation de persistance.',
      'Deux entrees de provenance sont incompletes, donc le front reste a risque.',
      'Le branch finisher maintient le blocage jusqu a regularisation des metadonnees de provenance.'
    ],
    state,
    ...buildScenarioViews('drift-and-compliance', state, controlPlane)
  };
}

export function createRuntimeViewsDemoScenarios(): readonly RuntimeViewsScenario[] {
  return [createBlockedGuardrailsScenario(), createReleaseReadyScenario(), createDriftAndComplianceScenario()];
}

export function createRuntimeViewsDemoData(defaultScenarioId = 'blocked-guardrails'): RuntimeViewsDemoData {
  return {
    scenarios: createRuntimeViewsDemoScenarios(),
    defaultScenarioId
  };
}
