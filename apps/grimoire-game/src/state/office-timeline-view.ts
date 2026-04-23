/**
 * office-timeline-view.ts — V3.S3.5 timeline scrubber pure projection.
 *
 * Given a chronological stream of HookEvents (typically the last N entries
 * read from ``_grimoire-runtime/_memory/activity.jsonl``), this module
 * exposes a renderer-agnostic timeline that can be scrubbed forward or
 * backward to replay the office state at any cursor position.
 *
 * Two layers:
 *   1. ``buildOfficeTimeline(events, options)`` sorts the events, caps
 *      them at ``maxEvents`` (most-recent suffix), and returns frame
 *      metadata (bounds, duration, per-frame timestamps).
 *   2. ``scrubOfficeTimeline(timeline, cursor, viewOptions)`` returns the
 *      OfficeView built from the events up to the cursor — by index, by
 *      timestamp, or by a normalised 0..1 ratio.
 *
 * Contract: pure (no DOM, no fs, no clock unless injected).
 */

import type { HookEvent } from '../contracts/hookEvents';
import {
  createOfficeView,
  type OfficeView,
  type OfficeViewOptions
} from './office-view';

export interface OfficeTimelineFrame {
  /** 0-based index in the timeline. */
  index: number;
  /** Event id at this frame. */
  eventId: string;
  /** ISO timestamp of the event. */
  ts: string;
  /** Hook scope, useful for renderer color-coding. */
  scope: HookEvent['scope'];
  phase: HookEvent['phase'];
  /** Optional agent id for this frame. */
  agentId: string | null;
}

export interface OfficeTimelineBounds {
  /** ISO timestamp of the first frame. */
  start: string;
  /** ISO timestamp of the last frame. */
  end: string;
  /** End - start in milliseconds (0 when only one frame, NaN if unparseable). */
  durationMs: number;
}

export interface OfficeTimeline {
  schemaVersion: string;
  /** Sorted, capped events backing the scrubber (most-recent suffix). */
  events: readonly HookEvent[];
  frames: readonly OfficeTimelineFrame[];
  bounds: OfficeTimelineBounds | null;
  empty: boolean;
}

export interface OfficeTimelineOptions {
  /** Cap the timeline to the most-recent N events. Default 30 (S3.5 spec). */
  maxEvents?: number;
}

export interface ScrubByIndex {
  index: number;
}
export interface ScrubByTs {
  ts: string;
}
export interface ScrubByRatio {
  ratio: number;
}
export type ScrubCursor = ScrubByIndex | ScrubByTs | ScrubByRatio;

export const OFFICE_TIMELINE_SCHEMA_VERSION = '1.0.0';
const DEFAULT_MAX_EVENTS = 30;

function sortChronological(events: readonly HookEvent[]): HookEvent[] {
  return events.slice().sort((a, b) => {
    if (a.ts < b.ts) return -1;
    if (a.ts > b.ts) return 1;
    return a.event_id < b.event_id ? -1 : a.event_id > b.event_id ? 1 : 0;
  });
}

function buildFrames(events: readonly HookEvent[]): OfficeTimelineFrame[] {
  return events.map((event, index) => ({
    index,
    eventId: event.event_id,
    ts: event.ts,
    scope: event.scope,
    phase: event.phase,
    agentId: event.agent?.id ?? null
  }));
}

function buildBounds(events: readonly HookEvent[]): OfficeTimelineBounds | null {
  if (events.length === 0) return null;
  const start = events[0]!.ts;
  const end = events[events.length - 1]!.ts;
  const startMs = Date.parse(start);
  const endMs = Date.parse(end);
  const durationMs =
    Number.isNaN(startMs) || Number.isNaN(endMs) ? Number.NaN : endMs - startMs;
  return { start, end, durationMs };
}

export function buildOfficeTimeline(
  events: readonly HookEvent[],
  options: OfficeTimelineOptions = {}
): OfficeTimeline {
  const maxEvents = options.maxEvents ?? DEFAULT_MAX_EVENTS;
  const sorted = sortChronological(events);
  const capped =
    maxEvents > 0 && sorted.length > maxEvents
      ? sorted.slice(sorted.length - maxEvents)
      : sorted;
  const frames = buildFrames(capped);
  return {
    schemaVersion: OFFICE_TIMELINE_SCHEMA_VERSION,
    events: capped,
    frames,
    bounds: buildBounds(capped),
    empty: capped.length === 0
  };
}

function clampIndex(index: number, length: number): number {
  if (length === 0) return -1;
  if (index < 0) return -1;
  if (index >= length) return length - 1;
  return index;
}

function resolveCursorIndex(timeline: OfficeTimeline, cursor: ScrubCursor): number {
  const length = timeline.events.length;
  if (length === 0) return -1;
  if ('index' in cursor) {
    return clampIndex(Math.floor(cursor.index), length);
  }
  if ('ratio' in cursor) {
    if (Number.isNaN(cursor.ratio)) return length - 1;
    const ratio = Math.max(0, Math.min(1, cursor.ratio));
    return clampIndex(Math.floor(ratio * (length - 1) + 0.0000001), length);
  }
  // ScrubByTs — find the highest index whose ts <= cursor.ts.
  let lo = 0;
  let hi = length - 1;
  let answer = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >>> 1;
    if (timeline.events[mid]!.ts <= cursor.ts) {
      answer = mid;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  return answer;
}

export interface OfficeScrubResult {
  /** Resolved frame index (-1 means "before the first frame"). */
  cursorIndex: number;
  /** Resolved frame, or null when cursor lands before the timeline. */
  frame: OfficeTimelineFrame | null;
  /** OfficeView rebuilt from events[0..cursorIndex]. */
  view: OfficeView;
}

export function scrubOfficeTimeline(
  timeline: OfficeTimeline,
  cursor: ScrubCursor,
  viewOptions: OfficeViewOptions = {}
): OfficeScrubResult {
  const cursorIndex = resolveCursorIndex(timeline, cursor);
  const slice =
    cursorIndex < 0 ? [] : timeline.events.slice(0, cursorIndex + 1);
  const view = createOfficeView(slice, viewOptions);
  const frame = cursorIndex >= 0 ? timeline.frames[cursorIndex]! : null;
  return { cursorIndex, frame, view };
}
