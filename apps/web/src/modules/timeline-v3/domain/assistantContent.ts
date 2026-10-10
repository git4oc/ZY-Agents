import type { TimelineEventV3 } from './protocol'

export type AssistantContentTarget = {
  content: string
}

/**
 * 仅把已判定为最终答复的文本提交到答复正文。
 *
 * 内核侧的临时增量保留在 ExecutionStoreV3，并在其稳定的事件序位置上渲染。
 * 待内核完成消息分类后，同一项要么就地成为 Timeline 旁注，要么在此提交为
 * 最终答复。这样在工具循环中无需在答复正文与 Timeline 之间反复改挂载关系。
 */
export function applyAssistantContentEvent(
  message: AssistantContentTarget,
  event: TimelineEventV3,
): void {
  if (event.item_kind === 'final_answer' && event.type === 'item.delta') {
    // 该原生增量已由执行 store 累积并暴露。
    // 在消息完成之前，它的语义归属尚不确定。
    return
  }
  if (event.item_kind === 'final_answer' && event.type === 'item.completed') {
    message.content = String(event.payload?.text ?? message.content)
  }
}
