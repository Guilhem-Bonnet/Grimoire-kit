import type { AgentRole } from '../../contracts/events';
import type { AuthContext } from '../auth/rbac';
import { LocalAuthTokenRegistry, type IssuedAuthToken } from '../auth/token-registry';

export const COMMAND_GATEWAY_COMMAND_TYPES = [
  'session.resync',
  'run.replay_delta',
  'lease.reclaim',
  'lease.release',
  'node.set_maintenance',
  'spectator.share',
  'focus.set_local'
] as const;

export type CommandGatewayCommandType = (typeof COMMAND_GATEWAY_COMMAND_TYPES)[number];
export type CommandGatewaySurface =
  | 'session_sync'
  | 'run_replay'
  | 'lease_ownership'
  | 'node_maintenance'
  | 'spectator_sharing'
  | 'focus_navigation';

export interface CommandGatewayFocus {
  runId?: string;
  traceId?: string;
  taskId?: string;
  nodeId?: string;
  agentId?: string;
}

export interface CommandGatewayCommand {
  commandId: string;
  type: CommandGatewayCommandType;
  requestId?: string;
  idempotencyKey?: string;
  projectId?: string;
  runId?: string;
  traceId?: string;
  taskId?: string;
  nodeId?: string;
  leaseId?: string;
  reason?: string;
  expiresAt?: string;
  leaseExpired?: boolean;
  ownershipResolved?: boolean;
  ownershipActive?: boolean;
  focus?: CommandGatewayFocus;
}

export interface CommandGatewayAuditEntry {
  auditId: string;
  at: string;
  commandId: string;
  commandType: CommandGatewayCommandType;
  surface: CommandGatewaySurface;
  principalId: string;
  role: AgentRole;
  allowed: boolean;
  mutation: boolean;
  replayed: boolean;
  reason?: string;
  idempotencyKey?: string;
}

export interface CommandGatewayResult {
  auditId: string;
  commandId: string;
  commandType: CommandGatewayCommandType;
  surface: CommandGatewaySurface;
  allowed: boolean;
  mutation: boolean;
  replayed: boolean;
  reason?: string;
  focus?: CommandGatewayFocus;
  issuedToken?: IssuedAuthToken;
}

export interface CommandGatewayOptions {
  tokenRegistry?: LocalAuthTokenRegistry;
}

export interface CommandGatewayAccessPreview {
  commandType: CommandGatewayCommandType;
  surface: CommandGatewaySurface;
  mutation: boolean;
  allowed: boolean;
  reason: string | null;
}

const COMMAND_ROLE_MATRIX: Record<CommandGatewayCommandType, readonly AgentRole[]> = {
  'session.resync': ['orchestrator', 'agent'],
  'run.replay_delta': ['orchestrator'],
  'lease.reclaim': ['orchestrator'],
  'lease.release': ['orchestrator'],
  'node.set_maintenance': ['orchestrator'],
  'spectator.share': ['orchestrator'],
  'focus.set_local': ['orchestrator', 'agent', 'spectator']
};

export function previewCommandGatewayAccess(auth: Pick<AuthContext, 'role'>): CommandGatewayAccessPreview[] {
  return COMMAND_GATEWAY_COMMAND_TYPES.map((commandType) => {
    const allowed = COMMAND_ROLE_MATRIX[commandType].includes(auth.role);

    return {
      commandType,
      surface: resolveCommandSurface(commandType),
      mutation: isMutationCommand(commandType),
      allowed,
      reason: allowed ? null : `Role ${auth.role} cannot execute ${commandType}.`
    };
  });
}

export class CommandGateway {
  private readonly tokenRegistry: LocalAuthTokenRegistry;
  private readonly auditLog: CommandGatewayAuditEntry[] = [];
  private readonly processedCommands = new Map<string, CommandGatewayResult>();
  private nextAuditIndex = 1;

  constructor(options: CommandGatewayOptions = {}) {
    this.tokenRegistry = options.tokenRegistry ?? new LocalAuthTokenRegistry();
  }

  execute(command: CommandGatewayCommand, auth: AuthContext): CommandGatewayResult {
    const surface = resolveCommandSurface(command.type);
    const mutation = isMutationCommand(command.type);

    if (!COMMAND_ROLE_MATRIX[command.type].includes(auth.role)) {
      return this.reject(command, auth, surface, mutation, `Role ${auth.role} cannot execute ${command.type}.`);
    }

    if (command.type !== 'focus.set_local' && command.idempotencyKey === undefined) {
      return this.reject(command, auth, surface, mutation, `Command ${command.type} requires an idempotencyKey.`);
    }

    const cacheKey =
      command.idempotencyKey === undefined ? undefined : `${command.type}:${command.idempotencyKey}`;
    if (cacheKey !== undefined) {
      const cached = this.processedCommands.get(cacheKey);
      if (cached !== undefined) {
        return this.recordResult({
          ...cached,
          auditId: this.allocateAuditId(),
          replayed: true
        }, auth, true);
      }
    }

    const validationError = validateCommand(command);
    if (validationError !== null) {
      return this.reject(command, auth, surface, mutation, validationError);
    }

    const result = this.fulfill(command, auth, surface, mutation);

    if (cacheKey !== undefined) {
      this.processedCommands.set(cacheKey, result);
    }

    return this.recordResult(result, auth, false);
  }

  getAuditLog(): readonly CommandGatewayAuditEntry[] {
    return this.auditLog;
  }

  getTokenRegistry(): LocalAuthTokenRegistry {
    return this.tokenRegistry;
  }

  private fulfill(
    command: CommandGatewayCommand,
    auth: AuthContext,
    surface: CommandGatewaySurface,
    mutation: boolean
  ): CommandGatewayResult {
    if (command.type === 'spectator.share') {
      const issuedToken = this.tokenRegistry.issueToken({
        principalId: `spectator:${auth.principalId}`,
        role: 'spectator',
        ...(command.expiresAt === undefined ? {} : { expiresAt: command.expiresAt })
      });

      return {
        auditId: this.allocateAuditId(),
        commandId: command.commandId,
        commandType: command.type,
        surface,
        allowed: true,
        mutation,
        replayed: false,
        issuedToken
      };
    }

    return {
      auditId: this.allocateAuditId(),
      commandId: command.commandId,
      commandType: command.type,
      surface,
      allowed: true,
      mutation,
      replayed: false,
      ...(command.focus === undefined ? {} : { focus: command.focus })
    };
  }

  private reject(
    command: CommandGatewayCommand,
    auth: AuthContext,
    surface: CommandGatewaySurface,
    mutation: boolean,
    reason: string
  ): CommandGatewayResult {
    return this.recordResult(
      {
        auditId: this.allocateAuditId(),
        commandId: command.commandId,
        commandType: command.type,
        surface,
        allowed: false,
        mutation,
        replayed: false,
        reason
      },
      auth,
      false
    );
  }

  private recordResult(
    result: CommandGatewayResult,
    auth: AuthContext,
    replayed: boolean
  ): CommandGatewayResult {
    if (result.commandType !== 'focus.set_local') {
      this.auditLog.push({
        auditId: result.auditId,
        at: new Date().toISOString(),
        commandId: result.commandId,
        commandType: result.commandType,
        surface: result.surface,
        principalId: auth.principalId,
        role: auth.role,
        allowed: result.allowed,
        mutation: result.mutation,
        replayed,
        ...(result.reason === undefined ? {} : { reason: result.reason })
      });
    }

    return {
      ...result,
      replayed
    };
  }

  private allocateAuditId(): string {
    const auditId = `cmd-audit-${this.nextAuditIndex}`;
    this.nextAuditIndex += 1;
    return auditId;
  }
}

function resolveCommandSurface(type: CommandGatewayCommandType): CommandGatewaySurface {
  switch (type) {
    case 'session.resync':
      return 'session_sync';
    case 'run.replay_delta':
      return 'run_replay';
    case 'lease.reclaim':
    case 'lease.release':
      return 'lease_ownership';
    case 'node.set_maintenance':
      return 'node_maintenance';
    case 'spectator.share':
      return 'spectator_sharing';
    case 'focus.set_local':
      return 'focus_navigation';
  }
}

function isMutationCommand(type: CommandGatewayCommandType): boolean {
  return type === 'lease.reclaim' || type === 'lease.release' || type === 'node.set_maintenance';
}

function validateCommand(command: CommandGatewayCommand): string | null {
  if (command.type === 'focus.set_local') {
    if (command.focus === undefined) {
      return 'Focus command requires at least one focus field.';
    }

    if (
      command.focus.runId === undefined &&
      command.focus.traceId === undefined &&
      command.focus.taskId === undefined &&
      command.focus.nodeId === undefined &&
      command.focus.agentId === undefined
    ) {
      return 'Focus command requires at least one focus field.';
    }

    return null;
  }

  if (command.type === 'session.resync') {
    return command.projectId !== undefined && command.runId !== undefined
      ? null
      : 'Session resync requires projectId and runId.';
  }

  if (command.type === 'run.replay_delta') {
    return command.runId !== undefined && command.traceId !== undefined
      ? null
      : 'Run replay requires runId and traceId.';
  }

  if (command.type === 'lease.reclaim') {
    if (command.leaseId === undefined || command.taskId === undefined) {
      return 'Lease reclaim requires leaseId and taskId.';
    }

    if (command.leaseExpired !== true) {
      return 'Lease reclaim requires an expired lease.';
    }

    if (command.ownershipResolved !== true) {
      return 'Lease reclaim requires resolved ownership metadata.';
    }

    return null;
  }

  if (command.type === 'lease.release') {
    if (command.leaseId === undefined) {
      return 'Lease release requires leaseId.';
    }

    if (command.ownershipActive !== true) {
      return 'Lease release requires an active ownership.';
    }

    return command.reason === undefined || command.reason.trim().length === 0
      ? 'Lease release requires an explicit justification.'
      : null;
  }

  if (command.type === 'node.set_maintenance') {
    return command.nodeId !== undefined ? null : 'Node maintenance requires nodeId.';
  }

  return null;
}