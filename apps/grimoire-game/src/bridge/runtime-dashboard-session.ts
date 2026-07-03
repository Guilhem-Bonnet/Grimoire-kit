import { createControlPlaneRunContext, type ClientEvent, type ControlPlaneRunContext, type ServerEvent } from '../contracts/events';
import type { RuntimeDashboardView, RuntimeDashboardViewOptions } from '../state/runtime-dashboard-view';
import {
  RuntimeDashboardStore,
  type RuntimeDashboardListener,
  type RuntimeDashboardStoreOptions
} from '../state/runtime-dashboard-store';

import type { AgentAdapter } from './agent-adapter';
import type { AuthContext } from '../server/auth/rbac';

export interface RuntimeDashboardSyncResult {
  events: readonly ServerEvent[];
  dashboard: RuntimeDashboardView;
}

export interface RuntimeDashboardSessionOptions {
  storeOptions?: RuntimeDashboardStoreOptions;
  controlPlane?: ControlPlaneRunContext;
}

export class RuntimeDashboardSession {
  private readonly store: RuntimeDashboardStore;
  private lastSequenceId: number | undefined;
  private readonly controlPlaneContext: ControlPlaneRunContext | undefined;

  constructor(
    private readonly adapter: AgentAdapter,
    options: RuntimeDashboardSessionOptions = {}
  ) {
    this.store = new RuntimeDashboardStore(options.storeOptions);
    this.controlPlaneContext =
      options.controlPlane === undefined ? undefined : createControlPlaneRunContext(options.controlPlane);
  }

  getDashboard(): RuntimeDashboardView {
    return this.store.getDashboard();
  }

  getLastSequenceId(): number | undefined {
    return this.lastSequenceId;
  }

  configureDashboard(options: RuntimeDashboardViewOptions): RuntimeDashboardView {
    return this.store.configureDashboard(options);
  }

  subscribe(listener: RuntimeDashboardListener): () => void {
    return this.store.subscribe(listener);
  }

  async bootstrap(auth: AuthContext): Promise<RuntimeDashboardSyncResult> {
    const events = await this.adapter.getInitialSnapshot(auth);
    return this.applyAndBuild(events);
  }

  async reconnect(lastSequenceId: number | undefined, auth: AuthContext): Promise<RuntimeDashboardSyncResult> {
    const events = await this.adapter.reconnect(lastSequenceId, auth);
    return this.applyAndBuild(events);
  }

  async sync(auth: AuthContext): Promise<RuntimeDashboardSyncResult> {
    return this.reconnect(this.lastSequenceId, auth);
  }

  async dispatch(event: ClientEvent, auth: AuthContext): Promise<RuntimeDashboardSyncResult> {
    const events = await this.adapter.handleClientEvent(event, auth);
    return this.applyAndBuild(events);
  }

  private applyAndBuild(events: readonly ServerEvent[]): RuntimeDashboardSyncResult {
    const dashboard =
      events.length === 0 ? this.store.getDashboard() : this.store.applyEvents(events, this.controlPlaneContext);
    this.lastSequenceId = dashboard.lastSequenceId;
    return {
      events,
      dashboard
    };
  }
}