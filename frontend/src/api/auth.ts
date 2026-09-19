/**
 * 登录会话：token 存 localStorage，请求头 `Authorization: Bearer <token>`。
 * 服务端是「令牌表」会话（可吊销、可过期），不是 JWT —— 前端只需要存和带。
 */

import { useEffect, useState, useSyncExternalStore } from "react";
import type { LowcodeUser } from "@/lowcode/schema/types";

const TOKEN_KEY = "lc:token";
const USER_KEY = "lc:user";

// 会话变更的订阅：localStorage 不是响应式的，而 React Compiler 会把
// “无依赖的 getStoredUser() 调用”缓存住（登录后侧栏不刷新）——
// 这里用版本号 + useSyncExternalStore 让读取变成可订阅的。
let version = 0;
const listeners = new Set<() => void>();

function bump(): void {
  version += 1;
  for (const listener of listeners) listener();
}

function subscribeAuth(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** 订阅登录态变化（登录/登出都会触发）；回调里 setState 是 React 认可的写法。 */
export { subscribeAuth };

export function useAuthVersion(): number {
  return useSyncExternalStore(subscribeAuth, () => version);
}

/**
 * 当前用户的响应式读取（登录/登出/换人后自动更新）。
 *
 * 不要在渲染期直接调 `getStoredUser()`：React Compiler 会把「无参外部读取」
 * 缓存成常量（登录后界面不刷新），所以统一走这个钩子。
 */
export function useCurrentUser(): LowcodeUser | null {
  const [user, setUser] = useState<LowcodeUser | null>(() => getStoredUser());
  useEffect(() => subscribeAuth(() => setUser(getStoredUser())), []);
  return user;
}

export interface AuthSession {
  token: string;
  user: LowcodeUser;
  expires_at: string;
}

export function getToken(): string {
  return localStorage.getItem(TOKEN_KEY) ?? "";
}

export function getStoredUser(): LowcodeUser | null {
  const raw = localStorage.getItem(USER_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as LowcodeUser;
  } catch {
    return null;
  }
}

export function setSession(session: AuthSession): void {
  localStorage.setItem(TOKEN_KEY, session.token);
  localStorage.setItem(USER_KEY, JSON.stringify(session.user));
  bump();
}

export function clearSession(): void {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
  bump();
}

export function authHeaders(): Record<string, string> {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export async function login(username: string, password: string): Promise<AuthSession> {
  const response = await fetch("/api/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  const data = (await response.json().catch(() => null)) as
    | (AuthSession & { error?: string })
    | null;
  if (!response.ok || !data) {
    throw new Error(data?.error ?? `HTTP ${response.status}`);
  }
  return data;
}

export async function logout(): Promise<void> {
  try {
    await fetch("/api/auth/logout", { method: "POST", headers: authHeaders() });
  } catch {
    // 登出失败（断网等）不阻塞前端清理，令牌到期后照样失效
  }
  clearSession();
}
