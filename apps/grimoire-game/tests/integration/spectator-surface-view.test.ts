import { MockAgentAdapter } from '../../src/bridge/agent-adapter';
import { RuntimeDashboardSession } from '../../src/bridge/runtime-dashboard-session';
import {
  createConfigUpdate,
  createTaskTransition,
  RUNTIME_PROTOCOL_VERSION,
  type GameStateSnapshot
} from '../../src/contracts/events';
import {
  authorizeClientEvent,
  createAuthorizationAuditEntry,
  type AuthContext
} from '../../src/server/auth/rbac';
import { LocalAuthTokenRegistry } from '../../src/server/auth/token-registry';
import { CommandGateway } from '../../src/server/control-plane/command-gateway';
import { createSpectatorSurfaceView } from '../../src/state/spectator-surface-view';

const ORCHESTRATOR_AUTH: AuthContext = {
  principalId: 'orch-1',
  role: 'orchestrator'
};

function createInitialSnapshot(): GameStateSnapshot {
  return {
    protocolVersion: RUNTIME_PROTOCOL_VERSION,
    generatedAt: '2026-04-12T11:00:00.000Z',
    lastSequenceId: 0,
    agents: [
      {
        id: 'orch-1',
        name: 'Orchestrator',
        role: 'orchestrator',
        status: 'idle',
        roomId: 'war-room',
        position: { x: 4, y: 4 }
      },
      {
        id: 'dev-1',
        name: 'Amelia',
        role: 'agent',
        status: 'working',
        roomId: 'build-room',
        position: { x: 8, y: 8 },
        parentId: 'orch-1',
        lastTool: 'create_file'
      }
    ],
    tasks: [
      {
        id: 'task-auth',
        title: 'Implement auth',
        status: 'todo',
        assigneeId: 'dev-1'
      }
    ],
    config: {},
    recentToolCalls: [],
    recentWorkflowSteps: []
  };
}

describe('spectator-surface-view', () => {
  it('projects a read-only web and VS Code surface with explicit mutation denials', async () => {
    const registry = new LocalAuthTokenRegistry();
    const issued = registry.issueToken({ principalId: 'spectator-1', role: 'spectator' });
    const spectatorAuth = registry.authenticate(issued.token);
    const adapter = new MockAgentAdapter(createInitialSnapshot());
    const session = new RuntimeDashboardSession(adapter);
    const gateway = new CommandGateway();

    const bootstrap = await session.bootstrap(spectatorAuth);
    const forbiddenEvent = createConfigUpdate('req-spectator', 'hud.theme', 'paper', 'cfg-spectator');
    const authDecision = authorizeClientEvent(spectatorAuth, forbiddenEvent);
    const authAudit = createAuthorizationAuditEntry(
      spectatorAuth,
      forbiddenEvent,
      authDecision,
      '2026-04-12T11:00:05.000Z'
    );
    const deniedEvents = await adapter.handleClientEvent(forbiddenEvent, spectatorAuth);
    const commandResult = gateway.execute(
      {
        commandId: 'cmd-spectator-mutation',
        type: 'node.set_maintenance',
        idempotencyKey: 'node-maint-spectator',
        nodeId: 'node-alpha'
      },
      spectatorAuth
    );
    const view = createSpectatorSurfaceView(bootstrap.dashboard, spectatorAuth, {
      authorizationAudit: [authAudit],
      commandAudit: gateway.getAuditLog()
    });

    expect(deniedEvents).toHaveLength(1);
    expect(deniedEvents[0]?.type).toBe('ERROR');
    if (deniedEvents[0]?.type === 'ERROR') {
      expect(deniedEvents[0].code).toBe('FORBIDDEN');
      expect(deniedEvents[0].message).toContain('read-only');
      expect(deniedEvents[0].correlationId).toBe('req-spectator');
    }

    expect(commandResult).toMatchObject({
      allowed: false,
      reason: 'Role spectator cannot execute node.set_maintenance.'
    });
    expect(view.banner).toMatchObject({
      title: 'Read-only spectator surface',
      principalId: 'spectator-1',
      role: 'spectator',
      tokenId: issued.token,
      readOnly: true,
      tone: 'warning'
    });
    expect(view.channels).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          channel: 'web',
          readOnly: true,
          reconnectable: true,
          focusNavigation: true,
          writeSurfaceCount: 0
        }),
        expect.objectContaining({
          channel: 'vscode',
          readOnly: true,
          reconnectable: true,
          focusNavigation: true,
          writeSurfaceCount: 0
        })
      ])
    );
    expect(view.blockedMutations).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          source: 'client_event',
          label: 'CONFIG_UPDATE',
          allowed: false,
          reason: 'Spectator tokens are read-only and cannot mutate runtime state.'
        }),
        expect.objectContaining({
          source: 'command',
          label: 'node.set_maintenance',
          allowed: false,
          reason: 'Role spectator cannot execute node.set_maintenance.'
        })
      ])
    );
    expect(view.auditTrail).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          source: 'auth',
          actionId: 'CONFIG_UPDATE',
          code: 'FORBIDDEN',
          tokenId: issued.token
        }),
        expect.objectContaining({
          source: 'command',
          actionId: 'node.set_maintenance',
          code: 'FORBIDDEN',
          tokenId: issued.token
        })
      ])
    );
  });

  it('keeps the read-only spectator surface stable after reconnect and replay', async () => {
    const adapter = new MockAgentAdapter(createInitialSnapshot());
    const writerSession = new RuntimeDashboardSession(adapter);
    const registry = new LocalAuthTokenRegistry();
    const issued = registry.issueToken({ principalId: 'spectator-1', role: 'spectator' });
    const spectatorAuth = registry.authenticate(issued.token);

    await writerSession.bootstrap(ORCHESTRATOR_AUTH);
    await writerSession.dispatch(
      createTaskTransition('req-task-1', 'task-auth', 'in_progress', 'task-1'),
      ORCHESTRATOR_AUTH
    );

    const bootstrapSession = new RuntimeDashboardSession(adapter);
    const reconnectSession = new RuntimeDashboardSession(adapter);
    const bootstrap = await bootstrapSession.bootstrap(spectatorAuth);
    const reconnect = await reconnectSession.reconnect(0, spectatorAuth);
    const bootView = createSpectatorSurfaceView(bootstrap.dashboard, spectatorAuth);
    const reconnectView = createSpectatorSurfaceView(reconnect.dashboard, spectatorAuth);

    expect(reconnectView).toEqual(bootView);
    expect(reconnectView.ui.lanes.find((lane) => lane.status === 'in_progress')?.count).toBe(1);
    expect(reconnectView.channels.every((channel) => channel.reconnectable)).toBe(true);
    expect(reconnectView.channels.every((channel) => channel.writeSurfaceCount === 0)).toBe(true);
  });
});