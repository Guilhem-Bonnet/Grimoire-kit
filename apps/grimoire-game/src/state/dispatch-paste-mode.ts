/**
 * dispatch-paste-mode.ts — V2.6 fallback when the extension host bridge
 * is unavailable (no runSubagent, no HTTP endpoint reachable).
 *
 * Produces a ready-to-paste prompt that the operator can drop into
 * Copilot Chat (or any agent UI) so the dispatch survives a broken
 * bridge. The output is deterministic, human-readable and includes the
 * correlation id so the resulting task/start event can still be
 * threaded back to the original card once the bridge comes back.
 */

import type { CardDispatchRequest } from './kanban-dispatch';

export interface PasteModePrompt {
  text: string;
  clipboardPayload: string;
  lines: readonly string[];
  hash: string;
}

function stableHash(text: string): string {
  // FNV-1a 32-bit — not cryptographic, just a short deterministic tag
  // that lets the UI show "same prompt copied twice" without collisions.
  let hash = 0x811c9dc5;
  for (let i = 0; i < text.length; i += 1) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return hash.toString(16).padStart(8, '0');
}

/**
 * Build a paste-mode prompt for a validated dispatch request.
 *
 * Pure function. Callers are expected to invoke the clipboard API with
 * `result.clipboardPayload` and display `result.text` in the UI for the
 * operator to review before pasting elsewhere.
 */
export function buildPasteModePrompt(
  request: CardDispatchRequest
): PasteModePrompt {
  const lines: string[] = [
    `# Mission Board dispatch — paste mode`,
    `# card: ${request.cardId}`,
    `# role: ${request.targetRole}`,
    `# agent: ${request.targetAgentId}`,
    `# correlation: ${request.correlationId}`,
    `# complexity: ${request.complexity}`,
    `# plannedAt: ${request.plannedAt}`,
    ''
  ];
  if (request.actorId) {
    lines.splice(7, 0, `# actor: ${request.actorId}`);
  }
  lines.push(
    `@${request.targetAgentId} ${request.title.trim()}`,
    ''
  );
  const trimmedContext = request.promptContext.trim();
  if (trimmedContext) {
    lines.push('Context:');
    for (const ctxLine of trimmedContext.split('\n')) {
      lines.push(`> ${ctxLine}`);
    }
    lines.push('');
  }
  lines.push(
    `When you are done, emit an event with correlation_id=${request.correlationId}`,
    `so the Mission Board card resolves correctly.`
  );

  const text = lines.join('\n');
  return {
    text,
    clipboardPayload: text,
    lines,
    hash: stableHash(text)
  };
}
