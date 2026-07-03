import type { GameState } from '../../src/state/game-state';
import { createCollaborationView } from '../../src/state/collaboration-view';

function createBaseState(): GameState {
  return {
    protocolVersion: 'v1',
    lastSequenceId: 17,
    hydratedAt: '2026-04-08T00:00:00.000Z',
    agents: {
      'orch-1': {
        id: 'orch-1',
        name: 'Orchestrator',
        role: 'orchestrator',
        status: 'idle',
        roomId: 'war-room',
        position: { x: 4, y: 4 }
      },
      'architect-1': {
        id: 'architect-1',
        name: 'Winston',
        role: 'agent',
        status: 'working',
        roomId: 'design-room',
        position: { x: 8, y: 4 },
        parentId: 'orch-1',
        lastTool: 'memory'
      },
      'dev-1': {
        id: 'dev-1',
        name: 'Amelia',
        role: 'agent',
        status: 'working',
        roomId: 'build-room',
        position: { x: 8, y: 8 },
        parentId: 'orch-1',
        lastTool: 'create_file'
      },
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
        status: 'done',
        assigneeId: 'dev-1'
      },
      'task-security': {
        id: 'task-security',
        title: 'Security review',
        status: 'review',
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
        sequenceId: 15,
        timestamp: '2026-04-08T00:00:15.000Z',
        agentId: 'dev-1'
      },
      {
        tool: 'memory',
        params: {
          edge: 'qa->architect',
          strength_before: 0.78,
          strength_after: 0.82,
          reason: 'cross-validation'
        },
        sourceEventType: 'graph_update',
        traceId: 'session-002',
        sequenceId: 17,
        timestamp: '2026-04-08T00:00:17.000Z',
        agentId: 'orch-1'
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
        sequenceId: 10,
        timestamp: '2026-04-08T00:00:10.000Z',
        agentId: 'orch-1'
      },
      {
        step: 'Decision recorded',
        detail: 'auth: JWT RS256 stateless',
        sourceEventType: 'decision',
        traceId: 'session-001',
        taskId: 'task-auth',
        metadata: { topic: 'auth', choice: 'JWT RS256 stateless' },
        sequenceId: 11,
        timestamp: '2026-04-08T00:00:11.000Z',
        agentId: 'architect-1'
      },
      {
        step: 'Implementation finished',
        detail: 'JWT middleware ready for review',
        sourceEventType: 'decision',
        traceId: 'session-001',
        taskId: 'task-auth',
        metadata: { topic: 'auth' },
        sequenceId: 12,
        timestamp: '2026-04-08T00:00:12.000Z',
        agentId: 'dev-1'
      },
      {
        step: 'Routing dispatch',
        detail: 'Intent routed: Security review',
        sourceEventType: 'routing',
        traceId: 'session-002',
        taskId: 'task-security',
        metadata: { intent: 'Security review' },
        sequenceId: 13,
        timestamp: '2026-04-08T00:00:13.000Z',
        agentId: 'orch-1'
      },
      {
        step: 'Decision recorded',
        detail: 'security: review in progress',
        sourceEventType: 'decision',
        traceId: 'session-002',
        taskId: 'task-security',
        metadata: { topic: 'security' },
        sequenceId: 14,
        timestamp: '2026-04-08T00:00:14.000Z',
        agentId: 'qa-1'
      }
    ],
    lastErrors: []
  };
}

describe('createCollaborationView', () => {
  it('derives hierarchy, shared trace, task handoff and graph update edges', () => {
    const view = createCollaborationView(createBaseState());

    expect(view.metrics).toEqual({
      nodeCount: 4,
      edgeCount: 11,
      hierarchyEdgeCount: 3,
      sharedTraceEdgeCount: 4,
      taskHandoffEdgeCount: 3,
      graphUpdateEdgeCount: 1,
      hotspotCount: 4
    });

    const graphEdge = view.edges.find((edge) => edge.relation === 'graph_update');
    expect(graphEdge).toMatchObject({
      fromAgentId: 'qa-1',
      toAgentId: 'architect-1',
      directed: true,
      traceIds: ['session-002'],
      strengthDelta: 0.039999999999999925
    });

    const sharedTraceEdge = view.edges.find(
      (edge) => edge.relation === 'shared_trace' && edge.fromAgentId === 'architect-1' && edge.toAgentId === 'dev-1'
    );
    expect(sharedTraceEdge).toMatchObject({
      traceIds: ['session-001'],
      weight: 1
    });

    const handoffEdge = view.edges.find(
      (edge) => edge.relation === 'task_handoff' && edge.fromAgentId === 'architect-1' && edge.toAgentId === 'dev-1'
    );
    expect(handoffEdge).toMatchObject({
      taskIds: ['task-auth'],
      traceIds: ['session-001']
    });
  });

  it('builds collaboration nodes with task, trace and hierarchy context', () => {
    const view = createCollaborationView(createBaseState());
    const orchestrator = view.nodes.find((node) => node.id === 'orch-1');
    const architect = view.nodes.find((node) => node.id === 'architect-1');

    expect(orchestrator).toMatchObject({
      childAgentIds: ['architect-1', 'dev-1', 'qa-1'],
      traceIds: ['session-001', 'session-002'],
      collaborationCount: 8
    });
    expect(architect).toMatchObject({
      parentId: 'orch-1',
      taskIds: [],
      traceIds: ['session-001', 'session-002']
    });
  });

  it('computes hotspots ordered by collaboration density', () => {
    const view = createCollaborationView(createBaseState());

    expect(view.hotspots[0]).toMatchObject({
      agentId: 'orch-1',
      collaborationCount: 8,
      traceCount: 2,
      hierarchyCount: 3
    });
    expect(view.hotspots.find((hotspot) => hotspot.agentId === 'qa-1')).toMatchObject({
      graphUpdateCount: 1,
      handoffCount: 1,
      collaborationCount: 4
    });
  });
});