import { describe, expect, it, vi } from 'vitest';

import {
  buildMammouthHandoffRequest,
  invokeMammouthReviewImport,
  prepareMammouthReviewImport
} from '../../src/bridge/mammouth-host-adapter';
import { createHostBridgeView } from '../../src/state/host-bridge-view';
import { createHostHandoffView } from '../../src/state/host-handoff-view';
import type { GameState } from '../../src/state/game-state';

function createAdapterState(): GameState {
  return {
    protocolVersion: 'v1',
    lastSequenceId: 72,
    hydratedAt: '2026-04-11T12:00:00.000Z',
    agents: {
      'orch-1': {
        id: 'orch-1',
        name: 'Orchestrator',
        role: 'orchestrator',
        status: 'working',
        roomId: 'war-room',
        position: { x: 4, y: 4 }
      },
      'dev-1': {
        id: 'dev-1',
        name: 'Amelia',
        role: 'agent',
        status: 'working',
        roomId: 'build-room',
        position: { x: 8, y: 8 },
        parentId: 'orch-1',
        lastTool: 'create_file'
      }
    },
    tasks: {
      'task-auth': {
        id: 'task-auth',
        title: 'Ship auth handoff',
        status: 'review',
        assigneeId: 'dev-1'
      }
    },
    config: {},
    recentToolCalls: [],
    recentWorkflowSteps: [
      {
        step: 'Routing dispatch',
        detail: 'Intent routed: Ship auth handoff',
        sourceEventType: 'routing',
        traceId: 'trace-auth',
        taskId: 'task-auth',
        metadata: {
          intent: 'Ship auth handoff',
          missionPack: {
            objective: 'Prepare a governed handoff for external review',
            scope: ['src/auth.ts', 'tests/auth.test.ts'],
            canonicalSources: ['src/auth.ts', 'tests/auth.test.ts'],
            constraints: ['repo-first', 'proof-before-merge'],
            expectedOutput: 'review-ready patch',
            expectedProof: ['verify://task-auth/handoff', 'artifact://host-bridge/review-copilot-001'],
            mode: 'preview'
          }
        },
        sequenceId: 11,
        timestamp: '2026-04-11T12:00:11.000Z',
        agentId: 'orch-1'
      }
    ],
    hostBindings: {
      'host-copilot': {
        sequenceId: 40,
        timestamp: '2026-04-11T12:00:40.000Z',
        binding: {
          hostId: 'host-copilot',
          hostType: 'copilot',
          displayName: 'GitHub Copilot',
          authMode: 'oauth',
          connectionState: 'online',
          trustStatus: 'trusted',
          scopes: ['fs', 'network'],
          capabilityManifestRef: 'manifest://host-copilot',
          sourceOfTruth: 'secondary',
          lastSeenAt: '2026-04-11T12:00:39.000Z'
        },
        manifest: {
          manifestId: 'manifest://host-copilot',
          hostId: 'host-copilot',
          routines: ['review.pull_request'],
          toolProviders: ['github-mcp'],
          reviewChannels: ['copilot_review'],
          contextSources: ['review_summary', 'selection'],
          permissionMode: 'hybrid',
          supportsStreaming: true,
          supportsReviewImport: true,
          supportsContextImport: true,
          supportsPreviewCommit: true
        },
        reason: 'connected'
      },
      'host-mammouth': {
        sequenceId: 41,
        timestamp: '2026-04-11T12:00:41.000Z',
        binding: {
          hostId: 'host-mammouth',
          hostType: 'other',
          displayName: 'Mammouth AI',
          authMode: 'token',
          connectionState: 'online',
          trustStatus: 'review',
          scopes: ['network'],
          capabilityManifestRef: 'manifest://host-mammouth',
          sourceOfTruth: 'secondary',
          lastSeenAt: '2026-04-11T12:00:38.000Z'
        },
        manifest: {
          manifestId: 'manifest://host-mammouth',
          hostId: 'host-mammouth',
          routines: ['review.external'],
          toolProviders: ['mammouth-api'],
          reviewChannels: ['other'],
          contextSources: ['session_context', 'review_summary'],
          permissionMode: 'policy',
          supportsStreaming: true,
          supportsReviewImport: true,
          supportsContextImport: true,
          supportsPreviewCommit: false
        },
        reason: 'connected'
      }
    },
    recentHostReviews: [
      {
        sequenceId: 52,
        timestamp: '2026-04-11T12:00:52.000Z',
        review: {
          reviewId: 'review-copilot-001',
          hostId: 'host-copilot',
          sourceType: 'copilot_review',
          subjectRef: 'task:task-auth',
          verdict: 'warn',
          findings: [
            {
              id: 'finding-auth-proof',
              severity: 'medium',
              message: 'Expected proof references must be attached before commit.',
              resolutionStatus: 'open'
            }
          ],
          linkedEvidenceRefs: ['verify://task-auth/handoff', 'artifact://host-bridge/review-copilot-001'],
          importedAt: '2026-04-11T12:00:52.000Z',
          traceId: 'trace-auth',
          taskId: 'task-auth'
        },
        meta: {
          traceId: 'trace-auth',
          taskId: 'task-auth',
          correlationId: 'corr-auth',
          hostId: 'host-copilot'
        }
      }
    ],
    recentHostContextEntries: [
      {
        sequenceId: 53,
        timestamp: '2026-04-11T12:00:53.000Z',
        entry: {
          entryId: 'context://task-auth/mission-pack',
          hostId: 'host-copilot',
          sourceType: 'session_context',
          visibility: 'shared',
          confidence: 9,
          importedAt: '2026-04-11T12:00:53.000Z',
          ttlSeconds: 3600,
          contentRef: 'artifact://mission-pack/task-auth',
          trustStatus: 'trusted'
        },
        meta: {
          traceId: 'trace-auth',
          taskId: 'task-auth',
          correlationId: 'corr-auth',
          hostId: 'host-copilot'
        }
      }
    ],
    lastErrors: []
  };
}

function getPacketAndHost() {
  const state = createAdapterState();
  const packet = createHostHandoffView(state).packets.find((entry) => entry.taskId === 'task-auth');
  const host = createHostBridgeView(state).hosts.find((entry) => entry.hostId === 'host-mammouth');

  if (packet === undefined || host === undefined) {
    throw new Error('Expected the Mammouth host handoff fixtures to be available.');
  }

  return { packet, host };
}

describe('mammouth host adapter', () => {
  it('builds a governed Mammouth handoff request from the reusable host packet', () => {
    const { packet, host } = getPacketAndHost();
    const request = buildMammouthHandoffRequest(packet, host);
    const prepared = prepareMammouthReviewImport(packet, host);

    expect(request).toMatchObject({
      version: 'mammouth-host-v1',
      packetId: 'host-handoff:task-auth',
      taskId: 'task-auth',
      targetHost: {
        hostId: 'host-mammouth',
        displayName: 'Mammouth AI',
        permissionMode: 'policy'
      },
      missionPack: {
        objective: 'Prepare a governed handoff for external review',
        mode: 'preview'
      },
      instructions: {
        requireRepoTruth: true,
        requireEvidence: true,
        responseFormat: 'review_artifact'
      }
    });
    expect(request.priorReviews).toHaveLength(1);
    expect(request.importedContext).toHaveLength(1);
    expect(request.canonicalEnvelopes.map((envelope) => envelope.header.messageType)).toContain('host.review');
    expect(prepared.envelope).toMatchObject({
      hostId: 'host-mammouth',
      actionKind: 'review_import',
      mode: 'validate',
      taskId: 'task-auth',
      evidencePolicy: 'strict'
    });
    expect(prepared.policy.decision).toBe('PROMPT');
    expect(prepared.request.packetId).toBe(packet.packetId);
  });

  it('does not invoke Mammouth while the prompt-gated policy remains unconfirmed', async () => {
    const { packet, host } = getPacketAndHost();
    const fetchImpl = vi.fn();
    const result = await invokeMammouthReviewImport(packet, host, {
      endpoint: 'https://mammouth.example/review',
      fetchImpl,
      allowPrompted: false
    });

    expect(fetchImpl).not.toHaveBeenCalled();
    expect(result.response).toBeNull();
    expect(result.policy.decision).toBe('PROMPT');
  });

  it('invokes Mammouth over HTTP once the prompt decision is allowed', async () => {
    const { packet, host } = getPacketAndHost();
    const fetchImpl = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        version: 'mammouth-host-v1',
        packetId: packet.packetId,
        review: {
          reviewId: 'review-mammouth-002',
          hostId: 'host-mammouth',
          sourceType: 'other',
          subjectRef: 'task:task-auth',
          verdict: 'warn',
          findings: [
            {
              id: 'finding-mammouth-proof',
              severity: 'medium',
              message: 'The imported evidence should cite the final test run.',
              resolutionStatus: 'open'
            }
          ],
          linkedEvidenceRefs: ['verify://task-auth/handoff', 'artifact://host-bridge/review-mammouth-002'],
          importedAt: '2026-04-11T12:01:00.000Z',
          traceId: 'trace-auth',
          taskId: 'task-auth'
        },
        importedContext: [],
        meta: {
          provider: 'mammouth',
          model: 'mammouth-reviewer',
          latencyMs: 420
        }
      })
    });

    const result = await invokeMammouthReviewImport(packet, host, {
      endpoint: 'https://mammouth.example/review',
      fetchImpl,
      allowPrompted: true,
      apiKey: 'secret-token',
      extraHeaders: {
        'x-grimoire-test': '1'
      },
      timeoutMs: 1_000
    });

    expect(fetchImpl).toHaveBeenCalledTimes(1);
    expect(fetchImpl).toHaveBeenCalledWith(
      'https://mammouth.example/review',
      expect.objectContaining({
        method: 'POST',
        headers: expect.objectContaining({
          'content-type': 'application/json',
          authorization: 'Bearer secret-token',
          'x-grimoire-test': '1'
        })
      })
    );
    expect(result.response).toMatchObject({
      packetId: packet.packetId,
      review: {
        reviewId: 'review-mammouth-002',
        hostId: 'host-mammouth',
        verdict: 'warn'
      },
      meta: {
        provider: 'mammouth',
        model: 'mammouth-reviewer'
      }
    });
  });
});