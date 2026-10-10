/**
 * agent-runner.mjs — the real agent loop for a turn.
 *
 * Drives one conversational turn against the model gateway:
 *
 *   user content ─┬─▶ [model stream] ──text delta──▶ message events
 *                 │        │
 *                 │        └──tool calls──▶ tool_call / tool_result events
 *                 │            │                    │
 *                 └──◀─────────┴── results fed back into messages ◀─┘
 *                 (loop, bounded by MAX_MODEL_ROUNDS)
 *
 * Every event is passed to the injected `emit` callback as
 * `{ type, payload }`; the HTTP layer owns envelope construction,
 * finalizeEvent normalization, the EventJournal and the live broadcast.
 */
import { EVENT_TYPE } from './protocol.mjs';

/** Upper bound of model round-trips per turn (anti run-away guard). */
export const MAX_MODEL_ROUNDS = 8;

/** Upper bound for a single tool instruction summary in the skills block. */
const SKILLS_INSTRUCTION_SUMMARY_CHARS = 200;

/**
 * Answer-assembly policy injected as a second system message. The model owns
 * the final composition of web / knowledge-base / parametric knowledge; this
 * block pins the priority (KB first) and forbids discarding KB hits as
 * "unrelated to the business" — hits come from org-curated content.
 */
const KB_ANSWER_POLICY_BLOCK = [
  '[知识库与作答策略]',
  '- 组织知识库是用户主动收录的权威内容：knowledge_search 的命中结果与用户提问相关，不得以"与企业业务无关"等理由忽略或丢弃命中项。',
  '- 作答优先级：知识库命中内容 > 联网抓取内容 > 自身记忆。知识库有命中时优先基于命中内容作答，并注明来源文档名。',
  '- 检索查询词取自用户问题中的关键词，不要自行编造查询主题；用户问及某主题而库内可能已有相关文档时，先检索再作答，不要直接用自身记忆回答。',
  '- 引用自身记忆中的事实（范文、引文等）时注明"凭记忆整理"；能通过检索或抓取查证的内容先查证再作答。',
].join('\n');

/**
 * Extracts the user's plain text from a `content` blocks array
 * (blocks shape: [{ type: 'text', data: { text } }]).
 *
 * Tolerates a bare string and missing/extra fields by design — malformed
 * content degrades to an empty string instead of throwing.
 *
 * @param {unknown} content blocks array or plain string
 * @returns {string}
 */
export function extractUserText(content) {
  if (typeof content === 'string') {
    return content.trim();
  }
  if (!Array.isArray(content)) {
    return '';
  }
  const parts = [];
  for (const block of content) {
    if (block == null) {
      continue;
    }
    if (typeof block === 'string') {
      parts.push(block);
      continue;
    }
    if (block.type === 'text' || block.type == null) {
      const text = block.data?.text ?? block.text ?? '';
      if (typeof text === 'string' && text) {
        parts.push(text);
      }
    }
  }
  return parts.join('\n').trim();
}

/**
 * Builds OpenAI function-call tool definitions from the Profile tools
 * (`profile.tools[].input_schema` is the JSON Schema `parameters`).
 *
 * @param {object[]} [profileTools]
 * @returns {{ type: 'function', function: { name: string, description: string, parameters: object } }[]}
 */
export function buildToolDefinitions(profileTools) {
  return (profileTools ?? []).map((t) => ({
    type: 'function',
    function: {
      name: String(t?.name ?? ''),
      description: String(t?.description ?? ''),
      parameters: t?.input_schema ?? { type: 'object', properties: {} },
    },
  }));
}

function truncate(text, maxChars) {
  const s = String(text);
  return s.length > maxChars ? `${s.slice(0, maxChars)}…` : s;
}

/**
 * Renders a system block summarizing the Profile skills (name / description /
 * instruction digest). Returns '' when skills are absent so callers can skip
 * the block gracefully.
 *
 * @param {unknown} skills profile.skills (array of objects or strings)
 * @returns {string}
 */
export function buildSkillsBlock(skills) {
  if (!Array.isArray(skills) || skills.length === 0) {
    return '';
  }
  const lines = ['[可用技能]'];
  let count = 0;
  for (const skill of skills) {
    if (skill == null) {
      continue;
    }
    if (typeof skill === 'string') {
      lines.push(`- ${skill}`);
      count += 1;
      continue;
    }
    const name = skill.name ?? skill.id ?? skill.skill_id;
    if (!name) {
      continue;
    }
    // 真实指令正文在 ProfileSkill.definition 内（skill_versions 落的
    // {instructions, files, sha256} 结构）；顶层 description/instructions 在
    // ProfileSkill 上不存在，须回退到 definition 内部字段。
    const def = skill.definition && typeof skill.definition === 'object' ? skill.definition : {};
    const description = skill.description ?? skill.desc ?? def.description ?? def.summary ?? '';
    const instructions = skill.instructions ?? skill.instruction ?? skill.prompt ?? def.instructions ?? def.prompt ?? '';
    let line = `- ${String(name)}`;
    if (description) {
      line += `：${truncate(description, 120)}`;
    }
    if (instructions) {
      line += `（指令摘要：${truncate(instructions, SKILLS_INSTRUCTION_SUMMARY_CHARS)}）`;
    }
    lines.push(line);
    count += 1;
  }
  if (count === 0) {
    return '';
  }
  return lines.join('\n');
}

/**
 * Creates a per-round aggregator that folds a DSH StreamChunk stream
 * (block-start / text-delta / reasoning-delta / tool-call-delta / block-end /
 * usage / finish) into final text, tool calls, usage and finish reason.
 *
 * `block-end` carries the authoritative complete block; `tool-call-delta`
 * fragments are folded by index as the fallback when a block-end is missing.
 *
 * @returns {{ push(chunk: object): void, result(): { text: string, toolCalls: {id:string,name:string,arguments:string}[], finishReason: string, usage: object|null } }}
 */
export function createStreamAggregator() {
  const state = {
    text: '',
    /** @type {Map<number, { id: string, name: string, arguments: string }>} */
    toolCalls: new Map(),
    finishReason: null,
    usage: null,
  };

  return {
    /**
     * @param {object} chunk one DSH StreamChunk dict from the gateway
     */
    push(chunk) {
      if (!chunk || typeof chunk !== 'object') {
        return;
      }
      switch (chunk.type) {
        case 'text-delta':
          if (typeof chunk.text === 'string') {
            state.text += chunk.text;
          }
          break;

        case 'tool-call-delta': {
          const index = Number.isInteger(chunk.index) ? chunk.index : 0;
          const buf = state.toolCalls.get(index) ?? { id: '', name: '', arguments: '' };
          if (chunk.id) {
            buf.id = chunk.id;
          }
          if (chunk.name) {
            buf.name = chunk.name;
          }
          const argsDelta = chunk.argumentsDelta ?? chunk.arguments ?? '';
          if (typeof argsDelta === 'string' && argsDelta) {
            buf.arguments += argsDelta;
          }
          state.toolCalls.set(index, buf);
          break;
        }

        case 'block-end': {
          const block = chunk.block ?? {};
          const index = Number.isInteger(chunk.index) ? chunk.index : 0;
          if (block.type === 'tool-call') {
            // block-end is authoritative for the complete tool call
            state.toolCalls.set(index, {
              id: block.id ?? '',
              name: block.name ?? '',
              arguments: block.arguments ?? '',
            });
          } else if (block.type === 'text' && typeof block.text === 'string') {
            state.text = block.text;
          }
          break;
        }

        case 'usage':
          state.usage = chunk.usage ?? null;
          break;

        case 'finish':
          state.finishReason = chunk.reason ?? 'stop';
          break;

        default:
          // block-start / reasoning-delta / unknown types are ignored
          break;
      }
    },

    /**
     * @returns {{ text: string, toolCalls: { id: string, name: string, arguments: string }[], finishReason: string, usage: object|null }}
     */
    result() {
      return {
        text: state.text,
        toolCalls: [...state.toolCalls.values()].filter((tc) => tc.name),
        finishReason: state.finishReason ?? 'stop',
        usage: state.usage,
      };
    },
  };
}

/**
 * Parses a tool-call arguments JSON string, tolerating empty/malformed input.
 * @param {string} raw
 * @returns {object}
 */
function parseToolArguments(raw) {
  if (!raw || typeof raw !== 'string') {
    return {};
  }
  try {
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch {
    throw new Error(`tool arguments are not valid JSON: ${truncate(raw, 100)}`);
  }
}

/**
 * Runs one full agent turn: model loop + tool execution loop + event emission.
 *
 * @param {object} opts
 * @param {import('./kernel-runtime.mjs').KernelRuntime} opts.runtime the bound runtime
 *        (needs modelAdapter, profile, logger)
 * @param {string} opts.sessionId session the turn belongs to
 * @param {unknown} opts.content user content blocks ([{type:'text',data:{text}}])
 * @param {(evt: { type: string, payload: object }) => void} [opts.emit]
 *        injected event sink (envelope + journal + broadcast owned by the caller)
 * @param {unknown} [opts.turnContext] opaque per-turn context, echoed on round 0
 * @returns {Promise<{ ok: boolean, text: string, finishReason: string, error?: object }>}
 */
export async function runTurn({ runtime, sessionId, content, emit, turnContext = null }) {
  const emitOrNoop = typeof emit === 'function' ? emit : () => {};
  const logger = runtime?.logger ?? console;
  const emitSafe = (evt) => {
    try {
      emitOrNoop(evt);
    } catch (err) {
      // journal/broadcast failures must never kill the turn itself
      logger.warn?.(`agent-runner: emit failed for ${sessionId}: ${err?.message ?? err}`);
    }
  };

  try {
    const profile = runtime.profile ?? {};
    const model = profile.model?.model_name ?? profile.model?.model ?? 'default';
    const userText = extractUserText(content);

    const messages = [];
    const skillsBlock = buildSkillsBlock(profile.skills);
    if (skillsBlock) {
      messages.push({ role: 'system', content: skillsBlock });
    }
    messages.push({ role: 'system', content: KB_ANSWER_POLICY_BLOCK });
    messages.push({ role: 'user', content: userText });

    // tool registry: Map<name, { definition, execute }>
    const tools = runtime.toolRegistry ?? new Map();
    const toolDefinitions = buildToolDefinitions(profile.tools);

    let finalText = '';
    let finishReason = 'stop';

    for (let round = 0; round < MAX_MODEL_ROUNDS; round += 1) {
      emitSafe({
        type: EVENT_TYPE.MODEL_CALL,
        payload: {
          round,
          model,
          tool_count: tools.size,
          ...(round === 0 ? { turn_context: turnContext ?? null } : {}),
        },
      });

      // --- consume the model stream, aggregating deltas ---
      const aggregator = createStreamAggregator();
      const stream = runtime.modelAdapter.stream({
        model,
        messages,
        ...(toolDefinitions.length > 0 ? { tools: toolDefinitions, toolChoice: 'auto' } : {}),
      });
      for await (const chunk of stream) {
        aggregator.push(chunk);
        if (chunk?.type === 'text-delta' && typeof chunk.text === 'string' && chunk.text) {
          emitSafe({
            type: EVENT_TYPE.MESSAGE,
            payload: { round, index: chunk.index ?? 0, delta: chunk.text },
          });
        }
      }

      const { text, toolCalls, finishReason: roundReason } = aggregator.result();
      finishReason = roundReason;
      finalText = text;

      if (toolCalls.length === 0) {
        // plain answer — turn finished
        break;
      }

      // --- execute tool calls and feed results back into the loop ---
      messages.push({
        role: 'assistant',
        content: text || null,
        tool_calls: toolCalls.map((tc, i) => ({
          id: tc.id || `call_${round}_${i}`,
          type: 'function',
          function: { name: tc.name, arguments: tc.arguments || '{}' },
        })),
      });

      for (let i = 0; i < toolCalls.length; i += 1) {
        const tc = toolCalls[i];
        const callId = tc.id || `call_${round}_${i}`;
        // 'tool_call' is a known native DSH session type (protocol.mjs
        // KNOWN_NATIVE_SESSION_EVENT_TYPES); EVENT_TYPE has no TOOL_CALL key.
        emitSafe({
          type: 'tool_call',
          payload: { round, id: callId, name: tc.name, arguments: tc.arguments || '{}' },
        });

        let toolPayload;
        try {
          const entry = tools.get(tc.name);
          if (!entry || typeof entry.execute !== 'function') {
            throw new Error(`unknown tool: ${tc.name}`);
          }
          const args = parseToolArguments(tc.arguments);
          const result = await entry.execute(args, { callId, sessionId });
          toolPayload = { ok: true, result: result ?? null };
        } catch (err) {
          toolPayload = {
            ok: false,
            error: { code: 'tool_execution_error', message: String(err?.message ?? err) },
          };
        }

        emitSafe({
          type: EVENT_TYPE.TOOL_RESULT,
          payload: { round, id: callId, name: tc.name, ...toolPayload },
        });
        messages.push({
          role: 'tool',
          tool_call_id: callId,
          content: JSON.stringify(toolPayload),
        });
      }
      // loop continues: the model sees the tool results next round
    }

    emitSafe({
      type: EVENT_TYPE.TURN_END,
      payload: { text: finalText, finish_reason: finishReason },
    });
    return { ok: true, text: finalText, finishReason };
  } catch (err) {
    logger.error?.(`agent-runner: turn failed for session ${sessionId}: ${err?.message ?? err}`);
    emitSafe({
      type: EVENT_TYPE.SESSION_ERROR,
      payload: { code: 'turn_failed', message: String(err?.message ?? err) },
    });
    emitSafe({
      type: EVENT_TYPE.TURN_END,
      payload: { text: '', finish_reason: 'error' },
    });
    return {
      ok: false,
      text: '',
      finishReason: 'error',
      error: { code: 'turn_failed', message: String(err?.message ?? err) },
    };
  }
}
