/**
 * event-journal.mjs — JSONL event persistence + ignorable-marker enforcement.
 *
 * ### Why this module matters (S-59 / docs/03 §7.3-②)
 *
 * DSH's session persistence read path REFUSES to interpret a log line whose
 * event type is unknown unless that event carries the envelope's `ignorable`
 * marker. For an unknown type:
 *   - WITH ignorable:true  -> the line is skipped safely (session stays readable)
 *   - WITHOUT ignorable    -> the WHOLE session log becomes unreadable
 *
 * Therefore any session event WE emit whose `type` is not a known native DSH
 * session event type MUST be written with `ignorable: true`. This is a
 * fail-fast, start-up asserted invariant (not a "fix at upgrade time" item).
 *
 * Implementation:
 *   - `isKnownNativeType(type)`  -> membership test against KNOWN_NATIVE_*
 *   - `finalizeEvent(evt)`       -> auto-adds `ignorable: true` for any event
 *                                   whose type is not known-native AND that
 *                                   does not already declare ignorable.
 *   - `assertEventTypesKnown()`  -> start-up assertion over the set of custom
 *                                   event types the host may emit.
 */
import { appendFileSync, createWriteStream, existsSync, mkdirSync } from 'node:fs';
import { dirname } from 'node:path';
import { KNOWN_NATIVE_SESSION_EVENT_TYPES } from './protocol.mjs';

/** Event types this host emits that are NOT native DSH session types. */
export const HOST_CUSTOM_EVENT_TYPES = Object.freeze([
  'tool_progress',
  'auth_required',
  'local_agent_offline',
  'turn_start',
  'turn_end',
  // 注意：session_error 是 DSH 原生类型（protocol.mjs KNOWN_NATIVE_SESSION_EVENT_TYPES），
  // 不可列进宿主自定义类型（否则 assertEventTypesKnown() 启动断言即冲突失败，S-59）。
]);

/**
 * Returns true if the event type is a known native DSH session event type.
 * @param {string} type
 * @returns {boolean}
 */
export function isKnownNativeType(type) {
  return KNOWN_NATIVE_SESSION_EVENT_TYPES.includes(type);
}

/**
 * Start-up assertion: every custom event type we could emit must be either
 * known-native or explicitly declared in HOST_CUSTOM_EVENT_TYPES (which the
 * journal always marks ignorable). Throws on violation -> fail-fast boot.
 *
 * @throws {Error}
 */
export function assertEventTypesKnown() {
  for (const type of HOST_CUSTOM_EVENT_TYPES) {
    if (isKnownNativeType(type)) {
      throw new Error(
        `event-journal: '${type}' is a known native type; it must not be listed ` +
        'as a host custom type (duplicate/conflicting declaration).',
      );
    }
  }
}

/**
 * Finalizes a session event before it is appended to the journal:
 * guarantees self-declared custom events carry `ignorable: true`.
 *
 * @param {object} evt raw event (must have a `type` field)
 * @returns {object} a copy of evt with ignorable asserted as needed
 */
export function finalizeEvent(evt) {
  const type = evt && evt.type;
  if (!type) {
    throw new Error('event-journal: cannot finalize event without a `type`');
  }
  if (isKnownNativeType(type)) {
    return { ...evt };
  }
  // Custom (self-authored) session event -> MUST be ignorable, else the whole
  // session log becomes unreadable on the DSH read path.
  if (evt.ignorable === false) {
    throw new Error(
      `event-journal: custom session event '${type}' explicitly set ignorable=false; ` +
      'this would make the session log unreadable (S-59).',
    );
  }
  return { ...evt, ignorable: true };
}

/** Single-file JSONL journal (one event per line). */
export class EventJournal {
  /**
   * @param {object} opts
   * @param {string} opts.filePath absolute path to the JSONL file
   */
  constructor({ filePath }) {
    this.filePath = filePath;
    this._stream = null;
  }

  /** Opens the journal file (creates parent dir); idempotent. */
  open() {
    if (this._stream) {
      return;
    }
    const dir = dirname(this.filePath);
    if (!existsSync(dir)) {
      mkdirSync(dir, { recursive: true });
    }
    this._stream = createWriteStream(this.filePath, { flags: 'a', encoding: 'utf8' });
  }

  /**
   * Appends a finalized event to the journal.
   * @param {object} evt
   */
  append(evt) {
    this.open();
    const line = JSON.stringify(finalizeEvent(evt)) + '\n';
    this._stream.write(line);
  }

  /** Flushes and closes the underlying stream. */
  close() {
    if (this._stream) {
      this._stream.end();
      this._stream = null;
    }
  }
}
