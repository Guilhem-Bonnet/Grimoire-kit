import {
  createConfigUpdate,
  RUNTIME_PROTOCOL_VERSION,
  type GameStateSnapshot
} from '../../src/contracts/events';
import { MockAgentAdapter } from '../../src/bridge/agent-adapter';
import { hydrateGameState, type GameState } from '../../src/state/game-state';
import { createConfigurationSkillTreeView } from '../../src/state/configuration-skill-tree-view';
import { createRuntimeDashboardView } from '../../src/state/runtime-dashboard-view';

function createBaseState(): GameState {
  return {
    protocolVersion: 'v1',
    lastSequenceId: 20,
    hydratedAt: '2026-04-11T12:00:00.000Z',
    agents: {
      'orch-1': {
        id: 'orch-1',
        name: 'Orchestrator',
        role: 'orchestrator',
        status: 'idle',
        roomId: 'war-room',
        position: { x: 4, y: 4 }
      }
    },
    tasks: {
      'task-config': {
        id: 'task-config',
        title: 'Configure skill tree',
        status: 'in_progress',
        assigneeId: 'orch-1'
      }
    },
    config: {
      'skillTree.runtimeSnapshot': {
        mcp: {
          github: { enabled: true },
          memory: { enabled: false }
        },
        skills: {
          hostBridge: { enabled: true },
          verificationEvidencePack: { enabled: false }
        }
      },
      'skillTree.storageSnapshot': {
        mcp: {
          github: { enabled: true },
          memory: { enabled: false }
        },
        skills: {
          hostBridge: { enabled: true },
          verificationEvidencePack: { enabled: false }
        }
      },
      'skillTree.nodeGovernance': {
        'mcp.github': {
          origin: 'runtime_api',
          requiredPolicy: 'surface_scoped',
          trustStatus: 'trusted',
          riskClass: 'high'
        },
        'skill.hostBridge': {
          origin: 'runtime_adapter',
          requiredPolicy: 'elevated',
          trustStatus: 'trusted',
          riskClass: 'critical'
        }
      }
    },
    recentToolCalls: [],
    recentWorkflowSteps: [
      {
        step: 'Skill tree activation applied',
        detail: 'GitHub MCP enabled from board configuration room.',
        sourceEventType: 'config_update',
        traceId: 'config-001',
        taskId: 'task-config',
        metadata: {
          configurationSkillTreeNodeId: 'mcp.github',
          enabled: true,
          allowed: true,
          actorId: 'orch-1'
        },
        sequenceId: 20,
        timestamp: '2026-04-11T12:00:20.000Z',
        agentId: 'orch-1'
      }
    ],
    hostBindings: {},
    recentHostInvocationDecisions: [],
    recentHostContextEntries: [],
    recentHostReviews: [],
    lastErrors: []
  };
}

function createBaseSnapshot(): GameStateSnapshot {
  return {
    protocolVersion: RUNTIME_PROTOCOL_VERSION,
    generatedAt: '2026-04-11T12:10:00.000Z',
    lastSequenceId: 0,
    agents: [
      {
        id: 'orch-1',
        name: 'Orchestrator',
        role: 'orchestrator',
        status: 'idle',
        roomId: 'war-room',
        position: { x: 4, y: 4 }
      }
    ],
    tasks: [],
    config: {
      'skillTree.runtimeSnapshot': {
        mcp: {
          github: { enabled: false },
          memory: { enabled: false }
        },
        skills: {
          hostBridge: { enabled: false },
          verificationEvidencePack: { enabled: false }
        }
      },
      'skillTree.storageSnapshot': {
        mcp: {
          github: { enabled: false },
          memory: { enabled: false }
        },
        skills: {
          hostBridge: { enabled: false },
          verificationEvidencePack: { enabled: false }
        }
      }
    },
    recentToolCalls: [],
    recentWorkflowSteps: []
  };
}

describe('configuration skill tree view', () => {
  it('projects the bounded S9 configuration tree and exposes it through the runtime dashboard', () => {
    const state = createBaseState();

    const view = createConfigurationSkillTreeView(state);
    const dashboard = createRuntimeDashboardView(state);

    expect(view.summary).toEqual({
      nodeCount: 4,
      enabledRuntimeCount: 2,
      enabledStorageCount: 2,
      divergedCount: 0,
      invalidCount: 0,
      blockedCount: 0,
      rejectedMutationCount: 0
    });
    expect(view.reloadReady).toBe(true);
    expect(view.nodes.find((node) => node.nodeId === 'mcp.github')).toMatchObject({
      label: 'GitHub MCP',
      category: 'mcp',
      runtimeEnabled: true,
      storageEnabled: true,
      persistenceStatus: 'synced',
      origin: 'runtime_api',
      requiredPolicy: 'surface_scoped',
      trustStatus: 'trusted',
      riskClass: 'high',
      lastMutation: {
        allowed: true,
        actorId: 'orch-1'
      }
    });
    expect(dashboard.configurationSkillTree.nodes.find((node) => node.nodeId === 'skill.hostBridge')).toMatchObject({
      runtimeEnabled: true,
      storageEnabled: true,
      riskClass: 'critical'
    });
  });

  it('persists activation changes through config updates and restores the same observable state after reload', async () => {
    const adapter = new MockAgentAdapter(createBaseSnapshot());
    const auth = { principalId: 'orch-1', role: 'orchestrator' as const };
    const activatedSnapshot = {
      mcp: {
        github: { enabled: true },
        memory: { enabled: false }
      },
      skills: {
        hostBridge: { enabled: true },
        verificationEvidencePack: { enabled: false }
      }
    };

    await adapter.handleClientEvent(
      createConfigUpdate('req-skill-tree-runtime', 'skillTree.runtimeSnapshot', activatedSnapshot, 'cfg-skill-tree-runtime'),
      auth
    );
    const events = await adapter.handleClientEvent(
      createConfigUpdate('req-skill-tree-storage', 'skillTree.storageSnapshot', activatedSnapshot, 'cfg-skill-tree-storage'),
      auth
    );

    expect(events).toHaveLength(1);
    expect(events[0]?.type).toBe('STATE_SNAPSHOT');
    if (events[0]?.type !== 'STATE_SNAPSHOT') {
      return;
    }

    const reloadedState = hydrateGameState(events[0].snapshot, events[0].timestamp);
    const view = createConfigurationSkillTreeView(reloadedState);

    expect(view.reloadReady).toBe(true);
    expect(view.nodes.find((node) => node.nodeId === 'mcp.github')).toMatchObject({
      runtimeEnabled: true,
      storageEnabled: true,
      effectiveEnabled: true,
      persistenceStatus: 'synced'
    });
    expect(view.nodes.find((node) => node.nodeId === 'skill.hostBridge')).toMatchObject({
      runtimeEnabled: true,
      storageEnabled: true,
      effectiveEnabled: true,
      persistenceStatus: 'synced'
    });
  });

  it('detects invalid runtime snapshots, runtime/storage divergence and governance blocks with actionable diagnostics', () => {
    const state = createBaseState();
    state.config['skillTree.runtimeSnapshot'] = {
      mcp: {
        github: { enabled: 'yes' },
        memory: { enabled: false }
      },
      skills: {
        hostBridge: { enabled: true },
        verificationEvidencePack: { enabled: false }
      }
    };
    state.config['skillTree.storageSnapshot'] = {
      mcp: {
        github: { enabled: false },
        memory: { enabled: true }
      },
      skills: {
        hostBridge: { enabled: true },
        verificationEvidencePack: { enabled: false }
      }
    };
    state.config['skillTree.nodeGovernance'] = {
      'skill.hostBridge': {
        origin: 'runtime_adapter',
        requiredPolicy: 'elevated',
        trustStatus: 'blocked',
        riskClass: 'critical'
      }
    };
    state.recentWorkflowSteps = [
      ...state.recentWorkflowSteps,
      {
        step: 'Skill tree activation rejected',
        detail: 'GitHub MCP activation refused because the config payload is invalid.',
        sourceEventType: 'config_update',
        traceId: 'config-002',
        taskId: 'task-config',
        metadata: {
          configurationSkillTreeNodeId: 'mcp.github',
          enabled: true,
          allowed: false,
          actorId: 'orch-1',
          validationError: 'skillTree.runtimeSnapshot.mcp.github.enabled must be boolean.'
        },
        sequenceId: 21,
        timestamp: '2026-04-11T12:00:21.000Z',
        agentId: 'orch-1'
      }
    ];
    state.lastSequenceId = 21;

    const view = createConfigurationSkillTreeView(state);

    expect(view.reloadReady).toBe(false);
    expect(view.nodes.find((node) => node.nodeId === 'mcp.github')).toMatchObject({
      persistenceStatus: 'invalid',
      issueCodes: expect.arrayContaining([
        'CONFIGURATION_SKILL_TREE_INVALID_RUNTIME_SNAPSHOT',
        'CONFIGURATION_SKILL_TREE_MUTATION_REJECTED'
      ]),
      diagnostic: 'skillTree.runtimeSnapshot.mcp.github.enabled must be boolean.'
    });
    expect(view.nodes.find((node) => node.nodeId === 'mcp.memory')).toMatchObject({
      persistenceStatus: 'storage_only',
      issueCodes: expect.arrayContaining(['CONFIGURATION_SKILL_TREE_RUNTIME_STORAGE_DIVERGENCE'])
    });
    expect(view.nodes.find((node) => node.nodeId === 'skill.hostBridge')).toMatchObject({
      issueCodes: expect.arrayContaining(['CONFIGURATION_SKILL_TREE_GOVERNANCE_BLOCKED']),
      diagnostic: 'Host Bridge Session is enabled while trust status is blocked.'
    });
    expect(view.reloadBlockingReasons).toEqual(
      expect.arrayContaining([
        'skillTree.runtimeSnapshot.mcp.github.enabled must be boolean.',
        'Memory MCP diverges between runtime and storage snapshots.',
        'Host Bridge Session is enabled while trust status is blocked.'
      ])
    );
  });
});