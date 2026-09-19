/**
 * 运行时仓储：页面变量（state）+ 数据源执行 + 动作分发。
 *
 * 渲染器用 useSyncExternalStore 订阅快照；编辑器画布和预览页共用同一个实现，
 * 保证「编辑时看到的效果 = 预览/发布时的效果」。
 */

import { useSyncExternalStore } from "react";
import type { Action, DataSourceDef, PageSchema } from "../schema/types";
import { evalRecord, evalValue } from "./expression";

export interface DsState {
  data?: unknown;
  loading?: boolean;
  error?: string;
}

export interface RuntimeSnapshot {
  state: Record<string, unknown>;
  /** 数据源运行时状态，按数据源 id 索引（表达式里写 dataSources.xxx.data / loading / error）。 */
  ds: Record<string, DsState>;
}

export type NotifyLevel = "info" | "success" | "warning" | "error";

export interface RuntimeHooks {
  notify?: (text: string, level?: NotifyLevel) => void;
  navigate?: (to: string) => void;
}

export class RuntimeStore {
  private schema: PageSchema;
  private readonly hooks: RuntimeHooks;
  private listeners = new Set<() => void>();
  private snapshot: RuntimeSnapshot;
  /** 渲染上下文里的 env（如 env.user），由宿主注入。 */
  readonly env: Record<string, unknown>;

  constructor(schema: PageSchema, hooks: RuntimeHooks = {}, env: Record<string, unknown> = {}) {
    this.schema = schema;
    this.hooks = hooks;
    this.env = env;
    this.snapshot = { state: { ...(schema.state ?? {}) }, ds: {} };
  }

  /** 编辑器里 schema 一直在变（数据源、表达式等），运行时始终以最新一份为准。 */
  setSchema(schema: PageSchema): void {
    this.schema = schema;
  }

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  getSnapshot = (): RuntimeSnapshot => this.snapshot;

  private commit(patch: Partial<RuntimeSnapshot>): void {
    this.snapshot = { ...this.snapshot, ...patch };
    for (const listener of this.listeners) listener();
  }

  setState(key: string, value: unknown): void {
    this.commit({ state: { ...this.snapshot.state, [key]: value } });
  }

  private patchDs(id: string, next: DsState): void {
    this.commit({ ds: { ...this.snapshot.ds, [id]: next } });
  }

  /** 表达式上下文：state / dataSources / env + 现场作用域（record、event）。 */
  scope(extra: Record<string, unknown> = {}): Record<string, unknown> {
    return { state: this.snapshot.state, dataSources: this.snapshot.ds, env: this.env, ...extra };
  }

  getDataSource(id: string): DataSourceDef | undefined {
    return this.schema.dataSources?.find((item) => item.id === id);
  }

  /** 执行一个 REST 数据源，结果（data / loading / error）写进快照。 */
  async runDataSource(id: string, overrides?: Record<string, unknown>, record?: unknown): Promise<void> {
    const def = this.getDataSource(id);
    if (!def) {
      this.hooks.notify?.(`数据源不存在：${id}`, "error");
      return;
    }
    const scope = this.scope({ record });
    const url = String(evalValue(def.url, scope) ?? "");
    const params = evalRecord({ ...(def.params ?? {}), ...(overrides ?? {}) }, scope);
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    for (const [key, value] of Object.entries(def.headers ?? {})) {
      headers[key] = String(evalValue(value, scope) ?? "");
    }

    let finalUrl = url;
    // 相对路径（/users 这类）按 env.baseUrl 解析：
    // 开发时页面在 5173、后端在 5000，宿主把 baseUrl 设成 "/api"（走 Vite 代理）；
    // 生产同源部署时留空即可，Schema 不用改。
    const baseUrl = typeof this.env.baseUrl === "string" ? this.env.baseUrl : "";
    if (url.startsWith("/")) finalUrl = `${baseUrl}${url}`;
    let body: string | undefined;
    if (def.method === "GET") {
      const query = new URLSearchParams();
      for (const [key, value] of Object.entries(params)) {
        if (value != null && value !== "") query.set(key, String(value));
      }
      const queryString = query.toString();
      if (queryString) finalUrl += url.includes("?") ? `&${queryString}` : `?${queryString}`;
    } else {
      body = JSON.stringify(params);
    }

    this.patchDs(id, { ...this.snapshot.ds[id], loading: true, error: undefined });
    try {
      const response = await fetch(finalUrl, { method: def.method, headers, body });
      const payload: unknown = await response.json().catch(() => null);
      if (!response.ok) {
        const detail = (payload as { error?: string } | null)?.error;
        throw new Error(detail ?? `HTTP ${response.status}`);
      }
      this.patchDs(id, { data: payload, loading: false });
    } catch (error) {
      const text = error instanceof Error ? error.message : String(error);
      this.patchDs(id, { data: this.snapshot.ds[id]?.data, loading: false, error: text });
      this.hooks.notify?.(`数据源 ${id} 请求失败：${text}`, "error");
    }
  }

  /** 消费一个（或一串）动作；callDataSource 支持 onSuccess / onError 链式后续动作。 */
  async handleAction(
    action: Action | Action[] | undefined,
    extra: Record<string, unknown> = {},
  ): Promise<void> {
    if (!action) return;
    const list = Array.isArray(action) ? action : [action];
    for (const item of list) {
      const scope = this.scope(extra);
      switch (item.kind) {
        case "setState":
          this.setState(item.key, evalValue(item.value, scope));
          break;
        case "navigate":
          this.hooks.navigate?.(String(evalValue(item.to, scope) ?? ""));
          break;
        case "notify":
          this.hooks.notify?.(String(evalValue(item.text, scope) ?? ""), item.level ?? "info");
          break;
        case "callDataSource": {
          await this.runDataSource(item.dataSource, evalRecord(item.params ?? {}, scope), extra.record);
          const dsState = this.snapshot.ds[item.dataSource];
          if (dsState?.error) {
            await this.handleAction(item.onError, extra);
          } else {
            await this.handleAction(item.onSuccess, extra);
            for (const refreshId of item.refresh ?? []) void this.runDataSource(refreshId);
          }
          break;
        }
      }
    }
  }

  /** 进页面自动加载所有标记了 autoLoad 的数据源。 */
  autoLoad(): void {
    for (const def of this.schema.dataSources ?? []) {
      if (def.autoLoad) void this.runDataSource(def.id);
    }
  }
}

/** 订阅仓储快照的 React Hook。 */
export function useRuntime(store: RuntimeStore): RuntimeSnapshot {
  return useSyncExternalStore(store.subscribe, store.getSnapshot);
}
