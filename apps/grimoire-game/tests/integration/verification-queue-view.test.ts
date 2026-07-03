import type { GameState } from '../../src/state/game-state';
import { createVerificationQueueView } from '../../src/state/verification-queue-view';

function createBaseState(): GameState {
  return {
    protocolVersion: 'v1',
    lastSequenceId: 49,
    hydratedAt: '2026-04-10T00:00:00.000Z',
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
        roomId: 'build-room',
        position: { x: 8, y: 8 },
        parentId: 'orch-1',
        lastTool: 'create_file'
      }
    },
    tasks: {
      'task-verifying': {
        id: 'task-verifying',
        title: 'Ship auth middleware',
        status: 'review',
        assigneeId: 'dev-1'
      },
      'task-queued': {
        id: 'task-queued',
        title: 'Investigate flaky runtime trace',
        status: 'in_progress',
        assigneeId: 'dev-1'
      },
      'task-accepted': {
        id: 'task-accepted',
        title: 'Finalize mission ledger',
        status: 'done',
        assigneeId: 'dev-1'
      },
      'task-rejected': {
        id: 'task-rejected',
        title: 'Close insecure branch',
        status: 'done',
        assigneeId: 'dev-1'
      },
      'task-needs-work': {
        id: 'task-needs-work',
        title: 'Patch verification gaps',
        status: 'review',
        assigneeId: 'dev-1'
      },
      'task-backlog': {
        id: 'task-backlog',
        title: 'Archive old notes',
        status: 'backlog'
      }
    },
    config: {},
    recentToolCalls: [
      {
        tool: 'create_file',
        params: { path: 'src/auth.ts', task_id: 'task-verifying' },
        sourceEventType: 'artifact_created',
        traceId: 'trace-verifying',
        sequenceId: 20,
        timestamp: '2026-04-10T00:00:20.000Z',
        agentId: 'dev-1'
      },
      {
        tool: 'edit_file',
        params: { path: 'src/ledger.ts', task_id: 'task-accepted' },
        sourceEventType: 'artifact_updated',
        traceId: 'trace-accepted',
        sequenceId: 30,
        timestamp: '2026-04-10T00:00:30.000Z',
        agentId: 'dev-1'
      },
      {
        tool: 'edit_file',
        params: { path: 'src/security.ts', task_id: 'task-rejected' },
        sourceEventType: 'artifact_updated',
        traceId: 'trace-rejected',
        sequenceId: 40,
        timestamp: '2026-04-10T00:00:40.000Z',
        agentId: 'dev-1'
      }
    ],
    recentWorkflowSteps: [
      {
        step: 'Routing dispatch',
        detail: 'Intent routed: Ship auth middleware',
        sourceEventType: 'routing',
        traceId: 'trace-verifying',
        taskId: 'task-verifying',
        metadata: { intent: 'Ship auth middleware' },
        sequenceId: 10,
        timestamp: '2026-04-10T00:00:10.000Z',
        agentId: 'orch-1'
      },
      {
        step: 'Decision recorded',
        detail: 'Auth middleware verified',
        sourceEventType: 'decision',
        traceId: 'trace-verifying',
        taskId: 'task-verifying',
        metadata: {
          topic: 'auth',
          actionId: 'task.transition.done',
          verificationRef: 'verify://task-verifying/runtime',
          controlsExecuted: ['tests:unit', 'review:critical-findings'],
          evidenceRefs: ['tests://runtime#task-verifying'],
          verdict: 'PASS'
        },
        sequenceId: 21,
        timestamp: '2026-04-10T00:00:21.000Z',
        agentId: 'dev-1'
      },
      {
        step: 'Root cause identified',
        detail: 'Trace misalignment reproduced',
        sourceEventType: 'decision',
        traceId: 'trace-queued',
        taskId: 'task-queued',
        metadata: { phase: 'root_cause_identified' },
        sequenceId: 22,
        timestamp: '2026-04-10T00:00:22.000Z',
        agentId: 'dev-1'
      },
      {
        step: 'Pattern identified',
        detail: 'Intermittent handoff loss',
        sourceEventType: 'decision',
        traceId: 'trace-queued',
        taskId: 'task-queued',
        metadata: { phase: 'pattern_identified' },
        sequenceId: 23,
        timestamp: '2026-04-10T00:00:23.000Z',
        agentId: 'dev-1'
      },
      {
        step: 'Hypothesis defined',
        detail: 'Ordering issue in trace merge',
        sourceEventType: 'decision',
        traceId: 'trace-queued',
        taskId: 'task-queued',
        metadata: { phase: 'hypothesis_defined' },
        sequenceId: 24,
        timestamp: '2026-04-10T00:00:24.000Z',
        agentId: 'dev-1'
      },
      {
        step: 'Implementation completed',
        detail: 'Added trace ordering guard',
        sourceEventType: 'decision',
        traceId: 'trace-queued',
        taskId: 'task-queued',
        metadata: { phase: 'implementation_completed' },
        sequenceId: 25,
        timestamp: '2026-04-10T00:00:25.000Z',
        agentId: 'dev-1'
      },
      {
        step: 'Decision recorded',
        detail: 'Mission ledger accepted',
        sourceEventType: 'decision',
        traceId: 'trace-accepted',
        taskId: 'task-accepted',
        metadata: {
          topic: 'ledger',
          actionId: 'task.transition.done',
          verificationRef: 'verify://task-accepted/runtime',
          controlsExecuted: ['tests:integration', 'review:critical-findings'],
          evidenceRefs: ['tests://runtime#task-accepted'],
          verdict: 'PASS'
        },
        sequenceId: 31,
        timestamp: '2026-04-10T00:00:31.000Z',
        agentId: 'dev-1'
      },
      {
        step: 'Decision recorded',
        detail: 'Security branch rejected',
        sourceEventType: 'decision',
        traceId: 'trace-rejected',
        taskId: 'task-rejected',
        metadata: {
          topic: 'security',
          actionId: 'task.transition.done',
          verificationRef: 'verify://task-rejected/runtime',
          controlsExecuted: ['tests:integration', 'review:critical-findings'],
          evidenceRefs: ['tests://runtime#task-rejected'],
          verdict: 'FAIL'
        },
        sequenceId: 41,
        timestamp: '2026-04-10T00:00:41.000Z',
        agentId: 'dev-1'
      },
      {
        step: 'Fix proposed too early',
        detail: 'Patched without root cause',
        sourceEventType: 'decision',
        traceId: 'trace-needs-work',
        taskId: 'task-needs-work',
        metadata: { phase: 'fix_proposed' },
        sequenceId: 42,
        timestamp: '2026-04-10T00:00:42.000Z',
        agentId: 'dev-1'
      }
    ],
    lastErrors: []
  };
}

describe('verification queue view', () => {
  it('projects canonical verification queue states from runtime gates', () => {
    const verificationQueue = createVerificationQueueView(createBaseState());

    expect(verificationQueue.metrics).toEqual({
      itemCount: 5,
      queuedCount: 1,
      verifyingCount: 1,
      acceptedCount: 1,
      rejectedCount: 1,
      needsWorkCount: 1
    });
    expect(verificationQueue.items.map((item) => [item.taskId, item.queueStatus])).toEqual([
      ['task-rejected', 'rejected'],
      ['task-needs-work', 'needs_work'],
      ['task-verifying', 'verifying'],
      ['task-queued', 'queued'],
      ['task-accepted', 'accepted']
    ]);

    const queuedItem = verificationQueue.items.find((item) => item.taskId === 'task-queued');
    expect(queuedItem).toMatchObject({
      reviewApplicable: true,
      reviewReady: true,
      queueStatus: 'queued'
    });

    const needsWorkItem = verificationQueue.items.find((item) => item.taskId === 'task-needs-work');
    expect(needsWorkItem?.unmetReviewRequirementCodes).toEqual(expect.arrayContaining([
      'TASK_ROOT_CAUSE_IDENTIFIED_BEFORE_FIX_PROPOSED',
      'TASK_DEBUG_PHASE_SEQUENCE_COMPLETE'
    ]));

    const acceptedItem = verificationQueue.items.find((item) => item.taskId === 'task-accepted');
    expect(acceptedItem).toMatchObject({
      doneReady: true,
      verdict: 'PASS',
      queueStatus: 'accepted'
    });
  });
});