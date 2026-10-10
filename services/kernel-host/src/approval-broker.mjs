/**
 * approval-broker.mjs — bridges in-loop human approval between DSH and the
 * ZY-Agents approval gateway.
 *
 * DSH 0.2.0's `ToolGuard` is fail-closed: a guard can only DENY a tool call, it
 * can never grant permission (03 §7.3-③). This is exactly the shape approval
 * needs. The broker therefore:
 *   - renders a guard that trips open when the main service reports the tool
 *     call requires (and has not yet received) approval;
 *   - when the main service approves, the broker releases the blocked call by
 *     allowing the execution path to proceed.
 *
 * Because guards have no "allow" result, the broker itself does not decide
 * approvals; it delegating the decision to `/internal/kernel/tools/approval`
 * and uses a local pending-set + an externally-driven gate to propagate the
 * decision back to the blocked execution.
 */
import { GatewayClient } from './gateway-client.mjs';

/** A promise-based gate that resolves when `approve()` or `reject()` is called. */
class DecisionGate {
  /**
   * @param {number} timeoutMs
   */
  constructor(timeoutMs) {
    this.timeoutMs = timeoutMs;
    this._resolver = null;
    this._rejecter = null;
    this._timer = null;
    this.promise = new Promise((resolve, reject) => {
      this._resolver = resolve;
      this._rejecter = reject;
    });
  }

  /**
   * @param {{ approved: boolean, reason?: string }} decision
   */
  resolve(decision) {
    this._clearTimer();
    this._resolver(decision);
  }

  reject(err) {
    this._clearTimer();
    this._rejecter(err);
  }

  _clearTimer() {
    if (this._timer) {
      clearTimeout(this._timer);
      this._timer = null;
    }
  }
}

export class ApprovalBroker {
  /**
   * @param {object} opts
   * @param {GatewayClient} opts.gateway tool-gateway HTTP client
   * @param {{ debug?: Function, info?: Function, warn?: Function }|*} [opts.logger]
   * @param {number} [opts.approvalTimeoutMs]
   */
  constructor({ gateway, logger, approvalTimeoutMs = 300000 }) {
    this.gateway = gateway;
    this.logger = logger ?? console;
    this.approvalTimeoutMs = approvalTimeoutMs;
    /** @type {Map<string, DecisionGate>} pending approvals keyed by approval id */
    this._pending = new Map();
  }

  /**
   * Creates a new approval gate for an in-loop tool call by asking the main
   * service to create an approval record (if the tool is configured
   * approval_required=true) and returns a promise that resolves to
   * { approved, reason }.
   *
   * @param {object} call context: { toolName, args, callId, sessionId, timeoutMs }
   * @returns {Promise<{ approved: boolean, reason?: string, approvalId?: string }>}
   */
  async requestApproval({ toolName, args, callId, sessionId, timeoutMs }) {
    const timeout = timeoutMs ?? this.approvalTimeoutMs;
    // Ask the main service to record the approval request and block for a
    // decision. The service blocks server-side and returns on decision or
    // timeout; this keeps the broker as a thin relay (single approval path).
    const reqBody = {
      tool: toolName,
      args,
      call_id: callId,
      session_id: sessionId,
      timeout_ms: timeout,
    };
    // No approval required unless the gateway says otherwise: we always ask the
    // service (it owns approval_required policy). Use a decision gate so the
    // HTTP round-trip result can settle it deterministically.
    const gate = new DecisionGate(timeout);
    this._pending.set(callId, gate);
    try {
      this.gateway.postJson('/internal/kernel/tools/approval', reqBody)
        .then((res) => gate.resolve({
          approved: !!(res && res.approved !== false),
          reason: res?.reason ?? '',
          approvalId: res?.approval_id ?? null,
        }))
        .catch((err) => gate.reject(err));

      return await gate.promise;
    } finally {
      this._pending.delete(callId);
    }
  }

  /**
   * Resolve a pending approval gate (called by the HTTP approval-decision
   * endpoint which is citizen for inbound decisions).
   *
   * @param {string} callId
   * @param {{ approved: boolean, reason?: string }} decision
   * @returns {boolean} true if a matching pending gate existed
   */
  settle(callId, decision) {
    const gate = this._pending.get(callId);
    if (!gate) {
      return false;
    }
    gate.resolve({
      approved: decision.approved,
      reason: decision.reason ?? '',
    });
    return true;
  }
}
