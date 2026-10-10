/**
 * tool-bridge.mjs — register Profile tools with DSH and bridge execution to the
 * ZY-Agents tool gateway.
 *
 * Each tool definition from the Runtime Profile is registered via
 * `ctx.tools.register(...)`. Its `execute` POSTs to the main service
 * `/internal/kernel/tools/execute` endpoint (with the short-lived tool credential
 * token), which performs authorization + approval + dispatch per tool category.
 *
 * See docs/03 §4.3, §6.2 for the tool definition shape (0.2.0-rc.1):
 *   { name, description, parameters, output{must be declared}, timeoutMs,
 *     isConcurrencySafe(args), execute(args, exec) }
 */
import { validateJsonSchemaValue } from '@deepseek-ai/dsh-tools';
import { GatewayClient } from './gateway-client.mjs';

/**
 * Registers a single Profile tool with the DSH tool runtime.
 *
 * @param {object} runtime the cordis context / tool runtime (runtime.ctx.tools)
 * @param {object} profileTool the tool definition from the compiled Profile
 * @param {object} deps { gateway: GatewayClient, logger }
 * @returns {{ disposer: Function, definition: object, execute: Function }}
 *          the registration entry (disposer + the DSH ToolSchema definition +
 *          its execute) so the agent runner can call tools directly
 */
export function registerProfileTool(runtime, profileTool, deps) {
  const { gateway, logger } = deps;
  const toolName = profileTool.name;
  const riskLevel = profileTool.risk_level ?? 'read';

  const definition = {
    name: toolName,
    description: profileTool.description ?? '',
    // ToolSchema.parameters is a JSON Schema object (03 §7.2). We pass the
    // compiled input_schema through unchanged.
    parameters: profileTool.input_schema ?? { type: 'object', properties: {} },
    output: {
      // 0.2.0 marks `output` as mandatory canonical output declaration.
      schema: profileTool.output_schema ?? { type: 'object' },
      render: (args, value) => [{
        type: 'text',
        text: JSON.stringify(value ?? null),
      }],
    },
    timeoutMs: profileTool.timeout_ms ?? 120000,
    // Only read-risk tools may run concurrently; everything else is exclusive.
    isConcurrencySafe: () => riskLevel === 'read',
    execute: async (args, exec) => {
      const callId = exec?.callId ?? `tool:${toolName}:${Date.now()}:${Math.random().toString(36).slice(2, 8)}`;
      logger?.debug?.(`tool-bridge: executing ${toolName} (${callId})`);
      // The gateway is authoritative for authorization, approval, dispatch and
      // audit. We never decide tool semantics here (S-xx: single execution path).
      // Field names follow the gateway contract (kernel/inbound/tool_endpoint.py):
      // `tool_name` / `arguments` / `call_id` / `session_id` / `timeout_ms`.
      const result = await gateway.postJson('/internal/kernel/tools/execute', {
        tool_name: toolName,
        arguments: args,
        call_id: callId,
        session_id: exec?.sessionId,
        timeout_ms: definition.timeoutMs,
      });
      if (!result || result.ok === false) {
        const err = result?.error ?? { code: 'tool_gateway_error', message: 'tool gateway error' };
        throw new Error(`${err.code}: ${err.message}`);
      }
      return result.result;
    },
  };

  // Validate our declared parameters against JSON Schema conventions before
  // registering (catches malformed Profile tool schemas early).
  if (definition.parameters && definition.parameters.type) {
    try {
      validateJsonSchemaValue(definition.parameters, {});
    } catch {
      // {} may legitimately fail if properties are required; this is only a
      // sanity probe, so a failure here is not fatal.
      logger?.debug?.(`tool-bridge: schema probe for ${toolName} did not accept {} (expected for required args)`);
    }
  }

  const disposer = runtime.ctx.tools.register(definition);
  logger?.info?.(`tool-bridge: registered tool '${toolName}' (risk=${riskLevel})`);
  return { disposer, definition, execute: definition.execute };
}

/**
 * Register every tool in the Profile with the DSH tool runtime.
 *
 * @param {object} runtime cordis context wrapper
 * @param {object[]} tools Profile tools[]
 * @param {object} deps { gateway: GatewayClient, logger }
 * @returns {Map<string, { disposer: Function, definition: object, execute: Function }>}
 *          registry keyed by tool name, consumed by the agent runner to
 *          dispatch model-requested tool calls directly
 */
export function registerAllProfileTools(runtime, tools, deps) {
  /** @type {Map<string, { disposer: Function, definition: object, execute: Function }>} */
  const registry = new Map();
  for (const t of tools ?? []) {
    const entry = registerProfileTool(runtime, t, deps);
    registry.set(entry.definition.name, entry);
  }
  return registry;
}
