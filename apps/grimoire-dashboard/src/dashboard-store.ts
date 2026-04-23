/**
 * dashboard-store.ts — live state store for the dashboard.
 *
 * Consumes WebSocket messages (`hello`, `snapshot`, `event`, `error`),
 * validates each event against the HookEvent schema (@game/contracts/hookEvents)
 * and re-derives:
 *   - OfficeView + OfficePlacement (pixel agents surface)
 *   - OfficeTimeline + OfficeDebugPanelView (observability surface)
 *   - a ring buffer of the last N raw events (kanban/console surfaces)
 *
 * Subscribers are notified whenever the derived state changes.
 * Pure of DOM; renderers pull snapshots via `getState()` or subscribe
 * via `subscribe(listener)`.
 */

import { HookEventSchema, type HookEvent } from '@game/contracts/hookEvents';
import {
  createOfficeView,
  type OfficeView
} from '@game/state/office-view';
import {
  resolveOfficePlacement,
  type OfficePlacement
} from '@game/state/office-placement';
import {
  buildOfficeTimeline,
  type OfficeTimeline
} from '@game/state/office-timeline-view';
import {
  createOfficeDebugPanelView,
  type OfficeDebugPanelView
} from '@game/state/office-debug-panel-view';
import {
  buildFlowGraphView,
  type FlowGraphView
} from '@game/state/flow-graph-view';

import type { ConnectionState } from './dashboard-ws-client';
import type { ServerMessage } from './contracts/wsProtocol';

export interface DashboardStateSnapshot {
  connection: ConnectionState;
  connectionDetail: string | null;
  source: string | null;
  events: readonly HookEvent[];
  office: OfficeView;
  placement: OfficePlacement;
  timeline: OfficeTimeline;
  debugPanel: OfficeDebugPanelView;
  flowGraph: FlowGraphView;
  lastError: string | null;
  receivedCount: number;
  droppedCount: number;
}

export interface DashboardStoreOptions {
  /** Max events kept in the ring buffer. Default 500. */
  bufferSize?: number;
}

type Listener = (snapshot: DashboardStateSnapshot) => void;

const DEFAULT_BUFFER = 500;

function emptySnapshot(): DashboardStateSnapshot {
  const office = createOfficeView([]);
  return {
    connection: 'idle',
    connectionDetail: null,
    source: null,
    events: [],
    office,
    placement: resolveOfficePlacement(office.characters, office.grid),
    timeline: buildOfficeTimeline([]),
    debugPanel: createOfficeDebugPanelView([]),
    flowGraph: buildFlowGraphView([]),
    lastError: null,
    receivedCount: 0,
    droppedCount: 0
  };
}

export class DashboardStore {
  private readonly bufferSize: number;
  private snapshot: DashboardStateSnapshot = emptySnapshot();
  private readonly listeners = new Set<Listener>();
  private previousPlacement: OfficePlacement | null = null;

  constructor(options: DashboardStoreOptions = {}) {
    this.bufferSize = options.bufferSize ?? DEFAULT_BUFFER;
  }

  getState(): DashboardStateSnapshot {
    return this.snapshot;
  }

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener);
    listener(this.snapshot);
    return () => this.listeners.delete(listener);
  }

  onConnectionState(state: ConnectionState, detail?: string): void {
    this.snapshot = {
      ...this.snapshot,
      connection: state,
      connectionDetail: detail ?? null
    };
    this.emit();
  }

  onServerMessage(message: ServerMessage): void {
    switch (message.type) {
      case 'hello':
        this.snapshot = { ...this.snapshot, source: message.source };
        this.emit();
        return;
      case 'snapshot': {
        const events = this.parseBatch(message.events);
        this.replaceEvents(events);
        return;
      }
      case 'event': {
        const parsed = this.parseBatch([message.event]);
        if (parsed.length === 0) {
          // parseBatch already counted the drop.
          this.emit();
          return;
        }
        this.appendEvents(parsed);
        return;
      }
      case 'error':
        this.snapshot = {
          ...this.snapshot,
          lastError: `${message.code}: ${message.message}`
        };
        this.emit();
        return;
    }
  }

  private parseBatch(raw: readonly Record<string, unknown>[]): HookEvent[] {
    const out: HookEvent[] = [];
    for (const entry of raw) {
      const parsed = HookEventSchema.safeParse(entry);
      if (parsed.success) {
        out.push(parsed.data);
      } else {
        this.snapshot = { ...this.snapshot, droppedCount: this.snapshot.droppedCount + 1 };
      }
    }
    return out;
  }

  private replaceEvents(events: HookEvent[]): void {
    const trimmed = events.slice(-this.bufferSize);
    this.snapshot = this.project({
      ...this.snapshot,
      events: trimmed,
      receivedCount: this.snapshot.receivedCount + events.length
    });
    this.emit();
  }

  private appendEvents(events: HookEvent[]): void {
    const merged = [...this.snapshot.events, ...events];
    const trimmed = merged.length > this.bufferSize ? merged.slice(-this.bufferSize) : merged;
    this.snapshot = this.project({
      ...this.snapshot,
      events: trimmed,
      receivedCount: this.snapshot.receivedCount + events.length
    });
    this.emit();
  }

  private project(partial: DashboardStateSnapshot): DashboardStateSnapshot {
    const office = createOfficeView(partial.events);
    const placement = resolveOfficePlacement(
      office.characters,
      office.grid,
      this.previousPlacement ? { previous: this.previousPlacement } : {}
    );
    this.previousPlacement = placement;
    const timeline = buildOfficeTimeline(partial.events);
    const debugPanel = createOfficeDebugPanelView(partial.events);
    const flowGraph = buildFlowGraphView(partial.events, { limit: 50 });
    return {
      ...partial,
      office,
      placement,
      timeline,
      debugPanel,
      flowGraph
    };
  }

  private emit(): void {
    for (const listener of this.listeners) listener(this.snapshot);
  }
}
