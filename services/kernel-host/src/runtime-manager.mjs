/**
 * runtime-manager.mjs — runtime ledger (Map<runtimeId, KernelRuntime>).
 *
 * The host is stateless except for session JSONL on disk, and can host multiple
 * runtimes (one per org_unit/user profile) concurrently. This module is the
 * process-local registry of those runtimes.
 */
import { randomUUID } from 'node:crypto';
import { KernelRuntime } from './kernel-runtime.mjs';

export class RuntimeManager {
  /**
   * @param {object} opts
   * @param {string} [opts.sessionsDir]
   * @param {object|*} [opts.logger]
   */
  constructor(opts = {}) {
    this.sessionsDir = opts.sessionsDir ?? './data/sessions';
    this.logger = opts.logger ?? console;
    /** @type {Map<string, KernelRuntime>} */
    this._runtimes = new Map();
  }

  /**
   * Creates and starts a new KernelRuntime from the given profile + credentials.
   *
   * @param {object} request { orgUnitId, profileVersion, isolationKey, profile }
   * @param {object} modelCredential { gatewayUrl, token }
   * @param {object} toolCredential  { gatewayUrl, token }
   * @returns {Promise<{ runtimeId: string, runtime: KernelRuntime, handle: object }>}
   */
  async createRuntime(request, modelCredential, toolCredential) {
    const runtimeId = request.runtimeId ?? randomUUID();
    if (this._runtimes.has(runtimeId)) {
      throw new Error(`runtime already exists: ${runtimeId}`);
    }
    const runtime = new KernelRuntime({
      runtimeId,
      profile: request.profile,
      modelCredential,
      toolCredential,
      sessionsDir: this.sessionsDir,
      logger: this.logger,
    });
    await runtime.start();
    this._runtimes.set(runtimeId, runtime);
    const handle = {
      runtime_id: runtimeId,
      org_unit_id: request.orgUnitId,
      profile_version: request.profileVersion,
      isolation_key: request.isolationKey,
      kernel: 'dsh',
      kernel_version: runtime.health().kernelVersion,
      model_instance_id: request.profile?.model?.model_instance_id ?? null,
    };
    return { runtimeId, runtime, handle };
  }

  /** @param {string} runtimeId */
  get(runtimeId) {
    return this._runtimes.get(runtimeId) ?? null;
  }

  /**
   * Lists all runtime handles.
   * @returns {object[]}
   */
  list() {
    return [...this._runtimes.values()].map((r) => ({
      runtime_id: r.runtimeId,
      ...r.health(),
    }));
  }

  /**
   * Disposes and removes a runtime.
   * @param {string} runtimeId
   * @returns {Promise<boolean>} true if a runtime was removed
   */
  async dispose(runtimeId) {
    const runtime = this._runtimes.get(runtimeId);
    if (!runtime) {
      return false;
    }
    await runtime.dispose();
    this._runtimes.delete(runtimeId);
    return true;
  }

  /** @returns {number} */
  get size() {
    return this._runtimes.size;
  }
}
