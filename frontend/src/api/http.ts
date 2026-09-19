/**
 * 普通 JSON 接口的统一请求封装。
 *
 * - 自动带登录 token（api/auth.ts）；401 时清会话并跳登录页；
 * - 后端所有非流式错误都是同一个形状（{"error": "...", "request_id": "..."}），
 *   这里统一把它们翻成异常；SSE 不走这里（见 api/chat.ts 的 postStream，
 *   错误可能以事件形式中途下发）。
 */

import { authHeaders, clearSession } from "./auth";

export const request = async <T>(url: string, init?: RequestInit): Promise<T> => {
  const resp = await fetch(url, {
    ...init,
    headers: {
      ...authHeaders(),
      ...((init?.headers as Record<string, string> | undefined) ?? {}),
    },
  });
  if (resp.status === 401) {
    clearSession();
    if (!window.location.pathname.startsWith("/login")) window.location.replace("/login");
    throw new Error("登录已过期，请重新登录");
  }
  const data = (await resp.json().catch(() => null)) as
    | (T & { error?: string })
    | null;
  if (!resp.ok) {
    throw new Error(data?.error ?? `HTTP ${resp.status}`);
  }
  return data as T;
};
