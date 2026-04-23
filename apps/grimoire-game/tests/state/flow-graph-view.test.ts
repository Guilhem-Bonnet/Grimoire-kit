import { describe, it, expect } from 'vitest';
import type { HookEvent } from '../../src/contracts/hookEvents';
import {
  buildFlowGraphView,
  FLOW_GRAPH_VIEW_SCHEMA_VERSION
} from '../../src/state/flow-graph-view';

function evt(partial: Partial<HookEvent>): HookEvent {
  return {
    schema_version: '1.0',
    event_id: partial.event_id ?? 'e',
    ts: partial.ts ?? '2026-04-23T10:00:00.000Z',
    scope: partial.scope ?? 'tool',
    phase: partial.phase ?? 'start',
    source_hook: partial.source_hook ?? 'h',
    agent: partial.agent ?? null,
    correlation_id: partial.correlation_id ?? null,
    payload: partial.payload ?? {}
  };
}

describe('flow-graph-view', () => {
  it('groups events by correlation_id', () => {
    const view = buildFlowGraphView([
      evt({ event_id: 'a', correlation_id: 'c1', ts: '2026-04-23T10:00:00.000Z' }),
      evt({ event_id: 'b', correlation_id: 'c1', ts: '2026-04-23T10:00:01.000Z' }),
      evt({ event_id: 'c', correlation_id: 'c2', ts: '2026-04-23T10:00:02.000Z' })
    ]);
    expect(view.schemaVersion).toBe(FLOW_GRAPH_VIEW_SCHEMA_VERSION);
    expect(view.flows).toHaveLength(2);
    const c1 = view.flows.find((f) => f.correlationId === 'c1');
    expect(c1?.nodes).toHaveLength(2);
  });

  it('sorts flow nodes chronologically', () => {
    const view = buildFlowGraphView([
      evt({ event_id: 'b', correlation_id: 'c', ts: '2026-04-23T10:00:02.000Z' }),
      evt({ event_id: 'a', correlation_id: 'c', ts: '2026-04-23T10:00:01.000Z' })
    ]);
    const nodes = view.flows[0]!.nodes;
    expect(nodes.map((n) => n.eventId)).toEqual(['a', 'b']);
  });

  it('computes durationMs and first/last ts', () => {
    const view = buildFlowGraphView([
      evt({ correlation_id: 'c', ts: '2026-04-23T10:00:00.000Z' }),
      evt({ correlation_id: 'c', ts: '2026-04-23T10:00:05.000Z' })
    ]);
    expect(view.flows[0]!.durationMs).toBe(5000);
    expect(view.flows[0]!.firstTs).toBe('2026-04-23T10:00:00.000Z');
    expect(view.flows[0]!.lastTs).toBe('2026-04-23T10:00:05.000Z');
  });

  it('deduplicates agents and scopes', () => {
    const view = buildFlowGraphView([
      evt({ correlation_id: 'c', agent: { id: 'dev', role: 'dev' }, scope: 'tool' }),
      evt({ correlation_id: 'c', agent: { id: 'dev', role: 'dev' }, scope: 'tool' }),
      evt({ correlation_id: 'c', agent: { id: 'qa', role: 'qa' }, scope: 'subagent' })
    ]);
    expect(view.flows[0]!.agents).toEqual(['dev', 'qa']);
    expect([...view.flows[0]!.scopes].sort()).toEqual(['subagent', 'tool']);
  });

  it('counts phases', () => {
    const view = buildFlowGraphView([
      evt({ correlation_id: 'c', phase: 'start' }),
      evt({ correlation_id: 'c', phase: 'start' }),
      evt({ correlation_id: 'c', phase: 'end' })
    ]);
    expect(view.flows[0]!.phaseCounters).toEqual({ start: 2, end: 1 });
  });

  it('separates orphans (no correlation_id)', () => {
    const view = buildFlowGraphView([
      evt({ event_id: 'x', correlation_id: null }),
      evt({ event_id: 'y', correlation_id: 'c' })
    ]);
    expect(view.orphans).toHaveLength(1);
    expect(view.orphans[0]!.eventId).toBe('x');
    expect(view.flows).toHaveLength(1);
  });

  it('respects limit (most-recent flows)', () => {
    const view = buildFlowGraphView(
      [
        evt({ correlation_id: 'old', ts: '2026-04-23T10:00:00.000Z' }),
        evt({ correlation_id: 'mid', ts: '2026-04-23T10:05:00.000Z' }),
        evt({ correlation_id: 'new', ts: '2026-04-23T10:10:00.000Z' })
      ],
      { limit: 2 }
    );
    expect(view.flows.map((f) => f.correlationId)).toEqual(['new', 'mid']);
  });

  it('drops flows below minSize', () => {
    const view = buildFlowGraphView(
      [
        evt({ correlation_id: 'a' }),
        evt({ correlation_id: 'b' }),
        evt({ correlation_id: 'b' })
      ],
      { minSize: 2 }
    );
    expect(view.flows).toHaveLength(1);
    expect(view.flows[0]!.correlationId).toBe('b');
  });
});
