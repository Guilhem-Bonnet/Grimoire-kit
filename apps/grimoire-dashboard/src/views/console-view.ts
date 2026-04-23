import type { DashboardStateSnapshot } from '../dashboard-store';
import { createViewShell, type View } from './view-common';
import type { ClientCommand } from '../contracts/wsProtocol';

export interface ConsoleViewDeps {
  sendCommand: (command: ClientCommand) => boolean;
}

/**
 * Console view — placeholder command channel.
 *
 * The protocol already defines the `command` message shape, but the
 * server replies with `error:not_implemented` in v0. The console echoes
 * both the outgoing request and the server error into a local log, so
 * the operator sees the wire is alive end-to-end.
 *
 * A real command loop (bound to runtime pilot APIs) is a follow-up.
 */
export function createConsoleView(deps: ConsoleViewDeps): View {
  let bodyRef: HTMLElement | null = null;
  const log: string[] = [];

  return {
    route: 'console',
    title: 'Console',
    status: 'partial',
    mount(root) {
      const { root: viewRoot, body } = createViewShell({
        title: 'Console',
        subtitle: 'Placeholder — canal commande déclaré en protocole mais non câblé (v0).',
        status: 'partial'
      });
      root.replaceChildren(viewRoot);
      bodyRef = body;
      render();

      body.addEventListener('submit', (event) => {
        event.preventDefault();
        const form = event.target as HTMLFormElement;
        const verbInput = form.querySelector<HTMLInputElement>('input[name="verb"]')!;
        const argsInput = form.querySelector<HTMLInputElement>('input[name="args"]')!;
        const verb = verbInput.value.trim();
        if (!verb) return;
        let args: Record<string, unknown> | undefined;
        if (argsInput.value.trim().length > 0) {
          try {
            args = JSON.parse(argsInput.value) as Record<string, unknown>;
          } catch {
            pushLog(`× JSON invalide pour args — commande non envoyée.`);
            return;
          }
        }
        const id = `cmd-${Date.now().toString(36)}`;
        const command: ClientCommand = {
          type: 'command',
          id,
          verb,
          ...(args !== undefined ? { args } : {})
        };
        const ok = deps.sendCommand(command);
        pushLog(`${ok ? '→' : '×'} ${JSON.stringify(command)}`);
        if (!ok) pushLog('  (WS non connecté — la commande n\'a pas quitté le client)');
        verbInput.value = '';
        argsInput.value = '';
      });
    },
    update(snapshot) {
      if (!bodyRef) return;
      if (snapshot.lastError) {
        pushLog(`← error ${snapshot.lastError}`);
      }
    }
  };

  function render(): void {
    if (!bodyRef) return;
    bodyRef.innerHTML = `
      <div class="banner">
        <strong>PARTIEL</strong>
        Le serveur dashboard (v0) répond <code>error:not_implemented</code> à toute commande.
        L'interface prouve que le canal existe end-to-end.
      </div>
      <section class="panel">
        <h2 class="panel__title">Envoyer une commande</h2>
        <form class="console__form">
          <input class="console__input" name="verb" placeholder="verb (ex: agent.start)" autocomplete="off" />
          <input class="console__input" name="args" placeholder='args JSON (ex: {"id":"dev"})' autocomplete="off" />
          <button class="console__button" type="submit">ENVOYER</button>
        </form>
      </section>
      <section class="panel">
        <h2 class="panel__title">Journal</h2>
        <pre class="console__log" id="console-log"></pre>
      </section>
    `;
    renderLog();
  }

  function pushLog(line: string): void {
    log.push(`[${new Date().toISOString()}] ${line}`);
    if (log.length > 200) log.splice(0, log.length - 200);
    renderLog();
  }

  function renderLog(): void {
    const el = bodyRef?.querySelector<HTMLElement>('#console-log');
    if (!el) return;
    el.textContent = log.join('\n');
    el.scrollTop = el.scrollHeight;
  }
}
