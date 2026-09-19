/**
 * 事件面板：给选中组件绑定事件动作（MVP 只编辑单个动作；
 * Schema 本身支持动作数组链式，后续 AI 或手改 JSON 也可用）。
 */

import { Button, Input, Select } from "antd";
import type { Action, ComponentNode, PageSchema } from "../schema/types";
import { getMaterial } from "../materials/registry";
import { JsonField } from "./JsonField";

export interface EventPanelProps {
  node: ComponentNode | null;
  schema: PageSchema;
  revision: number;
  onPatch: (patch: Partial<ComponentNode>) => void;
}

const KINDS: { label: string; value: Action["kind"] }[] = [
  { label: "调用数据源", value: "callDataSource" },
  { label: "设置变量", value: "setState" },
  { label: "页面跳转", value: "navigate" },
  { label: "提示消息", value: "notify" },
];

const LEVELS = ["info", "success", "warning", "error"];

export function EventPanel({ node, schema, revision, onPatch }: EventPanelProps) {
  if (!node) return <div className="lc-panel-tip">先在画布或组件树里选中一个组件</div>;
  const meta = getMaterial(node.type);
  const names = meta?.events ?? [];

  const setAction = (name: string, action: Action | undefined) => {
    const events = { ...(node.events ?? {}) };
    if (action) events[name] = action;
    else delete events[name];
    onPatch({ events: Object.keys(events).length ? events : undefined });
  };

  if (names.length === 0) {
    return <div className="lc-panel-tip">{meta?.label ?? node.type} 没有可配置的事件</div>;
  }

  const dsOptions = (schema.dataSources ?? []).map((item) => ({ label: item.id, value: item.id }));

  return (
    <div className="lc-panel">
      {names.map((name) => {
        const raw = node.events?.[name];
        const action = Array.isArray(raw) ? raw[0] : raw;
        return (
          <div className="lc-ds-card" key={name}>
            <div className="lc-panel-head">
              <span className="lc-panel-title">{name}</span>
              {action ? (
                <Button size="small" danger onClick={() => setAction(name, undefined)}>清除</Button>
              ) : null}
            </div>
            <div className="lc-field">
              <label className="lc-field-label">动作</label>
              <Select
                value={action?.kind}
                placeholder="未绑定"
                allowClear
                style={{ width: "100%" }}
                options={KINDS}
                onChange={(kind) =>
                  setAction(name, kind ? defaultAction(kind, dsOptions[0]?.value) : undefined)
                }
              />
            </div>

            {action?.kind === "callDataSource" ? (
              <>
                <div className="lc-field">
                  <label className="lc-field-label">数据源</label>
                  <Select
                    value={action.dataSource || undefined}
                    placeholder="选择数据源"
                    style={{ width: "100%" }}
                    options={dsOptions}
                    onChange={(value) => setAction(name, { ...action, dataSource: value })}
                  />
                </div>
                <div className="lc-field">
                  <label className="lc-field-label">参数（JSON；值可写表达式）</label>
                  <JsonField
                    key={`ev:${name}:${revision}`}
                    value={action.params}
                    onChange={(value) =>
                      setAction(name, { ...action, params: (value ?? {}) as Record<string, unknown> })
                    }
                  />
                </div>
                <div className="lc-field">
                  <label className="lc-field-label">成功后刷新（可多选）</label>
                  <Select
                    mode="multiple"
                    value={action.refresh ?? []}
                    style={{ width: "100%" }}
                    options={dsOptions}
                    onChange={(value) => setAction(name, { ...action, refresh: value })}
                  />
                </div>
              </>
            ) : null}

            {action?.kind === "setState" ? (
              <div className="lc-field-inline">
                <Input
                  value={action.key}
                  placeholder="变量名"
                  onChange={(event) => setAction(name, { ...action, key: event.target.value })}
                />
                <Input
                  value={typeof action.value === "string" ? action.value : ""}
                  placeholder="值（可写 {{ event.value }}）"
                  onChange={(event) => setAction(name, { ...action, value: event.target.value })}
                />
              </div>
            ) : null}

            {action?.kind === "navigate" ? (
              <div className="lc-field">
                <label className="lc-field-label">跳转路径</label>
                <Input
                  value={action.to}
                  placeholder="/lowcode"
                  onChange={(event) => setAction(name, { ...action, to: event.target.value })}
                />
              </div>
            ) : null}

            {action?.kind === "notify" ? (
              <div className="lc-field-inline">
                <Input
                  value={action.text}
                  placeholder="提示文案"
                  onChange={(event) => setAction(name, { ...action, text: event.target.value })}
                />
                <Select
                  value={action.level ?? "info"}
                  style={{ width: 110 }}
                  options={LEVELS.map((level) => ({ label: level, value: level }))}
                  onChange={(level) => setAction(name, { ...action, level })}
                />
              </div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

function defaultAction(kind: Action["kind"], firstDataSource?: string): Action {
  switch (kind) {
    case "callDataSource":
      return { kind: "callDataSource", dataSource: firstDataSource ?? "" };
    case "setState":
      return { kind: "setState", key: "keyword", value: "{{ event.value }}" };
    case "navigate":
      return { kind: "navigate", to: "/" };
    case "notify":
      return { kind: "notify", text: "操作成功", level: "success" };
  }
}
