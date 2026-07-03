import {
  CONTROL_PLANE_REGISTRY_VERSION,
  LEASE_STORE_VERSION,
  NODE_REGISTRY_VERSION,
  RUNTIME_PROTOCOL_VERSION,
  type AgentPresence
} from '../../src/contracts/events';
import type { GameState } from '../../src/state/game-state';
import { createRuntimeCockpitView } from '../../src/state/runtime-cockpit-view';
import { createRuntimeDashboardView } from '../../src/state/runtime-dashboard-view';

const DEV_AGENT: AgentPresence = {
  id: 'dev-1',
  name: 'Amelia',
  role: 'agent',
  status: 'working',
  roomId: 'build-room',
  position: { x: 8, y: 8 },
  parentId: 'orch-1',
  lastTool: 'create_file'
};

function createBaseState(): GameState {
  return {
    protocolVersion: 'v1',
    lastSequenceId: 220,
    hydratedAt: '2026-04-09T00:00:00.000Z',
    agents: {
      'orch-1': {
        id: 'orch-1',
        name: 'Orchestrator',
        role: 'orchestrator',
        status: 'idle',
        roomId: 'war-room',
        position: { x: 4, y: 4 }
      },
      'dev-1': DEV_AGENT,
      'qa-1': {
        id: 'qa-1',
        name: 'Quinn',
        role: 'agent',
        status: 'idle',
        roomId: 'qa-room',
        position: { x: 10, y: 8 },
        parentId: 'orch-1',
        lastTool: 'runTests'
      }
    },
    tasks: {
      'task-auth': {
        id: 'task-auth',
        title: 'Implement auth',
        status: 'review',
        assigneeId: 'dev-1'
      },
      'task-qa': {
        id: 'task-qa',
        title: 'QA pass',
        status: 'in_progress',
        assigneeId: 'qa-1'
      }
    },
    config: {},
    recentToolCalls: [
      {
        tool: 'create_file',
        params: { path: 'src/auth.ts', task_id: 'task-auth' },
        sourceEventType: 'artifact_created',
        traceId: 'session-001',
        sequenceId: 180,
        timestamp: '2026-04-09T00:03:00.000Z',
        agentId: 'dev-1'
      }
    ],
    hostBindings: {
      'host-claude': {
        sequenceId: 150,
        timestamp: '2026-04-09T00:02:30.000Z',
        binding: {
          hostId: 'host-claude',
          hostType: 'claude',
          displayName: 'Claude Code',
          authMode: 'token',
          connectionState: 'degraded',
          trustStatus: 'review',
          scopes: ['fs'],
          capabilityManifestRef: 'manifest-claude',
          sourceOfTruth: 'secondary',
          lastSeenAt: '2026-04-09T00:02:30.000Z'
        },
        manifest: {
          manifestId: 'manifest-claude',
          hostId: 'host-claude',
          routines: ['code-review'],
          toolProviders: ['claude-cli'],
          reviewChannels: ['review-import'],
          contextSources: ['selection'],
          permissionMode: 'prompt',
          supportsStreaming: true,
          supportsReviewImport: true,
          supportsContextImport: true,
          supportsPreviewCommit: true
        },
        reason: 'Connector drift forces review-only mode.'
      }
    },
    recentHostInvocationDecisions: [],
    recentHostContextEntries: [
      {
        sequenceId: 181,
        timestamp: '2026-04-09T00:03:01.000Z',
        entry: {
          entryId: 'ctx-claude-001',
          hostId: 'host-claude',
          sourceType: 'selection',
          visibility: 'shared',
          confidence: 7.4,
          importedAt: '2026-04-09T00:03:01.000Z',
          ttlSeconds: 600,
          contentRef: 'selection://runtime-cockpit/auth',
          trustStatus: 'review'
        },
        meta: {
          traceId: 'session-001',
          taskId: 'task-auth',
          correlationId: 'corr-auth-001',
          hostId: 'host-claude'
        }
      }
    ],
    recentHostReviews: [
      {
        sequenceId: 182,
        timestamp: '2026-04-09T00:03:02.000Z',
        review: {
          reviewId: 'review-claude-001',
          hostId: 'host-claude',
          sourceType: 'claude_review',
          subjectRef: 'task:task-auth',
          verdict: 'warn',
          findings: [
            {
              id: 'finding-001',
              severity: 'medium',
              message: 'Imported host remains degraded during auth review.',
              resolutionStatus: 'open'
            }
          ],
          linkedEvidenceRefs: ['artifact://host/review-auth-001'],
          importedAt: '2026-04-09T00:03:02.000Z',
          traceId: 'session-001',
          taskId: 'task-auth'
        },
        meta: {
          traceId: 'session-001',
          taskId: 'task-auth',
          correlationId: 'corr-auth-001',
          hostId: 'host-claude'
        }
      }
    ],
    recentWorkflowSteps: [
      {
        step: 'Routing dispatch',
        detail: 'Intent routed: Implement auth',
        sourceEventType: 'routing',
        traceId: 'session-001',
        taskId: 'task-auth',
        metadata: { intent: 'Implement auth' },
        sequenceId: 120,
        timestamp: '2026-04-09T00:01:00.000Z',
        agentId: 'orch-1'
      },
      {
        step: 'Decision recorded',
        detail: 'auth: JWT middleware ready',
        sourceEventType: 'decision',
        traceId: 'session-001',
        taskId: 'task-auth',
        metadata: {
          topic: 'auth',
          actionId: 'task.transition.done',
          verificationRef: 'verify://task-auth/runtime-dashboard',
          controlsExecuted: ['tests:unit', 'review:critical-findings'],
          evidenceRefs: ['tests://grimoire-game/runtime-dashboard#task-auth'],
          verdict: 'PASS'
        },
        sequenceId: 140,
        timestamp: '2026-04-09T00:01:20.000Z',
        agentId: 'dev-1'
      }
    ],
    lastErrors: []
  };
}

describe('runtime-cockpit-view', () => {
  it('assembles a compact operator cockpit from canonical runtime read models', () => {
    const dashboard = createRuntimeDashboardView(
      createBaseState(),
      {
        observability: {
          maxTimelineRows: 12,
          maxAttentionItems: 12
        }
      },
      {
        projectRegistry: {
          registryVersion: CONTROL_PLANE_REGISTRY_VERSION,
          generatedAt: '2026-04-11T10:00:10.000Z',
          activeProject: {
            protocolVersion: RUNTIME_PROTOCOL_VERSION,
            projectId: 'grimoire-game',
            runId: 'run-42',
            firstEventAt: '2026-04-11T10:00:00.000Z',
            lastEventAt: '2026-04-11T10:00:10.000Z',
            firstSequenceId: 1,
            lastSequenceId: 220,
            eventCount: 3,
            lastMessageId: 'task.update:220',
            nodeId: 'node-alpha',
            workerId: 'worker-dev-1',
            leaseId: 'lease-auth',
            worktreeId: 'wt-auth',
            nodeIds: ['node-alpha'],
            workerIds: ['worker-dev-1'],
            leaseIds: ['lease-auth'],
            worktreeIds: ['wt-auth'],
            channels: ['runtime'],
            messageTypes: ['task.update']
          }
        },
        nodeRegistry: {
          registryVersion: NODE_REGISTRY_VERSION,
          generatedAt: '2026-04-11T10:00:10.000Z',
          projectId: 'grimoire-game',
          runId: 'run-42',
          nodes: [
            {
              protocolVersion: RUNTIME_PROTOCOL_VERSION,
              projectId: 'grimoire-game',
              runId: 'run-42',
              nodeId: 'node-alpha',
              firstSeenAt: '2026-04-11T10:00:00.000Z',
              lastSeenAt: '2026-04-11T10:00:10.000Z',
              firstSequenceId: 1,
              lastSequenceId: 220,
              messageCount: 3,
              staleAfterMs: 5_000,
              offlineAfterMs: 15_000,
              ageMs: 1_000,
              status: 'live',
              leaseId: 'lease-auth',
              worktreeId: 'wt-auth',
              capabilityTags: ['typescript', 'tests'],
              workerIds: ['worker-dev-1'],
              workers: [
                {
                  workerId: 'worker-dev-1',
                  firstSeenAt: '2026-04-11T10:00:00.000Z',
                  lastSeenAt: '2026-04-11T10:00:10.000Z',
                  firstSequenceId: 1,
                  lastSequenceId: 220,
                  messageCount: 3,
                  leaseId: 'lease-auth',
                  worktreeId: 'wt-auth'
                }
              ],
              channels: ['runtime'],
              messageTypes: ['task.update']
            }
          ],
          summary: {
            nodeCount: 1,
            liveNodeCount: 1,
            staleNodeCount: 0,
            offlineNodeCount: 0,
            workerCount: 1
          }
        },
        leaseStore: {
          registryVersion: LEASE_STORE_VERSION,
          generatedAt: '2026-04-11T10:00:10.000Z',
          projectId: 'grimoire-game',
          runId: 'run-42',
          leases: [
            {
              protocolVersion: RUNTIME_PROTOCOL_VERSION,
              projectId: 'grimoire-game',
              runId: 'run-42',
              leaseId: 'lease-auth',
              taskId: 'task-auth',
              nodeId: 'node-alpha',
              workerId: 'worker-dev-1',
              worktreeId: 'wt-auth',
              branch: 'feature/auth',
              claimedAt: '2026-04-11T10:00:00.000Z',
              lastRenewedAt: '2026-04-11T10:00:05.000Z',
              expiresAt: '2026-04-11T10:00:35.000Z',
              ttlMs: 30_000,
              ageMs: 5_000,
              status: 'active',
              messageCount: 2,
              lastSequenceId: 220,
              channels: ['runtime'],
              messageTypes: ['task.update']
            }
          ],
          summary: {
            leaseCount: 1,
            activeLeaseCount: 1,
            expiredLeaseCount: 0
          }
        }
      }
    );

    const cockpit = createRuntimeCockpitView(dashboard, {
      maxTasksPerLane: 2,
      maxAttentionItems: 4,
      maxTimelinePoints: 6
    });

    expect(cockpit.header).toMatchObject({
      projectId: 'grimoire-game',
      runId: 'run-42',
      registryVersion: CONTROL_PLANE_REGISTRY_VERSION,
      tone: 'warning'
    });
    expect(cockpit.focus).toMatchObject({
      runId: 'run-42',
      nodeId: 'node-alpha',
      taskId: 'task-auth'
    });
    expect(cockpit.fleet[0]).toMatchObject({
      nodeId: 'node-alpha',
      activeLeaseCount: 1,
      workerCount: 1
    });
    expect(cockpit.hosts[0]).toMatchObject({
      hostId: 'host-claude',
      displayName: 'Claude Code',
      tone: 'warning',
      routines: ['code-review'],
      reviewArtifactCount: 1,
      openReviewFindingCount: 1
    });
    expect(cockpit.ownership[0]).toMatchObject({
      taskId: 'task-auth',
      ownerId: 'worker-dev-1',
      branch: 'feature/auth',
      worktreeId: 'wt-auth',
      dirtyStatus: 'dirty',
      ownershipStatus: 'owned'
    });
    expect(cockpit.proofs.some((proof) => proof.verificationRef === 'verify://task-auth/runtime-dashboard')).toBe(true);
    expect(cockpit.proofs).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          source: 'external_review',
          hostId: 'host-claude',
          verificationRef: 'verify://task-auth/runtime-dashboard',
          taskId: 'task-auth',
          traceId: 'session-001'
        })
      ])
    );
    expect(cockpit.ui.fleet).toHaveLength(1);
    expect(cockpit.ui.ownership).toHaveLength(1);
    expect(cockpit.ui.hosts).toHaveLength(1);
  });
});