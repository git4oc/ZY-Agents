/**
 * protocol.mjs — zy.dsh-host.v1 protocol constants.
 *
 * The single source of truth for the host HTTP protocol shared between the
 * ZY-Agents main service and the Node kernel host. See docs/03 §5.
 *
 * All identifiers here are VENDOR-layer, immutable identifiers (S-61). They
 * must never be made configurable per deployment.
 */

export const DSH_HOST_PROTOCOL_VERSION = 'zy.dsh-host.v1';

/** DSH kernel train pinned for this host (see package.json / VERSIONS.lock). */
export const KERNEL_VERSION = '0.2.0-rc.1';
export const KERNEL_KIND = 'dsh';

/** Default HTTP bind for the host. */
export const DEFAULT_HOST = '127.0.0.1';
export const DEFAULT_PORT = 8101;

/** Kernel preset id used when assembling a session (03 §2.3). */
export const KERNEL_PRESET_ID = 'zy-enterprise';

/**
 * Start-up handshake line printed to stdout once the HTTP server is listening.
 * The main service may use this (or /health polling) to confirm readiness.
 * @type {(host: string, port: number, pid: number) => string}
 */
export function readyLine(host, port, pid) {
  return JSON.stringify({
    type: 'zy-dsh-runtime-ready',
    host,
    port,
    kernel: KERNEL_KIND,
    kernelVersion: KERNEL_VERSION,
    protocolVersion: DSH_HOST_PROTOCOL_VERSION,
    pid,
  });
}

/**
 * Known-native session event types from DSH's session persistence read path.
 * Any event type we emit that is NOT in this set MUST carry { ignorable: true }
 * (S-59 / 03 §7.3-②), otherwise the session JSONL becomes unreadable.
 *
 * This is a deliberately conservative subset used by the host's start-up
 * assertion (see event-journal.mjs). It mirrors the upstream
 * KNOWN_SESSION_EVENT_TYPES contract; extend only after verifying upstream.
 */
export const KNOWN_NATIVE_SESSION_EVENT_TYPES = Object.freeze([
  'message',
  'tool_call',
  'tool_result',
  'model_call',
  'model_result',
  'session_state',
  'session_error',
]);

/** Standard normalized event type names used in the kernel-event envelope. */
export const EVENT_TYPE = Object.freeze({
  MESSAGE: 'message',
  TOOL_PROGRESS: 'tool_progress',
  TOOL_RESULT: 'tool_result',
  MODEL_CALL: 'model_call',
  MODEL_RESULT: 'model_result',
  AUTH_REQUIRED: 'auth_required',
  LOCAL_AGENT_OFFLINE: 'local_agent_offline',
  SESSION_STATE: 'session_state',
  SESSION_ERROR: 'session_error',
  TURN_START: 'turn_start',
  TURN_END: 'turn_end',
});
