import type { CollaborationView } from './collaboration-view';
import {
  createRuntimeDashboardUiView,
  type RuntimeDashboardUiTone,
  type RuntimeDashboardUiView,
  type RuntimeDashboardUiViewOptions
} from './runtime-dashboard-ui-view';
import type { RuntimeDashboardView } from './runtime-dashboard-view';

export interface RuntimeObserverRoom {
  id: string;
  label: string;
  tone: RuntimeDashboardUiTone;
  focus: boolean;
  agentIds: readonly string[];
  taskIds: readonly string[];
  nodeIds: readonly string[];
  alertCount: number;
}

export interface RuntimeObserverEntity {
  id: string;
  kind: 'node' | 'agent' | 'task';
  roomId: string;
  label: string;
  tone: RuntimeDashboardUiTone;
  badges: readonly string[];
  agentId: string | null;
  taskId: string | null;
  nodeId: string | null;
}

export interface RuntimeObserverHandoffArc {
  id: string;
  fromRoomId: string;
  toRoomId: string;
  relation: CollaborationView['edges'][number]['relation'];
  label: string;
  taskId: string | null;
  traceIds: readonly string[];
  tone: RuntimeDashboardUiTone;
}

export interface RuntimeObserverParity {
  runId: string | null;
  focusTaskId: string | null;
  focusNodeId: string | null;
  cockpitTaskCount: number;
  observerTaskCount: number;
  cockpitAttentionCount: number;
  observerAttentionCount: number;
  sameTaskCount: boolean;
  sameAttentionCount: boolean;
  sameFocus: boolean;
}

export interface RuntimeObserverView {
  protocolVersion: string;
  lastSequenceId: number;
  focus: RuntimeDashboardUiView['focus'];
  rooms: readonly RuntimeObserverRoom[];
  entities: readonly RuntimeObserverEntity[];
  handoffs: readonly RuntimeObserverHandoffArc[];
  warRoomAttention: readonly RuntimeDashboardUiView['attention'][number][];
  parity: RuntimeObserverParity;
  ui: RuntimeDashboardUiView;
}

export function createRuntimeObserverView(
  dashboard: RuntimeDashboardView,
  collaboration: CollaborationView,
  options: RuntimeDashboardUiViewOptions = {}
): RuntimeObserverView {
  const ui = createRuntimeDashboardUiView(dashboard, options);
  const roomNodeIds = indexNodeIdsByRoom(dashboard, ui);
  const rooms = dashboard.board.rooms.map((room) => {
    const alertCount = countRoomAlerts(room.id, dashboard, ui);

    return {
      id: room.id,
      label: room.id,
      tone: toneForRoom(alertCount, ui.focus),
      focus: room.taskIds.includes(ui.focus.taskId ?? '') || room.agentIds.includes(ui.focus.agentId ?? ''),
      agentIds: [...room.agentIds],
      taskIds: [...room.taskIds],
      nodeIds: roomNodeIds.get(room.id) ?? [],
      alertCount
    };
  });
  const roomIds = new Set(rooms.map((room) => room.id));
  const entities = [
    ...createNodeEntities(ui, roomNodeIds),
    ...createAgentEntities(dashboard),
    ...createTaskEntities(dashboard, ui)
  ].filter((entity) => roomIds.has(entity.roomId));
  const handoffs = collaboration.edges
    .filter((edge) => edge.relation === 'task_handoff' || edge.relation === 'shared_trace')
    .map((edge) => createHandoffArc(edge, dashboard))
    .filter((edge): edge is RuntimeObserverHandoffArc => edge !== null);

  return {
    protocolVersion: dashboard.protocolVersion,
    lastSequenceId: dashboard.lastSequenceId,
    focus: ui.focus,
    rooms,
    entities,
    handoffs,
    warRoomAttention: ui.attention,
    parity: {
      runId: ui.focus.runId,
      focusTaskId: ui.focus.taskId,
      focusNodeId: ui.focus.nodeId,
      cockpitTaskCount: dashboard.board.metrics.taskCount,
      observerTaskCount: entities.filter((entity) => entity.kind === 'task').length,
      cockpitAttentionCount: ui.attention.length,
      observerAttentionCount: ui.attention.length,
      sameTaskCount: dashboard.board.metrics.taskCount === entities.filter((entity) => entity.kind === 'task').length,
      sameAttentionCount: true,
      sameFocus: true
    },
    ui
  };
}

function createNodeEntities(
  ui: RuntimeDashboardUiView,
  roomNodeIds: Map<string, string[]>
): RuntimeObserverEntity[] {
  return ui.fleet.map((node) => ({
    id: `node:${node.nodeId}`,
    kind: 'node',
    roomId: findRoomIdForNode(roomNodeIds, node.nodeId) ?? 'war-room',
    label: node.nodeId,
    tone: node.tone,
    badges: [node.status, `${node.activeLeaseCount} lease(s)`],
    agentId: null,
    taskId: null,
    nodeId: node.nodeId
  }));
}

function createAgentEntities(dashboard: RuntimeDashboardView): RuntimeObserverEntity[] {
  return dashboard.board.agents.map((agent) => ({
    id: `agent:${agent.id}`,
    kind: 'agent',
    roomId: agent.roomId,
    label: agent.name,
    tone: agent.status === 'working' ? 'positive' : agent.status === 'offline' ? 'critical' : 'neutral',
    badges: [agent.status, `${agent.activeTaskCount} active task(s)`],
    agentId: agent.id,
    taskId: null,
    nodeId: null
  }));
}

function createTaskEntities(dashboard: RuntimeDashboardView, ui: RuntimeDashboardUiView): RuntimeObserverEntity[] {
  const ownershipByTaskId = new Map(ui.ownership.map((ownership) => [ownership.taskId, ownership]));

  return dashboard.board.taskColumns.flatMap((column) =>
    column.tasks.map((task) => {
      const assigneeRoomId =
        task.assigneeId === undefined ? 'war-room' : (dashboard.board.agents.find((agent) => agent.id === task.assigneeId)?.roomId ?? 'war-room');
      const ownership = ownershipByTaskId.get(task.id);

      return {
        id: `task:${task.id}`,
        kind: 'task' as const,
        roomId: assigneeRoomId,
        label: task.title,
        tone: ownership?.tone ?? (task.status === 'review' ? 'warning' : task.status === 'in_progress' ? 'positive' : 'neutral'),
        badges: [task.status, ...(ownership === undefined ? [] : [ownership.ownershipStatus])],
        agentId: task.assigneeId ?? null,
        taskId: task.id,
        nodeId: ownership?.nodeId ?? null
      };
    })
  );
}

function createHandoffArc(
  edge: CollaborationView['edges'][number],
  dashboard: RuntimeDashboardView
): RuntimeObserverHandoffArc | null {
  const fromAgent = dashboard.board.agents.find((agent) => agent.id === edge.fromAgentId);
  const toAgent = dashboard.board.agents.find((agent) => agent.id === edge.toAgentId);

  if (fromAgent === undefined || toAgent === undefined) {
    return null;
  }

  return {
    id: edge.id,
    fromRoomId: fromAgent.roomId,
    toRoomId: toAgent.roomId,
    relation: edge.relation,
    label: edge.label,
    taskId: edge.taskIds[0] ?? null,
    traceIds: [...edge.traceIds],
    tone: edge.relation === 'task_handoff' ? 'warning' : 'neutral'
  };
}

function indexNodeIdsByRoom(dashboard: RuntimeDashboardView, ui: RuntimeDashboardUiView): Map<string, string[]> {
  const nodeIdsByRoom = new Map<string, string[]>();
  const roomByTaskId = new Map(
    dashboard.board.taskColumns.flatMap((column) =>
      column.tasks.map((task) => {
        const roomId =
          task.assigneeId === undefined
            ? 'war-room'
            : (dashboard.board.agents.find((agent) => agent.id === task.assigneeId)?.roomId ?? 'war-room');
        return [task.id, roomId] as const;
      })
    )
  );

  for (const ownership of ui.ownership) {
    const roomId = roomByTaskId.get(ownership.taskId) ?? 'war-room';
    const current = nodeIdsByRoom.get(roomId) ?? [];
    if (!current.includes(ownership.nodeId)) {
      current.push(ownership.nodeId);
    }
    nodeIdsByRoom.set(roomId, current);
  }

  return nodeIdsByRoom;
}

function findRoomIdForNode(roomNodeIds: Map<string, string[]>, nodeId: string): string | null {
  for (const [roomId, nodeIds] of roomNodeIds.entries()) {
    if (nodeIds.includes(nodeId)) {
      return roomId;
    }
  }

  return null;
}

function countRoomAlerts(roomId: string, dashboard: RuntimeDashboardView, ui: RuntimeDashboardUiView): number {
  const roomTaskIds = new Set(
    dashboard.board.rooms.find((room) => room.id === roomId)?.taskIds ?? []
  );
  const roomAgentIds = new Set(
    dashboard.board.rooms.find((room) => room.id === roomId)?.agentIds ?? []
  );

  return ui.attention.filter(
    (item) =>
      (item.taskId !== null && roomTaskIds.has(item.taskId)) ||
      (item.context !== null && [...roomAgentIds].some((agentId) => item.context?.includes(agentId)))
  ).length;
}

function toneForRoom(alertCount: number, focus: RuntimeDashboardUiView['focus']): RuntimeDashboardUiTone {
  if (alertCount > 0) {
    return 'warning';
  }

  if (focus.taskId !== null || focus.nodeId !== null || focus.agentId !== null) {
    return 'positive';
  }

  return 'neutral';
}