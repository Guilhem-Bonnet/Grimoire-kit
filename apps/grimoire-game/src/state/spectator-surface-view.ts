import type { AuthContext, AuthorizationAuditEntry } from '../server/auth/rbac';
import { isReadOnlyRole, previewClientEventAccess } from '../server/auth/rbac';
import type {
  CommandGatewayAuditEntry,
  CommandGatewayCommandType
} from '../server/control-plane/command-gateway';
import { previewCommandGatewayAccess } from '../server/control-plane/command-gateway';

import {
  createRuntimeDashboardUiView,
  type RuntimeDashboardUiTone,
  type RuntimeDashboardUiView,
  type RuntimeDashboardUiViewOptions
} from './runtime-dashboard-ui-view';
import type { RuntimeDashboardView } from './runtime-dashboard-view';

export type SpectatorSurfaceChannelId = 'web' | 'vscode';
export type SpectatorSurfaceCapabilitySource = 'client_event' | 'command';

export interface SpectatorSurfaceBanner {
  title: string;
  detail: string;
  tone: RuntimeDashboardUiTone;
  principalId: string;
  role: AuthContext['role'];
  tokenId: string | null;
  readOnly: boolean;
}

export interface SpectatorSurfaceChannel {
  channel: SpectatorSurfaceChannelId;
  readOnly: boolean;
  reconnectable: boolean;
  focusNavigation: boolean;
  diagnostics: readonly string[];
  writeSurfaceCount: number;
}

export interface SpectatorSurfaceCapability {
  capabilityId: string;
  source: SpectatorSurfaceCapabilitySource;
  label: string;
  mutation: boolean;
  allowed: boolean;
  reason: string | null;
}

export interface SpectatorSurfaceAuditItem {
  auditId: string;
  source: 'auth' | 'command';
  code: 'FORBIDDEN';
  actionId: string;
  reason: string;
  at: string;
  principalId: string | null;
  tokenId: string | null;
}

export interface SpectatorSurfaceView {
  protocolVersion: string;
  lastSequenceId: number;
  banner: SpectatorSurfaceBanner;
  channels: readonly SpectatorSurfaceChannel[];
  capabilities: readonly SpectatorSurfaceCapability[];
  blockedMutations: readonly SpectatorSurfaceCapability[];
  auditTrail: readonly SpectatorSurfaceAuditItem[];
  ui: RuntimeDashboardUiView;
}

export interface SpectatorSurfaceViewOptions {
  ui?: RuntimeDashboardUiViewOptions;
  authorizationAudit?: readonly AuthorizationAuditEntry[];
  commandAudit?: readonly CommandGatewayAuditEntry[];
}

export function createSpectatorSurfaceView(
  dashboard: RuntimeDashboardView,
  auth: AuthContext,
  options: SpectatorSurfaceViewOptions = {}
): SpectatorSurfaceView {
  const ui = createRuntimeDashboardUiView(dashboard, options.ui ?? {});
  const readOnly = isReadOnlyRole(auth.role);
  const capabilities = [
    ...previewClientEventAccess(auth).map<SpectatorSurfaceCapability>((capability) => ({
      capabilityId: `client:${capability.eventType}`,
      source: 'client_event',
      label: capability.eventType,
      mutation: capability.mutation,
      allowed: capability.allowed,
      reason: capability.reason
    })),
    ...previewCommandGatewayAccess(auth).map<SpectatorSurfaceCapability>((capability) => ({
      capabilityId: `command:${capability.commandType}`,
      source: 'command',
      label: capability.commandType,
      mutation: capability.mutation,
      allowed: capability.allowed,
      reason: capability.reason
    }))
  ];
  const blockedMutations = capabilities.filter((capability) => capability.mutation && !capability.allowed);
  const focusNavigationEnabled = capabilities.some(
    (capability) => capability.source === 'command' && capability.label === 'focus.set_local' && capability.allowed
  );
  const auditTrail = createSpectatorSurfaceAuditTrail(auth, options.authorizationAudit ?? [], options.commandAudit ?? []);
  const diagnostics = createChannelDiagnostics(ui);

  return {
    protocolVersion: dashboard.protocolVersion,
    lastSequenceId: dashboard.lastSequenceId,
    banner: {
      title: readOnly ? 'Read-only spectator surface' : 'Runtime surface',
      detail: readOnly
        ? 'Web and VS Code stay synchronized in read-only mode. Focus navigation is allowed; runtime writes stay blocked.'
        : 'Runtime surface accepts mutations according to the active role policy.',
      tone: readOnly ? 'warning' : 'positive',
      principalId: auth.principalId,
      role: auth.role,
      tokenId: auth.tokenId ?? null,
      readOnly
    },
    channels: [
      {
        channel: 'web',
        readOnly,
        reconnectable: true,
        focusNavigation: focusNavigationEnabled,
        diagnostics: diagnostics.web,
        writeSurfaceCount: readOnly ? 0 : blockedMutations.length
      },
      {
        channel: 'vscode',
        readOnly,
        reconnectable: true,
        focusNavigation: focusNavigationEnabled,
        diagnostics: diagnostics.vscode,
        writeSurfaceCount: readOnly ? 0 : blockedMutations.length
      }
    ],
    capabilities,
    blockedMutations,
    auditTrail,
    ui
  };
}

function createChannelDiagnostics(ui: RuntimeDashboardUiView): {
  web: string[];
  vscode: string[];
} {
  return {
    web: [
      `${ui.attention.length} attention item(s)`,
      `${ui.timeline.length} timeline point(s)`,
      `${ui.evidencePacks.length} evidence pack card(s)`
    ],
    vscode: [
      `${ui.hosts.length} host card(s)`,
      `${ui.focus.traceId === null ? 'No trace focus' : `Trace focus ${ui.focus.traceId}`}`,
      `${ui.attention.length} runtime diagnostic item(s)`
    ]
  };
}

function createSpectatorSurfaceAuditTrail(
  auth: AuthContext,
  authorizationAudit: readonly AuthorizationAuditEntry[],
  commandAudit: readonly CommandGatewayAuditEntry[]
): SpectatorSurfaceAuditItem[] {
  const authEntries = authorizationAudit
    .filter(
      (entry) =>
        !entry.allowed &&
        entry.principalId === auth.principalId &&
        entry.role === auth.role &&
        (auth.tokenId === undefined || entry.tokenId === undefined || entry.tokenId === auth.tokenId)
    )
    .map<SpectatorSurfaceAuditItem>((entry, index) => ({
      auditId: `auth:${entry.eventType}:${index}`,
      source: 'auth',
      code: 'FORBIDDEN',
      actionId: entry.eventType,
      reason: entry.reason ?? 'Forbidden.',
      at: entry.at,
      principalId: entry.principalId,
      tokenId: entry.tokenId ?? null
    }));
  const commandEntries = commandAudit
    .filter((entry) => !entry.allowed && entry.principalId === auth.principalId && entry.role === auth.role)
    .map<SpectatorSurfaceAuditItem>((entry) => ({
      auditId: entry.auditId,
      source: 'command',
      code: 'FORBIDDEN',
      actionId: entry.commandType,
      reason: entry.reason ?? 'Forbidden.',
      at: entry.at,
      principalId: entry.principalId,
      tokenId: auth.tokenId ?? null
    }));

  return [...authEntries, ...commandEntries].sort(compareSpectatorSurfaceAuditItems);
}

function compareSpectatorSurfaceAuditItems(
  left: SpectatorSurfaceAuditItem,
  right: SpectatorSurfaceAuditItem
): number {
  if (left.at !== right.at) {
    return right.at.localeCompare(left.at);
  }

  return left.actionId.localeCompare(right.actionId);
}