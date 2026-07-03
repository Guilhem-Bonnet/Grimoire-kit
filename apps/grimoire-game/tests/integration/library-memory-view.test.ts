import {
  createAgentStateEvent,
  createHostBindingStateEvent,
  createHostContextLedgerUpdateEvent,
  createStateSnapshotEvent,
  createTaskUpdateEvent,
  createToolCallEvent,
  createWorkflowStepEvent,
  RUNTIME_PROTOCOL_VERSION,
  type AgentPresence,
  type GameStateSnapshot,
  type ServerEvent
} from '../../src/contracts/events';
import { applyServerEvents, createEmptyGameState, hydrateGameState } from '../../src/state/game-state';
import { createLibraryMemoryView, queryLibraryMemoryView } from '../../src/state/library-memory-view';

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
  roomId: 'build-room',
  position: { x: 8, y: 8 },
  parentId: 'orch-1',
  lastTool: 'grimoire_memory_search'
};

const SNAPSHOT: GameStateSnapshot = {
  protocolVersion: RUNTIME_PROTOCOL_VERSION,
  generatedAt: '2026-04-11T16:00:00.000Z',
  lastSequenceId: 1,
  agents: [ORCHESTRATOR, DEV_AGENT],
  tasks: [
    {
      id: 'task-memory',
      title: 'Sync auth learnings',
      status: 'in_progress',
      assigneeId: 'dev-1'
    }
  ],
  config: {},
  recentToolCalls: [],
  recentWorkflowSteps: []
};

function createLibraryMemoryEvents(): ServerEvent[] {
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
          lastSeenAt: '2026-04-11T16:10:01.000Z'
        },
        manifest: {
          manifestId: 'manifest://host-memory',
          hostId: 'host-memory',
          routines: ['memory.search', 'memory.store'],
          toolProviders: ['grimoire-memory'],
          reviewChannels: [],
          contextSources: ['memory', 'session_context'],
          permissionMode: 'policy',
          supportsStreaming: false,
          supportsReviewImport: false,
          supportsContextImport: true,
          supportsPreviewCommit: false
        },
        reason: 'Memory archive connected'
      },
      {
        timestamp: '2026-04-11T16:00:01.000Z'
      }
    ),
    createWorkflowStepEvent(
      3,
      {
        step: 'Memory sync started',
        detail: 'Preparing the auth learning sync.',
        sourceEventType: 'memory_sync',
        traceId: 'trace-memory-1',
        taskId: 'task-memory',
        metadata: {
          correlationId: 'corr-memory-1'
        }
      },
      {
        timestamp: '2026-04-11T16:00:02.000Z',
        agent: DEV_AGENT
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
          content_refs: ['memory://learnings/auth']
        },
        sourceEventType: 'memory_read',
        traceId: 'trace-memory-1'
      },
      {
        timestamp: '2026-04-11T16:00:10.000Z',
        agent: DEV_AGENT
      }
    ),
    createHostContextLedgerUpdateEvent(
      5,
      {
        entry: {
          entryId: 'ctx-long-1',
          hostId: 'host-memory',
          sourceType: 'memory',
          visibility: 'shared',
          confidence: 8.5,
          importedAt: '2026-04-11T16:00:12.000Z',
          ttlSeconds: 86400,
          contentRef: 'memory://learnings/auth',
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
        timestamp: '2026-04-11T16:00:12.000Z'
      }
    ),
    createToolCallEvent(
      6,
      {
        tool: 'grimoire_memory_store',
        params: {
          task_id: 'task-memory',
          host_id: 'host-memory',
          correlationId: 'corr-memory-1',
          memory_tier: 'short_term',
          content_refs: ['session://auth/notes']
        },
        sourceEventType: 'memory_write',
        traceId: 'trace-memory-1'
      },
      {
        timestamp: '2026-04-11T16:10:00.000Z',
        agent: DEV_AGENT
      }
    ),
    createHostContextLedgerUpdateEvent(
      7,
      {
        entry: {
          entryId: 'ctx-short-1',
          hostId: 'host-memory',
          sourceType: 'session_context',
          visibility: 'shared',
          confidence: 6.2,
          importedAt: '2026-04-11T16:00:00.000Z',
          ttlSeconds: 300,
          contentRef: 'session://auth/notes',
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
        timestamp: '2026-04-11T16:10:01.000Z'
      }
    )
  ];
}

describe('library memory view', () => {
  it('projects short-term and long-term references with read/write access logs, room objects and agent references', () => {
    const state = applyServerEvents(hydrateGameState(SNAPSHOT, '2026-04-11T16:00:00.000Z'), createLibraryMemoryEvents());
    const view = createLibraryMemoryView(state);

    expect(view.summary).toMatchObject({
      referenceCount: 2,
      shortTermReferenceCount: 1,
      longTermReferenceCount: 1,
      staleReferenceCount: 1,
      accessCount: 4,
      readCount: 1,
      writeCount: 3,
      roomObjectCount: 2,
      agentReferenceCount: 1
    });

    expect(view.references).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          entryId: 'ctx-long-1',
          tier: 'long_term',
          objectType: 'book_shelf',
          agentId: 'dev-1',
          stale: false
        }),
        expect.objectContaining({
          entryId: 'ctx-short-1',
          tier: 'short_term',
          objectType: 'reading_desk',
          agentId: 'dev-1',
          stale: true
        })
      ])
    );

    expect(view.accesses).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          accessType: 'read',
          tier: 'long_term',
          objectType: 'book_shelf',
          agentId: 'dev-1',
          traceId: 'trace-memory-1',
          referenceEntryIds: ['ctx-long-1']
        }),
        expect.objectContaining({
          accessType: 'write',
          tier: 'short_term',
          objectType: 'reading_desk',
          agentId: 'dev-1',
          referenceEntryIds: ['ctx-short-1'],
          stale: true
        })
      ])
    );

    expect(view.roomObjects).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          objectType: 'book_shelf',
          referenceEntryIds: ['ctx-long-1'],
          accessCount: 2
        }),
        expect.objectContaining({
          objectType: 'reading_desk',
          referenceEntryIds: ['ctx-short-1'],
          accessCount: 2,
          staleCount: 3
        })
      ])
    );

    expect(view.agentReferences).toEqual([
      expect.objectContaining({
        agentId: 'dev-1',
        shortTermEntryIds: ['ctx-short-1'],
        longTermEntryIds: ['ctx-long-1'],
        readCount: 1,
        writeCount: 3,
        traceIds: ['trace-memory-1'],
        taskIds: ['task-memory']
      })
    ]);

    const query = queryLibraryMemoryView(view, {
      agentId: 'dev-1',
      traceId: 'trace-memory-1',
      tier: 'long_term',
      accessType: 'read'
    });

    expect(query.references.map((reference) => reference.entryId)).toEqual(['ctx-long-1']);
    expect(query.accesses.map((access) => access.accessId)).toEqual(['library-memory-access:tool:4']);
    expect(query.roomObjects.map((roomObject) => roomObject.objectType)).toEqual(['book_shelf']);
    expect(query.agentReferences.map((agentReference) => agentReference.agentId)).toEqual(['dev-1']);
  });

  it('keeps memory references and access logs stable after replay', () => {
    const deltaEvents = createLibraryMemoryEvents();
    const hydratedState = applyServerEvents(hydrateGameState(SNAPSHOT, '2026-04-11T16:00:00.000Z'), deltaEvents);
    const replayedState = applyServerEvents(createEmptyGameState(), [
      createStateSnapshotEvent(1, SNAPSHOT, '2026-04-11T16:00:00.000Z'),
      ...deltaEvents
    ]);

    const view = createLibraryMemoryView(hydratedState);
    const replayedView = createLibraryMemoryView(replayedState);

    expect(replayedView).toEqual(view);
  });
});