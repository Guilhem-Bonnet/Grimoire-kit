import { z } from 'zod';

export const VS_CODE_PANEL_PROTOCOL_VERSION = 'v1';
export const VS_CODE_PANEL_TRANSPORT_MODES = ['vscode-webview', 'browser-fallback'] as const;
export const VS_CODE_PANEL_COMMANDS = ['focus.trace', 'focus.task', 'open.verification', 'sync'] as const;
export const VS_CODE_PANEL_MODES = [
  'cockpit',
  'mission-board',
  'kernel',
  'proofs',
  'game-ui',
  'observability',
  'spectator',
  'observer',
  'workflow',
  'expert',
  'observatory',
  'war-room',
  'host-bridge',
  'vscode'
] as const;
export const VS_CODE_PANEL_FILTERS = ['all', 'attention', 'blocked'] as const;

const NonEmptyStringSchema = z.string().min(1);

export const VsCodePanelTransportModeSchema = z.enum(VS_CODE_PANEL_TRANSPORT_MODES);
export const VsCodePanelCommandTypeSchema = z.enum(VS_CODE_PANEL_COMMANDS);
export const VsCodePanelModeSchema = z.enum(VS_CODE_PANEL_MODES);
export const VsCodePanelFilterSchema = z.enum(VS_CODE_PANEL_FILTERS);
export const VsCodePanelPersistedStateSchema = z
  .object({
    scenarioId: NonEmptyStringSchema,
    filter: VsCodePanelFilterSchema,
    mode: VsCodePanelModeSchema
  })
  .strict();

const FocusTraceCommandSchema = z
  .object({
    command: z.literal('focus.trace'),
    traceId: NonEmptyStringSchema
  })
  .strict();

const FocusTaskCommandSchema = z
  .object({
    command: z.literal('focus.task'),
    taskId: NonEmptyStringSchema
  })
  .strict();

const OpenVerificationCommandSchema = z
  .object({
    command: z.literal('open.verification'),
    verificationRef: NonEmptyStringSchema
  })
  .strict();

const SyncCommandSchema = z
  .object({
    command: z.literal('sync')
  })
  .strict();

export const VsCodePanelCommandPayloadSchema = z.discriminatedUnion('command', [
  FocusTraceCommandSchema,
  FocusTaskCommandSchema,
  OpenVerificationCommandSchema,
  SyncCommandSchema
]);

export const VsCodePanelReadyMessageSchema = z
  .object({
    type: z.literal('grimoire.vscode-panel.ready'),
    protocolVersion: z.literal(VS_CODE_PANEL_PROTOCOL_VERSION),
    transport: VsCodePanelTransportModeSchema,
    state: VsCodePanelPersistedStateSchema
  })
  .strict();

export const VsCodePanelCommandMessageSchema = z
  .object({
    type: z.literal('grimoire.vscode-panel.command'),
    protocolVersion: z.literal(VS_CODE_PANEL_PROTOCOL_VERSION),
    payload: VsCodePanelCommandPayloadSchema
  })
  .strict();

export const VsCodePanelOutboundMessageSchema = z.union([
  VsCodePanelReadyMessageSchema,
  VsCodePanelCommandMessageSchema
]);

export type VsCodePanelTransportMode = z.infer<typeof VsCodePanelTransportModeSchema>;
export type VsCodePanelCommandType = z.infer<typeof VsCodePanelCommandTypeSchema>;
export type VsCodePanelMode = z.infer<typeof VsCodePanelModeSchema>;
export type VsCodePanelFilter = z.infer<typeof VsCodePanelFilterSchema>;
export type VsCodePanelPersistedState = z.infer<typeof VsCodePanelPersistedStateSchema>;
export type VsCodePanelCommandPayload = z.infer<typeof VsCodePanelCommandPayloadSchema>;
export type VsCodePanelReadyMessage = z.infer<typeof VsCodePanelReadyMessageSchema>;
export type VsCodePanelCommandMessage = z.infer<typeof VsCodePanelCommandMessageSchema>;
export type VsCodePanelOutboundMessage = z.infer<typeof VsCodePanelOutboundMessageSchema>;

export interface VsCodeWebviewApiLike {
  postMessage(message: unknown): unknown;
  getState?(): unknown;
  setState?(state: unknown): unknown;
}

export interface VsCodePanelBridge {
  transport: VsCodePanelTransportMode;
  degraded: boolean;
  postReady(state: VsCodePanelPersistedState): boolean;
  postCommand(payload: VsCodePanelCommandPayload): boolean;
  persistState(state: VsCodePanelPersistedState): void;
  restoreState(): VsCodePanelPersistedState | null;
}

export function createVsCodePanelReadyMessage(
  state: VsCodePanelPersistedState,
  transport: VsCodePanelTransportMode
): VsCodePanelReadyMessage {
  return VsCodePanelReadyMessageSchema.parse({
    type: 'grimoire.vscode-panel.ready',
    protocolVersion: VS_CODE_PANEL_PROTOCOL_VERSION,
    transport,
    state
  });
}

export function createVsCodePanelCommandMessage(
  payload: VsCodePanelCommandPayload
): VsCodePanelCommandMessage {
  return VsCodePanelCommandMessageSchema.parse({
    type: 'grimoire.vscode-panel.command',
    protocolVersion: VS_CODE_PANEL_PROTOCOL_VERSION,
    payload
  });
}

export function createVsCodePanelBridge(
  api: VsCodeWebviewApiLike | null = resolveVsCodeWebviewApi()
): VsCodePanelBridge {
  const transport: VsCodePanelTransportMode = api === null ? 'browser-fallback' : 'vscode-webview';

  return {
    transport,
    degraded: api === null,
    postReady(state) {
      if (api === null) {
        return false;
      }

      api.postMessage(createVsCodePanelReadyMessage(state, transport));
      return true;
    },
    postCommand(payload) {
      if (api === null) {
        return false;
      }

      api.postMessage(createVsCodePanelCommandMessage(payload));
      return true;
    },
    persistState(state) {
      if (api?.setState === undefined) {
        return;
      }

      api.setState(VsCodePanelPersistedStateSchema.parse(state));
    },
    restoreState() {
      if (api?.getState === undefined) {
        return null;
      }

      const parsedState = VsCodePanelPersistedStateSchema.safeParse(api.getState());
      return parsedState.success ? parsedState.data : null;
    }
  };
}

function resolveVsCodeWebviewApi(): VsCodeWebviewApiLike | null {
  const scopedGlobal = globalThis as typeof globalThis & {
    acquireVsCodeApi?: () => VsCodeWebviewApiLike;
  };

  return typeof scopedGlobal.acquireVsCodeApi === 'function' ? scopedGlobal.acquireVsCodeApi() : null;
}