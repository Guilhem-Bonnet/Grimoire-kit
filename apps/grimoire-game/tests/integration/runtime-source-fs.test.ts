import { mkdtemp, unlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { AdapterGrimoire } from '../../src/bridge/adapter-grimoire';
import { FileSystemGrimoireRuntimeSource } from '../../src/bridge/runtime-source-fs';
import { LeaseStore } from '../../src/server/control-plane/lease-store';
import {
  createAgentStatusUpdate,
  createConfigUpdate,
  createTaskAssign,
  createTaskTransition
} from '../../src/contracts/events';
import { applyServerEvents, createEmptyGameState } from '../../src/state/game-state';

async function writeEventLog(lines: readonly string[]): Promise<string> {
  const directory = await mkdtemp(join(tmpdir(), 'grimoire-game-events-'));
  const filePath = join(directory, '.event-log.jsonl');
  await writeFile(filePath, `${lines.join('\n')}\n`, 'utf8');
  return filePath;
}

describe('FileSystemGrimoireRuntimeSource', () => {
  it('derives a coherent initial snapshot from the grimoire event log', async () => {
    const eventLogPath = await writeEventLog([
      JSON.stringify({
        id: 'evt-1',
        ts: '2026-03-06T09:00:01Z',
        agent: 'orchestrator',
        type: 'routing',
        payload: { intent: 'Implement auth' },
        trace_id: 'session-001',
        seq: 1
      }),
      JSON.stringify({
        id: 'evt-2',
        ts: '2026-03-06T09:00:05Z',
        agent: 'dev/Amelia',
        type: 'task_started',
        payload: { task_id: 'implement-auth', description: 'Implement auth layer' },
        trace_id: 'session-001',
        seq: 2
      }),
      JSON.stringify({
        id: 'evt-3',
        ts: '2026-03-06T09:00:10Z',
        agent: 'dev/Amelia',
        type: 'artifact_created',
        payload: { path: 'src/auth.ts' },
        trace_id: 'session-001',
        seq: 3
      }),
      JSON.stringify({
        id: 'evt-4',
        ts: '2026-03-06T09:00:20Z',
        agent: 'dev/Amelia',
        type: 'task_completed',
        payload: { task_id: 'implement-auth', description: 'Implement auth layer' },
        trace_id: 'session-001',
        seq: 4
      })
    ]);

    const adapter = new AdapterGrimoire(
      new FileSystemGrimoireRuntimeSource({
        eventLogPath,
        initialConfig: { 'hud.theme': 'paper' }
      })
    );

    const events = await adapter.getInitialSnapshot({ principalId: 'orch-1', role: 'orchestrator' });

    expect(events).toHaveLength(1);
    expect(events[0]?.type).toBe('STATE_SNAPSHOT');
    if (events[0]?.type === 'STATE_SNAPSHOT') {
      expect(events[0].snapshot.lastSequenceId).toBe(4);
      expect(events[0].snapshot.config).toMatchObject({ 'hud.theme': 'paper' });
      expect(events[0].snapshot.recentToolCalls).toEqual([
        {
          tool: 'create_file',
          params: { path: 'src/auth.ts' },
          sourceEventType: 'artifact_created',
          traceId: 'session-001',
          sequenceId: 3,
          timestamp: '2026-03-06T09:00:10Z',
          agentId: 'dev-amelia'
        }
      ]);
      expect(events[0].snapshot.recentWorkflowSteps).toEqual([
        {
          step: 'Routing dispatch',
          detail: 'Intent routed: Implement auth',
          sourceEventType: 'routing',
          traceId: 'session-001',
          metadata: { intent: 'Implement auth' },
          sequenceId: 1,
          timestamp: '2026-03-06T09:00:01Z',
          agentId: 'orchestrator-orchestrator'
        },
        {
          step: 'Task lifecycle update',
          detail: 'Task implement-auth changed via task_started',
          sourceEventType: 'task_started',
          traceId: 'session-001',
          taskId: 'implement-auth',
          metadata: { task_id: 'implement-auth', description: 'Implement auth layer' },
          sequenceId: 2,
          timestamp: '2026-03-06T09:00:05Z',
          agentId: 'dev-amelia'
        },
        {
          step: 'Task lifecycle update',
          detail: 'Task implement-auth changed via task_completed',
          sourceEventType: 'task_completed',
          traceId: 'session-001',
          taskId: 'implement-auth',
          metadata: { task_id: 'implement-auth', description: 'Implement auth layer' },
          sequenceId: 4,
          timestamp: '2026-03-06T09:00:20Z',
          agentId: 'dev-amelia'
        }
      ]);
      expect(events[0].snapshot.tasks).toEqual([
        {
          id: 'implement-auth',
          title: 'Implement auth layer',
          status: 'done',
          assigneeId: 'dev-amelia'
        }
      ]);
      expect(events[0].snapshot.agents.some((agent) => agent.id === 'orchestrator-orchestrator')).toBe(true);
      expect(events[0].snapshot.agents.some((agent) => agent.id === 'dev-amelia')).toBe(true);
    }
  });

  it('infers trace correlation identifiers from payload fields when trace_id is missing', async () => {
    const eventLogPath = await writeEventLog([
      JSON.stringify({
        id: 'evt-1',
        ts: '2026-03-06T09:00:01Z',
        agent: 'orchestrator',
        type: 'routing',
        payload: { intent: 'Implement auth', request_id: 'req-session-42' },
        seq: 1
      }),
      JSON.stringify({
        id: 'evt-2',
        ts: '2026-03-06T09:00:05Z',
        agent: 'dev/Amelia',
        type: 'artifact_created',
        payload: { path: 'src/auth.ts', correlationId: 'req-session-42' },
        seq: 2
      }),
      JSON.stringify({
        id: 'evt-3',
        ts: '2026-03-06T09:00:10Z',
        agent: 'architect/Winston',
        type: 'decision',
        payload: { topic: 'auth', choice: 'JWT RS256 stateless', traceId: 'req-session-42' },
        seq: 3
      })
    ]);
    const source = new FileSystemGrimoireRuntimeSource({ eventLogPath });

    const snapshot = await source.readSnapshot();
    if (snapshot.recentToolCalls === undefined || snapshot.recentWorkflowSteps === undefined) {
      throw new Error('Expected runtime snapshot logs to be present.');
    }

    expect(snapshot.recentToolCalls).toHaveLength(1);
    expect(snapshot.recentToolCalls[0]).toMatchObject({
      tool: 'create_file',
      traceId: 'req-session-42',
      params: {
        path: 'src/auth.ts',
        correlationId: 'req-session-42'
      }
    });

    expect(snapshot.recentWorkflowSteps).toHaveLength(2);
    expect(snapshot.recentWorkflowSteps.map((step) => step.traceId)).toEqual([
      'req-session-42',
      'req-session-42'
    ]);
    expect(snapshot.recentWorkflowSteps[0]).toMatchObject({
      step: 'Routing dispatch',
      traceId: 'req-session-42',
      metadata: {
        intent: 'Implement auth',
        request_id: 'req-session-42'
      }
    });
    expect(snapshot.recentWorkflowSteps[1]).toMatchObject({
      step: 'Decision recorded',
      traceId: 'req-session-42',
      metadata: {
        topic: 'auth',
        choice: 'JWT RS256 stateless',
        traceId: 'req-session-42'
      }
    });
  });

  it('exposes connection diagnostics with live/stale status per agent', async () => {
    const now = Date.now();
    const staleTs = new Date(now - 8_000).toISOString();
    const liveTs = new Date(now - 1_000).toISOString();
    const eventLogPath = await writeEventLog([
      JSON.stringify({
        id: 'evt-1',
        ts: staleTs,
        agent: 'dev/Amelia',
        type: 'task_started',
        payload: { task_id: 'task-dev', description: 'Implement feature' },
        seq: 1
      }),
      JSON.stringify({
        id: 'evt-2',
        ts: liveTs,
        agent: 'qa/Quinn',
        type: 'routing',
        payload: { intent: 'QA check' },
        seq: 2
      })
    ]);

    const source = new FileSystemGrimoireRuntimeSource({
      eventLogPath,
      connectionHealth: {
        enabled: true,
        staleAfterMs: 5_000
      }
    });

    const snapshot = await source.readSnapshot();

    expect(snapshot.config).toMatchObject({
      'live.connection.found': true,
      'live.connection.parsedLineCount': 2,
      'live.connection.status': 'live'
    });

    const byAgent = snapshot.config?.['live.connection.byAgent'];
    if (typeof byAgent !== 'object' || byAgent === null || Array.isArray(byAgent)) {
      throw new Error('Expected live.connection.byAgent map in runtime config.');
    }

    const byAgentRecord = byAgent as Record<string, Record<string, unknown>>;
    expect(byAgentRecord['dev-amelia']).toMatchObject({
      status: 'stale',
      found: true,
      parsedLineCount: 1
    });
    expect(byAgentRecord['qa-quinn']).toMatchObject({
      status: 'live',
      found: true,
      parsedLineCount: 1
    });
  });

  it('reports disconnected diagnostics when JSONL file is missing', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'grimoire-game-events-missing-'));
    const missingEventLogPath = join(directory, 'missing.event-log.jsonl');
    const source = new FileSystemGrimoireRuntimeSource({
      eventLogPath: missingEventLogPath,
      connectionHealth: {
        enabled: true,
        staleAfterMs: 5_000
      }
    });

    const snapshot = await source.readSnapshot();

    expect(snapshot.lastSequenceId).toBe(0);
    expect(snapshot.config).toMatchObject({
      'live.connection.found': false,
      'live.connection.parsedLineCount': 0,
      'live.connection.status': 'disconnected'
    });
  });

  it('refreshes connection diagnostics after a resync config update', async () => {
    const firstEntry = JSON.stringify({
      id: 'evt-1',
      ts: '2026-03-06T09:00:01Z',
      agent: 'dev/Amelia',
      type: 'task_started',
      payload: { task_id: 'task-dev', description: 'Implement feature' },
      seq: 1
    });
    const secondEntry = JSON.stringify({
      id: 'evt-2',
      ts: '2026-03-06T09:00:02Z',
      agent: 'qa/Quinn',
      type: 'routing',
      payload: { intent: 'QA check' },
      seq: 2
    });
    const eventLogPath = await writeEventLog([firstEntry]);
    const source = new FileSystemGrimoireRuntimeSource({
      eventLogPath,
      connectionHealth: {
        enabled: true,
        staleAfterMs: 5_000
      }
    });

    const initialSnapshot = await source.readSnapshot();
    expect(initialSnapshot.config).toMatchObject({
      'live.connection.parsedLineCount': 1
    });

    await writeFile(eventLogPath, `${firstEntry}\n${secondEntry}\n`, 'utf8');
    const mutationResult = await source.applyConfigUpdate({
      requestId: 'req-resync',
      idempotencyKey: 'cfg-resync',
      key: 'live.connection.resyncRequestedAt',
      value: '2026-03-06T09:00:03Z',
      auth: {
        principalId: 'orch-1',
        role: 'orchestrator'
      }
    });

    expect(mutationResult.snapshot.config).toMatchObject({
      'live.connection.resyncRequestedAt': '2026-03-06T09:00:03Z',
      'live.connection.parsedLineCount': 2
    });
  });

  it('tracks stale to live to disconnected transitions across scans', async () => {
    const staleEntry = JSON.stringify({
      id: 'evt-1',
      ts: new Date(Date.now() - 7_000).toISOString(),
      agent: 'dev/Amelia',
      type: 'task_started',
      payload: { task_id: 'task-dev', description: 'Implement feature' },
      seq: 1
    });
    const eventLogPath = await writeEventLog([staleEntry]);
    const source = new FileSystemGrimoireRuntimeSource({
      eventLogPath,
      connectionHealth: {
        enabled: true,
        staleAfterMs: 5_000
      }
    });

    const staleSnapshot = await source.readSnapshot();
    expect(staleSnapshot.config).toMatchObject({
      'live.connection.status': 'stale'
    });

    const liveEntry = JSON.stringify({
      id: 'evt-2',
      ts: new Date(Date.now() - 500).toISOString(),
      agent: 'dev/Amelia',
      type: 'task_completed',
      payload: { task_id: 'task-dev', description: 'Implement feature' },
      seq: 2
    });
    await writeFile(eventLogPath, `${staleEntry}\n${liveEntry}\n`, 'utf8');

    const liveSnapshot = await source.readSnapshot();
    expect(liveSnapshot.config).toMatchObject({
      'live.connection.status': 'live'
    });

    await unlink(eventLogPath);
    const disconnectedSnapshot = await source.readSnapshot();
    expect(disconnectedSnapshot.config).toMatchObject({
      'live.connection.found': false,
      'live.connection.status': 'disconnected'
    });
  });

  it('returns replay events for both agent-only and task updates', async () => {
    const replayOnlyPath = await writeEventLog([
      JSON.stringify({
        id: 'evt-1',
        ts: '2026-03-06T09:00:01Z',
        agent: 'orchestrator',
        type: 'routing',
        payload: { intent: 'Dispatch subagent' },
        trace_id: 'session-001',
        seq: 1
      }),
      JSON.stringify({
        id: 'evt-2',
        ts: '2026-03-06T09:00:05Z',
        agent: 'dev/Amelia',
        type: 'artifact_created',
        payload: { path: 'src/auth.ts' },
        trace_id: 'session-001',
        seq: 2
      })
    ]);
    const replayAdapter = new AdapterGrimoire(
      new FileSystemGrimoireRuntimeSource({ eventLogPath: replayOnlyPath })
    );

    const replayEvents = await replayAdapter.reconnect(1, { principalId: 'orch-1', role: 'orchestrator' });

    expect(replayEvents).toHaveLength(1);
    expect(replayEvents[0]?.type).toBe('TOOL_CALL');
    if (replayEvents[0]?.type === 'TOOL_CALL') {
      expect(replayEvents[0].sequenceId).toBe(2);
      expect(replayEvents[0].call.tool).toBe('create_file');
      expect(replayEvents[0].call.sourceEventType).toBe('artifact_created');
      expect(replayEvents[0].call.traceId).toBe('session-001');
      expect(replayEvents[0].agent?.id).toBe('dev-amelia');
      expect(replayEvents[0].agent?.lastTool).toBe('create_file');
      expect(replayEvents[0].agent?.status).toBe('working');
    }

    const taskFallbackPath = await writeEventLog([
      JSON.stringify({
        id: 'evt-1',
        ts: '2026-03-06T09:00:01Z',
        agent: 'dev/Amelia',
        type: 'task_started',
        payload: { task_id: 'write-tests', description: 'Write tests' },
        seq: 1
      }),
      JSON.stringify({
        id: 'evt-2',
        ts: '2026-03-06T09:00:10Z',
        agent: 'dev/Amelia',
        type: 'task_completed',
        payload: { task_id: 'write-tests', description: 'Write tests' },
        seq: 2
      })
    ]);
    const fallbackAdapter = new AdapterGrimoire(
      new FileSystemGrimoireRuntimeSource({ eventLogPath: taskFallbackPath })
    );

    const fallbackEvents = await fallbackAdapter.reconnect(0, { principalId: 'orch-1', role: 'orchestrator' });

    expect(fallbackEvents).toHaveLength(2);
    expect(fallbackEvents.every((event) => event.type === 'TASK_UPDATE')).toBe(true);

    const [startedEvent, completedEvent] = fallbackEvents;
    if (startedEvent?.type !== 'TASK_UPDATE' || completedEvent?.type !== 'TASK_UPDATE') {
      throw new Error('Expected task replay events.');
    }

    expect(startedEvent.sequenceId).toBe(1);
    expect(startedEvent.task.status).toBe('in_progress');
    expect(startedEvent.agent?.status).toBe('working');

    expect(completedEvent.sequenceId).toBe(2);
    expect(completedEvent.task.status).toBe('done');
    expect(completedEvent.agent?.status).toBe('idle');
  });

  it('returns workflow step replay for decision-like runtime events', async () => {
    const workflowPath = await writeEventLog([
      JSON.stringify({
        id: 'evt-1',
        ts: '2026-03-06T09:00:01Z',
        agent: 'architect/Winston',
        type: 'decision',
        payload: {
          task_id: 'write-tests',
          topic: 'auth',
          choice: 'JWT RS256 stateless',
          actionId: 'task.transition.done',
          verificationRef: 'verify://write-tests/7',
          controlsExecuted: ['tests:unit'],
          evidenceRefs: ['tests://grimoire-game/runtime-source-fs#replay'],
          result: 'PASS'
        },
        trace_id: 'session-001',
        seq: 1
      })
    ]);
    const adapter = new AdapterGrimoire(
      new FileSystemGrimoireRuntimeSource({ eventLogPath: workflowPath })
    );

    const replayEvents = await adapter.reconnect(0, { principalId: 'orch-1', role: 'orchestrator' });

    expect(replayEvents).toHaveLength(1);
    expect(replayEvents[0]?.type).toBe('WORKFLOW_STEP');
    if (replayEvents[0]?.type !== 'WORKFLOW_STEP') {
      throw new Error('Expected a workflow step event.');
    }

    expect(replayEvents[0].sequenceId).toBe(1);
    expect(replayEvents[0].step.step).toBe('Decision recorded');
    expect(replayEvents[0].step.detail).toContain('JWT RS256 stateless');
    expect(replayEvents[0].step.traceId).toBe('session-001');
    expect(replayEvents[0].step.taskId).toBe('write-tests');
    expect(replayEvents[0].step.metadata).toMatchObject({
      actionId: 'task.transition.done',
      verificationRef: 'verify://write-tests/7',
      controlsExecuted: ['tests:unit'],
      evidenceRefs: ['tests://grimoire-game/runtime-source-fs#replay'],
      result: 'PASS'
    });
    expect(replayEvents[0].agent?.id).toBe('architect-winston');
  });

  it('returns workflow step replay for security findings and branch finisher events', async () => {
    const workflowPath = await writeEventLog([
      JSON.stringify({
        id: 'evt-1',
        ts: '2026-03-06T09:00:01Z',
        agent: 'qa/Quinn',
        type: 'security_finding',
        payload: {
          finding_id: 'SEC-100',
          title: 'Missing policy on runtime_config',
          severity: 'critical',
          confidenceScore: 9.5,
          exploitScenario: 'Untrusted actor can mutate runtime_config without policy.',
          surfaceId: 'runtime_config',
          missingPolicy: true
        },
        trace_id: 'session-sec-001',
        seq: 1
      }),
      JSON.stringify({
        id: 'evt-2',
        ts: '2026-03-06T09:00:02Z',
        agent: 'orchestrator',
        type: 'branch_finish_options',
        payload: {
          branch: 'feature/security-audit',
          tests_passed: true,
          allowed_options: ['merge', 'pr', 'keep', 'discard'],
          typed_discard_confirmation: 'DROP-BRANCH'
        },
        trace_id: 'session-sec-001',
        seq: 2
      }),
      JSON.stringify({
        id: 'evt-3',
        ts: '2026-03-06T09:00:03Z',
        agent: 'orchestrator',
        type: 'branch_finish_decision',
        payload: {
          branch: 'feature/security-audit',
          selected_option: 'discard',
          typed_confirmation: 'DROP-BRANCH'
        },
        trace_id: 'session-sec-001',
        seq: 3
      })
    ]);
    const adapter = new AdapterGrimoire(
      new FileSystemGrimoireRuntimeSource({ eventLogPath: workflowPath })
    );

    const replayEvents = await adapter.reconnect(0, { principalId: 'orch-1', role: 'orchestrator' });

    expect(replayEvents).toHaveLength(3);
    expect(replayEvents.every((event) => event.type === 'WORKFLOW_STEP')).toBe(true);

    const first = replayEvents[0];
    if (first?.type !== 'WORKFLOW_STEP') {
      throw new Error('Expected security finding workflow step replay event.');
    }

    expect(first.step.step).toBe('Security finding recorded');
    expect(first.step.detail).toContain('SEC-100');
    expect(first.step.metadata).toMatchObject({
      finding_id: 'SEC-100',
      severity: 'critical',
      missingPolicy: true
    });

    const second = replayEvents[1];
    if (second?.type !== 'WORKFLOW_STEP') {
      throw new Error('Expected branch finish options workflow step replay event.');
    }

    expect(second.step.step).toBe('Branch finisher options updated');
    expect(second.step.detail).toContain('testsPassed=true');

    const third = replayEvents[2];
    if (third?.type !== 'WORKFLOW_STEP') {
      throw new Error('Expected branch finish decision workflow step replay event.');
    }

    expect(third.step.step).toBe('Branch finisher decision proposed');
    expect(third.step.detail).toContain('discard');
  });

  it('hydrates recent logs into GameState from the initial snapshot', async () => {
    const eventLogPath = await writeEventLog([
      JSON.stringify({
        id: 'evt-1',
        ts: '2026-03-06T09:00:01Z',
        agent: 'architect/Winston',
        type: 'decision',
        payload: { topic: 'auth', choice: 'JWT RS256 stateless' },
        trace_id: 'session-001',
        seq: 1
      }),
      JSON.stringify({
        id: 'evt-2',
        ts: '2026-03-06T09:00:02Z',
        agent: 'dev/Amelia',
        type: 'artifact_created',
        payload: { path: 'src/auth.ts' },
        trace_id: 'session-001',
        seq: 2
      })
    ]);

    const adapter = new AdapterGrimoire(
      new FileSystemGrimoireRuntimeSource({ eventLogPath })
    );

    const events = await adapter.getInitialSnapshot({ principalId: 'orch-1', role: 'orchestrator' });
    const state = applyServerEvents(createEmptyGameState(), events);

    expect(events).toHaveLength(1);
    expect(events[0]?.type).toBe('STATE_SNAPSHOT');
    if (events[0]?.type !== 'STATE_SNAPSHOT') {
      throw new Error('Expected an initial snapshot event.');
    }

    expect(state.lastSequenceId).toBe(2);
    expect(state.recentToolCalls).toEqual([
      {
        tool: 'create_file',
        params: { path: 'src/auth.ts' },
        sourceEventType: 'artifact_created',
        traceId: 'session-001',
        sequenceId: 2,
        timestamp: '2026-03-06T09:00:02Z',
        agentId: 'dev-amelia'
      }
    ]);
    expect(state.recentWorkflowSteps).toEqual([
      {
        step: 'Decision recorded',
        detail: 'auth: JWT RS256 stateless',
        sourceEventType: 'decision',
        traceId: 'session-001',
        metadata: { topic: 'auth', choice: 'JWT RS256 stateless' },
        sequenceId: 1,
        timestamp: '2026-03-06T09:00:01Z',
        agentId: 'architect-winston'
      }
    ]);
  });

  it('applies bounded CONFIG_UPDATE mutations and replays them after reconnect', async () => {
    const eventLogPath = await writeEventLog([
      JSON.stringify({
        id: 'evt-1',
        ts: '2026-03-06T09:00:01Z',
        agent: 'orchestrator',
        type: 'routing',
        payload: { intent: 'Runtime start' },
        seq: 1
      })
    ]);
    const source = new FileSystemGrimoireRuntimeSource({
      eventLogPath,
      initialConfig: { 'hud.theme': 'paper' }
    });
    const adapter = new AdapterGrimoire(source);

    const updated = await adapter.handleClientEvent(
      createConfigUpdate('req-1', 'hud.theme', 'neon', 'cfg-1'),
      { principalId: 'orch-1', role: 'orchestrator' }
    );

    expect(updated).toHaveLength(1);
    expect(updated[0]?.type).toBe('STATE_SNAPSHOT');
    if (updated[0]?.type !== 'STATE_SNAPSHOT') {
      throw new Error('Expected config update snapshot event.');
    }

    expect(updated[0].sequenceId).toBe(2);
    expect(updated[0].snapshot.config).toMatchObject({ 'hud.theme': 'neon' });

    const replay = await adapter.reconnect(1, { principalId: 'orch-1', role: 'orchestrator' });

    expect(replay).toHaveLength(1);
    expect(replay[0]?.type).toBe('STATE_SNAPSHOT');
    if (replay[0]?.type !== 'STATE_SNAPSHOT') {
      throw new Error('Expected replay snapshot event.');
    }

    expect(replay[0].sequenceId).toBe(2);
    expect(replay[0].snapshot.config).toMatchObject({ 'hud.theme': 'neon' });
  });

  it('dedupes direct CONFIG_UPDATE writes with the same idempotency key', async () => {
    const eventLogPath = await writeEventLog([
      JSON.stringify({
        id: 'evt-1',
        ts: '2026-03-06T09:00:01Z',
        agent: 'orchestrator',
        type: 'routing',
        payload: { intent: 'Runtime start' },
        seq: 1
      })
    ]);
    const source = new FileSystemGrimoireRuntimeSource({
      eventLogPath,
      initialConfig: { 'hud.theme': 'paper' }
    });

    const first = await source.applyConfigUpdate({
      requestId: 'req-config-dedupe-1',
      idempotencyKey: 'cfg-dedupe',
      key: 'hud.theme',
      value: 'neon',
      auth: {
        principalId: 'orch-1',
        role: 'orchestrator'
      }
    });
    const deduped = await source.applyConfigUpdate({
      requestId: 'req-config-dedupe-2',
      idempotencyKey: 'cfg-dedupe',
      key: 'hud.theme',
      value: 'paper',
      auth: {
        principalId: 'orch-1',
        role: 'orchestrator'
      }
    });

    expect(first.sequenceId).toBe(2);
    expect(deduped.sequenceId).toBe(2);
    expect(deduped.snapshot.config).toMatchObject({ 'hud.theme': 'neon' });

    const adapter = new AdapterGrimoire(source);
    const replay = await adapter.reconnect(1, { principalId: 'orch-1', role: 'orchestrator' });

    expect(replay).toHaveLength(1);
    expect(replay[0]?.type).toBe('STATE_SNAPSHOT');
    expect(replay[0]?.sequenceId).toBe(2);
  });

  it('evicts oldest direct idempotency keys when cache size is bounded', async () => {
    const eventLogPath = await writeEventLog([
      JSON.stringify({
        id: 'evt-1',
        ts: '2026-03-06T09:00:01Z',
        agent: 'orchestrator',
        type: 'routing',
        payload: { intent: 'Runtime start' },
        seq: 1
      })
    ]);
    const source = new FileSystemGrimoireRuntimeSource({
      eventLogPath,
      initialConfig: { 'hud.theme': 'paper' },
      processedMutationCacheMaxEntries: 1
    });

    const first = await source.applyConfigUpdate({
      requestId: 'req-config-evict-1',
      idempotencyKey: 'cfg-evict-1',
      key: 'hud.theme',
      value: 'neon',
      auth: {
        principalId: 'orch-1',
        role: 'orchestrator'
      }
    });
    const second = await source.applyConfigUpdate({
      requestId: 'req-config-evict-2',
      idempotencyKey: 'cfg-evict-2',
      key: 'hud.theme',
      value: 'paper',
      auth: {
        principalId: 'orch-1',
        role: 'orchestrator'
      }
    });
    const replayedAfterEviction = await source.applyConfigUpdate({
      requestId: 'req-config-evict-3',
      idempotencyKey: 'cfg-evict-1',
      key: 'hud.theme',
      value: 'midnight',
      auth: {
        principalId: 'orch-1',
        role: 'orchestrator'
      }
    });

    expect(first.sequenceId).toBe(2);
    expect(second.sequenceId).toBe(3);
    expect(replayedAfterEviction.sequenceId).toBe(4);
    expect(replayedAfterEviction.snapshot.config).toMatchObject({ 'hud.theme': 'midnight' });
  });

  it('keeps direct idempotency isolation across mutation types with a shared key', async () => {
    const eventLogPath = await writeEventLog([
      JSON.stringify({
        id: 'evt-1',
        ts: '2026-03-06T09:00:01Z',
        agent: 'dev/Amelia',
        type: 'task_started',
        payload: { task_id: 'write-tests', description: 'Write tests' },
        seq: 1
      })
    ]);
    const source = new FileSystemGrimoireRuntimeSource({
      eventLogPath,
      initialConfig: { 'hud.theme': 'paper' }
    });

    const configResult = await source.applyConfigUpdate({
      requestId: 'req-cross-direct-config',
      idempotencyKey: 'shared-direct-key',
      key: 'hud.theme',
      value: 'neon',
      auth: {
        principalId: 'orch-1',
        role: 'orchestrator'
      }
    });
    const transitionResult = await source.applyTaskTransition({
      requestId: 'req-cross-direct-transition',
      idempotencyKey: 'shared-direct-key',
      taskId: 'write-tests',
      status: 'review',
      auth: {
        principalId: 'orch-1',
        role: 'orchestrator'
      }
    });

    expect(configResult.sequenceId).toBe(2);
    expect(transitionResult.sequenceId).toBe(3);
    expect(transitionResult.snapshot.config).toMatchObject({ 'hud.theme': 'neon' });
    expect(transitionResult.snapshot.tasks).toEqual([
      {
        id: 'write-tests',
        title: 'Write tests',
        status: 'review',
        assigneeId: 'dev-amelia'
      }
    ]);
  });

  it('dedupes direct TASK_TRANSITION writes with the same idempotency key and replays once', async () => {
    const eventLogPath = await writeEventLog([
      JSON.stringify({
        id: 'evt-1',
        ts: '2026-03-06T09:00:01Z',
        agent: 'dev/Amelia',
        type: 'task_started',
        payload: { task_id: 'write-tests', description: 'Write tests' },
        seq: 1
      })
    ]);
    const source = new FileSystemGrimoireRuntimeSource({ eventLogPath });

    await source.applyTaskTransition({
      requestId: 'req-task-transition-dedupe-1',
      idempotencyKey: 'task-transition-dedupe',
      taskId: 'write-tests',
      status: 'review',
      auth: {
        principalId: 'orch-1',
        role: 'orchestrator'
      }
    });
    const deduped = await source.applyTaskTransition({
      requestId: 'req-task-transition-dedupe-2',
      idempotencyKey: 'task-transition-dedupe',
      taskId: 'write-tests',
      status: 'done',
      auth: {
        principalId: 'orch-1',
        role: 'orchestrator'
      }
    });

    expect(deduped.sequenceId).toBe(2);
    expect(deduped.snapshot.tasks).toEqual([
      {
        id: 'write-tests',
        title: 'Write tests',
        status: 'review',
        assigneeId: 'dev-amelia'
      }
    ]);

    const adapter = new AdapterGrimoire(source);
    const replay = await adapter.reconnect(1, { principalId: 'orch-1', role: 'orchestrator' });

    expect(replay).toHaveLength(1);
    expect(replay[0]?.type).toBe('STATE_SNAPSHOT');
    expect(replay[0]?.sequenceId).toBe(2);
  });

  it('dedupes direct TASK_ASSIGN writes with the same idempotency key and replays once', async () => {
    const eventLogPath = await writeEventLog([
      JSON.stringify({
        id: 'evt-1',
        ts: '2026-03-06T09:00:01Z',
        agent: 'dev/Amelia',
        type: 'task_started',
        payload: { task_id: 'write-tests', description: 'Write tests' },
        seq: 1
      }),
      JSON.stringify({
        id: 'evt-2',
        ts: '2026-03-06T09:00:02Z',
        agent: 'qa/Quinn',
        type: 'routing',
        payload: { intent: 'Review task assignment' },
        seq: 2
      })
    ]);
    const source = new FileSystemGrimoireRuntimeSource({ eventLogPath });

    await source.applyTaskAssign({
      requestId: 'req-task-assign-dedupe-1',
      idempotencyKey: 'task-assign-dedupe',
      taskId: 'write-tests',
      assigneeId: 'qa-quinn',
      auth: {
        principalId: 'orch-1',
        role: 'orchestrator'
      }
    });
    const deduped = await source.applyTaskAssign({
      requestId: 'req-task-assign-dedupe-2',
      idempotencyKey: 'task-assign-dedupe',
      taskId: 'write-tests',
      assigneeId: 'dev-amelia',
      auth: {
        principalId: 'orch-1',
        role: 'orchestrator'
      }
    });

    expect(deduped.sequenceId).toBe(3);
    expect(deduped.snapshot.tasks).toEqual([
      {
        id: 'write-tests',
        title: 'Write tests',
        status: 'in_progress',
        assigneeId: 'qa-quinn'
      }
    ]);

    const adapter = new AdapterGrimoire(source);
    const replay = await adapter.reconnect(2, { principalId: 'orch-1', role: 'orchestrator' });

    expect(replay).toHaveLength(1);
    expect(replay[0]?.type).toBe('STATE_SNAPSHOT');
    expect(replay[0]?.sequenceId).toBe(3);
  });

  it('dedupes direct AGENT_STATUS_UPDATE writes with the same idempotency key and replays once', async () => {
    const eventLogPath = await writeEventLog([
      JSON.stringify({
        id: 'evt-1',
        ts: '2026-03-06T09:00:01Z',
        agent: 'dev/Amelia',
        type: 'routing',
        payload: { intent: 'Agent status update' },
        seq: 1
      })
    ]);
    const source = new FileSystemGrimoireRuntimeSource({ eventLogPath });

    await source.applyAgentStatusUpdate({
      requestId: 'req-agent-status-dedupe-1',
      idempotencyKey: 'agent-status-dedupe',
      agentId: 'dev-amelia',
      status: 'paused',
      auth: {
        principalId: 'orch-1',
        role: 'orchestrator'
      }
    });
    const deduped = await source.applyAgentStatusUpdate({
      requestId: 'req-agent-status-dedupe-2',
      idempotencyKey: 'agent-status-dedupe',
      agentId: 'dev-amelia',
      status: 'working',
      auth: {
        principalId: 'orch-1',
        role: 'orchestrator'
      }
    });

    expect(deduped.sequenceId).toBe(2);
    expect(deduped.snapshot.agents).toContainEqual({
      id: 'dev-amelia',
      name: 'Amelia',
      role: 'agent',
      status: 'paused',
      roomId: 'build-room',
      position: { x: 8, y: 8 }
    });

    const adapter = new AdapterGrimoire(source);
    const replay = await adapter.reconnect(1, { principalId: 'orch-1', role: 'orchestrator' });

    expect(replay).toHaveLength(1);
    expect(replay[0]?.type).toBe('STATE_SNAPSHOT');
    expect(replay[0]?.sequenceId).toBe(2);
  });

  it('rejects direct mutations with an empty idempotency key', async () => {
    const eventLogPath = await writeEventLog([
      JSON.stringify({
        id: 'evt-1',
        ts: '2026-03-06T09:00:01Z',
        agent: 'orchestrator',
        type: 'routing',
        payload: { intent: 'Runtime start' },
        seq: 1
      })
    ]);
    const source = new FileSystemGrimoireRuntimeSource({ eventLogPath });

    await expect(
      source.applyConfigUpdate({
        requestId: 'req-empty-idempotency',
        idempotencyKey: '',
        key: 'hud.theme',
        value: 'paper',
        auth: {
          principalId: 'orch-1',
          role: 'orchestrator'
        }
      })
    ).rejects.toThrow('requires a non-empty idempotencyKey');
  });

  it('rejects direct mutations with an empty requestId', async () => {
    const eventLogPath = await writeEventLog([
      JSON.stringify({
        id: 'evt-1',
        ts: '2026-03-06T09:00:01Z',
        agent: 'orchestrator',
        type: 'routing',
        payload: { intent: 'Runtime start' },
        seq: 1
      })
    ]);
    const source = new FileSystemGrimoireRuntimeSource({ eventLogPath });

    await expect(
      source.applyConfigUpdate({
        requestId: '',
        idempotencyKey: 'cfg-empty-request-id',
        key: 'hud.theme',
        value: 'paper',
        auth: {
          principalId: 'orch-1',
          role: 'orchestrator'
        }
      })
    ).rejects.toThrow('requires a non-empty requestId');
  });

  it('rejects direct mutations with a non-canonical requestId', async () => {
    const eventLogPath = await writeEventLog([
      JSON.stringify({
        id: 'evt-1',
        ts: '2026-03-06T09:00:01Z',
        agent: 'orchestrator',
        type: 'routing',
        payload: { intent: 'Runtime start' },
        seq: 1
      })
    ]);
    const source = new FileSystemGrimoireRuntimeSource({ eventLogPath });

    await expect(
      source.applyConfigUpdate({
        requestId: ' req-trimmed ',
        idempotencyKey: 'cfg-canonical',
        key: 'hud.theme',
        value: 'paper',
        auth: {
          principalId: 'orch-1',
          role: 'orchestrator'
        }
      })
    ).rejects.toThrow('must not contain leading or trailing spaces');
  });

  it('rejects direct mutations with non-canonical idempotencyKey characters', async () => {
    const eventLogPath = await writeEventLog([
      JSON.stringify({
        id: 'evt-1',
        ts: '2026-03-06T09:00:01Z',
        agent: 'orchestrator',
        type: 'routing',
        payload: { intent: 'Runtime start' },
        seq: 1
      })
    ]);
    const source = new FileSystemGrimoireRuntimeSource({ eventLogPath });

    await expect(
      source.applyConfigUpdate({
        requestId: 'req-canonical',
        idempotencyKey: 'cfg key invalid',
        key: 'hud.theme',
        value: 'paper',
        auth: {
          principalId: 'orch-1',
          role: 'orchestrator'
        }
      })
    ).rejects.toThrow('contains unsupported characters');
  });

  it('rejects direct mutations with oversized requestId', async () => {
    const eventLogPath = await writeEventLog([
      JSON.stringify({
        id: 'evt-1',
        ts: '2026-03-06T09:00:01Z',
        agent: 'orchestrator',
        type: 'routing',
        payload: { intent: 'Runtime start' },
        seq: 1
      })
    ]);
    const source = new FileSystemGrimoireRuntimeSource({ eventLogPath });
    const oversizedRequestId = 'a'.repeat(129);

    await expect(
      source.applyConfigUpdate({
        requestId: oversizedRequestId,
        idempotencyKey: 'cfg-canonical',
        key: 'hud.theme',
        value: 'paper',
        auth: {
          principalId: 'orch-1',
          role: 'orchestrator'
        }
      })
    ).rejects.toThrow('requestId exceeds 128 characters');
  });

  it('rejects CONFIG_UPDATE outside bounded write budget on direct runtime source writes', async () => {
    const eventLogPath = await writeEventLog([
      JSON.stringify({
        id: 'evt-1',
        ts: '2026-03-06T09:00:01Z',
        agent: 'orchestrator',
        type: 'routing',
        payload: { intent: 'Runtime start' },
        seq: 1
      })
    ]);
    const source = new FileSystemGrimoireRuntimeSource({ eventLogPath });

    await expect(
      source.applyConfigUpdate({
        requestId: 'req-config-budget-out',
        idempotencyKey: 'cfg-budget-out',
        key: 'runtime.secret',
        value: true,
        auth: {
          principalId: 'orch-1',
          role: 'orchestrator'
        }
      })
    ).rejects.toThrow('outside the bounded V5 write budget');
  });

  it('rejects direct CONFIG_UPDATE writes for non-orchestrator roles', async () => {
    const eventLogPath = await writeEventLog([
      JSON.stringify({
        id: 'evt-1',
        ts: '2026-03-06T09:00:01Z',
        agent: 'orchestrator',
        type: 'routing',
        payload: { intent: 'Runtime start' },
        seq: 1
      })
    ]);
    const source = new FileSystemGrimoireRuntimeSource({ eventLogPath });

    await expect(
      source.applyConfigUpdate({
        requestId: 'req-config-direct-spectator',
        idempotencyKey: 'cfg-direct-spectator',
        key: 'hud.theme',
        value: 'paper',
        auth: {
          principalId: 'spectator-1',
          role: 'spectator'
        }
      })
    ).rejects.toThrow('Role spectator cannot mutate runtime state directly for CONFIG_UPDATE.');
  });

  it('applies bounded TASK_TRANSITION mutations and replays them after reconnect', async () => {
    const eventLogPath = await writeEventLog([
      JSON.stringify({
        id: 'evt-1',
        ts: '2026-03-06T09:00:01Z',
        agent: 'dev/Amelia',
        type: 'task_started',
        payload: { task_id: 'write-tests', description: 'Write tests' },
        seq: 1
      })
    ]);
    const source = new FileSystemGrimoireRuntimeSource({ eventLogPath });
    const adapter = new AdapterGrimoire(source);

    const updated = await adapter.handleClientEvent(
      createTaskTransition('req-task-1', 'write-tests', 'review', 'task-1'),
      { principalId: 'orch-1', role: 'orchestrator' }
    );

    expect(updated).toHaveLength(1);
    expect(updated[0]?.type).toBe('STATE_SNAPSHOT');
    if (updated[0]?.type !== 'STATE_SNAPSHOT') {
      throw new Error('Expected task transition snapshot event.');
    }

    expect(updated[0].sequenceId).toBe(2);
    expect(updated[0].snapshot.tasks).toEqual([
      {
        id: 'write-tests',
        title: 'Write tests',
        status: 'review',
        assigneeId: 'dev-amelia'
      }
    ]);

    const replay = await adapter.reconnect(1, { principalId: 'orch-1', role: 'orchestrator' });

    expect(replay).toHaveLength(1);
    expect(replay[0]?.type).toBe('STATE_SNAPSHOT');
    if (replay[0]?.type !== 'STATE_SNAPSHOT') {
      throw new Error('Expected replay snapshot event.');
    }

    expect(replay[0].sequenceId).toBe(2);
    expect(replay[0].snapshot.tasks).toEqual([
      {
        id: 'write-tests',
        title: 'Write tests',
        status: 'review',
        assigneeId: 'dev-amelia'
      }
    ]);
  });

  it('rejects TASK_TRANSITION target status outside bounded transition budget on direct runtime source writes', async () => {
    const eventLogPath = await writeEventLog([
      JSON.stringify({
        id: 'evt-1',
        ts: '2026-03-06T09:00:01Z',
        agent: 'dev/Amelia',
        type: 'task_started',
        payload: { task_id: 'write-tests', description: 'Write tests' },
        seq: 1
      })
    ]);
    const source = new FileSystemGrimoireRuntimeSource({ eventLogPath });

    await expect(
      source.applyTaskTransition({
        requestId: 'req-task-budget-out',
        idempotencyKey: 'task-budget-out',
        taskId: 'write-tests',
        status: 'backlog',
        auth: {
          principalId: 'orch-1',
          role: 'orchestrator'
        }
      })
    ).rejects.toThrow('outside the bounded V5 transition budget');
  });

  it('rejects TASK_TRANSITION outside bounded transition graph on direct runtime source writes', async () => {
    const eventLogPath = await writeEventLog([
      JSON.stringify({
        id: 'evt-1',
        ts: '2026-03-06T09:00:01Z',
        agent: 'dev/Amelia',
        type: 'task_started',
        payload: { task_id: 'write-tests', description: 'Write tests' },
        seq: 1
      })
    ]);
    const source = new FileSystemGrimoireRuntimeSource({ eventLogPath });

    await expect(
      source.applyTaskTransition({
        requestId: 'req-task-graph-out',
        idempotencyKey: 'task-graph-out',
        taskId: 'write-tests',
        status: 'done',
        auth: {
          principalId: 'orch-1',
          role: 'orchestrator'
        }
      })
    ).rejects.toThrow('outside the bounded V5 transition graph');
  });

  it('rejects direct TASK_TRANSITION writes for non-orchestrator roles', async () => {
    const eventLogPath = await writeEventLog([
      JSON.stringify({
        id: 'evt-1',
        ts: '2026-03-06T09:00:01Z',
        agent: 'dev/Amelia',
        type: 'task_started',
        payload: { task_id: 'write-tests', description: 'Write tests' },
        seq: 1
      })
    ]);
    const source = new FileSystemGrimoireRuntimeSource({ eventLogPath });

    await expect(
      source.applyTaskTransition({
        requestId: 'req-transition-direct-agent',
        idempotencyKey: 'task-transition-direct-agent',
        taskId: 'write-tests',
        status: 'review',
        auth: {
          principalId: 'agent-1',
          role: 'agent'
        }
      })
    ).rejects.toThrow('Role agent cannot mutate runtime state directly for TASK_TRANSITION.');
  });

  it('rejects AGENT_STATUS_UPDATE outside bounded pause/resume budget on direct runtime source writes', async () => {
    const eventLogPath = await writeEventLog([
      JSON.stringify({
        id: 'evt-1',
        ts: '2026-03-06T09:00:01Z',
        agent: 'orchestrator',
        type: 'routing',
        payload: { intent: 'Runtime start' },
        seq: 1
      })
    ]);
    const source = new FileSystemGrimoireRuntimeSource({ eventLogPath });

    await expect(
      source.applyAgentStatusUpdate({
        requestId: 'req-agent-budget-out',
        idempotencyKey: 'agent-budget-out',
        agentId: 'orchestrator-orchestrator',
        status: 'idle',
        auth: {
          principalId: 'orch-1',
          role: 'orchestrator'
        }
      })
    ).rejects.toThrow('outside the bounded V5 pause/resume budget');
  });

  it('rejects direct TASK_ASSIGN writes for non-orchestrator roles', async () => {
    const eventLogPath = await writeEventLog([
      JSON.stringify({
        id: 'evt-1',
        ts: '2026-03-06T09:00:01Z',
        agent: 'dev/Amelia',
        type: 'task_started',
        payload: { task_id: 'write-tests', description: 'Write tests' },
        seq: 1
      }),
      JSON.stringify({
        id: 'evt-2',
        ts: '2026-03-06T09:00:02Z',
        agent: 'qa/Quinn',
        type: 'routing',
        payload: { intent: 'Review task assignment' },
        seq: 2
      })
    ]);
    const source = new FileSystemGrimoireRuntimeSource({ eventLogPath });

    await expect(
      source.applyTaskAssign({
        requestId: 'req-assign-direct-spectator',
        idempotencyKey: 'task-assign-direct-spectator',
        taskId: 'write-tests',
        assigneeId: 'qa-quinn',
        auth: {
          principalId: 'spectator-1',
          role: 'spectator'
        }
      })
    ).rejects.toThrow('Role spectator cannot mutate runtime state directly for TASK_ASSIGN.');
  });

  it('rejects direct AGENT_STATUS_UPDATE writes for non-orchestrator roles', async () => {
    const eventLogPath = await writeEventLog([
      JSON.stringify({
        id: 'evt-1',
        ts: '2026-03-06T09:00:01Z',
        agent: 'dev/Amelia',
        type: 'routing',
        payload: { intent: 'Agent status update' },
        seq: 1
      })
    ]);
    const source = new FileSystemGrimoireRuntimeSource({ eventLogPath });

    await expect(
      source.applyAgentStatusUpdate({
        requestId: 'req-agent-status-direct-agent',
        idempotencyKey: 'agent-status-direct-agent',
        agentId: 'dev-amelia',
        status: 'paused',
        auth: {
          principalId: 'agent-1',
          role: 'agent'
        }
      })
    ).rejects.toThrow('Role agent cannot mutate runtime state directly for AGENT_STATUS_UPDATE.');
  });

  it('rejects TASK_TRANSITION in_progress -> review when investigation gate is not satisfied', async () => {
    const eventLogPath = await writeEventLog([
      JSON.stringify({
        id: 'evt-1',
        ts: '2026-03-06T09:00:01Z',
        agent: 'dev/Amelia',
        type: 'task_started',
        payload: { task_id: 'write-tests', description: 'Write tests' },
        trace_id: 'trace-review-001',
        seq: 1
      }),
      JSON.stringify({
        id: 'evt-2',
        ts: '2026-03-06T09:00:02Z',
        agent: 'dev/Amelia',
        type: 'decision',
        payload: {
          task_id: 'write-tests',
          topic: 'investigation',
          phase: 'fix_proposed'
        },
        trace_id: 'trace-review-001',
        seq: 2
      })
    ]);
    const source = new FileSystemGrimoireRuntimeSource({ eventLogPath });

    await expect(
      source.applyTaskTransition({
        requestId: 'req-task-review-gate-blocked',
        idempotencyKey: 'task-review-gate-blocked',
        taskId: 'write-tests',
        status: 'review',
        auth: {
          principalId: 'orch-1',
          role: 'orchestrator'
        }
      })
    ).rejects.toThrow('TASK_ROOT_CAUSE_IDENTIFIED_BEFORE_FIX_PROPOSED');

    await expect(
      source.applyTaskTransition({
        requestId: 'req-task-review-gate-blocked-2',
        idempotencyKey: 'task-review-gate-blocked-2',
        taskId: 'write-tests',
        status: 'review',
        auth: {
          principalId: 'orch-1',
          role: 'orchestrator'
        }
      })
    ).rejects.toThrow('cannot transition to review before investigation evidence is complete');
  });

  it('allows TASK_TRANSITION in_progress -> review when investigation gate is satisfied', async () => {
    const eventLogPath = await writeEventLog([
      JSON.stringify({
        id: 'evt-1',
        ts: '2026-03-06T09:00:01Z',
        agent: 'dev/Amelia',
        type: 'task_started',
        payload: { task_id: 'write-tests', description: 'Write tests' },
        trace_id: 'trace-review-ok-001',
        seq: 1
      }),
      JSON.stringify({
        id: 'evt-2',
        ts: '2026-03-06T09:00:02Z',
        agent: 'dev/Amelia',
        type: 'decision',
        payload: { task_id: 'write-tests', topic: 'investigation', phase: 'root_cause_identified' },
        trace_id: 'trace-review-ok-001',
        seq: 2
      }),
      JSON.stringify({
        id: 'evt-3',
        ts: '2026-03-06T09:00:03Z',
        agent: 'dev/Amelia',
        type: 'decision',
        payload: { task_id: 'write-tests', topic: 'investigation', phase: 'pattern_identified' },
        trace_id: 'trace-review-ok-001',
        seq: 3
      }),
      JSON.stringify({
        id: 'evt-4',
        ts: '2026-03-06T09:00:04Z',
        agent: 'dev/Amelia',
        type: 'decision',
        payload: { task_id: 'write-tests', topic: 'investigation', phase: 'hypothesis' },
        trace_id: 'trace-review-ok-001',
        seq: 4
      }),
      JSON.stringify({
        id: 'evt-5',
        ts: '2026-03-06T09:00:05Z',
        agent: 'dev/Amelia',
        type: 'decision',
        payload: { task_id: 'write-tests', topic: 'investigation', phase: 'implementation_completed' },
        trace_id: 'trace-review-ok-001',
        seq: 5
      }),
      JSON.stringify({
        id: 'evt-6',
        ts: '2026-03-06T09:00:06Z',
        agent: 'dev/Amelia',
        type: 'decision',
        payload: { task_id: 'write-tests', topic: 'investigation', phase: 'fix_proposed' },
        trace_id: 'trace-review-ok-001',
        seq: 6
      })
    ]);
    const source = new FileSystemGrimoireRuntimeSource({ eventLogPath });

    const reviewResult = await source.applyTaskTransition({
      requestId: 'req-task-review-gate-ok',
      idempotencyKey: 'task-review-gate-ok',
      taskId: 'write-tests',
      status: 'review',
      auth: {
        principalId: 'orch-1',
        role: 'orchestrator'
      }
    });

    expect(reviewResult.snapshot.tasks).toEqual([
      {
        id: 'write-tests',
        title: 'Write tests',
        status: 'review',
        assigneeId: 'dev-amelia'
      }
    ]);
  });

  it('rejects TASK_TRANSITION review -> done when verification gate is not satisfied', async () => {
    const eventLogPath = await writeEventLog([
      JSON.stringify({
        id: 'evt-1',
        ts: '2026-03-06T09:00:01Z',
        agent: 'dev/Amelia',
        type: 'task_started',
        payload: { task_id: 'write-tests', description: 'Write tests' },
        seq: 1
      })
    ]);
    const source = new FileSystemGrimoireRuntimeSource({ eventLogPath });

    await source.applyTaskTransition({
      requestId: 'req-task-review',
      idempotencyKey: 'task-review',
      taskId: 'write-tests',
      status: 'review',
      auth: {
        principalId: 'orch-1',
        role: 'orchestrator'
      }
    });

    await expect(
      source.applyTaskTransition({
        requestId: 'req-task-done-no-proof',
        idempotencyKey: 'task-done-no-proof',
        taskId: 'write-tests',
        status: 'done',
        auth: {
          principalId: 'orch-1',
          role: 'orchestrator'
        }
      })
    ).rejects.toThrow('TASK_HAS_TRACE');
  });

  it('rejects TASK_TRANSITION in_progress -> review when a critical review finding is still open', async () => {
    const eventLogPath = await writeEventLog([
      JSON.stringify({
        id: 'evt-1',
        ts: '2026-03-06T09:00:01Z',
        agent: 'dev/Amelia',
        type: 'task_started',
        payload: { task_id: 'write-tests', description: 'Write tests' },
        trace_id: 'trace-crit-001',
        seq: 1
      }),
      JSON.stringify({
        id: 'evt-2',
        ts: '2026-03-06T09:00:02Z',
        agent: 'dev/Amelia',
        type: 'artifact_created',
        payload: { path: 'tests/auth.spec.ts', task_id: 'write-tests' },
        trace_id: 'trace-crit-001',
        seq: 2
      }),
      JSON.stringify({
        id: 'evt-3',
        ts: '2026-03-06T09:00:03Z',
        agent: 'qa/Quinn',
        type: 'decision',
        payload: {
          task_id: 'write-tests',
          topic: 'verification',
          severity: 'critical',
          status: 'open',
          detail: 'Regression still open'
        },
        trace_id: 'trace-crit-001',
        seq: 3
      })
    ]);
    const source = new FileSystemGrimoireRuntimeSource({ eventLogPath });

    await expect(
      source.applyTaskTransition({
        requestId: 'req-task-review-critical-open',
        idempotencyKey: 'task-review-critical-open',
        taskId: 'write-tests',
        status: 'review',
        auth: {
          principalId: 'orch-1',
          role: 'orchestrator'
        }
      })
    ).rejects.toThrow('TASK_NO_OPEN_CRITICAL_FINDINGS');
  });

  it('rejects TASK_TRANSITION review -> done when a blocking security finding is still open', async () => {
    const eventLogPath = await writeEventLog([
      JSON.stringify({
        id: 'evt-1',
        ts: '2026-03-06T09:00:01Z',
        agent: 'dev/Amelia',
        type: 'task_started',
        payload: { task_id: 'write-tests', description: 'Write tests' },
        trace_id: 'trace-sec-001',
        seq: 1
      }),
      JSON.stringify({
        id: 'evt-2',
        ts: '2026-03-06T09:00:02Z',
        agent: 'dev/Amelia',
        type: 'artifact_created',
        payload: { path: 'tests/auth.spec.ts', task_id: 'write-tests' },
        trace_id: 'trace-sec-001',
        seq: 2
      }),
      JSON.stringify({
        id: 'evt-3',
        ts: '2026-03-06T09:00:03Z',
        agent: 'qa/Quinn',
        type: 'security_finding',
        payload: {
          task_id: 'write-tests',
          finding_id: 'SEC-310',
          severity: 'high',
          status: 'open',
          confidenceScore: 9.2,
          surfaceId: 'runtime_config',
          missingPolicy: true,
          exploitScenario: 'Untrusted actor can mutate runtime_config without elevated policy.'
        },
        trace_id: 'trace-sec-001',
        seq: 3
      })
    ]);
    const source = new FileSystemGrimoireRuntimeSource({ eventLogPath });

    await source.applyTaskTransition({
      requestId: 'req-task-review-security-open',
      idempotencyKey: 'task-review-security-open',
      taskId: 'write-tests',
      status: 'review',
      auth: {
        principalId: 'orch-1',
        role: 'orchestrator'
      }
    });

    await expect(
      source.applyTaskTransition({
        requestId: 'req-task-done-security-open',
        idempotencyKey: 'task-done-security-open',
        taskId: 'write-tests',
        status: 'done',
        auth: {
          principalId: 'orch-1',
          role: 'orchestrator'
        }
      })
    ).rejects.toThrow('TASK_NO_OPEN_BLOCKING_SECURITY_FINDINGS');
  });

  it('allows TASK_TRANSITION review -> done when verification gate is satisfied', async () => {
    const eventLogPath = await writeEventLog([
      JSON.stringify({
        id: 'evt-1',
        ts: '2026-03-06T09:00:01Z',
        agent: 'dev/Amelia',
        type: 'task_started',
        payload: { task_id: 'write-tests', description: 'Write tests' },
        trace_id: 'trace-001',
        seq: 1
      }),
      JSON.stringify({
        id: 'evt-2',
        ts: '2026-03-06T09:00:02Z',
        agent: 'dev/Amelia',
        type: 'decision',
        payload: {
          task_id: 'write-tests',
          topic: 'verification',
          actionId: 'task.transition.done',
          verificationRef: 'verify://write-tests/1',
          controlsExecuted: ['tests:unit'],
          evidenceRefs: ['tests://grimoire-game/runtime-source-fs#done-gate'],
          verdict: 'PASS'
        },
        trace_id: 'trace-001',
        seq: 2
      })
    ]);
    const source = new FileSystemGrimoireRuntimeSource({ eventLogPath });

    await source.applyTaskTransition({
      requestId: 'req-task-review-traced',
      idempotencyKey: 'task-review-traced',
      taskId: 'write-tests',
      status: 'review',
      auth: {
        principalId: 'orch-1',
        role: 'orchestrator'
      }
    });

    const doneResult = await source.applyTaskTransition({
      requestId: 'req-task-done-ok',
      idempotencyKey: 'task-done-ok',
      taskId: 'write-tests',
      status: 'done',
      auth: {
        principalId: 'orch-1',
        role: 'orchestrator'
      }
    });

    expect(doneResult.snapshot.tasks).toEqual([
      {
        id: 'write-tests',
        title: 'Write tests',
        status: 'done',
        assigneeId: 'dev-amelia'
      }
    ]);
  });

  it('keeps idempotency isolation across mutation types on filesystem reconnect replay', async () => {
    const eventLogPath = await writeEventLog([
      JSON.stringify({
        id: 'evt-1',
        ts: '2026-03-06T09:00:01Z',
        agent: 'dev/Amelia',
        type: 'task_started',
        payload: { task_id: 'write-tests', description: 'Write tests' },
        seq: 1
      })
    ]);
    const source = new FileSystemGrimoireRuntimeSource({
      eventLogPath,
      initialConfig: { 'hud.theme': 'paper' }
    });
    const adapter = new AdapterGrimoire(source);

    const configUpdate = await adapter.handleClientEvent(
      createConfigUpdate('req-cross-type-config', 'hud.theme', 'neon', 'shared-key'),
      { principalId: 'orch-1', role: 'orchestrator' }
    );
    const taskTransition = await adapter.handleClientEvent(
      createTaskTransition('req-cross-type-transition', 'write-tests', 'review', 'shared-key'),
      { principalId: 'orch-1', role: 'orchestrator' }
    );

    expect(configUpdate).toHaveLength(1);
    expect(taskTransition).toHaveLength(1);
    expect(configUpdate[0]?.type).toBe('STATE_SNAPSHOT');
    expect(taskTransition[0]?.type).toBe('STATE_SNAPSHOT');
    expect(configUpdate[0]?.sequenceId).toBe(2);
    expect(taskTransition[0]?.sequenceId).toBe(3);

    const replay = await adapter.reconnect(1, { principalId: 'orch-1', role: 'orchestrator' });

    expect(replay).toHaveLength(2);
    expect(replay[0]?.type).toBe('STATE_SNAPSHOT');
    expect(replay[1]?.type).toBe('STATE_SNAPSHOT');
    expect(replay[0]?.sequenceId).toBe(2);
    expect(replay[1]?.sequenceId).toBe(3);
    if (replay[1]?.type !== 'STATE_SNAPSHOT') {
      throw new Error('Expected replay snapshot event.');
    }

    expect(replay[1].snapshot.config).toMatchObject({ 'hud.theme': 'neon' });
    expect(replay[1].snapshot.tasks).toEqual([
      {
        id: 'write-tests',
        title: 'Write tests',
        status: 'review',
        assigneeId: 'dev-amelia'
      }
    ]);

    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'CONFIG_APPLIED' && entry.requestId === 'req-cross-type-config')
    ).toBe(true);
    expect(
      adapter
        .getAuditLog()
        .some(
          (entry) =>
            entry.type === 'TASK_TRANSITION_APPLIED' && entry.requestId === 'req-cross-type-transition'
        )
    ).toBe(true);
    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'CONFIG_DEDUPED' && entry.requestId === 'req-cross-type-config')
    ).toBe(false);
    expect(
      adapter
        .getAuditLog()
        .some(
          (entry) =>
            entry.type === 'TASK_TRANSITION_DEDUPED' && entry.requestId === 'req-cross-type-transition'
        )
    ).toBe(false);
  });

  it('keeps idempotency isolation across mutation types on filesystem reconnect replay in reverse order', async () => {
    const eventLogPath = await writeEventLog([
      JSON.stringify({
        id: 'evt-1',
        ts: '2026-03-06T09:00:01Z',
        agent: 'dev/Amelia',
        type: 'task_started',
        payload: { task_id: 'write-tests', description: 'Write tests' },
        seq: 1
      })
    ]);
    const source = new FileSystemGrimoireRuntimeSource({
      eventLogPath,
      initialConfig: { 'hud.theme': 'paper' }
    });
    const adapter = new AdapterGrimoire(source);

    const taskTransition = await adapter.handleClientEvent(
      createTaskTransition(
        'req-cross-type-transition-rev',
        'write-tests',
        'review',
        'shared-key-rev'
      ),
      { principalId: 'orch-1', role: 'orchestrator' }
    );
    const configUpdate = await adapter.handleClientEvent(
      createConfigUpdate('req-cross-type-config-rev', 'hud.theme', 'neon', 'shared-key-rev'),
      { principalId: 'orch-1', role: 'orchestrator' }
    );

    expect(taskTransition).toHaveLength(1);
    expect(configUpdate).toHaveLength(1);
    expect(taskTransition[0]?.type).toBe('STATE_SNAPSHOT');
    expect(configUpdate[0]?.type).toBe('STATE_SNAPSHOT');
    expect(taskTransition[0]?.sequenceId).toBe(2);
    expect(configUpdate[0]?.sequenceId).toBe(3);

    const replay = await adapter.reconnect(1, { principalId: 'orch-1', role: 'orchestrator' });

    expect(replay).toHaveLength(2);
    expect(replay[0]?.type).toBe('STATE_SNAPSHOT');
    expect(replay[1]?.type).toBe('STATE_SNAPSHOT');
    expect(replay[0]?.sequenceId).toBe(2);
    expect(replay[1]?.sequenceId).toBe(3);
    if (replay[1]?.type !== 'STATE_SNAPSHOT') {
      throw new Error('Expected replay snapshot event.');
    }

    expect(replay[1].snapshot.config).toMatchObject({ 'hud.theme': 'neon' });
    expect(replay[1].snapshot.tasks).toEqual([
      {
        id: 'write-tests',
        title: 'Write tests',
        status: 'review',
        assigneeId: 'dev-amelia'
      }
    ]);

    expect(
      adapter
        .getAuditLog()
        .some(
          (entry) =>
            entry.type === 'TASK_TRANSITION_APPLIED' &&
            entry.requestId === 'req-cross-type-transition-rev'
        )
    ).toBe(true);
    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'CONFIG_APPLIED' && entry.requestId === 'req-cross-type-config-rev')
    ).toBe(true);
    expect(
      adapter
        .getAuditLog()
        .some(
          (entry) =>
            entry.type === 'TASK_TRANSITION_DEDUPED' &&
            entry.requestId === 'req-cross-type-transition-rev'
        )
    ).toBe(false);
    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'CONFIG_DEDUPED' && entry.requestId === 'req-cross-type-config-rev')
    ).toBe(false);
  });

  it('keeps idempotency isolation for TASK_TRANSITION and AGENT_STATUS_UPDATE on filesystem replay', async () => {
    const eventLogPath = await writeEventLog([
      JSON.stringify({
        id: 'evt-1',
        ts: '2026-03-06T09:00:01Z',
        agent: 'dev/Amelia',
        type: 'task_started',
        payload: { task_id: 'write-tests', description: 'Write tests' },
        seq: 1
      }),
      JSON.stringify({
        id: 'evt-2',
        ts: '2026-03-06T09:00:05Z',
        agent: 'qa/Quinn',
        type: 'routing',
        payload: { intent: 'Review task transition' },
        seq: 2
      })
    ]);
    const source = new FileSystemGrimoireRuntimeSource({ eventLogPath });
    const adapter = new AdapterGrimoire(source);

    const transitionUpdate = await adapter.handleClientEvent(
      createTaskTransition(
        'req-cross-transition-status',
        'write-tests',
        'review',
        'shared-transition-status-key'
      ),
      { principalId: 'orch-1', role: 'orchestrator' }
    );
    const statusUpdate = await adapter.handleClientEvent(
      createAgentStatusUpdate(
        'req-cross-agent-status',
        'dev-amelia',
        'paused',
        'shared-transition-status-key'
      ),
      { principalId: 'orch-1', role: 'orchestrator' }
    );

    expect(transitionUpdate).toHaveLength(1);
    expect(statusUpdate).toHaveLength(1);
    expect(transitionUpdate[0]?.type).toBe('STATE_SNAPSHOT');
    expect(statusUpdate[0]?.type).toBe('STATE_SNAPSHOT');
    expect(transitionUpdate[0]?.sequenceId).toBe(3);
    expect(statusUpdate[0]?.sequenceId).toBe(4);

    const replay = await adapter.reconnect(2, { principalId: 'orch-1', role: 'orchestrator' });

    expect(replay).toHaveLength(2);
    expect(replay[0]?.type).toBe('STATE_SNAPSHOT');
    expect(replay[1]?.type).toBe('STATE_SNAPSHOT');
    expect(replay[0]?.sequenceId).toBe(3);
    expect(replay[1]?.sequenceId).toBe(4);
    if (replay[1]?.type !== 'STATE_SNAPSHOT') {
      throw new Error('Expected replay snapshot event.');
    }

    expect(replay[1].snapshot.tasks).toEqual([
      {
        id: 'write-tests',
        title: 'Write tests',
        status: 'review',
        assigneeId: 'dev-amelia'
      }
    ]);
    expect(replay[1].snapshot.agents).toContainEqual({
      id: 'dev-amelia',
      name: 'Amelia',
      role: 'agent',
      status: 'paused',
      roomId: 'build-room',
      position: { x: 8, y: 8 }
    });

    expect(
      adapter
        .getAuditLog()
        .some(
          (entry) =>
            entry.type === 'TASK_TRANSITION_APPLIED' &&
            entry.requestId === 'req-cross-transition-status'
        )
    ).toBe(true);
    expect(
      adapter
        .getAuditLog()
        .some(
          (entry) =>
            entry.type === 'AGENT_STATUS_APPLIED' &&
            entry.requestId === 'req-cross-agent-status'
        )
    ).toBe(true);
    expect(
      adapter
        .getAuditLog()
        .some(
          (entry) =>
            entry.type === 'TASK_TRANSITION_DEDUPED' &&
            entry.requestId === 'req-cross-transition-status'
        )
    ).toBe(false);
    expect(
      adapter
        .getAuditLog()
        .some(
          (entry) =>
            entry.type === 'AGENT_STATUS_DEDUPED' &&
            entry.requestId === 'req-cross-agent-status'
        )
    ).toBe(false);
  });

  it('keeps idempotency isolation for AGENT_STATUS_UPDATE then TASK_TRANSITION on filesystem replay', async () => {
    const eventLogPath = await writeEventLog([
      JSON.stringify({
        id: 'evt-1',
        ts: '2026-03-06T09:00:01Z',
        agent: 'dev/Amelia',
        type: 'task_started',
        payload: { task_id: 'write-tests', description: 'Write tests' },
        seq: 1
      }),
      JSON.stringify({
        id: 'evt-2',
        ts: '2026-03-06T09:00:05Z',
        agent: 'qa/Quinn',
        type: 'routing',
        payload: { intent: 'Review task transition' },
        seq: 2
      })
    ]);
    const source = new FileSystemGrimoireRuntimeSource({ eventLogPath });
    const adapter = new AdapterGrimoire(source);

    const statusUpdate = await adapter.handleClientEvent(
      createAgentStatusUpdate(
        'req-cross-agent-status-rev',
        'dev-amelia',
        'paused',
        'shared-status-transition-key'
      ),
      { principalId: 'orch-1', role: 'orchestrator' }
    );
    const transitionUpdate = await adapter.handleClientEvent(
      createTaskTransition(
        'req-cross-transition-status-rev',
        'write-tests',
        'review',
        'shared-status-transition-key'
      ),
      { principalId: 'orch-1', role: 'orchestrator' }
    );

    expect(statusUpdate).toHaveLength(1);
    expect(transitionUpdate).toHaveLength(1);
    expect(statusUpdate[0]?.type).toBe('STATE_SNAPSHOT');
    expect(transitionUpdate[0]?.type).toBe('STATE_SNAPSHOT');
    expect(statusUpdate[0]?.sequenceId).toBe(3);
    expect(transitionUpdate[0]?.sequenceId).toBe(4);

    const replay = await adapter.reconnect(2, { principalId: 'orch-1', role: 'orchestrator' });

    expect(replay).toHaveLength(2);
    expect(replay[0]?.type).toBe('STATE_SNAPSHOT');
    expect(replay[1]?.type).toBe('STATE_SNAPSHOT');
    expect(replay[0]?.sequenceId).toBe(3);
    expect(replay[1]?.sequenceId).toBe(4);
    if (replay[1]?.type !== 'STATE_SNAPSHOT') {
      throw new Error('Expected replay snapshot event.');
    }

    expect(replay[1].snapshot.tasks).toEqual([
      {
        id: 'write-tests',
        title: 'Write tests',
        status: 'review',
        assigneeId: 'dev-amelia'
      }
    ]);
    expect(replay[1].snapshot.agents).toContainEqual({
      id: 'dev-amelia',
      name: 'Amelia',
      role: 'agent',
      status: 'paused',
      roomId: 'build-room',
      position: { x: 8, y: 8 }
    });

    expect(
      adapter
        .getAuditLog()
        .some(
          (entry) =>
            entry.type === 'AGENT_STATUS_APPLIED' &&
            entry.requestId === 'req-cross-agent-status-rev'
        )
    ).toBe(true);
    expect(
      adapter
        .getAuditLog()
        .some(
          (entry) =>
            entry.type === 'TASK_TRANSITION_APPLIED' &&
            entry.requestId === 'req-cross-transition-status-rev'
        )
    ).toBe(true);
    expect(
      adapter
        .getAuditLog()
        .some(
          (entry) =>
            entry.type === 'AGENT_STATUS_DEDUPED' &&
            entry.requestId === 'req-cross-agent-status-rev'
        )
    ).toBe(false);
    expect(
      adapter
        .getAuditLog()
        .some(
          (entry) =>
            entry.type === 'TASK_TRANSITION_DEDUPED' &&
            entry.requestId === 'req-cross-transition-status-rev'
        )
    ).toBe(false);
  });

  it('keeps idempotency isolation for TASK_TRANSITION and TASK_ASSIGN on filesystem replay', async () => {
    const eventLogPath = await writeEventLog([
      JSON.stringify({
        id: 'evt-1',
        ts: '2026-03-06T09:00:01Z',
        agent: 'dev/Amelia',
        type: 'task_started',
        payload: { task_id: 'write-tests', description: 'Write tests' },
        seq: 1
      }),
      JSON.stringify({
        id: 'evt-2',
        ts: '2026-03-06T09:00:05Z',
        agent: 'qa/Quinn',
        type: 'routing',
        payload: { intent: 'Review task transition and assignment' },
        seq: 2
      })
    ]);
    const source = new FileSystemGrimoireRuntimeSource({ eventLogPath });
    const adapter = new AdapterGrimoire(source);

    const transitionUpdate = await adapter.handleClientEvent(
      createTaskTransition(
        'req-cross-transition-assign',
        'write-tests',
        'review',
        'shared-transition-assign-key'
      ),
      { principalId: 'orch-1', role: 'orchestrator' }
    );
    const assignUpdate = await adapter.handleClientEvent(
      createTaskAssign(
        'req-cross-assign-transition',
        'write-tests',
        'qa-quinn',
        'shared-transition-assign-key'
      ),
      { principalId: 'orch-1', role: 'orchestrator' }
    );

    expect(transitionUpdate).toHaveLength(1);
    expect(assignUpdate).toHaveLength(1);
    expect(transitionUpdate[0]?.type).toBe('STATE_SNAPSHOT');
    expect(assignUpdate[0]?.type).toBe('STATE_SNAPSHOT');
    expect(transitionUpdate[0]?.sequenceId).toBe(3);
    expect(assignUpdate[0]?.sequenceId).toBe(4);

    const replay = await adapter.reconnect(2, { principalId: 'orch-1', role: 'orchestrator' });

    expect(replay).toHaveLength(2);
    expect(replay[0]?.type).toBe('STATE_SNAPSHOT');
    expect(replay[1]?.type).toBe('STATE_SNAPSHOT');
    expect(replay[0]?.sequenceId).toBe(3);
    expect(replay[1]?.sequenceId).toBe(4);
    if (replay[1]?.type !== 'STATE_SNAPSHOT') {
      throw new Error('Expected replay snapshot event.');
    }

    expect(replay[1].snapshot.tasks).toEqual([
      {
        id: 'write-tests',
        title: 'Write tests',
        status: 'review',
        assigneeId: 'qa-quinn'
      }
    ]);

    expect(
      adapter
        .getAuditLog()
        .some(
          (entry) =>
            entry.type === 'TASK_TRANSITION_APPLIED' &&
            entry.requestId === 'req-cross-transition-assign'
        )
    ).toBe(true);
    expect(
      adapter
        .getAuditLog()
        .some(
          (entry) =>
            entry.type === 'TASK_ASSIGN_APPLIED' &&
            entry.requestId === 'req-cross-assign-transition'
        )
    ).toBe(true);
    expect(
      adapter
        .getAuditLog()
        .some(
          (entry) =>
            entry.type === 'TASK_TRANSITION_DEDUPED' &&
            entry.requestId === 'req-cross-transition-assign'
        )
    ).toBe(false);
    expect(
      adapter
        .getAuditLog()
        .some(
          (entry) =>
            entry.type === 'TASK_ASSIGN_DEDUPED' &&
            entry.requestId === 'req-cross-assign-transition'
        )
    ).toBe(false);
  });

  it('keeps idempotency isolation for TASK_ASSIGN then TASK_TRANSITION on filesystem replay', async () => {
    const eventLogPath = await writeEventLog([
      JSON.stringify({
        id: 'evt-1',
        ts: '2026-03-06T09:00:01Z',
        agent: 'dev/Amelia',
        type: 'task_started',
        payload: { task_id: 'write-tests', description: 'Write tests' },
        seq: 1
      }),
      JSON.stringify({
        id: 'evt-2',
        ts: '2026-03-06T09:00:05Z',
        agent: 'qa/Quinn',
        type: 'routing',
        payload: { intent: 'Review task assignment and transition' },
        seq: 2
      })
    ]);
    const source = new FileSystemGrimoireRuntimeSource({ eventLogPath });
    const adapter = new AdapterGrimoire(source);

    const assignUpdate = await adapter.handleClientEvent(
      createTaskAssign(
        'req-cross-assign-transition-rev',
        'write-tests',
        'qa-quinn',
        'shared-assign-transition-key'
      ),
      { principalId: 'orch-1', role: 'orchestrator' }
    );
    const transitionUpdate = await adapter.handleClientEvent(
      createTaskTransition(
        'req-cross-transition-assign-rev',
        'write-tests',
        'review',
        'shared-assign-transition-key'
      ),
      { principalId: 'orch-1', role: 'orchestrator' }
    );

    expect(assignUpdate).toHaveLength(1);
    expect(transitionUpdate).toHaveLength(1);
    expect(assignUpdate[0]?.type).toBe('STATE_SNAPSHOT');
    expect(transitionUpdate[0]?.type).toBe('STATE_SNAPSHOT');
    expect(assignUpdate[0]?.sequenceId).toBe(3);
    expect(transitionUpdate[0]?.sequenceId).toBe(4);

    const replay = await adapter.reconnect(2, { principalId: 'orch-1', role: 'orchestrator' });

    expect(replay).toHaveLength(2);
    expect(replay[0]?.type).toBe('STATE_SNAPSHOT');
    expect(replay[1]?.type).toBe('STATE_SNAPSHOT');
    expect(replay[0]?.sequenceId).toBe(3);
    expect(replay[1]?.sequenceId).toBe(4);
    if (replay[1]?.type !== 'STATE_SNAPSHOT') {
      throw new Error('Expected replay snapshot event.');
    }

    expect(replay[1].snapshot.tasks).toEqual([
      {
        id: 'write-tests',
        title: 'Write tests',
        status: 'review',
        assigneeId: 'qa-quinn'
      }
    ]);

    expect(
      adapter
        .getAuditLog()
        .some(
          (entry) =>
            entry.type === 'TASK_ASSIGN_APPLIED' &&
            entry.requestId === 'req-cross-assign-transition-rev'
        )
    ).toBe(true);
    expect(
      adapter
        .getAuditLog()
        .some(
          (entry) =>
            entry.type === 'TASK_TRANSITION_APPLIED' &&
            entry.requestId === 'req-cross-transition-assign-rev'
        )
    ).toBe(true);
    expect(
      adapter
        .getAuditLog()
        .some(
          (entry) =>
            entry.type === 'TASK_ASSIGN_DEDUPED' &&
            entry.requestId === 'req-cross-assign-transition-rev'
        )
    ).toBe(false);
    expect(
      adapter
        .getAuditLog()
        .some(
          (entry) =>
            entry.type === 'TASK_TRANSITION_DEDUPED' &&
            entry.requestId === 'req-cross-transition-assign-rev'
        )
    ).toBe(false);
  });

  it('keeps idempotency isolation for TASK_ASSIGN and AGENT_STATUS_UPDATE on filesystem replay', async () => {
    const eventLogPath = await writeEventLog([
      JSON.stringify({
        id: 'evt-1',
        ts: '2026-03-06T09:00:01Z',
        agent: 'dev/Amelia',
        type: 'task_started',
        payload: { task_id: 'write-tests', description: 'Write tests' },
        seq: 1
      }),
      JSON.stringify({
        id: 'evt-2',
        ts: '2026-03-06T09:00:05Z',
        agent: 'qa/Quinn',
        type: 'routing',
        payload: { intent: 'Review task assignment' },
        seq: 2
      })
    ]);
    const source = new FileSystemGrimoireRuntimeSource({ eventLogPath });
    const adapter = new AdapterGrimoire(source);

    const assignUpdate = await adapter.handleClientEvent(
      createTaskAssign('req-cross-assign', 'write-tests', 'qa-quinn', 'shared-assign-status-key'),
      { principalId: 'orch-1', role: 'orchestrator' }
    );
    const statusUpdate = await adapter.handleClientEvent(
      createAgentStatusUpdate('req-cross-status', 'dev-amelia', 'paused', 'shared-assign-status-key'),
      { principalId: 'orch-1', role: 'orchestrator' }
    );

    expect(assignUpdate).toHaveLength(1);
    expect(statusUpdate).toHaveLength(1);
    expect(assignUpdate[0]?.type).toBe('STATE_SNAPSHOT');
    expect(statusUpdate[0]?.type).toBe('STATE_SNAPSHOT');
    expect(assignUpdate[0]?.sequenceId).toBe(3);
    expect(statusUpdate[0]?.sequenceId).toBe(4);

    const replay = await adapter.reconnect(2, { principalId: 'orch-1', role: 'orchestrator' });

    expect(replay).toHaveLength(2);
    expect(replay[0]?.type).toBe('STATE_SNAPSHOT');
    expect(replay[1]?.type).toBe('STATE_SNAPSHOT');
    expect(replay[0]?.sequenceId).toBe(3);
    expect(replay[1]?.sequenceId).toBe(4);
    if (replay[1]?.type !== 'STATE_SNAPSHOT') {
      throw new Error('Expected replay snapshot event.');
    }

    expect(replay[1].snapshot.tasks).toEqual([
      {
        id: 'write-tests',
        title: 'Write tests',
        status: 'in_progress',
        assigneeId: 'qa-quinn'
      }
    ]);
    expect(replay[1].snapshot.agents).toContainEqual({
      id: 'dev-amelia',
      name: 'Amelia',
      role: 'agent',
      status: 'paused',
      roomId: 'build-room',
      position: { x: 8, y: 8 }
    });

    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'TASK_ASSIGN_APPLIED' && entry.requestId === 'req-cross-assign')
    ).toBe(true);
    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'AGENT_STATUS_APPLIED' && entry.requestId === 'req-cross-status')
    ).toBe(true);
    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'TASK_ASSIGN_DEDUPED' && entry.requestId === 'req-cross-assign')
    ).toBe(false);
    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'AGENT_STATUS_DEDUPED' && entry.requestId === 'req-cross-status')
    ).toBe(false);
  });

  it('keeps idempotency isolation for AGENT_STATUS_UPDATE then TASK_ASSIGN on filesystem replay', async () => {
    const eventLogPath = await writeEventLog([
      JSON.stringify({
        id: 'evt-1',
        ts: '2026-03-06T09:00:01Z',
        agent: 'dev/Amelia',
        type: 'task_started',
        payload: { task_id: 'write-tests', description: 'Write tests' },
        seq: 1
      }),
      JSON.stringify({
        id: 'evt-2',
        ts: '2026-03-06T09:00:05Z',
        agent: 'qa/Quinn',
        type: 'routing',
        payload: { intent: 'Review task assignment' },
        seq: 2
      })
    ]);
    const source = new FileSystemGrimoireRuntimeSource({ eventLogPath });
    const adapter = new AdapterGrimoire(source);

    const statusUpdate = await adapter.handleClientEvent(
      createAgentStatusUpdate('req-cross-status-rev', 'dev-amelia', 'paused', 'shared-status-assign-key'),
      { principalId: 'orch-1', role: 'orchestrator' }
    );
    const assignUpdate = await adapter.handleClientEvent(
      createTaskAssign('req-cross-assign-rev', 'write-tests', 'qa-quinn', 'shared-status-assign-key'),
      { principalId: 'orch-1', role: 'orchestrator' }
    );

    expect(statusUpdate).toHaveLength(1);
    expect(assignUpdate).toHaveLength(1);
    expect(statusUpdate[0]?.type).toBe('STATE_SNAPSHOT');
    expect(assignUpdate[0]?.type).toBe('STATE_SNAPSHOT');
    expect(statusUpdate[0]?.sequenceId).toBe(3);
    expect(assignUpdate[0]?.sequenceId).toBe(4);

    const replay = await adapter.reconnect(2, { principalId: 'orch-1', role: 'orchestrator' });

    expect(replay).toHaveLength(2);
    expect(replay[0]?.type).toBe('STATE_SNAPSHOT');
    expect(replay[1]?.type).toBe('STATE_SNAPSHOT');
    expect(replay[0]?.sequenceId).toBe(3);
    expect(replay[1]?.sequenceId).toBe(4);
    if (replay[1]?.type !== 'STATE_SNAPSHOT') {
      throw new Error('Expected replay snapshot event.');
    }

    expect(replay[1].snapshot.tasks).toEqual([
      {
        id: 'write-tests',
        title: 'Write tests',
        status: 'in_progress',
        assigneeId: 'qa-quinn'
      }
    ]);
    expect(replay[1].snapshot.agents).toContainEqual({
      id: 'dev-amelia',
      name: 'Amelia',
      role: 'agent',
      status: 'paused',
      roomId: 'build-room',
      position: { x: 8, y: 8 }
    });

    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'AGENT_STATUS_APPLIED' && entry.requestId === 'req-cross-status-rev')
    ).toBe(true);
    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'TASK_ASSIGN_APPLIED' && entry.requestId === 'req-cross-assign-rev')
    ).toBe(true);
    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'AGENT_STATUS_DEDUPED' && entry.requestId === 'req-cross-status-rev')
    ).toBe(false);
    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'TASK_ASSIGN_DEDUPED' && entry.requestId === 'req-cross-assign-rev')
    ).toBe(false);
  });

  it('keeps idempotency isolation for CONFIG_UPDATE and TASK_ASSIGN on filesystem replay', async () => {
    const eventLogPath = await writeEventLog([
      JSON.stringify({
        id: 'evt-1',
        ts: '2026-03-06T09:00:01Z',
        agent: 'dev/Amelia',
        type: 'task_started',
        payload: { task_id: 'write-tests', description: 'Write tests' },
        seq: 1
      }),
      JSON.stringify({
        id: 'evt-2',
        ts: '2026-03-06T09:00:05Z',
        agent: 'qa/Quinn',
        type: 'routing',
        payload: { intent: 'Review config and assignment' },
        seq: 2
      })
    ]);
    const source = new FileSystemGrimoireRuntimeSource({
      eventLogPath,
      initialConfig: { 'hud.theme': 'paper' }
    });
    const adapter = new AdapterGrimoire(source);

    const configUpdate = await adapter.handleClientEvent(
      createConfigUpdate('req-cross-config-assign', 'hud.theme', 'neon', 'shared-config-assign-key'),
      { principalId: 'orch-1', role: 'orchestrator' }
    );
    const assignUpdate = await adapter.handleClientEvent(
      createTaskAssign(
        'req-cross-assign-config',
        'write-tests',
        'qa-quinn',
        'shared-config-assign-key'
      ),
      { principalId: 'orch-1', role: 'orchestrator' }
    );

    expect(configUpdate).toHaveLength(1);
    expect(assignUpdate).toHaveLength(1);
    expect(configUpdate[0]?.type).toBe('STATE_SNAPSHOT');
    expect(assignUpdate[0]?.type).toBe('STATE_SNAPSHOT');
    expect(configUpdate[0]?.sequenceId).toBe(3);
    expect(assignUpdate[0]?.sequenceId).toBe(4);

    const replay = await adapter.reconnect(2, { principalId: 'orch-1', role: 'orchestrator' });

    expect(replay).toHaveLength(2);
    expect(replay[0]?.type).toBe('STATE_SNAPSHOT');
    expect(replay[1]?.type).toBe('STATE_SNAPSHOT');
    expect(replay[0]?.sequenceId).toBe(3);
    expect(replay[1]?.sequenceId).toBe(4);
    if (replay[1]?.type !== 'STATE_SNAPSHOT') {
      throw new Error('Expected replay snapshot event.');
    }

    expect(replay[1].snapshot.config).toMatchObject({ 'hud.theme': 'neon' });
    expect(replay[1].snapshot.tasks).toEqual([
      {
        id: 'write-tests',
        title: 'Write tests',
        status: 'in_progress',
        assigneeId: 'qa-quinn'
      }
    ]);

    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'CONFIG_APPLIED' && entry.requestId === 'req-cross-config-assign')
    ).toBe(true);
    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'TASK_ASSIGN_APPLIED' && entry.requestId === 'req-cross-assign-config')
    ).toBe(true);
    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'CONFIG_DEDUPED' && entry.requestId === 'req-cross-config-assign')
    ).toBe(false);
    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'TASK_ASSIGN_DEDUPED' && entry.requestId === 'req-cross-assign-config')
    ).toBe(false);
  });

  it('keeps idempotency isolation for TASK_ASSIGN then CONFIG_UPDATE on filesystem replay', async () => {
    const eventLogPath = await writeEventLog([
      JSON.stringify({
        id: 'evt-1',
        ts: '2026-03-06T09:00:01Z',
        agent: 'dev/Amelia',
        type: 'task_started',
        payload: { task_id: 'write-tests', description: 'Write tests' },
        seq: 1
      }),
      JSON.stringify({
        id: 'evt-2',
        ts: '2026-03-06T09:00:05Z',
        agent: 'qa/Quinn',
        type: 'routing',
        payload: { intent: 'Review assignment and config' },
        seq: 2
      })
    ]);
    const source = new FileSystemGrimoireRuntimeSource({
      eventLogPath,
      initialConfig: { 'hud.theme': 'paper' }
    });
    const adapter = new AdapterGrimoire(source);

    const assignUpdate = await adapter.handleClientEvent(
      createTaskAssign(
        'req-cross-assign-config-rev',
        'write-tests',
        'qa-quinn',
        'shared-assign-config-key'
      ),
      { principalId: 'orch-1', role: 'orchestrator' }
    );
    const configUpdate = await adapter.handleClientEvent(
      createConfigUpdate('req-cross-config-assign-rev', 'hud.theme', 'neon', 'shared-assign-config-key'),
      { principalId: 'orch-1', role: 'orchestrator' }
    );

    expect(assignUpdate).toHaveLength(1);
    expect(configUpdate).toHaveLength(1);
    expect(assignUpdate[0]?.type).toBe('STATE_SNAPSHOT');
    expect(configUpdate[0]?.type).toBe('STATE_SNAPSHOT');
    expect(assignUpdate[0]?.sequenceId).toBe(3);
    expect(configUpdate[0]?.sequenceId).toBe(4);

    const replay = await adapter.reconnect(2, { principalId: 'orch-1', role: 'orchestrator' });

    expect(replay).toHaveLength(2);
    expect(replay[0]?.type).toBe('STATE_SNAPSHOT');
    expect(replay[1]?.type).toBe('STATE_SNAPSHOT');
    expect(replay[0]?.sequenceId).toBe(3);
    expect(replay[1]?.sequenceId).toBe(4);
    if (replay[1]?.type !== 'STATE_SNAPSHOT') {
      throw new Error('Expected replay snapshot event.');
    }

    expect(replay[1].snapshot.config).toMatchObject({ 'hud.theme': 'neon' });
    expect(replay[1].snapshot.tasks).toEqual([
      {
        id: 'write-tests',
        title: 'Write tests',
        status: 'in_progress',
        assigneeId: 'qa-quinn'
      }
    ]);

    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'TASK_ASSIGN_APPLIED' && entry.requestId === 'req-cross-assign-config-rev')
    ).toBe(true);
    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'CONFIG_APPLIED' && entry.requestId === 'req-cross-config-assign-rev')
    ).toBe(true);
    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'TASK_ASSIGN_DEDUPED' && entry.requestId === 'req-cross-assign-config-rev')
    ).toBe(false);
    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'CONFIG_DEDUPED' && entry.requestId === 'req-cross-config-assign-rev')
    ).toBe(false);
  });

  it('keeps idempotency isolation for CONFIG_UPDATE and AGENT_STATUS_UPDATE on filesystem replay', async () => {
    const eventLogPath = await writeEventLog([
      JSON.stringify({
        id: 'evt-1',
        ts: '2026-03-06T09:00:01Z',
        agent: 'dev/Amelia',
        type: 'task_started',
        payload: { task_id: 'write-tests', description: 'Write tests' },
        seq: 1
      }),
      JSON.stringify({
        id: 'evt-2',
        ts: '2026-03-06T09:00:05Z',
        agent: 'qa/Quinn',
        type: 'routing',
        payload: { intent: 'Review config and agent status' },
        seq: 2
      })
    ]);
    const source = new FileSystemGrimoireRuntimeSource({
      eventLogPath,
      initialConfig: { 'hud.theme': 'paper' }
    });
    const adapter = new AdapterGrimoire(source);

    const configUpdate = await adapter.handleClientEvent(
      createConfigUpdate('req-cross-config-status', 'hud.theme', 'neon', 'shared-config-status-key'),
      { principalId: 'orch-1', role: 'orchestrator' }
    );
    const statusUpdate = await adapter.handleClientEvent(
      createAgentStatusUpdate(
        'req-cross-status-config',
        'dev-amelia',
        'paused',
        'shared-config-status-key'
      ),
      { principalId: 'orch-1', role: 'orchestrator' }
    );

    expect(configUpdate).toHaveLength(1);
    expect(statusUpdate).toHaveLength(1);
    expect(configUpdate[0]?.type).toBe('STATE_SNAPSHOT');
    expect(statusUpdate[0]?.type).toBe('STATE_SNAPSHOT');
    expect(configUpdate[0]?.sequenceId).toBe(3);
    expect(statusUpdate[0]?.sequenceId).toBe(4);

    const replay = await adapter.reconnect(2, { principalId: 'orch-1', role: 'orchestrator' });

    expect(replay).toHaveLength(2);
    expect(replay[0]?.type).toBe('STATE_SNAPSHOT');
    expect(replay[1]?.type).toBe('STATE_SNAPSHOT');
    expect(replay[0]?.sequenceId).toBe(3);
    expect(replay[1]?.sequenceId).toBe(4);
    if (replay[1]?.type !== 'STATE_SNAPSHOT') {
      throw new Error('Expected replay snapshot event.');
    }

    expect(replay[1].snapshot.config).toMatchObject({ 'hud.theme': 'neon' });
    expect(replay[1].snapshot.agents).toContainEqual({
      id: 'dev-amelia',
      name: 'Amelia',
      role: 'agent',
      status: 'paused',
      roomId: 'build-room',
      position: { x: 8, y: 8 }
    });

    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'CONFIG_APPLIED' && entry.requestId === 'req-cross-config-status')
    ).toBe(true);
    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'AGENT_STATUS_APPLIED' && entry.requestId === 'req-cross-status-config')
    ).toBe(true);
    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'CONFIG_DEDUPED' && entry.requestId === 'req-cross-config-status')
    ).toBe(false);
    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'AGENT_STATUS_DEDUPED' && entry.requestId === 'req-cross-status-config')
    ).toBe(false);
  });

  it('keeps idempotency isolation for AGENT_STATUS_UPDATE then CONFIG_UPDATE on filesystem replay', async () => {
    const eventLogPath = await writeEventLog([
      JSON.stringify({
        id: 'evt-1',
        ts: '2026-03-06T09:00:01Z',
        agent: 'dev/Amelia',
        type: 'task_started',
        payload: { task_id: 'write-tests', description: 'Write tests' },
        seq: 1
      }),
      JSON.stringify({
        id: 'evt-2',
        ts: '2026-03-06T09:00:05Z',
        agent: 'qa/Quinn',
        type: 'routing',
        payload: { intent: 'Review agent status and config' },
        seq: 2
      })
    ]);
    const source = new FileSystemGrimoireRuntimeSource({
      eventLogPath,
      initialConfig: { 'hud.theme': 'paper' }
    });
    const adapter = new AdapterGrimoire(source);

    const statusUpdate = await adapter.handleClientEvent(
      createAgentStatusUpdate(
        'req-cross-status-config-rev',
        'dev-amelia',
        'paused',
        'shared-status-config-key'
      ),
      { principalId: 'orch-1', role: 'orchestrator' }
    );
    const configUpdate = await adapter.handleClientEvent(
      createConfigUpdate('req-cross-config-status-rev', 'hud.theme', 'neon', 'shared-status-config-key'),
      { principalId: 'orch-1', role: 'orchestrator' }
    );

    expect(statusUpdate).toHaveLength(1);
    expect(configUpdate).toHaveLength(1);
    expect(statusUpdate[0]?.type).toBe('STATE_SNAPSHOT');
    expect(configUpdate[0]?.type).toBe('STATE_SNAPSHOT');
    expect(statusUpdate[0]?.sequenceId).toBe(3);
    expect(configUpdate[0]?.sequenceId).toBe(4);

    const replay = await adapter.reconnect(2, { principalId: 'orch-1', role: 'orchestrator' });

    expect(replay).toHaveLength(2);
    expect(replay[0]?.type).toBe('STATE_SNAPSHOT');
    expect(replay[1]?.type).toBe('STATE_SNAPSHOT');
    expect(replay[0]?.sequenceId).toBe(3);
    expect(replay[1]?.sequenceId).toBe(4);
    if (replay[1]?.type !== 'STATE_SNAPSHOT') {
      throw new Error('Expected replay snapshot event.');
    }

    expect(replay[1].snapshot.config).toMatchObject({ 'hud.theme': 'neon' });
    expect(replay[1].snapshot.agents).toContainEqual({
      id: 'dev-amelia',
      name: 'Amelia',
      role: 'agent',
      status: 'paused',
      roomId: 'build-room',
      position: { x: 8, y: 8 }
    });

    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'AGENT_STATUS_APPLIED' && entry.requestId === 'req-cross-status-config-rev')
    ).toBe(true);
    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'CONFIG_APPLIED' && entry.requestId === 'req-cross-config-status-rev')
    ).toBe(true);
    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'AGENT_STATUS_DEDUPED' && entry.requestId === 'req-cross-status-config-rev')
    ).toBe(false);
    expect(
      adapter
        .getAuditLog()
        .some((entry) => entry.type === 'CONFIG_DEDUPED' && entry.requestId === 'req-cross-config-status-rev')
    ).toBe(false);
  });

  it('applies bounded TASK_ASSIGN mutations and replays them after reconnect', async () => {
    const eventLogPath = await writeEventLog([
      JSON.stringify({
        id: 'evt-1',
        ts: '2026-03-06T09:00:01Z',
        agent: 'dev/Amelia',
        type: 'task_started',
        payload: { task_id: 'write-tests', description: 'Write tests' },
        seq: 1
      }),
      JSON.stringify({
        id: 'evt-2',
        ts: '2026-03-06T09:00:05Z',
        agent: 'qa/Quinn',
        type: 'routing',
        payload: { intent: 'Review task assignment' },
        seq: 2
      })
    ]);
    const source = new FileSystemGrimoireRuntimeSource({ eventLogPath });
    const adapter = new AdapterGrimoire(source);

    const updated = await adapter.handleClientEvent(
      createTaskAssign('req-assign-1', 'write-tests', 'qa-quinn', 'assign-1'),
      { principalId: 'orch-1', role: 'orchestrator' }
    );

    expect(updated).toHaveLength(1);
    expect(updated[0]?.type).toBe('STATE_SNAPSHOT');
    if (updated[0]?.type !== 'STATE_SNAPSHOT') {
      throw new Error('Expected task assign snapshot event.');
    }

    expect(updated[0].sequenceId).toBe(3);
    expect(updated[0].snapshot.tasks).toEqual([
      {
        id: 'write-tests',
        title: 'Write tests',
        status: 'in_progress',
        assigneeId: 'qa-quinn'
      }
    ]);

    const replay = await adapter.reconnect(2, { principalId: 'orch-1', role: 'orchestrator' });

    expect(replay).toHaveLength(1);
    expect(replay[0]?.type).toBe('STATE_SNAPSHOT');
    if (replay[0]?.type !== 'STATE_SNAPSHOT') {
      throw new Error('Expected replay snapshot event.');
    }

    expect(replay[0].sequenceId).toBe(3);
    expect(replay[0].snapshot.tasks).toEqual([
      {
        id: 'write-tests',
        title: 'Write tests',
        status: 'in_progress',
        assigneeId: 'qa-quinn'
      }
    ]);
  });

  it('applies bounded AGENT_STATUS_UPDATE mutations and replays them after reconnect', async () => {
    const eventLogPath = await writeEventLog([
      JSON.stringify({
        id: 'evt-1',
        ts: '2026-03-06T09:00:01Z',
        agent: 'dev/Amelia',
        type: 'routing',
        payload: { intent: 'Start work' },
        seq: 1
      })
    ]);
    const source = new FileSystemGrimoireRuntimeSource({ eventLogPath });
    const adapter = new AdapterGrimoire(source);

    const updated = await adapter.handleClientEvent(
      createAgentStatusUpdate('req-agent-status-1', 'dev-amelia', 'paused', 'agent-status-1'),
      { principalId: 'orch-1', role: 'orchestrator' }
    );

    expect(updated).toHaveLength(1);
    expect(updated[0]?.type).toBe('STATE_SNAPSHOT');
    if (updated[0]?.type !== 'STATE_SNAPSHOT') {
      throw new Error('Expected agent status update snapshot event.');
    }

    expect(updated[0].sequenceId).toBe(2);
    expect(updated[0].snapshot.agents).toEqual([
      {
        id: 'dev-amelia',
        name: 'Amelia',
        role: 'agent',
        status: 'paused',
        roomId: 'build-room',
        position: { x: 8, y: 8 }
      }
    ]);

    const replay = await adapter.reconnect(1, { principalId: 'orch-1', role: 'orchestrator' });

    expect(replay).toHaveLength(1);
    expect(replay[0]?.type).toBe('STATE_SNAPSHOT');
    if (replay[0]?.type !== 'STATE_SNAPSHOT') {
      throw new Error('Expected replay snapshot event.');
    }

    expect(replay[0].sequenceId).toBe(2);
    expect(replay[0].snapshot.agents).toEqual([
      {
        id: 'dev-amelia',
        name: 'Amelia',
        role: 'agent',
        status: 'paused',
        roomId: 'build-room',
        position: { x: 8, y: 8 }
      }
    ]);
  });

  it('rejects task mutations when the active lease is missing or expired', async () => {
    const eventLogPath = await writeEventLog([
      JSON.stringify({
        id: 'evt-1',
        ts: '2026-03-06T09:00:01Z',
        agent: 'dev/Amelia',
        type: 'task_started',
        payload: { task_id: 'write-tests', description: 'Write tests' },
        seq: 1
      }),
      JSON.stringify({
        id: 'evt-2',
        ts: '2026-03-06T09:00:02Z',
        agent: 'qa/Quinn',
        type: 'routing',
        payload: { intent: 'QA check' },
        seq: 2
      })
    ]);
    const leaseStore = new LeaseStore({ ttlMs: 5_000 });
    leaseStore.claim({
      projectId: 'grimoire-game',
      runId: 'run-42',
      leaseId: 'lease-tests',
      taskId: 'write-tests',
      nodeId: 'node-alpha',
      workerId: 'worker-dev-1',
      worktreeId: 'wt-tests',
      branch: 'feature/write-tests',
      claimedAt: '2026-03-06T09:00:00Z'
    });

    const source = new FileSystemGrimoireRuntimeSource({ eventLogPath, leaseStore });

    await expect(
      source.applyTaskAssign({
        requestId: 'req-lease-missing',
        idempotencyKey: 'lease-missing',
        taskId: 'write-tests',
        assigneeId: 'qa-quinn',
        auth: {
          principalId: 'orch-1',
          role: 'orchestrator'
        }
      })
    ).rejects.toThrow('requires an active lease context');

    await expect(
      source.applyTaskAssign({
        requestId: 'req-lease-expired',
        idempotencyKey: 'lease-expired',
        taskId: 'write-tests',
        assigneeId: 'qa-quinn',
        leaseContext: {
          projectId: 'grimoire-game',
          runId: 'run-42',
          leaseId: 'lease-tests',
          nodeId: 'node-alpha',
          workerId: 'worker-dev-1',
          worktreeId: 'wt-tests',
          branch: 'feature/write-tests'
        },
        auth: {
          principalId: 'orch-1',
          role: 'orchestrator'
        }
      })
    ).rejects.toThrow('expired');
  });

  it('allows adapter task writes when the configured lease context matches active ownership', async () => {
    const eventLogPath = await writeEventLog([
      JSON.stringify({
        id: 'evt-1',
        ts: '2026-03-06T09:00:01Z',
        agent: 'dev/Amelia',
        type: 'task_started',
        payload: { task_id: 'write-tests', description: 'Write tests' },
        seq: 1
      }),
      JSON.stringify({
        id: 'evt-2',
        ts: '2026-03-06T09:00:02Z',
        agent: 'qa/Quinn',
        type: 'routing',
        payload: { intent: 'QA check' },
        seq: 2
      })
    ]);
    const leaseStore = new LeaseStore({ ttlMs: 60_000 });
    leaseStore.claim({
      projectId: 'grimoire-game',
      runId: 'run-42',
      leaseId: 'lease-tests-active',
      taskId: 'write-tests',
      nodeId: 'node-alpha',
      workerId: 'worker-dev-1',
      worktreeId: 'wt-tests',
      branch: 'feature/write-tests',
      claimedAt: new Date().toISOString()
    });

    const adapter = new AdapterGrimoire(
      new FileSystemGrimoireRuntimeSource({ eventLogPath, leaseStore }),
      {
        taskLeaseContext: {
          projectId: 'grimoire-game',
          runId: 'run-42',
          leaseId: 'lease-tests-active',
          nodeId: 'node-alpha',
          workerId: 'worker-dev-1',
          worktreeId: 'wt-tests',
          branch: 'feature/write-tests'
        }
      }
    );

    const updated = await adapter.handleClientEvent(
      createTaskAssign('req-lease-assign', 'write-tests', 'qa-quinn', 'lease-assign'),
      { principalId: 'orch-1', role: 'orchestrator' }
    );

    expect(updated).toHaveLength(1);
    expect(updated[0]?.type).toBe('STATE_SNAPSHOT');
    if (updated[0]?.type !== 'STATE_SNAPSHOT') {
      throw new Error('Expected task assign snapshot event.');
    }

    expect(updated[0].snapshot.tasks).toEqual([
      {
        id: 'write-tests',
        title: 'Write tests',
        status: 'in_progress',
        assigneeId: 'qa-quinn'
      }
    ]);
  });

  it('audits adapter rejections when the lease branch does not match active ownership', async () => {
    const eventLogPath = await writeEventLog([
      JSON.stringify({
        id: 'evt-1',
        ts: '2026-03-06T09:00:01Z',
        agent: 'dev/Amelia',
        type: 'task_started',
        payload: { task_id: 'write-tests', description: 'Write tests' },
        seq: 1
      }),
      JSON.stringify({
        id: 'evt-2',
        ts: '2026-03-06T09:00:02Z',
        agent: 'qa/Quinn',
        type: 'routing',
        payload: { intent: 'QA check' },
        seq: 2
      })
    ]);
    const leaseStore = new LeaseStore({ ttlMs: 60_000 });
    leaseStore.claim({
      projectId: 'grimoire-game',
      runId: 'run-42',
      leaseId: 'lease-tests-active',
      taskId: 'write-tests',
      nodeId: 'node-alpha',
      workerId: 'worker-dev-1',
      worktreeId: 'wt-tests',
      branch: 'feature/write-tests',
      claimedAt: new Date().toISOString()
    });

    const adapter = new AdapterGrimoire(
      new FileSystemGrimoireRuntimeSource({ eventLogPath, leaseStore }),
      {
        taskLeaseContext: {
          projectId: 'grimoire-game',
          runId: 'run-42',
          leaseId: 'lease-tests-active',
          nodeId: 'node-alpha',
          workerId: 'worker-dev-1',
          worktreeId: 'wt-tests',
          branch: 'feature/other-tests'
        }
      }
    );

    const updated = await adapter.handleClientEvent(
      createTaskAssign('req-lease-assign-branch-mismatch', 'write-tests', 'qa-quinn', 'lease-assign-branch-mismatch'),
      { principalId: 'orch-1', role: 'orchestrator' }
    );

    expect(updated).toHaveLength(1);
    expect(updated[0]?.type).toBe('ERROR');
    if (updated[0]?.type !== 'ERROR') {
      throw new Error('Expected runtime error event for ownership branch mismatch.');
    }

    expect(updated[0].code).toBe('RUNTIME_WRITE_FAILED');
    expect(
      adapter
        .getAuditLog()
        .some(
          (entry) =>
            entry.type === 'ERROR_EMITTED' &&
            entry.detail?.includes('does not match branch feature/other-tests')
        )
    ).toBe(true);
  });
});