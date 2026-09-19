/**
 * 预览页：只读渲染页面；带 ?draft=1 时优先读编辑器刚写入的本地草稿
 * （看未保存的修改）。数据源由运行时真实执行，与编辑器画布同一套实现。
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router";
import { App as AntdApp, Button, Space, Tag } from "antd";
import { getPage, readDraft } from "../../api/lowcode";
import { useCurrentUser } from "../../api/auth";
import type { PageSchema } from "../schema/types";
import { Renderer } from "../runtime/Renderer";
import { RuntimeStore } from "../runtime/store";
import "../lowcode.css";

interface Fetched {
  schema: PageSchema;
  name: string;
  error: string;
}

export function PreviewPage() {
  const { id = "" } = useParams();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const { message } = AntdApp.useApp();
  const user = useCurrentUser();
  const userId = user?.id ?? "";
  const userRole = user?.role ?? "";
  const wantDraft = searchParams.get("draft") === "1";
  // 渲染期派生（useMemo 固定引用），避免在 effect 里同步 setState（react-hooks v7 会报错）
  const draftSchema = useMemo(() => (wantDraft ? readDraft<PageSchema>(id) : null), [wantDraft, id]);
  const [fetched, setFetched] = useState<Fetched | null>(null);

  useEffect(() => {
    if (draftSchema) return; // 草稿模式无需请求
    let alive = true;
    getPage(id)
      .then((detail) => {
        if (alive) setFetched({ schema: detail.schema, name: detail.name, error: "" });
      })
      .catch((error: unknown) => {
        if (!alive) return;
        const text = error instanceof Error ? error.message : String(error);
        setFetched({ schema: { schemaVersion: "1.0", root: { id: "root", type: "Card" } }, name: "预览", error: text });
      });
    return () => {
      alive = false;
    };
  }, [id, draftSchema]);

  const schema = draftSchema ?? fetched?.schema ?? null;
  const pageName = draftSchema?.name ?? fetched?.name ?? "预览";
  const error = draftSchema ? "" : fetched?.error ?? "";

  const store = useMemo(
    () =>
      schema
        ? new RuntimeStore(
            schema,
            { notify: (text, level) => message.open({ type: level ?? "info", content: text }) },
            { user: userId, role: userRole, baseUrl: "/api" },
          )
        : null,
    [schema, message, userId, userRole],
  );

  // StrictMode 下 effect 会跑两遍：按 store 实例记账，避免重复 autoLoad
  const loaded = useRef<RuntimeStore | null>(null);
  useEffect(() => {
    if (!store || loaded.current === store) return;
    loaded.current = store;
    store.autoLoad();
  }, [store]);

  return (
    <div className="lc-preview">
      <header className="lc-toolbar">
        <Space size={8}>
          <Button size="small" onClick={() => navigate(`/lowcode/${id}`)}>
            ← 回到编辑器
          </Button>
          <span className="lc-title">{pageName}</span>
          {draftSchema ? <Tag color="orange">草稿预览（未保存）</Tag> : null}
        </Space>
      </header>
      {error ? <div className="lc-error">{error}</div> : null}
      {store && schema ? <Renderer schema={schema} store={store} mode="view" /> : null}
      {!store && !error ? <div className="lc-loading">加载中…</div> : null}
    </div>
  );
}
