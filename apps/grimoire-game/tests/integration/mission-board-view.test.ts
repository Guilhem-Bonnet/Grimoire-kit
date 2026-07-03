import { createRuntimeViewsDemoData } from '../../examples/runtime-views-demo-data'

describe('mission board view', () => {
  it('projects the six rooms and a focus drawer from the blocked runtime scenario', () => {
    const demoData = createRuntimeViewsDemoData('blocked-guardrails')
    const scenario = demoData.scenarios.find((candidate) => candidate.id === 'blocked-guardrails')

    expect(scenario).toBeDefined()
    if (scenario === undefined) {
      return
    }

    const view = scenario.webViews.missionBoardView

    expect(view.header).toMatchObject({
      title: 'Mission Board',
      branch: 'feature/provenance-clean'
    })
    expect(view.rooms.map((room) => room.roomId)).toEqual([
      'intake-desk',
      'war-room',
      'workshop',
      'branch-finisher',
      'seance-archive',
      'watchtower'
    ])
    expect(view.rooms.find((room) => room.roomId === 'watchtower')).toMatchObject({
      tone: 'critical'
    })
    expect(view.cards.some((card) => card.roomId === 'branch-finisher')).toBe(true)
    expect(view.cards.some((card) => card.roomId === 'watchtower' && card.seal === 'NO-GO')).toBe(true)
    expect(view.drawer.sections.some((section) => section.id === 'acceptance')).toBe(true)
    expect(view.timeline.length).toBeGreaterThan(0)
  })
})