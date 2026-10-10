/**
 * http-server.mjs — host HTTP routes (zy.dsh-host.v1).
 *
 * Implements the full route table from docs/03 §5. All endpoints except
 * /health require `Authorization: Bearer <KERNEL_HOST_TOKEN>`. Transport is
 * JSON for commands and NDJSON for the event-stream (lines of JSON objects).
 */
import { createServer } from 'node:http';
import { extractBearerToken, verifyBearer } from './auth.mjs';
import { DSH_HOST_PROTOCOL_VERSION, KERNEL_VERSION, readyLine } from './protocol.mjs';
import { finalizeEvent } from './event-journal.mjs';

const json = (res, status, body) => {
  const payload = JSON.stringify(body);
  res.writeHead(status, { 'content-type': 'application/json; charset=utf-8' });
  res.end(payload);
};

const readJsonBody = (req) => new Promise((resolve, reject) => {
  const chunks = [];
  let size = 0;
  req.on('data', (c) => {
    size += c.length;
    if (size > 8 * 1024 * 1024) {
      reject(new Error('request body too large'));
      req.destroy();
      return;
    }
    chunks.push(c);
  });
  req.on('end', () => {
    const raw = Buffer.concat(chunks).toString('utf8');
    if (!raw) {
      resolve({});
      return;
    }
    try {
      resolve(JSON.parse(raw));
    } catch {
      reject(new Error('invalid JSON body'));
    }
  });
  req.on('error', reject);
});

/** A small in-memory SSE/NDJSON broadcaster for a session's live event-stream. */
class NdjsonBroadcaster {
  constructor() {
    /** @type {Set<import('node:http').ServerResponse>} */
    this.clients = new Set();
  }

  broadcast(obj) {
    const line = JSON.stringify(obj) + '\n';
    for (const res of this.clients) {
      try {
        res.write(line);
      } catch {
        /* client gone */
      }
    }
  }

  add(res) {
    this.clients.add(res);
    res.on('close', () => this.clients.delete(res));
  }
}

/**
 * Creates the host HTTP server.
 *
 * @param {object} deps
 * @param {import('./runtime-manager.mjs').RuntimeManager} deps.runtimes
 * @param {import('./event-journal.mjs').EventJournal} [deps.journal] host-level events journal
 * @param {string} deps.token expected KERNEL_HOST_TOKEN
 * @param {object|*} [deps.logger]
 * @returns {import('node:http').Server}
 */
export function createHostServer(deps) {
  const { runtimes, token, logger = console } = deps;
  const hostJournal = deps.journal ?? null;
  /** @type {Map<string, NdjsonBroadcaster>} sessionId -> live stream */
  const streams = new Map();
  /** @type {Map<string, import('./kernel-runtime.mjs').KernelRuntime>} sessionId -> bound runtime */
  const sessionRuntimes = new Map();

  const sessionStream = (sessionId) => {
    let b = streams.get(sessionId);
    if (!b) {
      b = new NdjsonBroadcaster();
      streams.set(sessionId, b);
    }
    return b;
  };

  /**
   * Builds a raw (pre-finalize) kernel event envelope.
   * @param {string} runtimeId
   * @param {string} sessionId
   * @param {string} type
   * @param {object} [payload]
   */
  const makeEvent = (runtimeId, sessionId, type, payload = {}) => ({
    event_id: `evt_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
    runtime_id: runtimeId,
    session_id: sessionId,
    cursor: 0,
    type,
    occurred_at: new Date().toISOString(),
    payload,
  });

  /**
   * Builds the per-session event sink injected into the agent runner: every
   * event is finalized (S-59 ignorable markers), appended to the runtime's
   * EventJournal and broadcast on the session's live event-stream.
   *
   * @param {import('./kernel-runtime.mjs').KernelRuntime|null} rt bound runtime
   * @param {string} runtimeId runtime id from the request path (may be '_')
   * @param {string} sessionId
   */
  const makeSessionEmitter = (rt, runtimeId, sessionId) => {
    const bc = sessionStream(sessionId);
    const journal = rt?.eventJournal ?? null;
    const rid = rt?.runtimeId ?? runtimeId;
    return (evt) => {
      const finalized = finalizeEvent(makeEvent(rid, sessionId, evt.type, evt.payload));
      // journal append re-finalizes idempotently
      journal?.append({ ...finalized });
      bc.broadcast(finalized);
    };
  };

  /**
   * Resolves the runtime a session turn should run against: an explicit
   * runtime id in the path wins; `_` (API-side "any runtime" wildcard) falls
   * back to the session->runtime binding, then to the single-runtime case.
   *
   * @param {string} runtimeId
   * @param {string} sessionId
   * @returns {import('./kernel-runtime.mjs').KernelRuntime|null}
   */
  const resolveRuntimeForSession = (runtimeId, sessionId) => {
    if (runtimeId && runtimeId !== '_') {
      return runtimes.get(runtimeId);
    }
    const bound = sessionRuntimes.get(sessionId);
    if (bound) {
      return bound;
    }
    const all = runtimes.list();
    if (all.length === 1) {
      return runtimes.get(all[0].runtime_id);
    }
    return null;
  };

  const requireAuth = (req) => verifyBearer(extractBearerToken(req), token);

  const server = createServer(async (req, res) => {
    const url = new URL(req.url, `http://${req.headers.host ?? 'localhost'}`);
    const path = url.pathname;
    const method = (req.method ?? 'GET').toUpperCase();

    try {
      // ---- health (no auth) ----
      if (method === 'GET' && path === '/health') {
        json(res, 200, {
          ok: true,
          kernel: 'dsh',
          version: KERNEL_VERSION,
          protocolVersion: DSH_HOST_PROTOCOL_VERSION,
          runtimes: runtimes.list(),
        });
        return;
      }

      // ---- auth gate for everything else ----
      if (!requireAuth(req)) {
        json(res, 401, { ok: false, error: { code: 'unauthorized', message: 'invalid bearer token' } });
        return;
      }

      const mRuntimeId = /^\/v1\/runtimes\/([^/]+)$/.exec(path);
      const mCredential = /^\/v1\/runtimes\/([^/]+)\/(model-credential|tool-credential)$/.exec(path);
      const mSession = /^\/v1\/runtimes\/([^/]+)\/sessions\/([^/]+)(\/[^/]+)*$/.exec(path);

      // ---- POST /v1/runtimes ----
      if (method === 'POST' && path === '/v1/runtimes') {
        const body = await readJsonBody(req);
        const runtimeId = body.runtime_id;
        const created = await runtimes.createRuntime(
          {
            runtimeId,
            orgUnitId: body.orgUnitId ?? body.org_unit_id,
            profileVersion: body.profileVersion ?? body.profile_version,
            isolationKey:
              body.isolationKey ??
              body.isolation_key ??
              `${body.orgUnitId ?? body.org_unit_id}:${body.subject_user_id ?? ''}`,
            profile: body.profile ?? {},
          },
          { gatewayUrl: body.model_credential?.gateway_url, token: body.model_credential?.token },
          { gatewayUrl: body.tool_credential?.gateway_url, token: body.tool_credential?.token },
        );
        json(res, 201, { ok: true, runtime: created.handle });
        return;
      }

      // ---- GET /v1/runtimes ----
      if (method === 'GET' && path === '/v1/runtimes') {
        json(res, 200, { ok: true, runtimes: runtimes.list() });
        return;
      }

      // ---- GET /v1/runtimes/{id} ----
      if (method === 'GET' && mRuntimeId) {
        const runtime = runtimes.get(decodeURIComponent(mRuntimeId[1]));
        if (!runtime) {
          json(res, 404, { ok: false, error: { code: 'runtime_not_found', message: 'runtime not found' } });
          return;
        }
        json(res, 200, { ok: true, runtime: runtime.health() });
        return;
      }

      // ---- DELETE /v1/runtimes/{id} ----
      if (method === 'DELETE' && mRuntimeId) {
        const removed = await runtimes.dispose(decodeURIComponent(mRuntimeId[1]));
        json(res, removed ? 200 : 404, {
          ok: removed,
          error: removed ? undefined : { code: 'runtime_not_found', message: 'runtime not found' },
        });
        return;
      }

      // ---- PUT /v1/runtimes/{id}/{model|tool}-credential ----
      if (method === 'PUT' && mCredential) {
        const [, id, kind] = mCredential;
        const runtime = runtimes.get(decodeURIComponent(id));
        if (!runtime) {
          json(res, 404, { ok: false, error: { code: 'runtime_not_found', message: 'runtime not found' } });
          return;
        }
        const body = await readJsonBody(req);
        if (kind === 'model-credential') {
          runtime.modelCredential = {
            gatewayUrl: body.gateway_url ?? body.gatewayUrl,
            token: body.token ?? '',
          };
        } else {
          runtime.toolCredential = {
            gatewayUrl: body.gateway_url ?? body.gatewayUrl,
            token: body.token ?? '',
          };
        }
        json(res, 200, { ok: true });
        return;
      }

      // ---- POST /v1/runtimes/{id}/sessions ----
      const mCreateSession = /^\/v1\/runtimes\/([^/]+)\/sessions$/.exec(path);
      if (method === 'POST' && mCreateSession) {
        const runtime = runtimes.get(decodeURIComponent(mCreateSession[1]));
        if (!runtime) {
          json(res, 404, { ok: false, error: { code: 'runtime_not_found', message: 'runtime not found' } });
          return;
        }
        const body = await readJsonBody(req);
        const spec = body.session_spec ?? {};
        const sessionId = body.session_id ?? `sess_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
        // session -> runtime binding: the host is the single source of truth
        // for which runtime backs a session (API-side uses `_` as wildcard).
        sessionRuntimes.set(sessionId, runtime);
        hostJournal?.append({
          type: 'session_state',
          runtime_id: runtime.runtimeId,
          session_id: sessionId,
          payload: { state: 'creating', presetId: spec.preset_id },
        });
        json(res, 201, {
          ok: true,
          session: {
            runtime_id: runtime.runtimeId,
            session_id: sessionId,
            conversation_id: spec.conversation_id,
            profile_version: spec.profile_version,
            preset_id: spec.preset_id,
            status: 'creating',
          },
        });
        return;
      }

      // ---- session-scoped routes ----
      if (mSession) {
        const runtimeId = decodeURIComponent(mSession[1]);
        const sessionId = decodeURIComponent(mSession[2]);
        const sub = mSession[3] ?? '';
        // API 侧以 `_` 作「会话所属 runtime」通配（host 是 session↔runtime 唯一事实源）。
        // 会话级操作（send/cancel/events/event-stream）按 session_id 定址，不依赖 runtime；
        // 仅裸 GET（会话详情）需要 runtime 数据，那里再单独兜底。
        const runtime = runtimeId === '_' ? undefined : runtimes.get(runtimeId);
        if (runtimeId !== '_' && !runtime) {
          json(res, 404, { ok: false, error: { code: 'runtime_not_found', message: 'runtime not found' } });
          return;
        }
        const action = sub.split('/').filter(Boolean).join('/');

        switch (action) {
          // GET /v1/runtimes/{id}/sessions/{sid}
          case '':
            if (method === 'GET') {
              json(res, 200, {
                ok: true,
                session: {
                  runtime_id: runtime?.runtimeId ?? runtimeId,
                  session_id: sessionId,
                  status: 'idle',
                  profile_version: runtime?.profile?.profile_version ?? '',
                },
              });
              return;
            }
            break;

          // POST .../resume
          case 'resume':
            if (method === 'POST') {
              json(res, 200, {
                ok: true,
                session: {
                  runtime_id: runtime.runtimeId,
                  session_id: sessionId,
                  status: 'idle',
                  resumed: true,
                },
              });
              return;
            }
            break;

          // POST .../send -> initiate a real agent turn
          case 'send':
            if (method === 'POST') {
              const body = await readJsonBody(req);
              const bc = sessionStream(sessionId);
              const resolved = resolveRuntimeForSession(runtimeId, sessionId);
              if (!resolved) {
                // No runtime resolvable: emit a terminal session_error so
                // stream listeners never hang, and reject explicitly.
                const errEv = finalizeEvent(makeEvent(runtimeId, sessionId, 'session_error', {
                  code: 'runtime_not_resolved',
                  message: 'no runtime bound to session (create one via POST /v1/runtimes/{id}/sessions first, or address an explicit runtime)',
                }));
                hostJournal?.append({ ...errEv });
                bc.broadcast(errEv);
                json(res, 409, { ok: false, error: errEv.payload });
                return;
              }
              const emit = makeSessionEmitter(resolved, runtimeId, sessionId);
              // Synthetic turn opener on the wire keeps the turn lifecycle
              // observable before the first model call completes.
              const startEv = finalizeEvent(makeEvent(resolved.runtimeId, sessionId, 'turn_start', {
                request_id: body.request_id ?? null,
                mode: body.mode ?? 'prompt',
                turn_context: body.turn_context ?? null,
              }));
              hostJournal?.append({ ...startEv });
              bc.broadcast(startEv);
              // Kick off the real agent loop without blocking the response.
              // runTurn never throws (errors surface as session_error +
              // turn_end events); the catch below is a last-resort net.
              resolved
                .runTurn(sessionId, body.content, emit, body.turn_context ?? null)
                .catch((err) => {
                  logger.error?.(`send: turn crashed for session ${sessionId}: ${err?.message ?? err}`);
                  emit({ type: 'session_error', payload: { code: 'turn_crashed', message: String(err?.message ?? err) } });
                });
              json(res, 200, { ok: true, request_id: body.request_id ?? null, accepted: true });
              return;
            }
            break;

          // POST .../cancel
          case 'cancel':
            if (method === 'POST') {
              const body = await readJsonBody(req);
              json(res, 200, { ok: true, cancelled: true, cause: body.cause ?? 'user-request' });
              return;
            }
            break;

          // GET .../events (history, JSON)
          case 'events':
            if (method === 'GET') {
              json(res, 200, { ok: true, events: [] });
              return;
            }
            break;

          // GET .../event-stream (NDJSON live)
          case 'event-stream':
            if (method === 'GET') {
              res.writeHead(200, {
                'content-type': 'application/x-ndjson; charset=utf-8',
                'cache-control': 'no-cache, no-transform',
                connection: 'keep-alive',
              });
              const bc = sessionStream(sessionId);
              bc.add(res);
              // keep-alive; client disconnects by closing
              req.on('close', () => res.end());
              return;
            }
            break;

          // GET .../approvals (pending)
          case 'approvals':
            if (method === 'GET') {
              json(res, 200, { ok: true, approvals: [] });
              return;
            }
            break;

          // POST .../approvals/{aid}/decision
          default: {
            const mDecision = /^approvals\/([^/]+)\/decision$/.exec(action);
            if (method === 'POST' && mDecision) {
              const approvalId = decodeURIComponent(mDecision[1]);
              const body = await readJsonBody(req);
              const settled = runtime.approvalBroker?.settle(approvalId, {
                approved: body.approved !== false,
                reason: body.reason ?? '',
              });
              json(res, settled ? 200 : 404, {
                ok: true,
                settled,
                approval_id: approvalId,
              });
              return;
            }
            break;
          }
        }
      }

      // ---- DELETE .../sessions/{sid} ----
      if (method === 'DELETE' && mSession && !mSession[3]) {
        const sessionId = decodeURIComponent(mSession[2]);
        streams.delete(sessionId);
        sessionRuntimes.delete(sessionId);
        json(res, 200, { ok: true, disposed: true, session_id: sessionId });
        return;
      }

      json(res, 404, { ok: false, error: { code: 'not_found', message: `no route: ${method} ${path}` } });
    } catch (err) {
      logger.error?.(`http-server: ${method} ${path} -> ${err.message}`);
      json(res, 500, { ok: false, error: { code: 'internal', message: err.message } });
    }
  });

  server.on('clientError', (err, socket) => {
    logger.warn?.(`http-server: clientError ${err.message}`);
    if (socket.writable) {
      socket.end('HTTP/1.1 400 Bad Request\r\n\r\n');
    }
  });

  return server;
}

/**
 * Starts the host server on host/port and returns the listen info.
 *
 * @param {import('node:http').Server} server
 * @param {{ host: string, port: number }} bind
 * @returns {Promise<{ host: string, port: number, pid: number }>}
 */
export function startHostServer(server, bind = {}) {
  const host = bind.host ?? '127.0.0.1';
  const port = bind.port ?? 8101;
  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(port, host, () => {
      const addr = server.address();
      const actualPort = typeof addr === 'object' && addr ? addr.port : port;
      resolve({
        host,
        port: actualPort,
        pid: process.pid,
        ready: readyLine(host, actualPort, process.pid),
      });
    });
  });
}
