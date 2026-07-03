import type { JsonValue } from '../../src/contracts/events';
import type { AuthContext } from '../../src/server/auth/rbac';
import type { GameState, WorkflowStepLogEntry } from '../../src/state/game-state';
import { createCoverageSlotsView } from '../../src/state/coverage-slots-view';

function createBaseState(
  config: Record<string, JsonValue> = {},
  recentWorkflowSteps: readonly WorkflowStepLogEntry[] = []
): GameState {
  return {
    protocolVersion: 'v1',
    lastSequenceId: 40,
    hydratedAt: '2026-04-12T00:00:00.000Z',
    agents: {
      'orch-1': {
        id: 'orch-1',
        name: 'Orchestrator',
        role: 'orchestrator',
        status: 'idle',
        roomId: 'war-room',
        position: { x: 4, y: 4 }
      },
      'dev-1': {
        id: 'dev-1',
        name: 'Amelia',
        role: 'agent',
        status: 'working',
        roomId: 'team-tech',
        position: { x: 8, y: 8 },
        parentId: 'orch-1',
        lastTool: 'runTests'
      },
      'qa-1': {
        id: 'qa-1',
        name: 'Quinn',
        role: 'agent',
        status: 'working',
        roomId: 'team-qa',
        position: { x: 12, y: 8 },
        parentId: 'orch-1',
        lastTool: 'semantic_search'
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
        title: 'Audit auth',
        status: 'in_progress',
        assigneeId: 'qa-1'
      }
    },
    config,
    recentToolCalls: [
      {
        tool: 'runTests',
        params: { task_id: 'task-auth', path: 'src/auth.ts' },
        sourceEventType: 'test_run',
        traceId: 'trace-auth',
        sequenceId: 18,
        timestamp: '2026-04-12T00:00:18.000Z',
        agentId: 'dev-1'
      }
    ],
    recentWorkflowSteps,
    lastErrors: []
  };
}

describe('coverage slots view', () => {
  it('covers map editor history, team rooms, agent runtime cards and desk floating labels', () => {
    const view = createCoverageSlotsView(
      createBaseState(
        {
          coverageSlots: {
            mapEditor: {
              width: 3,
              height: 3,
              desks: {
                'desk-a': {
                  position: { x: 0, y: 0 },
                  teamId: 'team-tech'
                }
              }
            },
            roomThemes: {
              'team-tech': 'screens-and-code',
              'war-room': 'control-room'
            },
            roomAccess: {
              'team-tech': ['team-tech', 'orchestrator'],
              'war-room': ['orchestrator']
            },
            agentDeskAssignments: {
              'dev-1': 'desk-a'
            },
            deskDirectoryMap: {
              'desk-a': '/workspace/src/server'
            }
          },
          agents: {
            'dev-1': {
              model: 'gpt-5.4',
              systemPrompt: 'Implement auth middleware',
              tokenBudget: 1000,
              tokenUsed: 250
            }
          }
        },
        [
          {
            step: 'Move desk',
            detail: 'desk-a -> 1,0',
            sourceEventType: 'map_editor',
            metadata: {
              mapAction: 'move_desk',
              deskId: 'desk-a',
              position: { x: 1, y: 0 }
            },
            sequenceId: 20,
            timestamp: '2026-04-12T00:00:20.000Z'
          },
          {
            step: 'Undo desk move',
            detail: 'undo',
            sourceEventType: 'map_editor',
            metadata: {
              mapAction: 'undo',
              deskId: 'desk-a'
            },
            sequenceId: 21,
            timestamp: '2026-04-12T00:00:21.000Z'
          },
          {
            step: 'Redo desk move',
            detail: 'redo',
            sourceEventType: 'map_editor',
            metadata: {
              mapAction: 'redo',
              deskId: 'desk-a'
            },
            sequenceId: 22,
            timestamp: '2026-04-12T00:00:22.000Z'
          },
          {
            step: 'Invalid desk placement',
            detail: 'desk-b outside grid',
            sourceEventType: 'map_editor',
            metadata: {
              mapAction: 'place_desk',
              deskId: 'desk-b',
              position: { x: 3, y: 0 }
            },
            sequenceId: 23,
            timestamp: '2026-04-12T00:00:23.000Z'
          }
        ]
      )
    );

    expect(view.mapEditor).toMatchObject({
      width: 3,
      height: 3,
      readOnly: false,
      canUndo: true,
      canRedo: false
    });
    expect(view.mapEditor.desks).toEqual([
      {
        deskId: 'desk-a',
        position: { x: 1, y: 0 },
        teamId: 'team-tech',
        directory: '/workspace/src/server',
        floatingLabel: '[dir] server',
        assignedAgentIds: ['dev-1']
      }
    ]);
    expect(view.mapEditor.issues).toMatchObject([
      {
        code: 'grid_bounds',
        deskId: 'desk-b'
      }
    ]);
    expect(view.teamRooms.find((room) => room.roomId === 'team-tech')).toMatchObject({
      decorationTheme: 'screens-and-code',
      visitableBy: ['team-tech', 'orchestrator'],
      occupantAgentIds: ['dev-1']
    });
    expect(view.agentStates.find((agent) => agent.agentId === 'orch-1')).toMatchObject({
      badge: 'ORCH',
      detailPanelAvailable: true
    });
    expect(view.agentStates.find((agent) => agent.agentId === 'dev-1')).toMatchObject({
      parentId: 'orch-1',
      model: 'gpt-5.4',
      activeTool: 'runTests',
      detailPanelAvailable: true,
      availableActions: ['pause', 'chat_direct', 'redirect', 'restart']
    });
    expect(view.slotMatrix.filter((slot) => ['F01', 'F02', 'F03', 'F21'].includes(slot.slotId))).toMatchObject([
      { slotId: 'F01', covered: true },
      { slotId: 'F02', covered: true },
      { slotId: 'F03', covered: true },
      { slotId: 'F21', covered: true }
    ]);
  });

  it('blocks map mutations for read-only spectator sharing and exposes one-click token sharing', () => {
    const auth: AuthContext = {
      principalId: 'spectator-1',
      role: 'spectator',
      tokenId: 'share-123'
    };
    const view = createCoverageSlotsView(
      createBaseState(
        {
          coverageSlots: {
            mapEditor: {
              width: 2,
              height: 2
            },
            spectator: {
              shareBaseUrl: 'https://grimoire.local/share',
              oneClickCopy: true
            }
          }
        },
        [
          {
            step: 'Spectator place blocked',
            detail: 'desk-spectator',
            sourceEventType: 'map_editor',
            metadata: {
              mapAction: 'place_desk',
              deskId: 'desk-spectator',
              position: { x: 0, y: 0 }
            },
            sequenceId: 20,
            timestamp: '2026-04-12T00:00:20.000Z'
          },
          {
            step: 'Spectator move blocked',
            detail: 'desk-spectator',
            sourceEventType: 'map_editor',
            metadata: {
              mapAction: 'move_desk',
              deskId: 'desk-spectator',
              position: { x: 1, y: 0 }
            },
            sequenceId: 21,
            timestamp: '2026-04-12T00:00:21.000Z'
          }
        ]
      ),
      { auth }
    );

    expect(view.spectator).toEqual({
      readOnly: true,
      tokenId: 'share-123',
      shareUrl: 'https://grimoire.local/share/share-123',
      oneClickCopy: true,
      blockedMutationCount: 2
    });
    expect(view.mapEditor.desks).toEqual([]);
    expect(view.mapEditor.issues.map((issue) => issue.code)).toEqual(['read_only', 'read_only']);
    expect(view.slotMatrix.find((slot) => slot.slotId === 'F19')).toMatchObject({
      covered: true
    });
  });

  it('persists desk to directory bindings without ambiguity and flags duplicate directories', () => {
    const initialView = createCoverageSlotsView(
      createBaseState({
        coverageSlots: {
          deskDirectoryMap: [
            { deskId: 'desk-a', directory: '/tmp/grimoire-desk-a' },
            { deskId: 'desk-b', directory: '/tmp/grimoire-desk-b' }
          ],
          agentDeskAssignments: {
            'dev-1': 'desk-a',
            'qa-1': 'desk-b'
          }
        }
      })
    );
    const restartedView = createCoverageSlotsView(
      createBaseState({
        coverageSlots: {
          deskDirectoryMap: initialView.deskDirectoryMap.persistenceState,
          agentDeskAssignments: {
            'dev-1': 'desk-a',
            'qa-1': 'desk-b'
          }
        }
      })
    );
    const ambiguousView = createCoverageSlotsView(
      createBaseState({
        coverageSlots: {
          deskDirectoryMap: [
            { deskId: 'desk-a', directory: '/tmp/grimoire-shared' },
            { deskId: 'desk-b', directory: '/tmp/grimoire-shared' }
          ]
        }
      })
    );

    expect(restartedView.deskDirectoryMap).toMatchObject({
      ambiguousBindingCount: 0,
      persistenceState: {
        'desk-a': '/tmp/grimoire-desk-a',
        'desk-b': '/tmp/grimoire-desk-b'
      }
    });
    expect(restartedView.deskDirectoryMap.bindings).toEqual([
      {
        deskId: 'desk-a',
        directory: '/tmp/grimoire-desk-a',
        floatingLabel: '[dir] grimoire-desk-a',
        assignedAgentIds: ['dev-1']
      },
      {
        deskId: 'desk-b',
        directory: '/tmp/grimoire-desk-b',
        floatingLabel: '[dir] grimoire-desk-b',
        assignedAgentIds: ['qa-1']
      }
    ]);
    expect(ambiguousView.deskDirectoryMap.issues).toMatchObject([
      {
        code: 'ambiguous_directory',
        deskId: 'desk-b',
        directory: '/tmp/grimoire-shared'
      }
    ]);
  });

  it('tracks a full worktree room lifecycle and blocks invalid transitions after closure', () => {
    const view = createCoverageSlotsView(
      createBaseState(
        {},
        [
          {
            step: 'Create worktree room',
            detail: 'feature-auth',
            sourceEventType: 'worktree_room_transition',
            metadata: {
              worktreeRoomId: 'feature-auth',
              worktreeAction: 'create',
              directory: '/tmp/worktree-auth',
              branch: 'feature/auth',
              wallActions: ['merge', 'pr', 'discard', 'keep']
            },
            sequenceId: 20,
            timestamp: '2026-04-12T00:00:20.000Z'
          },
          {
            step: 'Activate worktree room',
            detail: 'feature-auth',
            sourceEventType: 'worktree_room_transition',
            metadata: {
              worktreeRoomId: 'feature-auth',
              worktreeAction: 'activate',
              directory: '/tmp/worktree-auth',
              branch: 'feature/auth'
            },
            sequenceId: 21,
            timestamp: '2026-04-12T00:00:21.000Z'
          },
          {
            step: 'Archive worktree room',
            detail: 'feature-auth',
            sourceEventType: 'worktree_room_transition',
            metadata: {
              worktreeRoomId: 'feature-auth',
              worktreeAction: 'archive',
              directory: '/tmp/worktree-auth',
              branch: 'feature/auth',
              wallActions: ['merge', 'pr', 'discard', 'keep']
            },
            sequenceId: 22,
            timestamp: '2026-04-12T00:00:22.000Z'
          },
          {
            step: 'Close worktree room',
            detail: 'feature-auth',
            sourceEventType: 'worktree_room_transition',
            metadata: {
              worktreeRoomId: 'feature-auth',
              worktreeAction: 'close',
              directory: '/tmp/worktree-auth',
              branch: 'feature/auth'
            },
            sequenceId: 23,
            timestamp: '2026-04-12T00:00:23.000Z'
          },
          {
            step: 'Invalid reopen',
            detail: 'feature-auth',
            sourceEventType: 'worktree_room_transition',
            metadata: {
              worktreeRoomId: 'feature-auth',
              worktreeAction: 'activate',
              directory: '/tmp/worktree-auth',
              branch: 'feature/auth'
            },
            sequenceId: 24,
            timestamp: '2026-04-12T00:00:24.000Z'
          }
        ]
      )
    );

    expect(view.worktreeRooms).toHaveLength(1);
    expect(view.worktreeRooms[0]).toMatchObject({
      roomId: 'feature-auth',
      directory: '/tmp/worktree-auth',
      branch: 'feature/auth',
      status: 'closed',
      wallActions: ['merge', 'pr', 'discard', 'keep']
    });
    expect(view.worktreeRooms[0]?.transitionHistory.map((transition) => [transition.toStatus, transition.valid])).toEqual([
      ['created', true],
      ['active', true],
      ['archived', true],
      ['closed', true],
      ['active', false]
    ]);
    expect(view.worktreeRooms[0]?.issues).toMatchObject([
      {
        code: 'invalid_transition',
        roomId: 'feature-auth'
      }
    ]);
    expect(view.summary.invalidTransitionCount).toBe(1);
    expect(view.slotMatrix.find((slot) => slot.slotId === 'F22')).toMatchObject({
      covered: true
    });
  });
});