/**
 * 低代码平台接口客户端。
 *
 * - 统一带 token（api/auth.ts）；401 时清会话并跳登录页；
 * - 页面 CRUD / 发布 / 版本 / 回滚；
 * - AI 生成走 SSE（agentStream）：事件类型见 AgentEvent。
 */

import type { PageDetail, PageSchema, PageSummary, PatchOp } from "@/lowcode/schema/types";
import { authHeaders, clearSession } from "./auth";

export interface PageSavePayload {
  schema?: PageSchema;
  name?: string;
  visibility?: string;
  baseVersion?: number;
}

export interface VersionInfo {
  version: number;
  comment: string;
  created_at: string;
}

async function http<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...authHeaders(),
      ...((init?.headers as Record<string, string> | undefined) ?? {}),
    },
  });
  if (response.status === 401) {
    clearSession();
    if (!window.location.pathname.startsWith("/login")) window.location.replace("/login");
    throw new Error("登录已过期，请重新登录");
  }
  const data = (await response.json().catch(() => null)) as (T & { error?: string }) | null;
  if (!response.ok) throw new Error(data?.error ?? `HTTP ${response.status}`);
  return data as T;
}

export const listPages = () => http<{ pages: PageSummary[] }>("/api/lowcode/pages");

export const createPage = (body: { name: string; description?: string; visibility?: string }) =>
  http<PageDetail>("/api/lowcode/pages", { method: "POST", body: JSON.stringify(body) });

export const getPage = (id: string) => http<PageDetail>(`/api/lowcode/pages/${id}`);

export const savePage = (id: string, body: PageSavePayload) =>
  http<PageDetail>(`/api/lowcode/pages/${id}`, { method: "PUT", body: JSON.stringify(body) });

export const deletePage = (id: string) =>
  http<{ ok: boolean }>(`/api/lowcode/pages/${id}`, { method: "DELETE" });

export const publishPage = (id: string, comment: string) =>
  http<PageDetail>(`/api/lowcode/pages/${id}/publish`, {
    method: "POST",
    body: JSON.stringify({ comment }),
  });

export const listVersions = (id: string) =>
  http<{ versions: VersionInfo[] }>(`/api/lowcode/pages/${id}/versions`);

export interface VersionDetail extends VersionInfo {
  schema: PageSchema;
}

/** 单个发布快照的完整内容（版本对比预览用） */
export const getVersion = (id: string, version: number) =>
  http<VersionDetail>(`/api/lowcode/pages/${id}/versions/${version}`);

export const rollbackPage = (id: string, version: number) =>
  http<PageDetail>(`/api/lowcode/pages/${id}/rollback`, {
    method: "POST",
    body: JSON.stringify({ version }),
  });

// ---------------- 本地草稿 ----------------
// 编辑器点「预览」时把当前（可能未保存的）schema 写进 localStorage，预览页读它，
// 做到「预览的就是屏幕上这一版」；保存成功后清除。

const draftKey = (id: string) => `lc:draft:${id}`;

export function writeDraft(id: string, schema: PageSchema): void {
  localStorage.setItem(draftKey(id), JSON.stringify(schema));
}

export function readDraft<T>(id: string): T | null {
  const raw = localStorage.getItem(draftKey(id));
  if (!raw) return null;
  try {
    return JSON.parse(raw) as T;
  } catch {
    return null;
  }
}

export function clearDraft(id: string): void {
  localStorage.removeItem(draftKey(id));
}

// ---------------- AI 生成（SSE） ----------------

export interface MaterialBrief {
  type: string;
  label: string;
  category: string;
  container?: boolean;
  formControl?: boolean;
  events?: string[];
  props?: { name: string; label?: string; type?: string; options?: unknown }[];
  defaultProps?: Record<string, unknown>;
}

export type AgentEvent =
  | { type: "status"; text: string }
  | { type: "text"; content: string }
  | { type: "schema"; schema: PageSchema }
  | { type: "patch"; patches: PatchOp[]; schema: PageSchema | null; count: number }
  | { type: "error"; message: string }
  | { type: "done" };

export async function agentStream(
  body: { instruction: string; schema: PageSchema | null; materials: MaterialBrief[] },
  onEvent: (event: AgentEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch("/api/lowcode/agent/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify(body),
    signal,
  });
  if (response.status === 401) {
    clearSession();
    window.location.replace("/login");
    throw new Error("登录已过期，请重新登录");
  }
  if (!response.ok || !response.body) {
    const data = (await response.json().catch(() => null)) as { error?: string } | null;
    throw new Error(data?.error ?? `HTTP ${response.status}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let index = buffer.indexOf("\n\n");
    while (index >= 0) {
      const frame = buffer.slice(0, index);
      buffer = buffer.slice(index + 2);
      for (const line of frame.split("\n")) {
        if (!line.startsWith("data:")) continue;
        const payload = line.slice(5).trim();
        if (!payload) continue;
        try {
          onEvent(JSON.parse(payload) as AgentEvent);
        } catch {
          // 坏帧忽略，不打断整条流
        }
      }
      index = buffer.indexOf("\n\n");
    }
  }
}
