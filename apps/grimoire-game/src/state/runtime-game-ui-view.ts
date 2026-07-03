import type { BoardAlert, BoardDecisionCard } from './board-view';
import type { SecurityKanbanCard } from './branch-finisher-view';
import {
  createRuntimeDashboardUiView,
  type RuntimeDashboardUiAttentionItem,
  type RuntimeDashboardUiFocus,
  type RuntimeDashboardUiStatCard,
  type RuntimeDashboardUiTaskLane,
  type RuntimeDashboardUiTone,
  type RuntimeDashboardUiVerificationLane,
  type RuntimeDashboardUiView,
  type RuntimeDashboardUiViewOptions
} from './runtime-dashboard-ui-view';
import type { RuntimeDashboardView } from './runtime-dashboard-view';

export interface RuntimeGameUiHeader {
  title: string;
  subtitle: string;
  tone: RuntimeDashboardUiTone;
  summary: string;
}

export interface RuntimeGameUiRoomCard {
  roomId: string;
  leadAgentId: string | null;
  leadAgentName: string | null;
  tone: RuntimeDashboardUiTone;
  focus: boolean;
  agentCount: number;
  activeTaskCount: number;
  nodeCount: number;
  alertCount: number;
  workingCount: number;
  pausedCount: number;
  idleCount: number;
  offlineCount: number;
}

export interface RuntimeGameUiAgentCard {
  agentId: string;
  name: string;
  role: RuntimeDashboardView['board']['agents'][number]['role'];
  status: RuntimeDashboardView['board']['agents'][number]['status'];
  roomId: string;
  tone: RuntimeDashboardUiTone;
  activeTaskCount: number;
  childAgentCount: number;
  lastTool: string | null;
}

export interface RuntimeGameUiAlertCard {
  code: BoardAlert['code'];
  level: BoardAlert['level'];
  tone: RuntimeDashboardUiTone;
  message: string;
  roomId: string | null;
  taskId: string | null;
  agentId: string | null;
  sequenceId: number | null;
}

export interface RuntimeGameUiDecisionCard {
  id: string;
  title: string;
  taskId: string | null;
  taskTitle: string | null;
  roomId: string | null;
  tone: RuntimeDashboardUiTone;
  missingFieldCount: number;
  evidenceCount: number;
  detail: string;
}

export interface RuntimeGameUiSecurityCard {
  id: string;
  findingId: string;
  title: string;
  severity: SecurityKanbanCard['severity'];
  tone: RuntimeDashboardUiTone;
  surfaceId: string;
  taskId: string | null;
  blocksShip: boolean;
  detail: string;
}

export interface RuntimeGameUiView {
  protocolVersion: string;
  lastSequenceId: number;
  header: RuntimeGameUiHeader;
  focus: RuntimeDashboardUiFocus;
  statCards: readonly RuntimeDashboardUiStatCard[];
  rooms: readonly RuntimeGameUiRoomCard[];
  agents: readonly RuntimeGameUiAgentCard[];
  taskLanes: readonly RuntimeDashboardUiTaskLane[];
  verificationLanes: readonly RuntimeDashboardUiVerificationLane[];
  alerts: readonly RuntimeGameUiAlertCard[];
  attention: readonly RuntimeDashboardUiAttentionItem[];
  decisionCards: readonly RuntimeGameUiDecisionCard[];
  securityCards: readonly RuntimeGameUiSecurityCard[];
  ui: RuntimeDashboardUiView;
}

export function createRuntimeGameUiView(
  dashboard: RuntimeDashboardView,
  options: RuntimeDashboardUiViewOptions = {}
): RuntimeGameUiView {
  const ui = createRuntimeDashboardUiView(dashboard, options);
  const roomIdsByTaskId = indexRoomIdsByTaskId(dashboard);
  const nodeIdsByRoomId = indexNodeIdsByRoomId(ui, roomIdsByTaskId);
  const hotRoomCount = dashboard.board.rooms.filter(
    (room) => room.activeTaskCount > 0 || room.workingCount > 0 || countRoomAlerts(room, dashboard.board.alerts) > 0
  ).length;
  const threatCount =
    dashboard.board.alerts.length +
    dashboard.board.securityCards.filter((card) => card.blocksShip || card.severity === 'critical' || card.severity === 'high').length;

  return {
    protocolVersion: dashboard.protocolVersion,
    lastSequenceId: dashboard.lastSequenceId,
    header: {
      title: 'Command Table',
      subtitle: 'Tactical projection of rooms, agents, mission lanes and guardrails, read directly from the same GameState.',
      tone: ui.header.tone,
      summary: `${hotRoomCount} hot sector(s), ${dashboard.board.metrics.activeTaskCount} live mission(s), ${threatCount} threat(s).`
    },
    focus: ui.focus,
    statCards: ui.statCards.slice(0, 6),
    rooms: dashboard.board.rooms.map((room) => {
      const alertCount = countRoomAlerts(room, dashboard.board.alerts);

      return {
        roomId: room.id,
        leadAgentId: room.leadAgentId,
        leadAgentName: room.leadAgentId === null ? null : (dashboard.board.agents.find((agent) => agent.id === room.leadAgentId)?.name ?? null),
        tone: toneForRoom(room, alertCount, ui.focus),
        focus: room.taskIds.includes(ui.focus.taskId ?? '') || room.agentIds.includes(ui.focus.agentId ?? ''),
        agentCount: room.agentCount,
        activeTaskCount: room.activeTaskCount,
        nodeCount: nodeIdsByRoomId.get(room.id)?.length ?? 0,
        alertCount,
        workingCount: room.workingCount,
        pausedCount: room.pausedCount,
        idleCount: room.idleCount,
        offlineCount: room.offlineCount
      };
    }),
    agents: dashboard.board.agents.map((agent) => ({
      agentId: agent.id,
      name: agent.name,
      role: agent.role,
      status: agent.status,
      roomId: agent.roomId,
      tone: toneForAgentStatus(agent.status),
      activeTaskCount: agent.activeTaskCount,
      childAgentCount: agent.childAgentIds.length,
      lastTool: agent.lastTool
    })),
    taskLanes: ui.lanes.filter((lane) => lane.count > 0),
    verificationLanes: ui.verificationQueue.filter((lane) => lane.count > 0),
    alerts: dashboard.board.alerts.map((alert) => ({
      code: alert.code,
      level: alert.level,
      tone: toneForAlertLevel(alert.level),
      message: alert.message,
      roomId: alert.roomId ?? null,
      taskId: alert.taskId ?? null,
      agentId: alert.agentId ?? null,
      sequenceId: alert.sequenceId ?? null
    })),
    attention: ui.attention,
    decisionCards: dashboard.board.decisionCards.map((card) => ({
      id: card.id,
      title: card.title,
      taskId: card.taskId,
      taskTitle: card.taskTitle,
      roomId: card.roomId,
      tone: card.missingFields.length > 0 ? 'warning' : 'positive',
      missingFieldCount: card.missingFields.length,
      evidenceCount: card.evidence.length + card.supportingToolCalls.length,
      detail: card.detail
    })),
    securityCards: dashboard.board.securityCards.map((card) => ({
      id: card.id,
      findingId: card.findingId,
      title: card.title,
      severity: card.severity,
      tone: toneForSecurityCard(card),
      surfaceId: card.surfaceId,
      taskId: card.taskId,
      blocksShip: card.blocksShip,
      detail: card.detail
    })),
    ui
  };
}

function indexRoomIdsByTaskId(dashboard: RuntimeDashboardView): Map<string, string> {
  const roomIdsByTaskId = new Map<string, string>();

  for (const room of dashboard.board.rooms) {
    for (const taskId of room.taskIds) {
      roomIdsByTaskId.set(taskId, room.id);
    }
  }

  return roomIdsByTaskId;
}

function indexNodeIdsByRoomId(
  ui: RuntimeDashboardUiView,
  roomIdsByTaskId: Map<string, string>
): Map<string, string[]> {
  const nodeIdsByRoomId = new Map<string, Set<string>>();

  for (const ownership of ui.ownership) {
    const roomId = roomIdsByTaskId.get(ownership.taskId);
    if (roomId === undefined) {
      continue;
    }

    const nodeIds = nodeIdsByRoomId.get(roomId) ?? new Set<string>();
    nodeIds.add(ownership.nodeId);
    nodeIdsByRoomId.set(roomId, nodeIds);
  }

  return new Map(
    Array.from(nodeIdsByRoomId.entries()).map(([roomId, nodeIds]) => [roomId, [...nodeIds].sort((left, right) => left.localeCompare(right))])
  );
}

function countRoomAlerts(
  room: RuntimeDashboardView['board']['rooms'][number],
  alerts: readonly BoardAlert[]
): number {
  const roomAgentIds = new Set(room.agentIds);
  const roomTaskIds = new Set(room.taskIds);

  return alerts.filter(
    (alert) =>
      alert.roomId === room.id ||
      (alert.agentId !== undefined && roomAgentIds.has(alert.agentId)) ||
      (alert.taskId !== undefined && roomTaskIds.has(alert.taskId))
  ).length;
}

function toneForRoom(
  room: RuntimeDashboardView['board']['rooms'][number],
  alertCount: number,
  focus: RuntimeDashboardUiFocus
): RuntimeDashboardUiTone {
  if (alertCount > 0 || room.offlineCount > 0) {
    return 'warning';
  }

  if (room.taskIds.includes(focus.taskId ?? '') || room.agentIds.includes(focus.agentId ?? '')) {
    return 'positive';
  }

  if (room.activeTaskCount > 0 || room.workingCount > 0) {
    return 'positive';
  }

  return 'neutral';
}

function toneForAgentStatus(status: RuntimeDashboardView['board']['agents'][number]['status']): RuntimeDashboardUiTone {
  switch (status) {
    case 'working':
      return 'positive';
    case 'paused':
      return 'warning';
    case 'offline':
      return 'critical';
    default:
      return 'neutral';
  }
}

function toneForAlertLevel(level: BoardAlert['level']): RuntimeDashboardUiTone {
  switch (level) {
    case 'error':
      return 'critical';
    case 'warning':
      return 'warning';
    default:
      return 'neutral';
  }
}

function toneForSecurityCard(card: SecurityKanbanCard): RuntimeDashboardUiTone {
  if (card.blocksShip || card.severity === 'critical' || card.severity === 'high') {
    return 'critical';
  }

  if (card.severity === 'medium') {
    return 'warning';
  }

  return 'neutral';
}