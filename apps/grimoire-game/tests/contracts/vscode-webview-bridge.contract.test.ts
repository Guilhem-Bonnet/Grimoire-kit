import {
  createVsCodePanelCommandMessage,
  createVsCodePanelReadyMessage,
  VsCodePanelCommandMessageSchema,
  VsCodePanelReadyMessageSchema
} from '../../src/bridge/vscode-webview-bridge';

describe('vscode webview bridge contract', () => {
  it('accepts a ready message with persisted browser state', () => {
    const message = createVsCodePanelReadyMessage(
      {
        scenarioId: 'release-ready',
        filter: 'attention',
        mode: 'host-bridge'
      },
      'vscode-webview'
    );

    expect(message).toMatchObject({
      type: 'grimoire.vscode-panel.ready',
      transport: 'vscode-webview',
      state: {
        scenarioId: 'release-ready',
        filter: 'attention',
        mode: 'host-bridge'
      }
    });
  });

  it('accepts read-only command payloads for host routing', () => {
    const message = createVsCodePanelCommandMessage({
      command: 'open.verification',
      verificationRef: 'verify://task-auth/review'
    });

    expect(message).toMatchObject({
      type: 'grimoire.vscode-panel.command',
      payload: {
        command: 'open.verification',
        verificationRef: 'verify://task-auth/review'
      }
    });
  });

  it('rejects focus commands without their bounded target', () => {
    expect(() =>
      VsCodePanelCommandMessageSchema.parse({
        type: 'grimoire.vscode-panel.command',
        protocolVersion: 'v1',
        payload: {
          command: 'focus.task'
        }
      })
    ).toThrow();
  });

  it('rejects invalid persisted state for the webview', () => {
    expect(() =>
      VsCodePanelReadyMessageSchema.parse({
        type: 'grimoire.vscode-panel.ready',
        protocolVersion: 'v1',
        transport: 'browser-fallback',
        state: {
          scenarioId: '',
          filter: 'attention',
          mode: 'vscode'
        }
      })
    ).toThrow();
  });

  it('accepts the extended shell modes in persisted state', () => {
    const missionBoardReady = createVsCodePanelReadyMessage(
      {
        scenarioId: 'blocked-guardrails',
        filter: 'all',
        mode: 'mission-board'
      },
      'browser-fallback'
    )

    const observabilityReady = createVsCodePanelReadyMessage(
      {
        scenarioId: 'release-ready',
        filter: 'all',
        mode: 'observability'
      },
      'browser-fallback'
    );

    const gameUiReady = createVsCodePanelReadyMessage(
      {
        scenarioId: 'release-ready',
        filter: 'attention',
        mode: 'game-ui'
      },
      'vscode-webview'
    );

    const kernelReady = createVsCodePanelReadyMessage(
      {
        scenarioId: 'release-ready',
        filter: 'attention',
        mode: 'kernel'
      },
      'browser-fallback'
    );

    const proofsReady = createVsCodePanelReadyMessage(
      {
        scenarioId: 'release-ready',
        filter: 'blocked',
        mode: 'proofs'
      },
      'vscode-webview'
    );

    expect(missionBoardReady.state.mode).toBe('mission-board')
    expect(observabilityReady.state.mode).toBe('observability');
    expect(gameUiReady.state.mode).toBe('game-ui');
    expect(kernelReady.state.mode).toBe('kernel');
    expect(proofsReady.state.mode).toBe('proofs');
  });
});