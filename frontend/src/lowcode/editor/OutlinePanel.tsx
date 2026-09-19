/** 组件树（大纲）：平铺展示整页结构，点一下就能选中。 */

import type { ComponentNode } from "../schema/types";
import { walk } from "../schema/commands";
import { getMaterial } from "../materials/registry";

export interface OutlinePanelProps {
  root: ComponentNode;
  selectedId: string | null;
  onSelect: (id: string) => void;
}

export function OutlinePanel({ root, selectedId, onSelect }: OutlinePanelProps) {
  const rows: { node: ComponentNode; depth: number }[] = [];
  walk(root, (node, depth) => rows.push({ node, depth }));

  return (
    <div className="lc-panel">
      <div className="lc-panel-title">组件树</div>
      <div className="lc-outline">
        {rows.map(({ node, depth }) => (
          <div
            key={node.id}
            className={`lc-outline-row${node.id === selectedId ? " active" : ""}`}
            style={{ paddingLeft: 8 + depth * 14 }}
            onClick={() => onSelect(node.id)}
          >
            <span className="lc-outline-label">{getMaterial(node.type)?.label ?? node.type}</span>
            <span className="lc-outline-id">{node.id}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
