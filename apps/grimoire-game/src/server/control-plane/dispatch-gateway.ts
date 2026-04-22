/**
 * dispatch-gateway.ts — V2.3 server-side bridge between Mission Board and
 * the canonical hook ledger.
 *
 * Takes a validated `CardDispatchRequest` (produced by the pure planner
 * in `state/kanban-dispatch.ts`) and emits a canonical `task/start`
 * HookEvent via an injected emitter. The real persistence (jsonl append,
 * Copilot runSubagent invocation) is out of scope here — those hooks are
 * provided by the extension host bridge.
 *
 * The module is deliberately pure: no fs, no process, no singleton. It
 * can be exercised in tests with a recording emitter and clock.
 */

import {
  HOOK_EVENT_SCHEMA_VERSION,
  type HookEvent
} from '../../contracts/hookEvents';
import type { CardDispatchRequest } from '../../state/kanban-dispatch';

export const DISPATCH_SOURCE_HOOK = 'mission-board/dispatch-gateway';

export type DispatchEventEmitter = (event: HookEvent) => void;

export interface DispatchGatewayOptions {
  /** Emitter used to persist / broadcast the event. Defaults to noop. */
  emit?: DispatchEventEmitter;
  /** DI for tests — defaults to crypto.randomUUID. */
  eventIdFactory?: () => string;
  /** DI for tests — defaults to new Date().toISOString(). */
  clock?: () => string;
}

export interface DispatchGatewayResult {
  accepted: boolean;
  reason: string;
  event: HookEvent;
}

function defaultEventIdFactory(): string {
  return globalThis.crypto.randomUUID();
}

function defaultClock(): string {
  return new Date().toISOString();
}

/**
 * Build the canonical `task/start` event for a dispatch request. Pure:
 * no emission, no side effect. Useful for snapshot tests and for the
 * host bridge that wants to decorate the event before persisting.
 */
export function buildDispatchStartEvent(
  request: CardDispatchRequest,
  options: Pick<DispatchGatewayOptions, 'eventIdFactory' | 'clock'> = {}
): HookEvent {
  const eventIdFactory = options.eventIdFactory ?? defaultEventIdFactory;
  const clock = options.clock ?? defaultClock;
  return {
    schema_version: HOOK_EVENT_SCHEMA_VERSION,
    event_id: eventIdFactory(),
    ts: clock(),
    scope: 'task',
    phase: 'start',
    source_hook: DISPATCH_SOURCE_HOOK,
    agent: {
      id: request.targetAgentId,
      role: request.targetRole
    },
    correlation_id: request.correlationId,
    payload: {
      cardId: request.cardId,
      title: request.title,
      complexity: request.complexity,
      actorId: request.actorId,
      promptContext: request.promptContext,
      plannedAt: request.plannedAt
    }
  };
}

/**
 * Emit a dispatch start event through the provided emitter. The emitter
 * is trusted to persist/broadcast; the gateway only guarantees that the
 * event is well-formed and records an acknowledgement.
 *
 * Fail-open: if the emitter throws, the error is captured in the result
 * so the UI can surface it without crashing the drop handler.
 */
export function dispatchCardStart(
  request: CardDispatchRequest,
  options: DispatchGatewayOptions = {}
): DispatchGatewayResult {
  const event = buildDispatchStartEvent(request, options);
  const emit = options.emit;
  if (!emit) {
    return {
      accepted: true,
      reason: 'event built (no emitter configured)',
      event
    };
  }
  try {
    emit(event);
    return {
      accepted: true,
      reason: `task/start emitted for card ${request.cardId} → ${request.targetAgentId}`,
      event
    };
  } catch (error) {
    return {
      accepted: false,
      reason: `emitter failed: ${error instanceof Error ? error.message : String(error)}`,
      event
    };
  }
}
