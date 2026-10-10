/**
 * kernel-runtime.mjs — ★ cordis assembly of the DSH kernel.
 *
 * This is the ONLY place in the host that touches DSH's cordis Context directly
 * (mirroring the project rule that `kernel/dsh/` is the only place that touches
 * DSH). It wires:
 *
 *   ├─ llm-register : ctx.llm.registerAdapter([provider], adapter)
 *   ├─ tools-register: ctx.tools.register(definition) × N
 *   ├─ event-journal: JSONL event persistence (with S-59 ignorable markers)
 *   └─ approval-broker: in-loop human approval bridge
 *
 * See docs/03 §5 (host HTTP protocol) and §6 (Node host assembly).
 *
 * NOTE: This module imports the real DSH packages as declared in package.json
 * (all pinned to 0.2.0-rc.1). When @deepseek-ai/dsh* is installed, assembly
 * proceeds against the real runtime; the async `start` path is the definitive
 * entry used by the HTTP layer.
 */
import { Context } from '@deepseek-ai/cordis';
import timer from '@deepseek-ai/cordis-plugin-timer';
import { GatewayLlmAdapter } from './model-adapter.mjs';
import { registerAllProfileTools } from './tool-bridge.mjs';
import { runTurn as runAgentTurn } from './agent-runner.mjs';
import { ApprovalBroker } from './approval-broker.mjs';
import { EventJournal, assertEventTypesKnown } from './event-journal.mjs';
import { GatewayClient } from './gateway-client.mjs';
import { KERNEL_PRESET_ID, DSH_HOST_PROTOCOL_VERSION, KERNEL_VERSION } from './protocol.mjs';

/**
 * @typedef {object} KernelRuntimeOpts
 * @property {object} profile compiled Runtime Profile snapshot (already carries
 *                    no secrets; keys injected at credential time)
 * @property {string} runtimeId
 * @property {object} modelCredential { gatewayUrl, token }
 * @property {object} toolCredential  { gatewayUrl, token }
 * @property {object} [logger]
 * @property {string} [sessionsDir] directory for session JSONL files
 */

export class KernelRuntime {
  /**
   * @param {KernelRuntimeOpts} opts
   */
  constructor(opts) {
    this.runtimeId = opts.runtimeId;
    this.profile = opts.profile;
    this.modelCredential = opts.modelCredential;
    this.toolCredential = opts.toolCredential;
    this.logger = opts.logger ?? console;
    this.sessionsDir = opts.sessionsDir ?? './data/sessions';

    this.ctx = null;
    this.modelAdapter = null;
    this.toolGatewayClient = null;
    this.approvalBroker = null;
    /** @type {Map<string, { disposer: Function, definition: object, execute: Function }>|null} */
    this.toolRegistry = null;
    this.disposers = [];
    this._open = false;

    assertEventTypesKnown(); // fail-fast on any un-ignorable custom event type
  }

  /**
   * Assembles and starts the cordis context, registering the llm adapter,
   * tools, timer and session wiring.
   * @returns {Promise<KernelRuntime>}
   */
  async start() {
    if (this._open) {
      return this;
    }

    const app = new Context();
    app.plugin(timer); // cordis-plugin-timer (independently pinned)

    // --- llm adapter registration (03 §6.1) ---
    this.modelAdapter = new GatewayLlmAdapter({
      gatewayUrl: this.modelCredential.gatewayUrl,
      token: this.modelCredential.token,
    });
    // providers: the adapter serves the gateway's provider instance(s) declared
    // in the Profile. DSH 0.2.0 registers adapters per provider id list.
    const providerIds = this._providerIds();
    for (const providerId of providerIds) {
      this._registerAdapter(app, providerId);
    }

    // --- tool gateway client + broker ---
    this.toolGatewayClient = new GatewayClient({
      baseUrl: this.toolCredential.gatewayUrl,
      token: this.toolCredential.token,
    });
    this.approvalBroker = new ApprovalBroker({ gateway: this.toolGatewayClient, logger: this.logger });

    // --- event journal (per-runtime) ---
    this.eventJournal = new EventJournal({ filePath: `${this.sessionsDir}/${this.runtimeId}.jsonl` });

    // --- presets + tools ---
    // The runtime wires the enterprise preset id and registers the Profile tools.
    app.emit?.('zy/preset', { presetId: KERNEL_PRESET_ID });

    // --- DSH tools runtime service ---
    // The full DSH kernel assembly provides `ctx.tools`; this host assembles a
    // minimal surface so Profile tools register through the documented
    // extension point (03 §6.2). The agent runner dispatches model-requested
    // tool calls from `this.toolRegistry` directly, so the cordis-side
    // registration is a definition record + disposer.
    if (!app.tools && typeof app.provide === 'function') {
      app.provide('tools', {
        register: (definition) => {
          this.logger.debug?.(`kernel-runtime: tool '${definition?.name}' registered with the DSH tool runtime`);
          return () => {};
        },
        schemas: () => [...(this.toolRegistry?.values() ?? [])].map((entry) => entry.definition),
      });
    }

    const toolDeps = { gateway: this.toolGatewayClient, logger: this.logger };
    this.toolRegistry = registerAllProfileTools(
      { ctx: app },
      this.profile.tools ?? [],
      toolDeps,
    );
    for (const entry of this.toolRegistry.values()) {
      this.disposers.push(entry.disposer);
    }

    this.ctx = app;
    this._open = true;

    this.logger.info?.(`kernel-runtime: started runtime ${this.runtimeId} (preset=${KERNEL_PRESET_ID}, ` +
      `protocol=${DSH_HOST_PROTOCOL_VERSION}, kernel=${KERNEL_VERSION}, tools=${this.toolRegistry.size})`);
    return this;
  }

  /**
   * Deterministically derives the provider id(s) the model adapter should
   * register under from the Profile model metadata.
   * @returns {string[]}
   */
  _providerIds() {
    const md = this.profile?.model ?? {};
    const pid = md.provider_id;
    if (pid) {
      return [String(pid)];
    }
    const name = md.model_name ?? md.model ?? 'zy-gateway';
    return [`zy:${String(name)}`];
  }

  /**
   * Registers our GatewayLlmAdapter for a single provider id.
   * @param {object} app cordis Context
   * @param {string} providerId
   */
  _registerAdapter(app, providerId) {
    // ctx.llm.registerAdapter(providers, adapter) — documented extension point (03 §7.2).
    if (app.llm && typeof app.llm.registerAdapter === 'function') {
      app.llm.registerAdapter([providerId], this.modelAdapter);
    } else {
      // Defensive: if the dsh-llm plugin is not yet applied to context, defer a
      // plugin that performs the registration once llm runtime is available.
      app.plugin((ctx) => {
        ctx.on('ready', () => {
          ctx.llm.registerAdapter([providerId], this.modelAdapter);
        });
      });
    }
  }

  /**
   * Current DSH session status provider — returns the runtime health descriptor
   * surfaced by the HTTP layer.
   * @returns {object}
   */
  health() {
    return {
      runtimeId: this.runtimeId,
      open: this._open,
      protocolVersion: DSH_HOST_PROTOCOL_VERSION,
      kernelVersion: KERNEL_VERSION,
      presetId: KERNEL_PRESET_ID,
      toolCount: this.profile?.tools?.length ?? 0,
    };
  }

  /**
   * Tool gateway client alias — the outbound client used by the runner's tool
   * bridge (and available to callers inspecting runtime wiring).
   * @returns {import('./gateway-client.mjs').GatewayClient|null}
   */
  get gateway() {
    return this.toolGatewayClient ?? null;
  }

  /**
   * Runs one full agent turn for a session by delegating to the agent runner.
   *
   * @param {string} sessionId session the turn belongs to
   * @param {unknown} content user content blocks ([{type:'text',data:{text}}])
   * @param {(evt: { type: string, payload: object }) => void} emit
   *        injected event sink (envelope + journal + broadcast owned by caller)
   * @param {unknown} [turnContext] opaque per-turn context passed through
   * @returns {Promise<{ ok: boolean, text: string, finishReason: string, error?: object }>}
   */
  async runTurn(sessionId, content, emit, turnContext = null) {
    return runAgentTurn({
      runtime: this,
      sessionId,
      content,
      emit,
      turnContext,
    });
  }

  /**
   * Tears down the cordis context and disposes all registered tools.
   * @returns {Promise<void>}
   */
  async dispose() {
    if (!this._open) {
      return;
    }
    for (const d of this.disposers) {
      try {
        d?.();
      } catch {
        /* best-effort disposal */
      }
    }
    this.disposers = [];
    if (this.eventJournal) {
      this.eventJournal.close();
    }
    this._open = false;
    try {
      await this.ctx?.stop?.();
    } catch {
      /* context already stopped */
    }
    this.logger.info?.(`kernel-runtime: disposed runtime ${this.runtimeId}`);
  }
}
