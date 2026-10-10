// 统一的后端错误文案提取（三域共用：会话 / 知识 / skills）。
//
// 后端并存三种错误形状（均为既成契约，前端统一兜住）：
// 1. data.detail 纯文本          —— FastAPI 原生 HTTPException / 部分端点直传
// 2. data.detail 结构化对象      —— ZIP 校验（{code, file, field}，调用方按 code 映射文案）
// 3. data.error.message          —— core.errors DomainError 统一体（02 §4.1）
//    {"error": {code, message, details, trace_id}}，由 ErrorMappingMiddleware 产出
//
// extractErrorMessage 只负责"拿到一句给人看的话"；结构化 code 的文案映射
// （如 skillInstallError.ts）在调用方自行处理。

export function extractErrorMessage(data: any, fallback: string): string {
  const detail = data?.detail;
  if (typeof detail === 'string' && detail.trim()) return detail;
  const unifiedMessage = data?.error?.message;
  if (typeof unifiedMessage === 'string' && unifiedMessage.trim()) return unifiedMessage;
  if (typeof data?.message === 'string' && data.message.trim()) return data.message;
  return fallback;
}
