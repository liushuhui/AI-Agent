/**
 * 数据源面板：配置页面用到的 REST 接口（别名 / 方法 / URL / 参数 / 自动加载）。
 * 「运行」按钮即时调用运行时执行，下面回显 loading / error / 返回片段，
 * 编辑器里不用保存就能试接口 —— 与预览页共用同一套运行时实现。
 */

import { Button, Input, Select, Switch } from "antd";
import type { DataSourceDef, PageSchema } from "../schema/types";
import type { RuntimeStore } from "../runtime/store";
import { useRuntime } from "../runtime/store";
import { JsonField } from "./JsonField";

export interface DataSourcePanelProps {
  schema: PageSchema;
  revision: number;
  store: RuntimeStore | null;
  onChange: (schema: PageSchema) => void;
}

const METHODS = ["GET", "POST", "PUT", "DELETE", "PATCH"];

export function DataSourcePanel({ schema, revision, store, onChange }: DataSourcePanelProps) {
  const sources = schema.dataSources ?? [];

  const updateAt = (index: number, patch: Partial<DataSourceDef>) => {
    const next = sources.map((item, i) => (i === index ? { ...item, ...patch } : item));
    onChange({ ...schema, dataSources: next });
  };

  const addSource = () => {
    let index = sources.length + 1;
    while (sources.some((item) => item.id === `ds${index}`)) index += 1;
    onChange({
      ...schema,
      dataSources: [
        ...sources,
        { id: `ds${index}`, kind: "rest", method: "GET", url: "/users", autoLoad: false },
      ],
    });
  };

  const removeAt = (index: number) => {
    onChange({ ...schema, dataSources: sources.filter((_, i) => i !== index) });
  };

  return (
    <div className="lc-panel">
      <div className="lc-panel-head">
        <span className="lc-panel-title">数据源（REST）</span>
        <Button size="small" onClick={addSource}>新增</Button>
      </div>
      {sources.length === 0 ? (
        <div className="lc-panel-tip">还没有数据源。组件属性里用 {"{{ dataSources.<id>.data }}"} 取数据。</div>
      ) : null}
      {sources.map((source, index) => (
        <div className="lc-ds-card" key={`${source.id}:${index}`}>
          <div className="lc-field">
            <label className="lc-field-label">标识 id（表达式里用它取数）</label>
            <Input value={source.id} onChange={(event) => updateAt(index, { id: event.target.value })} />
          </div>
          <div className="lc-field-inline">
            <Select
              value={source.method}
              style={{ width: 100 }}
              options={METHODS.map((method) => ({ label: method, value: method }))}
              onChange={(value) => updateAt(index, { method: value })}
            />
            <Input
              value={source.url}
              placeholder="/users 或 https://..."
              onChange={(event) => updateAt(index, { url: event.target.value })}
            />
          </div>
          <div className="lc-field">
            <label className="lc-field-label">参数（JSON；值可写表达式）</label>
            <JsonField
              key={`ds:${source.id}:${revision}`}
              value={source.params}
              onChange={(value) => updateAt(index, { params: (value ?? {}) as Record<string, unknown> })}
            />
          </div>
          <div className="lc-field-inline">
            <label className="lc-field-label">进页面自动加载</label>
            <Switch
              size="small"
              checked={Boolean(source.autoLoad)}
              onChange={(checked) => updateAt(index, { autoLoad: checked })}
            />
            <span className="lc-spacer" />
            <Button size="small" disabled={!store} onClick={() => void store?.runDataSource(source.id)}>
              运行
            </Button>
            <Button size="small" danger onClick={() => removeAt(index)}>删除</Button>
          </div>
          {store ? <DsStatus store={store} id={source.id} /> : null}
        </div>
      ))}
    </div>
  );
}

function DsStatus({ store, id }: { store: RuntimeStore; id: string }) {
  const snapshot = useRuntime(store);
  const state = snapshot.ds[id];
  if (!state) return <div className="lc-tip">未运行</div>;
  if (state.loading) return <div className="lc-tip">请求中…</div>;
  if (state.error) return <div className="lc-tip lc-tip-error">{state.error}</div>;
  const preview = JSON.stringify(state.data);
  return <div className="lc-tip">返回：{preview && preview.length > 160 ? `${preview.slice(0, 160)}…` : preview}</div>;
}
