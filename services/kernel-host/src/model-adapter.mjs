/**
 * model-adapter.mjs — LlmAdapter that reverse-calls the ZY-Agents model gateway.
 *
 * The host never holds model API keys. When DSH's LLM runtime wants a model
 * call, this adapter POSTs to the main service's `/internal/kernel/model/generate`
 * endpoint (with the short-lived model credential token) and consumes the
 * NDJSON stream back into DSH's `stream(options)` contract.
 *
 * See docs/03 §4, §6.1. Every outbound provider request MUST attach
 * `attributionHeaders()` as required by DSH 0.2.0.
 */
import { LlmAdapter, attributionHeaders } from '@deepseek-ai/dsh-llm';
import { GatewayClient } from './gateway-client.mjs';

/**
 * @typedef {object} ModelGatewayOpts
 * @property {string} gatewayUrl  base URL of the main service (model endpoint path is appended)
 * @property {string} token       short-lived model credential token
 * @property {number} [timeoutMs] per-request timeout
 */

export class GatewayLlmAdapter extends LlmAdapter {
  /**
   * @param {ModelGatewayOpts} opts
   */
  constructor(opts) {
    super();
    this.gatewayUrl = opts.gatewayUrl;
    this.token = opts.token;
    this.timeoutMs = opts.timeoutMs ?? 120000;
    this._client = new GatewayClient({
      baseUrl: opts.gatewayUrl,
      token: opts.token,
      timeoutMs: this.timeoutMs,
    });
  }

  /**
   * Adapter identity — the provider this adapter serves (configured at runtime
   * assembly time).
   * @param {any} provider
   * @returns {any}
   */
  providerInfo(provider) {
    return {
      id: String(provider?.id ?? 'zy-gateway'),
      name: String(provider?.name ?? 'ZY Model Gateway'),
    };
  }

  /**
   * The set of models available through the gateway. The gateway is the single
   * source of truth for model availability (the main service owns provider
   * configuration), so we simply claim every model we are asked about and let
   * the gateway validate at call time.
   * @param {any} provider
   * @returns {Promise<string[]>}
   */
  async listModels(provider) {
    const idOf = (p) => String(p?.id ?? p ?? 'default');
    // No enumeration RPC is guaranteed on the gateway; defer by returning the
    // provider's own declared model when present.
    const declared = provider?.models ?? provider?.model;
    if (Array.isArray(declared)) {
      return declared.map((m) => String(m));
    }
    return idOf(provider);
  }

  /**
   * Resolve a model reference to the canonical model name to send upstream.
   * @param {any} provider
   * @param {string} model
   * @returns {Promise<string>}
   */
  async resolveModel(provider, model) {
    return String(model ?? provider?.model ?? 'default');
  }

  /**
   * Stream a model generation by POSTing to the gateway and parsing the NDJSON
   * stream. Each NDJSON object is forwarded to DSH through the iterator.
   *
   * `options.tools` (OpenAI function format) and `options.toolChoice` are
   * forwarded as top-level `tools` / `tool_choice` request keys — the model
   * gateway's `_build_provider_request` passes them through to the provider.
   * JSON.stringify drops undefined keys, so absent tools are not sent.
   *
   * @param {object} options DSH stream options (messages, model, tools, etc.)
   * @returns {AsyncGenerator<any>}
   */
  async *stream(options) {
    const identity = (options && options.identity) || { source: 'zy-kernel-host' };
    const attribution = attributionHeaders(identity);
    const effectiveHeaders = { ...attribution };

    const request = {
      model: options?.model ?? 'default',
      messages: options?.messages ?? [],
      stream: true,
      temperature: options?.temperature,
      top_p: options?.topP,
      max_tokens: options?.maxTokens,
      stop: options?.stop,
      tools: options?.tools,
      tool_choice: options?.toolChoice,
      extra: options?.extraBody ?? {},
      attribution: effectiveHeaders,
    };

    yield* this._client.streamFromGenerator(request);
  }
}
