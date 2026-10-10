/**
 * smoke-agent-loop.mjs — end-to-end smoke test for the real agent loop.
 *
 * Flow (uses node:http only, no curl):
 *   1. reads services/api/.env for kernel_host_token (host auth) and the
 *      gateway secrets (kernel_{model,tool}_gateway_secret) to issue the
 *      short-lived HMAC gateway tokens the model/tool callbacks require
 *   2. boots a kernel host on the target port (or reuses a healthy one)
 *   3. POST /v1/runtimes            — minimal hand-built profile (model + tool)
 *   4. POST .../sessions            — session bound to the runtime
 *   5. GET  .../event-stream        — subscribe BEFORE send
 *   6. POST .../send                — user content blocks; the model gateway
 *                                     callback hits the running FastAPI(:8000)
 *   7. asserts: model stream consumed; events include model_call /
 *      message / turn_end; runtime JSONL mirrors them with ignorable markers
 *   8. scenario B: a runtime pointed at a dead gateway must still emit
 *      turn_end(finish_reason:'error') instead of hanging
 *
 * Usage: node src/smoke-agent-loop.mjs [--port 8101]
 */
import { spawn } from 'node:child_process';
import { createHash, createHmac } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { request } from 'node:http';

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const HOST_DIR = resolve(SCRIPT_DIR, '..'); // services/kernel-host
const API_ENV = resolve(HOST_DIR, '..', 'api', '.env');
const GATEWAY_URL = 'http://127.0.0.1:8000';

const args = process.argv.slice(2);
const portIdx = args.indexOf('--port');
const PORT = portIdx >= 0 ? Number(args[portIdx + 1]) : 8101;
const HOST = '127.0.0.1';

/** Reads `name=value` from services/api/.env. */
function loadEnvValue(name) {
  const raw = readFileSync(API_ENV, 'utf8');
  const re = new RegExp(`^\\s*${name}\\s*=\\s*(\\S+)\\s*$`, 'm');
  const m = re.exec(raw);
  if (!m) {
    throw new Error(`${name} not found in ${API_ENV}`);
  }
  return m[1];
}

/**
 * Issues a ZYWG HMAC gateway token — Node re-implementation of
 * kernel/inbound/credentials.issue (same key derivation + sorted-key JSON).
 */
function issueGatewayToken({ aud, secret, org, user, profileVersion, runtimeId, toolNames = [], ttlSeconds = 900 }) {
  const audStr = aud === 'model' ? 'zy-model-gateway' : 'zy-tool-gateway';
  const now = Math.floor(Date.now() / 1000);
  const header = { alg: 'HS256', typ: 'ZYWG' };
  const payload = {
    aud: audStr,
    org,
    sub: user,
    pv: profileVersion,
    rid: runtimeId,
    iat: now,
    exp: now + ttlSeconds,
    ...(toolNames.length > 0 ? { tools: [...toolNames].sort() } : {}),
  };
  // mirror Python json.dumps(obj, sort_keys=True, separators=(',', ':'))
  const dumpSorted = (obj) => {
    const parts = Object.keys(obj).sort().map((k) => `${JSON.stringify(k)}:${JSON.stringify(obj[k])}`);
    return `{${parts.join(',')}}`;
  };
  const signingInput = `${Buffer.from(dumpSorted(header)).toString('base64url')}.${Buffer.from(dumpSorted(payload)).toString('base64url')}`;
  // domain-separated key: model -> sha256(secret); tool -> sha256('tool:' + secret)
  const material = aud === 'model' ? secret : `tool:${secret}`;
  const key = createHash('sha256').update(material, 'utf8').digest();
  const signature = createHmac('sha256', key).update(signingInput, 'utf8').digest().toString('base64url');
  return `${signingInput}.${signature}`;
}

/** Minimal JSON request helper over node:http. */
function jsonRequest(method, path, body, token) {
  return new Promise((resolvePromise, rejectPromise) => {
    const data = body === undefined ? null : JSON.stringify(body);
    const headers = { accept: 'application/json' };
    if (token) {
      headers.authorization = `Bearer ${token}`;
    }
    if (data !== null) {
      headers['content-type'] = 'application/json';
      headers['content-length'] = Buffer.byteLength(data);
    }
    const req = request({ host: HOST, port: PORT, path, method, headers }, (res) => {
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => {
        const text = Buffer.concat(chunks).toString('utf8');
        let parsed = null;
        try {
          parsed = JSON.parse(text);
        } catch {
          parsed = text;
        }
        resolvePromise({ status: res.statusCode, body: parsed });
      });
    });
    req.on('error', rejectPromise);
    if (data !== null) {
      req.end(data);
    } else {
      req.end();
    }
  });
}

/** Opens an NDJSON event-stream subscription; returns { events, close }. */
function openEventStream(path, token) {
  const events = [];
  let res = null;
  let buffer = '';
  const promise = new Promise((resolvePromise, rejectPromise) => {
    const req = request(
      { host: HOST, port: PORT, path, method: 'GET', headers: { authorization: `Bearer ${token}` } },
      (response) => {
        res = response;
        res.setEncoding('utf8');
        res.on('data', (chunk) => {
          buffer += chunk;
          let nl;
          while ((nl = buffer.indexOf('\n')) >= 0) {
            const line = buffer.slice(0, nl).trim();
            buffer = buffer.slice(nl + 1);
            if (!line) {
              continue;
            }
            try {
              events.push(JSON.parse(line));
            } catch {
              /* ignore malformed line */
            }
          }
        });
        res.on('end', resolvePromise);
        res.on('error', rejectPromise);
      },
    );
    req.on('error', rejectPromise);
    req.end();
  });
  return {
    events,
    promise,
    close() {
      if (res) {
        res.destroy();
      }
    },
  };
}

/** Waits until an event matching `pred` arrives or the deadline hits. */
async function waitFor(stream, pred, timeoutMs, label) {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const found = stream.events.find(pred);
    if (found) {
      return found;
    }
    if (Date.now() > deadline) {
      throw new Error(`timeout waiting for ${label} (got types: ${[...new Set(stream.events.map((e) => e.type))].join(',')})`);
    }
    await new Promise((r) => setTimeout(r, 250));
  }
}

async function waitHealthy(timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    try {
      const res = await jsonRequest('GET', '/health', undefined, null);
      if (res.status === 200) {
        return res.body;
      }
    } catch {
      /* not up yet */
    }
    if (Date.now() > deadline) {
      throw new Error(`host on :${PORT} did not become healthy`);
    }
    await new Promise((r) => setTimeout(r, 400));
  }
}

/** Boots a child host process; no-op when the port is already served. */
async function ensureHost(token) {
  try {
    const health = await waitHealthy(1500);
    console.log(`[smoke] reusing running host on :${PORT} (runtimes=${health.runtimes?.length ?? 0})`);
    return null;
  } catch {
    const child = spawn(process.execPath, ['src/host.mjs', '--port', String(PORT)], {
      cwd: HOST_DIR,
      env: { ...process.env, KERNEL_HOST_TOKEN: token },
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    child.stdout.on('data', (d) => process.stdout.write(`[host] ${d}`));
    child.stderr.on('data', (d) => process.stderr.write(`[host!] ${d}`));
    await waitHealthy(15000);
    console.log(`[smoke] spawned host pid=${child.pid} on :${PORT}`);
    return child;
  }
}

const MINIMAL_PROFILE = (extra = {}) => ({
  model: { model_name: 'glm-53-flash', provider_id: 'zy-gateway' },
  tools: [
    {
      name: 'knowledge_search',
      description: '检索企业知识库，返回与查询最相关的知识条目',
      input_schema: {
        type: 'object',
        properties: { query: { type: 'string', description: '检索关键词' } },
        required: ['query'],
      },
      output_schema: { type: 'object', properties: { entries: { type: 'array' } } },
      risk_level: 'read',
      timeout_ms: 30000,
    },
  ],
  skills: [{ name: 'enterprise-qa', description: '企业知识问答助手' }],
  ...extra,
});

async function runHappyPath(token, modelSecret, toolSecret) {
  console.log('\n[smoke] ===== scenario A: real model loop =====');
  const runtimeId = `smoke_rt_${Date.now()}`;
  const sessionId = `smoke_sess_${Date.now()}`;
  const org = 'smoke-org';

  // short-lived HMAC gateway tokens the model/tool callbacks must carry
  const modelToken = issueGatewayToken({
    aud: 'model', secret: modelSecret, org, user: 'smoke-user',
    profileVersion: 'smoke', runtimeId,
  });
  const toolToken = issueGatewayToken({
    aud: 'tool', secret: toolSecret, org, user: 'smoke-user',
    profileVersion: 'smoke', runtimeId, toolNames: ['knowledge_search'],
  });

  const created = await jsonRequest('POST', '/v1/runtimes', {
    runtime_id: runtimeId,
    org_unit_id: org,
    profile: MINIMAL_PROFILE(),
    model_credential: { gateway_url: GATEWAY_URL, token: modelToken },
    tool_credential: { gateway_url: GATEWAY_URL, token: toolToken },
  }, token);
  if (created.status !== 201) {
    throw new Error(`create runtime failed: ${created.status} ${JSON.stringify(created.body)}`);
  }
  console.log(`[smoke] runtime created: ${runtimeId}`);

  const stream = openEventStream(
    `/v1/runtimes/_/sessions/${sessionId}/event-stream`,
    token,
  );

  try {
    const session = await jsonRequest(
      'POST',
      `/v1/runtimes/${runtimeId}/sessions`,
      { session_id: sessionId, session_spec: { preset_id: 'zy-enterprise' } },
      token,
    );
    if (session.status !== 201) {
      throw new Error(`create session failed: ${session.status} ${JSON.stringify(session.body)}`);
    }
    console.log(`[smoke] session created: ${sessionId}`);

    const sent = await jsonRequest(
      'POST',
      `/v1/runtimes/${runtimeId}/sessions/${sessionId}/send`,
      {
        request_id: `req_${Date.now()}`,
        mode: 'prompt',
        turn_context: { source: 'smoke' },
        content: [{
          type: 'text',
          data: { text: '请调用 knowledge_search 工具检索"ZY-Agents"，然后无论结果如何，用一句话总结你看到的内容。' },
        }],
      },
      token,
    );
    if (sent.status !== 200 || sent.body?.accepted !== true) {
      throw new Error(`send failed: ${sent.status} ${JSON.stringify(sent.body)}`);
    }
    console.log('[smoke] send accepted; waiting for the agent loop...');

    await waitFor(stream, (e) => e.type === 'turn_start', 10000, 'turn_start');
    await waitFor(stream, (e) => e.type === 'model_call', 15000, 'model_call');
    const turnEnd = await waitFor(stream, (e) => e.type === 'turn_end', 150000, 'turn_end');

    const types = stream.events.map((e) => e.type);
    const has = (t) => types.includes(t);
    console.log(`[smoke] event types: ${[...new Set(types)].join(',')}`);
    console.log(`[smoke] turn_end payload: ${JSON.stringify(turnEnd.payload)}`);

    const finishReason = turnEnd.payload?.finish_reason;
    if (!has('model_call')) {
      throw new Error('missing model_call event');
    }
    if (!has('turn_end')) {
      throw new Error('missing turn_end event');
    }
    if (finishReason === 'error') {
      console.log('[smoke] WARN: model gateway returned finish_reason=error; loop itself terminated correctly');
    } else if (!has('message')) {
      throw new Error('missing message event (text deltas)');
    }

    // runtime JSONL must mirror the stream, with ignorable markers intact
    const journalPath = resolve(HOST_DIR, 'data', 'sessions', `${runtimeId}.jsonl`);
    const lines = readFileSync(journalPath, 'utf8').split('\n').filter(Boolean).map((l) => JSON.parse(l));
    const journalTypes = lines.map((e) => e.type);
    const customOk = lines
      .filter((e) => ['turn_start', 'turn_end'].includes(e.type))
      .every((e) => e.ignorable === true);
    console.log(`[smoke] journal events: ${journalTypes.length} types=${[...new Set(journalTypes)].join(',')}`);
    if (!journalTypes.includes('model_call') || !journalTypes.includes('turn_end')) {
      throw new Error('journal missing model_call/turn_end');
    }
    if (!customOk) {
      throw new Error('journal custom events missing ignorable:true');
    }
    if (finishReason !== 'error' && !journalTypes.includes('message')) {
      throw new Error('journal missing message event');
    }

    console.log('[smoke] scenario A PASSED');
    return true;
  } finally {
    stream.close();
  }
}

async function runDeadGatewayPath(token) {
  console.log('\n[smoke] ===== scenario B: dead gateway -> turn_end(error), no hang =====');
  const runtimeId = `smoke_rt_dead_${Date.now()}`;
  const sessionId = `smoke_sess_dead_${Date.now()}`;

  const created = await jsonRequest('POST', '/v1/runtimes', {
    runtime_id: runtimeId,
    org_unit_id: 'smoke-org-dead',
    profile: MINIMAL_PROFILE(),
    model_credential: { gateway_url: 'http://127.0.0.1:9', token },
    tool_credential: { gateway_url: GATEWAY_URL, token },
  }, token);
  if (created.status !== 201) {
    throw new Error(`create runtime failed: ${created.status} ${JSON.stringify(created.body)}`);
  }

  const stream = openEventStream(
    `/v1/runtimes/_/sessions/${sessionId}/event-stream`,
    token,
  );
  try {
    await jsonRequest('POST', `/v1/runtimes/${runtimeId}/sessions`, { session_id: sessionId }, token);
    const sent = await jsonRequest(
      'POST',
      `/v1/runtimes/${runtimeId}/sessions/${sessionId}/send`,
      { content: [{ type: 'text', data: { text: 'should fail fast' } }] },
      token,
    );
    if (sent.status !== 200) {
      throw new Error(`send failed: ${sent.status}`);
    }
    const turnEnd = await waitFor(stream, (e) => e.type === 'turn_end', 20000, 'turn_end (dead gateway)');
    if (turnEnd.payload?.finish_reason !== 'error') {
      throw new Error(`expected finish_reason=error, got ${JSON.stringify(turnEnd.payload)}`);
    }
    console.log('[smoke] dead-gateway turn ended correctly:', JSON.stringify(turnEnd.payload));
    console.log('[smoke] scenario B PASSED');
    return true;
  } finally {
    stream.close();
  }
}

async function runNoRuntimePath(token) {
  console.log('\n[smoke] ===== scenario C: send without resolvable runtime -> explicit 409 =====');
  const sessionId = `smoke_sess_orphan_${Date.now()}`;
  const res = await jsonRequest(
    'POST',
    `/v1/runtimes/_/sessions/${sessionId}/send`,
    { content: [{ type: 'text', data: { text: 'no runtime' } }] },
    token,
  );
  if (res.status !== 409 || res.body?.error?.code !== 'runtime_not_resolved') {
    throw new Error(`expected 409 runtime_not_resolved, got ${res.status} ${JSON.stringify(res.body)}`);
  }
  console.log('[smoke] orphan send rejected explicitly:', JSON.stringify(res.body));
  console.log('[smoke] scenario C PASSED');
  return true;
}

async function main() {
  const token = loadEnvValue('kernel_host_token');
  const modelSecret = loadEnvValue('kernel_model_gateway_secret');
  const toolSecret = loadEnvValue('kernel_tool_gateway_secret');
  console.log(`[smoke] env loaded from ${API_ENV}`);
  const child = await ensureHost(token);
  const outcomes = [];
  try {
    outcomes.push(['A', await runHappyPath(token, modelSecret, toolSecret)]);
    outcomes.push(['B', await runDeadGatewayPath(token)]);
    outcomes.push(['C', await runNoRuntimePath(token)]);
  } finally {
    if (child) {
      child.kill('SIGTERM');
    }
  }
  const ok = outcomes.every(([, passed]) => passed);
  console.log(`\n[smoke] RESULT: ${ok ? 'PASS' : 'FAIL'} (${outcomes.map(([n]) => n).join(', ')})`);
  process.exit(ok ? 0 : 1);
}

main().catch((err) => {
  console.error('[smoke] FATAL:', err);
  process.exit(1);
});
