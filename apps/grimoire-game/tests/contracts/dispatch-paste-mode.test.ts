import { describe, expect, it } from 'vitest';

import { buildPasteModePrompt } from '../../src/state/dispatch-paste-mode';
import type { CardDispatchRequest } from '../../src/state/kanban-dispatch';

const REQUEST: CardDispatchRequest = {
  cardId: 'card-42',
  correlationId: 'corr-abc',
  targetRole: 'coder',
  targetAgentId: 'dev',
  title: '  Wire dispatch  ',
  promptContext: 'Read V2.6 of the plan\nCheck paste mode fallback',
  complexity: 'medium',
  actorId: 'guilhem',
  plannedAt: '2026-04-22T12:30:00.000Z'
};

describe('buildPasteModePrompt', () => {
  it('includes the mandatory headers', () => {
    const prompt = buildPasteModePrompt(REQUEST);
    expect(prompt.text).toContain('# card: card-42');
    expect(prompt.text).toContain('# role: coder');
    expect(prompt.text).toContain('# agent: dev');
    expect(prompt.text).toContain('# correlation: corr-abc');
    expect(prompt.text).toContain('# complexity: medium');
    expect(prompt.text).toContain('# actor: guilhem');
    expect(prompt.text).toContain('# plannedAt: 2026-04-22T12:30:00.000Z');
  });

  it('addresses the target agent and trims the title', () => {
    const prompt = buildPasteModePrompt(REQUEST);
    expect(prompt.text).toContain('@dev Wire dispatch');
  });

  it('quotes the prompt context line by line', () => {
    const prompt = buildPasteModePrompt(REQUEST);
    expect(prompt.text).toContain('> Read V2.6 of the plan');
    expect(prompt.text).toContain('> Check paste mode fallback');
  });

  it('omits the actor line when actorId is null', () => {
    const prompt = buildPasteModePrompt({ ...REQUEST, actorId: null });
    expect(prompt.text).not.toContain('# actor:');
  });

  it('omits the context block when context is blank', () => {
    const prompt = buildPasteModePrompt({ ...REQUEST, promptContext: '   ' });
    expect(prompt.text).not.toContain('Context:');
  });

  it('always emits the correlation reminder tail', () => {
    const prompt = buildPasteModePrompt(REQUEST);
    expect(prompt.text).toMatch(/correlation_id=corr-abc/);
  });

  it('produces a stable hash for identical prompts', () => {
    const a = buildPasteModePrompt(REQUEST);
    const b = buildPasteModePrompt(REQUEST);
    expect(a.hash).toBe(b.hash);
    expect(a.hash).toHaveLength(8);
  });

  it('produces different hashes for different titles', () => {
    const a = buildPasteModePrompt(REQUEST);
    const b = buildPasteModePrompt({ ...REQUEST, title: 'Other title' });
    expect(a.hash).not.toBe(b.hash);
  });

  it('clipboardPayload equals text', () => {
    const prompt = buildPasteModePrompt(REQUEST);
    expect(prompt.clipboardPayload).toBe(prompt.text);
  });
});
