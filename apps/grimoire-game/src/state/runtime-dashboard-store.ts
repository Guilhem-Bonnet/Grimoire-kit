import type { ControlPlaneRunContext, GameStateSnapshot, ServerEvent } from '../contracts/events';

import { LeaseStore, type LeaseStoreBuildOptions } from '../server/control-plane/lease-store';
import { ProjectRegistry } from '../server/control-plane/project-registry';
import { NodeRegistry, type NodeRegistryBuildOptions } from '../server/control-plane/node-registry';

import { applyServerEvents, createEmptyGameState, hydrateGameState, type GameState } from './game-state';
import { projectServerEventsToCanonicalEnvelopes } from './canonical-envelope-pilot';
import {
  createRuntimeDashboardView,
  type RuntimeDashboardView,
  type RuntimeDashboardViewOptions
} from './runtime-dashboard-view';

export interface RuntimeDashboardStoreControlPlaneOptions {
  nodeRegistry?: NodeRegistryBuildOptions;
  leaseStore?: LeaseStoreBuildOptions;
}

export interface RuntimeDashboardStoreOptions {
  initialState?: GameState;
  dashboard?: RuntimeDashboardViewOptions;
  controlPlane?: RuntimeDashboardStoreControlPlaneOptions;
}

export type RuntimeDashboardListener = (dashboard: RuntimeDashboardView) => void;

export class RuntimeDashboardStore {
  private state: GameState;
  private dashboardOptions: RuntimeDashboardViewOptions;
  private readonly listeners = new Set<RuntimeDashboardListener>();
  private projectRegistry = new ProjectRegistry();
  private nodeRegistry: NodeRegistry;
  private leaseStore: LeaseStore;
  private readonly controlPlaneOptions: RuntimeDashboardStoreControlPlaneOptions;

  constructor(options: RuntimeDashboardStoreOptions = {}) {
    this.state = options.initialState ?? createEmptyGameState();
    this.dashboardOptions = options.dashboard ?? {};
    this.controlPlaneOptions = options.controlPlane ?? {};
    this.nodeRegistry = new NodeRegistry(this.controlPlaneOptions.nodeRegistry);
    this.leaseStore = new LeaseStore(this.controlPlaneOptions.leaseStore);
  }

  getState(): GameState {
    return this.state;
  }

  getDashboard(): RuntimeDashboardView {
    return createRuntimeDashboardView(this.state, this.dashboardOptions, {
      projectRegistry: this.projectRegistry.getSnapshot(),
      nodeRegistry: this.nodeRegistry.getSnapshot(),
      leaseStore: this.leaseStore.getSnapshot()
    });
  }

  subscribe(listener: RuntimeDashboardListener): () => void {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }

  hydrateSnapshot(snapshot: GameStateSnapshot, hydratedAt: string | null = null): RuntimeDashboardView {
    this.state = hydrateGameState(snapshot, hydratedAt);
    this.resetControlPlane();
    return this.emitDashboard();
  }

  applyEvents(events: readonly ServerEvent[], controlPlane?: ControlPlaneRunContext): RuntimeDashboardView {
    const orderedEvents = [...events].sort((left, right) => left.sequenceId - right.sequenceId);
    this.state = applyServerEvents(this.state, orderedEvents);

    if (controlPlane !== undefined) {
      const canonicalEnvelopes = projectServerEventsToCanonicalEnvelopes(orderedEvents, 'runtime', controlPlane);
      if (canonicalEnvelopes.length > 0) {
        this.projectRegistry.applyEnvelopes(canonicalEnvelopes);

        if (canonicalEnvelopes.some((envelope) => envelope.context.nodeId !== undefined)) {
          this.nodeRegistry.applyEnvelopes(canonicalEnvelopes);
        }

        if (
          canonicalEnvelopes.some(
            (envelope) =>
              envelope.context.leaseId !== undefined &&
              envelope.context.taskId !== undefined &&
              envelope.context.nodeId !== undefined
          )
        ) {
          this.leaseStore.applyEnvelopes(canonicalEnvelopes);
        }
      }
    }

    return this.emitDashboard();
  }

  configureDashboard(options: RuntimeDashboardViewOptions): RuntimeDashboardView {
    this.dashboardOptions = { ...options };
    return this.emitDashboard();
  }

  reset(nextState: GameState = createEmptyGameState()): RuntimeDashboardView {
    this.state = nextState;
    this.resetControlPlane();
    return this.emitDashboard();
  }

  private resetControlPlane(): void {
    this.projectRegistry = new ProjectRegistry();
    this.nodeRegistry = new NodeRegistry(this.controlPlaneOptions.nodeRegistry);
    this.leaseStore = new LeaseStore(this.controlPlaneOptions.leaseStore);
  }

  private emitDashboard(): RuntimeDashboardView {
    const dashboard = this.getDashboard();
    for (const listener of this.listeners) {
      listener(dashboard);
    }

    return dashboard;
  }
}