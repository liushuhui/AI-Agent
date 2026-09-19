/**
 * 属性面板：按物料元数据（propSchema）自动生成表单，不写死任何一个组件。
 * 另含：显示条件（visible 表达式）、上移/下移/删除。
 */

import { Button, Input, InputNumber, Select, Switch } from "antd";
import type { ComponentNode, PropField } from "../schema/types";
import { getMaterial } from "../materials/registry";
import { JsonField } from "./JsonField";

export interface PropertyPanelProps {
  node: ComponentNode | null;
  /** 编辑器修订号：撤销/重做后变化，用来重建 JSON 编辑框里的文本。 */
  revision: number;
  onPatch: (patch: Partial<ComponentNode>) => void;
  onDelete: () => void;
  onMove: (delta: number) => void;
}

export function PropertyPanel({ node, revision, onPatch, onDelete, onMove }: PropertyPanelProps) {
  if (!node) return <div className="lc-panel-tip">先在画布或组件树里选中一个组件</div>;
  const meta = getMaterial(node.type);

  const setProp = (key: string, value: unknown) => {
    const props = { ...(node.props ?? {}) };
    if (value === undefined) delete props[key];
    else props[key] = value;
    onPatch({ props });
  };

  const renderControl = (key: string, field: PropField) => {
    const value = node.props?.[key];
    switch (field.type) {
      case "number":
        return (
          <InputNumber
            value={value as number | undefined}
            style={{ width: "100%" }}
            onChange={(next) => setProp(key, next ?? undefined)}
          />
        );
      case "boolean":
        return <Switch size="small" checked={Boolean(value)} onChange={(next) => setProp(key, next)} />;
      case "select":
        return (
          <Select
            value={value as string | undefined}
            options={field.options}
            allowClear
            placeholder="选择"
            style={{ width: "100%" }}
            onChange={(next) => setProp(key, next)}
          />
        );
      case "json":
        return (
          <JsonField
            key={`json:${key}:${revision}`}
            value={value}
            onChange={(next) => setProp(key, next)}
          />
        );
      default:
        return (
          <Input
            value={typeof value === "string" ? value : value === undefined ? "" : JSON.stringify(value)}
            placeholder={field.placeholder ?? (field.type === "expression" ? "支持 {{ 表达式 }}" : undefined)}
            onChange={(event) => setProp(key, event.target.value)}
          />
        );
    }
  };

  return (
    <div className="lc-panel">
      <div className="lc-panel-head">
        <span className="lc-panel-title">{meta?.label ?? node.type}</span>
        <span className="lc-node-id">{node.id}</span>
      </div>
      <div className="lc-row-actions">
        <Button size="small" onClick={() => onMove(-1)}>上移</Button>
        <Button size="small" onClick={() => onMove(1)}>下移</Button>
        <Button size="small" danger disabled={node.id === "root"} onClick={onDelete}>删除</Button>
      </div>

      <div className="lc-field">
        <label className="lc-field-label">显示条件（表达式，留空 = 显示）</label>
        <Input
          value={node.visible ?? ""}
          placeholder="例如：{{ env.user === 'admin' }}"
          onChange={(event) => onPatch({ visible: event.target.value || undefined })}
        />
      </div>

      {Object.entries(meta?.props ?? {}).map(([key, field]) => (
        <div className="lc-field" key={key}>
          <label className="lc-field-label">{field.label}</label>
          {renderControl(key, field)}
        </div>
      ))}

      {meta?.container ? <div className="lc-tip">容器组件：可以把其他物料拖进来</div> : null}
    </div>
  );
}
