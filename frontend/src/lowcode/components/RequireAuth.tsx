/** 路由守卫：没有 token 就送去登录页，并记住来路（登录后跳回）。
 *
 * 登录态走订阅（不在渲染期直接读 localStorage/getToken，否则会被
 * React Compiler 缓存成常量，登录后守卫仍然放行不了）。
 */

import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router";
import { getToken, subscribeAuth } from "../../api/auth";

export function RequireAuth({ children }: { children: ReactNode }) {
  const location = useLocation();
  const [authed, setAuthed] = useState(() => Boolean(getToken()));
  useEffect(() => subscribeAuth(() => setAuthed(Boolean(getToken()))), []);

  if (!authed) {
    return (
      <Navigate
        to="/login"
        replace
        state={{ from: `${location.pathname}${location.search}` }}
      />
    );
  }
  return <>{children}</>;
}
