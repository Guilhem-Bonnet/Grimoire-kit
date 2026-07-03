import { MUTATION_SURFACES, type AgentRole, type MutationSurface, type MutationTrustLevel } from '../../contracts/events';
import type { AuthContext } from './rbac';
import { isReadOnlyRole } from './rbac';

export interface TokenIssueRequest {
  principalId: string;
  role: AgentRole;
  expiresAt?: string;
  trustLevel?: MutationTrustLevel;
  authorizedMutationSurfaces?: readonly MutationSurface[];
}

export interface IssuedAuthToken {
  token: string;
  principalId: string;
  role: AgentRole;
  issuedAt: string;
  expiresAt?: string;
  readOnly: boolean;
  trustLevel: MutationTrustLevel;
  authorizedMutationSurfaces: readonly MutationSurface[];
}

export interface AuthTokenAuditEntry {
  type: 'ISSUED' | 'AUTHENTICATED' | 'REJECTED' | 'REVOKED';
  at: string;
  token?: string;
  principalId?: string;
  role?: AgentRole;
  reason?: string;
}

export class AuthenticationError extends Error {
  readonly code = 'UNAUTHENTICATED';

  constructor(message: string) {
    super(message);
    this.name = 'AuthenticationError';
  }
}

export class LocalAuthTokenRegistry {
  private nextTokenIndex = 1;
  private readonly issuedTokens = new Map<string, IssuedAuthToken>();
  private readonly auditLog: AuthTokenAuditEntry[] = [];

  issueToken(request: TokenIssueRequest): IssuedAuthToken {
    const token = `grimoire_local_${request.role}_${this.nextTokenIndex}`;
    this.nextTokenIndex += 1;
    const readOnly = isReadOnlyRole(request.role);
    const trustLevel = request.trustLevel ?? (request.role === 'orchestrator' ? 'trusted' : 'restricted');
    const authorizedMutationSurfaces = readOnly
      ? []
      : [...(request.authorizedMutationSurfaces ?? (request.role === 'orchestrator' ? MUTATION_SURFACES : []))];

    const issued: IssuedAuthToken = {
      token,
      principalId: request.principalId,
      role: request.role,
      issuedAt: new Date().toISOString(),
      ...(request.expiresAt === undefined ? {} : { expiresAt: request.expiresAt }),
      readOnly,
      trustLevel,
      authorizedMutationSurfaces
    };

    this.issuedTokens.set(token, issued);
    this.recordAudit({
      type: 'ISSUED',
      at: issued.issuedAt,
      token,
      principalId: issued.principalId,
      role: issued.role
    });

    return issued;
  }

  authenticate(token: string | null | undefined): AuthContext {
    const normalizedToken = token?.trim();

    if (!normalizedToken) {
      return this.reject(undefined, 'Missing token.');
    }

    const issued = this.issuedTokens.get(normalizedToken);

    if (issued === undefined) {
      return this.reject(normalizedToken, 'Unknown token.');
    }

    if (issued.expiresAt !== undefined && Date.parse(issued.expiresAt) <= Date.now()) {
      return this.reject(normalizedToken, 'Expired token.', issued.principalId, issued.role);
    }

    this.recordAudit({
      type: 'AUTHENTICATED',
      at: new Date().toISOString(),
      token: normalizedToken,
      principalId: issued.principalId,
      role: issued.role
    });

    return {
      principalId: issued.principalId,
      role: issued.role,
      tokenId: issued.token,
      trustLevel: issued.trustLevel,
      authorizedMutationSurfaces: [...issued.authorizedMutationSurfaces]
    };
  }

  revoke(token: string): boolean {
    const issued = this.issuedTokens.get(token);
    if (issued === undefined) {
      return false;
    }

    this.issuedTokens.delete(token);
    this.recordAudit({
      type: 'REVOKED',
      at: new Date().toISOString(),
      token,
      principalId: issued.principalId,
      role: issued.role
    });
    return true;
  }

  getAuditLog(): readonly AuthTokenAuditEntry[] {
    return this.auditLog;
  }

  private reject(
    token: string | undefined,
    reason: string,
    principalId?: string,
    role?: AgentRole
  ): never {
    this.recordAudit({
      type: 'REJECTED',
      at: new Date().toISOString(),
      ...(token === undefined ? {} : { token }),
      ...(principalId === undefined ? {} : { principalId }),
      ...(role === undefined ? {} : { role }),
      reason
    });
    throw new AuthenticationError(reason);
  }

  private recordAudit(entry: AuthTokenAuditEntry): void {
    this.auditLog.push(entry);
  }
}