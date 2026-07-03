import {
  createTaskTransition,
  createVerificationGateEvent,
  parseClientEvent,
  parseServerEvent,
  RUNTIME_PROTOCOL_VERSION
} from '../../src/contracts/events';

describe('verification gate contract', () => {
  it('accepts VERIFICATION_GATE events with critical verification metadata', () => {
    const event = parseServerEvent(
      createVerificationGateEvent(
        7,
        {
          result: 'PASS',
          actionId: 'task.transition.done',
          verificationRef: 'verify://task-auth/42',
          controlsExecuted: ['tests:unit', 'review:critical-findings'],
          evidenceRefs: [
            { kind: 'test', ref: 'tests://grimoire-game/runtime-source-fs#done-gate' },
            { kind: 'artifact', ref: 'artifact://audit/task-auth/42' }
          ],
          unmetControls: [],
          traceId: 'trace-auth-42',
          taskId: 'task-auth',
          meta: {
            actorId: 'dev-amelia',
            actorRole: 'agent'
          }
        },
        {
          timestamp: '2026-04-09T00:00:07.000Z'
        }
      )
    );

    expect(event.type).toBe('VERIFICATION_GATE');
    if (event.type !== 'VERIFICATION_GATE') {
      throw new Error('Expected VERIFICATION_GATE event.');
    }

    expect(event.result).toBe('PASS');
    expect(event.actionId).toBe('task.transition.done');
    expect(event.verificationRef).toBe('verify://task-auth/42');
    expect(event.traceId).toBe('trace-auth-42');
    expect(event.taskId).toBe('task-auth');
    expect(event.controlsExecuted).toEqual(['tests:unit', 'review:critical-findings']);
    expect(event.evidenceRefs).toEqual([
      { kind: 'test', ref: 'tests://grimoire-game/runtime-source-fs#done-gate' },
      { kind: 'artifact', ref: 'artifact://audit/task-auth/42' }
    ]);
  });

  it('rejects VERIFICATION_GATE events with incomplete chain metadata', () => {
    expect(() =>
      parseServerEvent({
        type: 'VERIFICATION_GATE',
        version: RUNTIME_PROTOCOL_VERSION,
        sequenceId: 8,
        timestamp: '2026-04-09T00:00:08.000Z',
        result: 'FAIL',
        actionId: 'task.transition.done',
        verificationRef: '',
        controlsExecuted: [],
        evidenceRefs: []
      })
    ).toThrow();
  });

  it('accepts TASK_TRANSITION payloads enriched with verification chain metadata', () => {
    const event = parseClientEvent(
      createTaskTransition('req-task-done-verify', 'task-auth', 'done', 'task-done-verify', undefined, {
        actionId: 'task.transition.done',
        traceId: 'trace-auth-42',
        verificationRef: 'verify://task-auth/42',
        controlsExecuted: ['tests:unit'],
        evidenceRefs: ['tests://grimoire-game/runtime-source-fs#done-gate'],
        verdict: 'PASS',
        unmetControls: []
      })
    );

    expect(event.type).toBe('TASK_TRANSITION');
    if (event.type !== 'TASK_TRANSITION') {
      throw new Error('Expected TASK_TRANSITION event.');
    }

    expect(event.verification).toEqual({
      actionId: 'task.transition.done',
      traceId: 'trace-auth-42',
      verificationRef: 'verify://task-auth/42',
      controlsExecuted: ['tests:unit'],
      evidenceRefs: ['tests://grimoire-game/runtime-source-fs#done-gate'],
      requestId: 'req-task-done-verify',
      idempotencyKey: 'task-done-verify',
      verdict: 'PASS',
      unmetControls: []
    });
  });

  it('rejects TASK_TRANSITION verification metadata when evidence refs are empty', () => {
    expect(() =>
      parseClientEvent({
        type: 'TASK_TRANSITION',
        version: RUNTIME_PROTOCOL_VERSION,
        requestId: 'req-task-done-invalid',
        taskId: 'task-auth',
        status: 'done',
        idempotencyKey: 'task-done-invalid',
        verification: {
          actionId: 'task.transition.done',
          traceId: 'trace-auth-42',
          verificationRef: 'verify://task-auth/42',
          controlsExecuted: ['tests:unit'],
          evidenceRefs: []
        }
      })
    ).toThrow();
  });
});
