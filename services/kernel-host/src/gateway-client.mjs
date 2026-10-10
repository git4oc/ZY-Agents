/**
 * gateway-client.mjs — outbound HTTP client used for model/tool gateway
 * reverse callbacks.
 *
 * The host never holds model API keys. When DSH decides to make a model call
 * or execute a tool, the host POSTs to the main service gateway URL, carrying
 * the short-lived gateway token it was issued via the credential endpoints.
 *
 * See docs/03 §4 (双网关反向回调) and §5.
 */

export class GatewayError extends Error {
  /**
   * @param {string} message
   * @param {number} status
   * @param {unknown} [payload]
   */
  constructor(message, status, payload) {
    super(message);
    this.name = 'GatewayError';
    this.status = status;
    this.payload = payload;
  }
}

/**
 * Thin wrapper over fetch with Bearer auth and NDJSON body handling.
 */
export class GatewayClient {
  /**
   * @param {{ baseUrl?: string, token?: string, timeoutMs?: number, fetchImpl?: Function }} opts
   */
  constructor(opts = {}) {
    this.baseUrl = (opts.baseUrl ?? '').replace(/\/+$/, '');
    this.token = opts.token ?? '';
    this.timeoutMs = opts.timeoutMs ?? 120000;
    this._fetch = opts.fetchImpl ?? globalThis.fetch;
    this._abortController = null;
  }

  /** @returns {Record<string,string>} */
  _headers(contentType = 'application/json') {
    const h = { 'content-type': contentType, accept: 'application/json' };
    if (this.token) {
      h.authorization = `Bearer ${this.token}`;
    }
    return h;
  }

  _applyTimeout(signal) {
    this._abortController = new AbortController();
    const timer = setTimeout(() => this._abortController.abort(new Error('gateway timeout')), this.timeoutMs);
    // wire the external signal (if any) to our controller
    if (signal) {
      if (signal.aborted) {
        this._abortController.abort(signal.reason);
      } else {
        signal.addEventListener('abort', () => this._abortController.abort(signal.reason), { once: true });
      }
    }
    return { controller: this._abortController, timer };
  }

  /**
   * POST JSON to a gateway path, returning a parsed JSON object.
   * @param {string} path e.g. '/internal/kernel/model/generate'
   * @param {unknown} body
   * @param {{ signal?: AbortSignal }} [extra]
   * @returns {Promise<any>}
   */
  async postJson(path, body, extra = {}) {
    const { controller, timer } = this._applyTimeout(extra.signal);
    try {
      const res = await this._fetch(`${this.baseUrl}${path}`, {
        method: 'POST',
        headers: this._headers(),
        body: JSON.stringify(body),
        signal: controller.signal,
      });
      const text = await res.text();
      if (!res.ok) {
        let payload;
        try {
          payload = text ? JSON.parse(text) : null;
        } catch {
          payload = text;
        }
        throw new GatewayError(`gateway ${path} returned ${res.status}`, res.status, payload);
      }
      if (!text) {
        return null;
      }
      try {
        return JSON.parse(text);
      } catch {
        return text;
      }
    } finally {
      clearTimeout(timer);
    }
  }

  /**
   * POST a JSON body and consume NDJSON lines from the response body stream,
   * invoking `onLine` for each parsed object.
   *
   * Used for streaming model generation (a line may itself be an SSE-style
   * `data: {...}` payload or a bare JSON object; we parse whatever is there).
   *
   * @param {string} path
   * @param {unknown} body
   * @param {(obj: any) => void | Promise<void>} onLine
   * @param {{ signal?: AbortSignal }} [extra]
   * @returns {Promise<void>}
   */
  async postNdjson(path, body, onLine, extra = {}) {
    for await (const payload of this._ndjsonGenerator(path, body, extra)) {
      await onLine(payload);
    }
  }

  /**
   * Convenience wrapper: POST JSON and pull the received NDJSON chunk objects
   * through an async generator. This plays nicely with DSH `LlmAdapter.stream`.
   * @param {string} path
   * @param {unknown} body
   * @param {{ signal?: AbortSignal }} [extra]
   * @returns {AsyncGenerator<any, void, void>}
   */
  async *postNdjsonGenerator(path, body, extra = {}) {
    yield* this._ndjsonGenerator(path, body, extra);
  }

  /**
   * Stream helper used by model adapters: POST and yield chunk objects from the
   * model gateway endpoint.
   * @param {object} request
   * @returns {AsyncGenerator<any, void, void>}
   */
  async *streamFromGenerator(request) {
    yield* this._ndjsonGenerator('/internal/kernel/model/generate', request);
  }

  /**
   * Shared NDJSON pull-reader used by stream helpers.
   * @param {string} path
   * @param {unknown} body
   * @param {{ signal?: AbortSignal }} [extra] optional; callers may omit it
   * @returns {AsyncGenerator<any, void, void>}
   */
  async *_ndjsonGenerator(path, body, extra = {}) {
    const { controller, timer } = this._applyTimeout(extra.signal);
    try {
      const res = await this._fetch(`${this.baseUrl}${path}`, {
        method: 'POST',
        headers: this._headers(),
        body: JSON.stringify(body),
        signal: controller.signal,
      });
      if (!res.ok || !res.body) {
        const text = await res.text().catch(() => '');
        throw new GatewayError(`gateway ${path} returned ${res.status}`, res.status, text);
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      for (;;) {
        const { done, value } = await reader.read();
        if (done) {
          break;
        }
        buffer += decoder.decode(value, { stream: true });
        let nl;
        while ((nl = buffer.indexOf('\n')) >= 0) {
          const line = buffer.slice(0, nl);
          buffer = buffer.slice(nl + 1);
          const trimmed = line.trim();
          if (!trimmed) {
            continue;
          }
          let payload;
          if (trimmed.startsWith('data:')) {
            const data = trimmed.slice('data:'.length).trim();
            payload = data && data !== '[DONE]' ? JSON.parse(data) : null;
          } else {
            payload = JSON.parse(trimmed);
          }
          if (payload != null) {
            yield payload;
          }
        }
      }
    } finally {
      clearTimeout(timer);
    }
  }
}
