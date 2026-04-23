import { describe, it, expect } from 'vitest';
import type { HookEvent } from '../../src/contracts/hookEvents';
import { createOfficeView } from '../../src/state/office-view';
import { resolveOfficePlacement } from '../../src/state/office-placement';
import {
  buildOfficeDrawPlan,
  drawPlanForAgent,
  drawPlanOpsByAgent,
  OFFICE_DRAW_PLAN_SCHEMA_VERSION
} from '../../src/rendering/office-draw-plan';
import {
  createSpriteRegistry,
  type SpriteRegistryInput
} from '../../src/rendering/sprite-registry';

function makeEvent(agentId: string, role: string): HookEvent {
  return {
    schema_version: '1.0',
    event_id: `evt-${agentId}`,
    ts: '2026-04-23T10:00:00.000Z',
    scope: 'subagent',
    phase: 'start',
    source_hook: 'test',
    agent: { id: agentId, role },
    payload: {}
  };
}

function registryFixture(includeQa = true): SpriteRegistryInput {
  const roles: Record<string, SpriteRegistryInput['defaultRole']> = {
    dev: {
      base: { sheetId: 'base', col: 0, row: 0 },
      cosmetics: [
        { sheetId: 'base', col: 1, row: 0 },
        { sheetId: 'base', col: 2, row: 0 }
      ]
    }
  };
  if (includeQa) {
    roles.qa = {
      base: { sheetId: 'base', col: 3, row: 0 }
    };
  }
  return {
    sheets: [
      { id: 'base', url: 'b.png', frameWidth: 16, frameHeight: 32, cols: 4, rows: 2 }
    ],
    roles,
    defaultRole: { base: { sheetId: 'base', col: 0, row: 1 } }
  };
}

describe('office-draw-plan', () => {
  it('returns an empty plan when office is empty', () => {
    const office = createOfficeView([]);
    const placement = resolveOfficePlacement(office.characters, office.grid);
    const plan = buildOfficeDrawPlan(office, placement, createSpriteRegistry(registryFixture()));
    expect(plan.schemaVersion).toBe(OFFICE_DRAW_PLAN_SCHEMA_VERSION);
    expect(plan.ops).toHaveLength(0);
    expect(plan.missingAgents).toHaveLength(0);
  });

  it('emits base + cosmetic ops per character', () => {
    const events = [makeEvent('dev-1', 'dev'), makeEvent('qa-1', 'qa')];
    const office = createOfficeView(events);
    const placement = resolveOfficePlacement(office.characters, office.grid);
    const registry = createSpriteRegistry(registryFixture());
    const plan = buildOfficeDrawPlan(office, placement, registry);

    const byAgent = drawPlanOpsByAgent(plan);
    expect(byAgent.get('dev-1')).toBe(3); // base + 2 cosmetics
    expect(byAgent.get('qa-1')).toBe(1); // base only
  });

  it('sorts ops by (y, x, agentId, layer)', () => {
    const events = [makeEvent('a-dev', 'dev'), makeEvent('b-dev', 'dev')];
    const office = createOfficeView(events);
    const placement = resolveOfficePlacement(office.characters, office.grid);
    const registry = createSpriteRegistry(registryFixture());
    const plan = buildOfficeDrawPlan(office, placement, registry, { cellSize: 16 });

    for (let i = 1; i < plan.ops.length; i += 1) {
      const a = plan.ops[i - 1]!;
      const b = plan.ops[i]!;
      const aKey = `${a.y.toString().padStart(6, '0')}|${a.x.toString().padStart(6, '0')}|${a.agentId}|${a.layer}`;
      const bKey = `${b.y.toString().padStart(6, '0')}|${b.x.toString().padStart(6, '0')}|${b.agentId}|${b.layer}`;
      expect(aKey <= bKey).toBe(true);
    }
  });

  it('uses the default role when role is unknown', () => {
    const events = [makeEvent('ghost', 'phantom-role')];
    const office = createOfficeView(events);
    const placement = resolveOfficePlacement(office.characters, office.grid);
    const registry = createSpriteRegistry(registryFixture());
    const plan = buildOfficeDrawPlan(office, placement, registry);
    expect(plan.ops).toHaveLength(1);
    expect(plan.ops[0]!.frame.row).toBe(1); // default role's base
    expect(plan.missingAgents).toHaveLength(0);
  });

  it('applies origin offsets', () => {
    const events = [makeEvent('dev-1', 'dev')];
    const office = createOfficeView(events);
    const placement = resolveOfficePlacement(office.characters, office.grid);
    const registry = createSpriteRegistry(registryFixture(false));
    const plan = buildOfficeDrawPlan(office, placement, registry, {
      cellSize: 16,
      originX: 100,
      originY: 200
    });
    const ops = drawPlanForAgent(plan, 'dev-1');
    expect(ops[0]!.x).toBeGreaterThanOrEqual(100);
    expect(ops[0]!.y).toBeGreaterThanOrEqual(200);
  });

  it('is deterministic across calls', () => {
    const events = [makeEvent('dev-z', 'dev'), makeEvent('dev-a', 'dev')];
    const office = createOfficeView(events);
    const placement = resolveOfficePlacement(office.characters, office.grid);
    const registry = createSpriteRegistry(registryFixture());
    const plan1 = buildOfficeDrawPlan(office, placement, registry);
    const plan2 = buildOfficeDrawPlan(office, placement, registry);
    expect(JSON.stringify(plan1.ops.map((o) => ({ x: o.x, y: o.y, a: o.agentId, l: o.layer }))))
      .toBe(JSON.stringify(plan2.ops.map((o) => ({ x: o.x, y: o.y, a: o.agentId, l: o.layer }))));
  });

  it('scales frame dims when fitSquare is true', () => {
    const events = [makeEvent('dev-1', 'dev')];
    const office = createOfficeView(events);
    const placement = resolveOfficePlacement(office.characters, office.grid);
    const registry = createSpriteRegistry(registryFixture(false));
    const plan = buildOfficeDrawPlan(office, placement, registry, {
      cellSize: 24,
      fitSquare: true
    });
    for (const op of plan.ops) {
      expect(op.width).toBe(24);
      expect(op.height).toBe(24);
    }
  });

  it('preserves aspect ratio by default', () => {
    const events = [makeEvent('dev-1', 'dev')];
    const office = createOfficeView(events);
    const placement = resolveOfficePlacement(office.characters, office.grid);
    const registry = createSpriteRegistry(registryFixture(false));
    const plan = buildOfficeDrawPlan(office, placement, registry, { cellSize: 16 });
    // frame 16×32 → width 16, height 32
    expect(plan.ops[0]!.width).toBe(16);
    expect(plan.ops[0]!.height).toBe(32);
  });
});
