import {
  createHostBindingStateEvent,
  createHostContextLedgerUpdateEvent,
  createStateSnapshotEvent,
  createToolCallEvent,
  createWorkflowStepEvent,
  RUNTIME_PROTOCOL_VERSION,
  type AgentPresence,
  type GameStateSnapshot,
  type ServerEvent
} from '../../src/contracts/events';
import { applyServerEvents, createEmptyGameState, hydrateGameState } from '../../src/state/game-state';
import { createMemoryRecallView } from '../../src/state/memory-recall-view';

const ORCHESTRATOR: AgentPresence = {
  id: 'orch-1',
  name: 'Orchestrator',
  role: 'orchestrator',
  status: 'idle',
  roomId: 'war-room',
  position: { x: 4, y: 4 }
};

const DEV_AGENT: AgentPresence = {
  id: 'dev-1',
  name: 'Amelia',
  role: 'agent',
  status: 'working',
  roomId: 'library-room',
  position: { x: 6, y: 8 },
  parentId: 'orch-1',
  lastTool: 'grimoire_memory_search'
};

const SNAPSHOT: GameStateSnapshot = {
  protocolVersion: RUNTIME_PROTOCOL_VERSION,
  generatedAt: '2026-04-11T18:00:00.000Z',
  lastSequenceId: 1,
  agents: [ORCHESTRATOR, DEV_AGENT],
  tasks: [
    {
      id: 'task-memory',
      title: 'Refresh auth guidance',
      status: 'in_progress',
      assigneeId: 'dev-1'
    }
  ],
  config: {},
  recentToolCalls: [],
  recentWorkflowSteps: []
};

function createMixedRecallEvents(): ServerEvent[] {
  return [
    createHostBindingStateEvent(
      2,
      {
        binding: {
          hostId: 'host-memory',
          hostType: 'mcp',
          displayName: 'Mnemo Archive',
          authMode: 'delegated',
          connectionState: 'online',
          trustStatus: 'trusted',
          scopes: ['fs'],
          capabilityManifestRef: 'manifest://host-memory',
          sourceOfTruth: 'secondary',
          lastSeenAt: '2026-04-11T18:20:00.000Z'
        },
        manifest: {
          manifestId: 'manifest://host-memory',
          hostId: 'host-memory',
          routines: ['memory.search', 'memory.store'],
          toolProviders: ['grimoire-memory'],
          reviewChannels: ['review-import'],
          contextSources: ['memory', 'session_context'],
          permissionMode: 'policy',
          supportsStreaming: false,
          supportsReviewImport: true,
          supportsContextImport: true,
          supportsPreviewCommit: false
        },
        reason: 'Memory archive connected'
      },
      {
        timestamp: '2026-04-11T18:00:01.000Z'
      }
    ),
    createHostContextLedgerUpdateEvent(
      3,
      {
        entry: {
          entryId: 'ctx-fresh-1',
          hostId: 'host-memory',
          sourceType: 'memory',
          visibility: 'shared',
          confidence: 8.9,
          importedAt: '2026-04-11T18:05:00.000Z',
          ttlSeconds: 86_400,
          contentRef: 'memory://auth/current',
          trustStatus: 'trusted'
        },
        meta: {
          traceId: 'trace-memory-1',
          taskId: 'task-memory',
          correlationId: 'corr-memory-1',
          hostId: 'host-memory'
        }
      },
      {
        timestamp: '2026-04-11T18:05:00.000Z'
      }
    ),
    createHostContextLedgerUpdateEvent(
      4,
      {
        entry: {
          entryId: 'ctx-stale-1',
          hostId: 'host-memory',
          sourceType: 'session_context',
          visibility: 'shared',
          confidence: 6.4,
          importedAt: '2026-04-11T18:00:00.000Z',
          ttlSeconds: 60,
          contentRef: 'session://auth/old-note',
          trustStatus: 'review'
        },
        meta: {
          traceId: 'trace-memory-1',
          taskId: 'task-memory',
          correlationId: 'corr-memory-1',
          hostId: 'host-memory'
        }
      },
      {
        timestamp: '2026-04-11T18:00:00.000Z'
      }
    ),
    createHostContextLedgerUpdateEvent(
      5,
      {
        entry: {
          entryId: 'ctx-legacy-1',
          hostId: 'host-memory',
          sourceType: 'memory',
          visibility: 'shared',
          confidence: 7.1,
          importedAt: '2026-04-11T18:06:00.000Z',
          ttlSeconds: 86_400,
          contentRef: 'memory://auth/legacy-guidance',
          trustStatus: 'review'
        },
        meta: {
          traceId: 'trace-memory-1',
          taskId: 'task-memory',
          correlationId: 'corr-memory-1',
          hostId: 'host-memory'
        }
      },
      {
        timestamp: '2026-04-11T18:06:00.000Z'
      }
    ),
    createHostContextLedgerUpdateEvent(
      6,
      {
        entry: {
          entryId: 'ctx-current-2',
          hostId: 'host-memory',
          sourceType: 'review_summary',
          visibility: 'shared',
          confidence: 9.2,
          importedAt: '2026-04-11T18:15:00.000Z',
          ttlSeconds: 86_400,
          contentRef: 'memory://auth/current-guidance',
          supersedes: 'ctx-legacy-1',
          trustStatus: 'trusted'
        },
        meta: {
          traceId: 'trace-memory-1',
          taskId: 'task-memory',
          correlationId: 'corr-memory-1',
          hostId: 'host-memory'
        }
      },
      {
        timestamp: '2026-04-11T18:15:00.000Z'
      }
    ),
    createToolCallEvent(
      7,
      {
        tool: 'grimoire_memory_search',
        params: {
          task_id: 'task-memory',
          host_id: 'host-memory',
          correlationId: 'corr-memory-1',
          memory_tier: 'long_term',
          content_refs: ['memory://auth/current']
        },
        sourceEventType: 'memory_read',
        traceId: 'trace-memory-1'
      },
      {
        timestamp: '2026-04-11T18:16:00.000Z',
        agent: DEV_AGENT
      }
    ),
    createWorkflowStepEvent(
      8,
      {
        step: 'Review obsolete note',
        detail: 'Review imported the expired session note.',
        sourceEventType: 'review',
        traceId: 'trace-memory-1',
        taskId: 'task-memory',
        metadata: {
          memoryAccess: 'read',
          contentRefs: ['session://auth/old-note'],
          correlationId: 'corr-memory-1'
        }
      },
      {
        timestamp: '2026-04-11T18:17:00.000Z',
        agent: DEV_AGENT
      }
    ),
    createWorkflowStepEvent(
      9,
      {
        step: 'Challenge archived guidance',
        detail: 'Challenge critique relied on superseded guidance.',
        sourceEventType: 'challenge_critique',
        traceId: 'trace-memory-1',
        taskId: 'task-memory',
        metadata: {
          memoryAccess: 'read',
          memoryRefs: ['memory://auth/legacy-guidance'],
          correlationId: 'corr-memory-1'
        }
      },
      {
        timestamp: '2026-04-11T18:18:00.000Z',
        agent: DEV_AGENT
      }
    ),
    createToolCallEvent(
      10,
      {
        tool: 'grimoire_memory_search',
        params: {
          task_id: 'task-memory',
          host_id: 'host-memory',
          correlationId: 'corr-memory-1',
          memory_tier: 'long_term',
          content_refs: ['memory://auth/missing']
        },
        sourceEventType: 'memory_read',
        traceId: 'trace-memory-1'
      },
      {
        timestamp: '2026-04-11T18:20:00.000Z',
        agent: DEV_AGENT
      }
    )
  ];
}

function createRemediatedRecallEvents(): ServerEvent[] {
  return [
    createHostBindingStateEvent(
      2,
      {
        binding: {
          hostId: 'host-memory',
          hostType: 'mcp',
          displayName: 'Mnemo Archive',
          authMode: 'delegated',
          connectionState: 'online',
          trustStatus: 'trusted',
          scopes: ['fs'],
          capabilityManifestRef: 'manifest://host-memory',
          sourceOfTruth: 'secondary',
          lastSeenAt: '2026-04-11T18:12:00.000Z'
        },
        manifest: {
          manifestId: 'manifest://host-memory',
          hostId: 'host-memory',
          routines: ['memory.search', 'memory.store'],
          toolProviders: ['grimoire-memory'],
          reviewChannels: ['review-import'],
          contextSources: ['memory', 'session_context'],
          permissionMode: 'policy',
          supportsStreaming: false,
          supportsReviewImport: true,
          supportsContextImport: true,
          supportsPreviewCommit: false
        },
        reason: 'Memory archive connected'
      },
      {
        timestamp: '2026-04-11T18:00:01.000Z'
      }
    ),
    createHostContextLedgerUpdateEvent(
      3,
      {
        entry: {
          entryId: 'ctx-current-1',
          hostId: 'host-memory',
          sourceType: 'memory',
          visibility: 'shared',
          confidence: 9.1,
          importedAt: '2026-04-11T18:08:00.000Z',
          ttlSeconds: 86_400,
          contentRef: 'memory://auth/current-guidance',
          trustStatus: 'trusted'
        },
        meta: {
          traceId: 'trace-memory-1',
          taskId: 'task-memory',
          correlationId: 'corr-memory-1',
          hostId: 'host-memory'
        }
      },
      {
        timestamp: '2026-04-11T18:08:00.000Z'
      }
    ),
    createToolCallEvent(
      4,
      {
        tool: 'grimoire_memory_search',
        params: {
          task_id: 'task-memory',
          host_id: 'host-memory',
          correlationId: 'corr-memory-1',
          memory_tier: 'long_term',
          content_refs: ['memory://auth/current-guidance']
        },
        sourceEventType: 'memory_read',
        traceId: 'trace-memory-1'
      },
      {
        timestamp: '2026-04-11T18:10:00.000Z',
        agent: DEV_AGENT
      }
    ),
    createWorkflowStepEvent(
      5,
      {
        step: 'Review refreshed guidance',
        detail: 'Review used refreshed memory references only.',
        sourceEventType: 'review',
        traceId: 'trace-memory-1',
        taskId: 'task-memory',
        metadata: {
          memoryAccess: 'read',
          contentRefs: ['memory://auth/current-guidance'],
          correlationId: 'corr-memory-1'
        }
      },
      {
        timestamp: '2026-04-11T18:12:00.000Z',
        agent: DEV_AGENT
      }
    )
  ];
}

describe('memory recall view', () => {
  it('projects recall precision, recall and obsolescence metrics with explicit review and challenge locations', () => {
    const state = applyServerEvents(hydrateGameState(SNAPSHOT, '2026-04-11T18:00:00.000Z'), createMixedRecallEvents());
    const view = createMemoryRecallView(state);

    expect(view.metrics.sampleSize).toBe(4);
    expect(view.metrics.resolvedReadCount).toBe(3);
    expect(view.metrics.preciseReadCount).toBe(1);
    expect(view.metrics.unresolvedReadCount).toBe(1);
    expect(view.metrics.obsoleteReadCount).toBe(2);
    expect(view.metrics.referenceCount).toBe(4);
    expect(view.metrics.obsoleteReferenceCount).toBe(2);
    expect(view.metrics.staleReferenceCount).toBe(1);
    expect(view.metrics.supersededReferenceCount).toBe(1);
    expect(view.metrics.precision).toBeCloseTo(1 / 3);
    expect(view.metrics.recall).toBeCloseTo(0.75);
    expect(view.metrics.obsolescenceRate).toBeCloseTo(0.5);

    expect(view.periodicReport).toMatchObject({
      threshold: 0.25,
      sampleSize: 4
    });

    expect(view.findings).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          accessId: 'library-memory-access:workflow:8',
          locationType: 'review',
          taskId: 'task-memory',
          traceId: 'trace-memory-1',
          sourceEventType: 'review',
          referenceEntryIds: ['ctx-stale-1']
        }),
        expect.objectContaining({
          accessId: 'library-memory-access:workflow:9',
          locationType: 'challenge',
          taskId: 'task-memory',
          traceId: 'trace-memory-1',
          sourceEventType: 'challenge_critique',
          referenceEntryIds: ['ctx-legacy-1']
        })
      ])
    );

    expect(view.taskGates).toEqual([
      expect.objectContaining({
        taskId: 'task-memory',
        readCount: 4,
        resolvedReadCount: 3,
        obsoleteReadCount: 2,
        blocked: true
      })
    ]);
  });

  it('keeps recall reports stable after replay and clears the gate once refreshed references replace obsolete ones', () => {
    const deltaEvents = createRemediatedRecallEvents();
    const hydratedState = applyServerEvents(hydrateGameState(SNAPSHOT, '2026-04-11T18:00:00.000Z'), deltaEvents);
    const replayedState = applyServerEvents(createEmptyGameState(), [
      createStateSnapshotEvent(1, SNAPSHOT, '2026-04-11T18:00:00.000Z'),
      ...deltaEvents
    ]);

    const view = createMemoryRecallView(hydratedState);
    const replayedView = createMemoryRecallView(replayedState);

    expect(replayedView).toEqual(view);
    expect(view.metrics.obsolescenceRate).toBe(0);
    expect(view.findings).toEqual([]);
    expect(view.taskGates).toEqual([
      expect.objectContaining({
        taskId: 'task-memory',
        blocked: false,
        obsoleteReadCount: 0
      })
    ]);
  });
});