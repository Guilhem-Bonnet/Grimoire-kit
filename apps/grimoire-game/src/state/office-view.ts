/**
 * office-view.ts — V3.S3.1 + V3.S3.2 Office View pure projection.
 *
 * Renderer-agnostic state model that turns a stream of ``HookEvent`` (V1
 * event ledger, BM-59) into:
 *   - a fixed 16×12 office grid (S3.6, layout deferred to V5+)
 *   - one ``OfficeCharacter`` per active agent with a deterministic seat
 *   - a finite state machine per character: idle → walk → type → read → wait
 *   - parent/child links so a ``subagent/start`` event ties the spawned
 *     character to the master visually (S3.3)
 *
 * Contract: pure (no DOM, no fs, no clock unless injected via ``options.now``).
 * The renderer (Aurora cartoon, pixel-agents PNG, or anything else) receives
 * the resulting ``OfficeView`` and chooses how to draw each character —
 * appearance is intentionally decoupled from state.
 *
 * NOTE on activity decay: a character returns to ``idle`` after
 * ``options.idleAfterMs`` (default 8s) without any matching event.
 */

import type {
  HookEvent,
  HookEventPhase,
  HookEventScope
} from '../contracts/hookEvents';

export type OfficeCharacterState =
  | 'idle'
  | 'walk'
  | 'type'
  | 'read'
  | 'wait';

export interface OfficeSeat {
  /** 0-based column on the office grid. */
  col: number;
  /** 0-based row on the office grid. */
  row: number;
}

export interface OfficeCharacter {
  /** Stable agent id (matches HookEvent.agent.id). */
  agentId: string;
  /** Display role (e.g. "dev", "qa"); defaults to ``agentId`` when absent. */
  role: string;
  state: OfficeCharacterState;
  seat: OfficeSeat;
  /** Parent agent id for sub-agents (set by subagent/start). */
  parent: string | null;
  /** ISO timestamp of the last event that touched this character. */
  lastEventTs: string;
  /** Last event that updated this character (id only — payload not retained). */
  lastEventId: string;
  /** Last scope/phase pair that updated this character. */
  lastEventKind: string;
  /** True while the character is participating in a sub-agent fan-out. */
  isMaster: boolean;
}

export interface OfficeGrid {
  cols: number;
  rows: number;
}

export interface OfficeViewOptions {
  /**
   * Office grid size. Defaults to the 16×12 minimum layout (S3.6).
   * Renderers may scale visually but the pure projection always uses
   * ``cols × rows`` cells.
   */
  grid?: OfficeGrid;
  /** Reference time for idle decay; ISO-8601. Required when ``idleAfterMs`` > 0. */
  now?: string;
  /** ms without an event before a character drops back to ``idle``. */
  idleAfterMs?: number;
}

export interface OfficeView {
  schemaVersion: string;
  grid: OfficeGrid;
  characters: readonly OfficeCharacter[];
  /** Per-state count, useful for debug/HUD overlays. */
  stateCounters: Readonly<Record<OfficeCharacterState, number>>;
  /** True when no character is present. */
  empty: boolean;
}

export const OFFICE_VIEW_SCHEMA_VERSION = '1.0.0';
export const DEFAULT_OFFICE_GRID: OfficeGrid = { cols: 16, rows: 12 };
const DEFAULT_IDLE_AFTER_MS = 8_000;

const STATE_RANK: Record<OfficeCharacterState, number> = {
  walk: 0,
  type: 1,
  read: 2,
  wait: 3,
  idle: 4
};

/**
 * Map a (scope, phase) pair to a character state.
 * Returns null when the event must not transition the FSM (e.g. session start).
 */
export function mapEventToState(
  scope: HookEventScope,
  phase: HookEventPhase
): OfficeCharacterState | null {
  if (scope === 'tool') {
    if (phase === 'start') return 'type';
    if (phase === 'end') return 'idle';
    if (phase === 'block' || phase === 'correct') return 'wait';
    return null;
  }
  if (scope === 'subagent') {
    if (phase === 'start') return 'walk';
    if (phase === 'end') return 'idle';
    return null;
  }
  if (scope === 'prompt') {
    if (phase === 'start') return 'read';
    if (phase === 'end') return 'idle';
    return null;
  }
  if (scope === 'task') {
    if (phase === 'start') return 'walk';
    if (phase === 'end') return 'idle';
    if (phase === 'block') return 'wait';
    return null;
  }
  if (scope === 'compact' || scope === 'stop') {
    if (phase === 'start' || phase === 'info') return 'wait';
    if (phase === 'end') return 'idle';
    return null;
  }
  if (scope === 'anomaly') {
    return 'wait';
  }
  return null;
}

/**
 * Deterministic seat assignment from agent id; spreads agents across the
 * grid via two coprime hash strides so adjacent ids don't collide visually.
 */
export function assignSeat(agentId: string, grid: OfficeGrid): OfficeSeat {
  let hash = 2166136261;
  for (let i = 0; i < agentId.length; i += 1) {
    hash ^= agentId.charCodeAt(i);
    hash = Math.imul(hash, 16777619) >>> 0;
  }
  const col = hash % grid.cols;
  const row = Math.floor(hash / grid.cols) % grid.rows;
  return { col, row };
}

interface MutableCharacter extends OfficeCharacter {
  state: OfficeCharacterState;
  parent: string | null;
  lastEventTs: string;
  lastEventId: string;
  lastEventKind: string;
  isMaster: boolean;
}

function emptyStateCounters(): Record<OfficeCharacterState, number> {
  return { idle: 0, walk: 0, type: 0, read: 0, wait: 0 };
}

function ensureCharacter(
  registry: Map<string, MutableCharacter>,
  agentId: string,
  role: string | undefined,
  grid: OfficeGrid,
  ts: string
): MutableCharacter {
  const existing = registry.get(agentId);
  if (existing) {
    return existing;
  }
  const created: MutableCharacter = {
    agentId,
    role: role && role.length > 0 ? role : agentId,
    state: 'idle',
    seat: assignSeat(agentId, grid),
    parent: null,
    lastEventTs: ts,
    lastEventId: '',
    lastEventKind: '',
    isMaster: false
  };
  registry.set(agentId, created);
  return created;
}

function applyEvent(
  registry: Map<string, MutableCharacter>,
  event: HookEvent,
  grid: OfficeGrid
): void {
  const agentId = event.agent?.id;
  if (!agentId) {
    return;
  }
  const character = ensureCharacter(
    registry,
    agentId,
    event.agent?.role,
    grid,
    event.ts
  );
  const role = event.agent?.role;
  if (role && role.length > 0) {
    character.role = role;
  }

  // S3.3 — sub-agent visualization: subagent/start ties the new character to
  // the master agent (taken from ``agent.parent`` when provided, else from
  // ``correlation_id``).
  if (event.scope === 'subagent' && event.phase === 'start') {
    const parent = event.agent?.parent ?? event.correlation_id ?? null;
    if (parent && parent !== agentId) {
      character.parent = parent;
      const master = ensureCharacter(registry, parent, undefined, grid, event.ts);
      master.isMaster = true;
    }
  }
  if (event.scope === 'subagent' && event.phase === 'end') {
    if (character.parent) {
      const master = registry.get(character.parent);
      if (master && !hasOtherChildren(registry, master.agentId, agentId)) {
        master.isMaster = false;
      }
    }
  }

  const next = mapEventToState(event.scope, event.phase);
  if (next !== null) {
    character.state = next;
  }
  character.lastEventTs = event.ts;
  character.lastEventId = event.event_id;
  character.lastEventKind = `${event.scope}/${event.phase}`;
}

function hasOtherChildren(
  registry: Map<string, MutableCharacter>,
  parentId: string,
  excludeChildId: string
): boolean {
  for (const c of registry.values()) {
    if (c.agentId !== excludeChildId && c.parent === parentId) {
      return true;
    }
  }
  return false;
}

function applyIdleDecay(
  registry: Map<string, MutableCharacter>,
  nowIso: string,
  idleAfterMs: number
): void {
  if (idleAfterMs <= 0) return;
  const nowMs = Date.parse(nowIso);
  if (Number.isNaN(nowMs)) return;
  for (const character of registry.values()) {
    if (character.state === 'idle') continue;
    const lastMs = Date.parse(character.lastEventTs);
    if (Number.isNaN(lastMs)) continue;
    if (nowMs - lastMs >= idleAfterMs) {
      character.state = 'idle';
    }
  }
}

function sortCharacters(chars: MutableCharacter[]): OfficeCharacter[] {
  return chars.slice().sort((a, b) => {
    const r = STATE_RANK[a.state] - STATE_RANK[b.state];
    if (r !== 0) return r;
    if (b.lastEventTs < a.lastEventTs) return -1;
    if (b.lastEventTs > a.lastEventTs) return 1;
    return a.agentId < b.agentId ? -1 : a.agentId > b.agentId ? 1 : 0;
  });
}

/**
 * Build an OfficeView from a chronological stream of HookEvents. Events are
 * applied in input order — the caller must pre-sort if the source is mixed.
 */
export function createOfficeView(
  events: readonly HookEvent[],
  options: OfficeViewOptions = {}
): OfficeView {
  const grid = options.grid ?? DEFAULT_OFFICE_GRID;
  const idleAfterMs = options.idleAfterMs ?? DEFAULT_IDLE_AFTER_MS;
  const registry = new Map<string, MutableCharacter>();

  for (const event of events) {
    applyEvent(registry, event, grid);
  }

  if (options.now !== undefined) {
    applyIdleDecay(registry, options.now, idleAfterMs);
  }

  const ordered = sortCharacters(Array.from(registry.values()));
  const counters = emptyStateCounters();
  for (const c of ordered) {
    counters[c.state] += 1;
  }

  return {
    schemaVersion: OFFICE_VIEW_SCHEMA_VERSION,
    grid,
    characters: ordered,
    stateCounters: counters,
    empty: ordered.length === 0
  };
}
