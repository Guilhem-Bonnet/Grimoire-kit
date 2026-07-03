import {
  VS_CODE_PANEL_COMMANDS,
  type VsCodePanelCommandType,
  type VsCodePanelTransportMode
} from '../bridge/vscode-webview-bridge';

import {
  createRuntimeDashboardUiView,
  type RuntimeDashboardUiAttentionItem,
  type RuntimeDashboardUiHeader,
  type RuntimeDashboardUiHostCard,
  type RuntimeDashboardUiStatCard,
  type RuntimeDashboardUiTaskLane,
  type RuntimeDashboardUiVerificationLane,
  type RuntimeDashboardUiView,
  type RuntimeDashboardUiViewOptions
} from './runtime-dashboard-ui-view';
import type { RuntimeDashboardView } from './runtime-dashboard-view';

export interface VsCodePanelConnectionState {
  transport: VsCodePanelTransportMode;
  degraded: boolean;
  reason: string | null;
  supportedCommands: readonly VsCodePanelCommandType[];
}

export interface VsCodePanelCommand {
  commandId: VsCodePanelCommandType;
  label: string;
  detail: string;
  enabled: boolean;
  traceId: string | null;
  taskId: string | null;
  verificationRef: string | null;
}

export interface VsCodePanelView {
  protocolVersion: string;
  lastSequenceId: number;
  header: RuntimeDashboardUiHeader;
  focus: RuntimeDashboardUiView['focus'];
  connection: VsCodePanelConnectionState;
  statCards: readonly RuntimeDashboardUiStatCard[];
  attention: readonly RuntimeDashboardUiAttentionItem[];
  taskLanes: readonly RuntimeDashboardUiTaskLane[];
  verificationLanes: readonly RuntimeDashboardUiVerificationLane[];
  hosts: readonly RuntimeDashboardUiHostCard[];
  commands: readonly VsCodePanelCommand[];
  ui: RuntimeDashboardUiView;
}

export interface VsCodePanelViewOptions extends RuntimeDashboardUiViewOptions {
  transport?: VsCodePanelTransportMode;
  supportedCommands?: readonly VsCodePanelCommandType[];
}

export function createVsCodePanelView(
  dashboard: RuntimeDashboardView,
  options: VsCodePanelViewOptions = {}
): VsCodePanelView {
  const transport = options.transport ?? 'browser-fallback';
  const ui = createRuntimeDashboardUiView(dashboard, {
    maxTasksPerLane: options.maxTasksPerLane ?? 3,
    maxAttentionItems: options.maxAttentionItems ?? 6,
    maxVerificationItemsPerLane: options.maxVerificationItemsPerLane ?? 3,
    maxEvidencePacks: options.maxEvidencePacks ?? 2,
    maxTimelinePoints: options.maxTimelinePoints ?? 6
  });
  const supportedCommands = [...(options.supportedCommands ?? VS_CODE_PANEL_COMMANDS)];
  const verificationRef =
    ui.verificationQueue.flatMap((lane) => lane.items).find((item) => item.verificationRef !== null)?.verificationRef ??
    ui.evidencePacks.find((pack) => pack.verificationRef !== null)?.verificationRef ??
    null;
  const taskLanes = ui.lanes.filter((lane) => lane.count > 0).slice(0, 3);
  const verificationLanes = ui.verificationQueue.filter((lane) => lane.count > 0).slice(0, 3);
  const hosts = ui.hosts.slice(0, 3);

  return {
    protocolVersion: dashboard.protocolVersion,
    lastSequenceId: dashboard.lastSequenceId,
    header: {
      title: 'VS Code webview panel',
      subtitle:
        transport === 'vscode-webview'
          ? 'Le panel lit les memes read models que le cockpit web et remonte uniquement des commandes read-only.'
          : 'Preview navigateur du panel VS Code: la causalite est la meme, mais les commandes restent locales hors host.',
      tone: transport === 'vscode-webview' ? ui.header.tone : 'warning'
    },
    focus: ui.focus,
    connection: {
      transport,
      degraded: transport !== 'vscode-webview',
      reason:
        transport === 'vscode-webview'
          ? null
          : 'API VS Code indisponible. Le panel reste en preview navigateur et n envoie aucune commande au host.',
      supportedCommands
    },
    statCards: ui.statCards.slice(0, 6),
    attention: ui.attention.slice(0, 6),
    taskLanes,
    verificationLanes,
    hosts,
    commands: createVsCodePanelCommands(ui, supportedCommands, verificationRef),
    ui
  };
}

function createVsCodePanelCommands(
  ui: RuntimeDashboardUiView,
  supportedCommands: readonly VsCodePanelCommandType[],
  verificationRef: string | null
): VsCodePanelCommand[] {
  return [
    {
      commandId: 'focus.trace',
      label: 'Reveler la trace',
      detail: ui.focus.traceId ?? 'Aucune trace en focus pour ce scenario.',
      enabled: supportedCommands.includes('focus.trace') && ui.focus.traceId !== null,
      traceId: ui.focus.traceId,
      taskId: null,
      verificationRef: null
    },
    {
      commandId: 'focus.task',
      label: 'Reveler la tache',
      detail: ui.focus.taskTitle ?? ui.focus.taskId ?? 'Aucune tache en focus pour ce scenario.',
      enabled: supportedCommands.includes('focus.task') && ui.focus.taskId !== null,
      traceId: null,
      taskId: ui.focus.taskId,
      verificationRef: null
    },
    {
      commandId: 'open.verification',
      label: 'Ouvrir la verification',
      detail: verificationRef ?? 'Aucune verification exploitable n est disponible.',
      enabled: supportedCommands.includes('open.verification') && verificationRef !== null,
      traceId: null,
      taskId: null,
      verificationRef
    },
    {
      commandId: 'sync',
      label: 'Rafraichir le panel',
      detail: 'Demande un resync cote host sans ouvrir de write path.',
      enabled: supportedCommands.includes('sync'),
      traceId: null,
      taskId: null,
      verificationRef: null
    }
  ];
}