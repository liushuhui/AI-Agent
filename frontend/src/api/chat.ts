import type { StreamEvent } from "../types";

/**
 * SSE 基础设施：读流 + 发流。
 *
 * 只负责协议层，具体地址由调用方给（会话相关的在 api/conversation.ts 里拼），
 * 统一走 Vite 代理 /api → VITE_PROXY_TARGET（默认 127.0.0.1:5000）。
 */

/**
 * 逐行解析 SSE：按空行切分事件，处理跨 chunk 的半截数据。
 * 遇到 [DONE] 结束标记即返回。
 */
export const readSSE = async (
  response: Response,
  onEvent: (evt: StreamEvent) => void
): Promise<void> => {
  if (!response.body) throw new Error("响应没有可读取的流");

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    const blocks = buffer.split("\n\n");
    buffer = blocks.pop() ?? ""; // 最后一段可能不完整，留到下一轮

    for (const block of blocks) {
      for (const line of block.split("\n")) {
        if (!line.startsWith("data:")) continue;
        const raw = line.slice(5).trim();
        if (raw === "[DONE]") return;
        try {
          onEvent(JSON.parse(raw) as StreamEvent);
        } catch (e) {
          console.warn("坏数据", raw, e);
        }
      }
    }
  }
};

/** 把后端统一错误体（{"error": "...", "request_id": "..."}）翻成人话 */
const toError = async (resp: Response): Promise<Error> => {
  const text = await resp.text();
  try {
    const data = JSON.parse(text) as { error?: string; request_id?: string };
    if (data.error) {
      const suffix = data.request_id ? `（请求 ID：${data.request_id}）` : "";
      return new Error(`${data.error}${suffix}`);
    }
  } catch {
    // 不是 JSON（例如网关直接返回的 HTML 错误页），退回原文
  }
  return new Error(`HTTP ${resp.status} ${text}`);
};

/** 发一次 POST 并把 SSE 事件逐条交给 onEvent；HTTP 错误直接抛出 */
export const postStream = async (
  url: string,
  body: unknown,
  signal: AbortSignal,
  onEvent: (evt: StreamEvent) => void
): Promise<void> => {
  const resp = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  // 后端所有非流式响应（400/413/429/500…）都是同一个 JSON 形状，
  // 带上 request_id 方便用户报障时直接定位服务端日志。
  if (!resp.ok) throw await toError(resp);
  await readSSE(resp, onEvent);
};
