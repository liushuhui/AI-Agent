/**
 * 编辑器画布：= 渲染器 + 选中/悬停/拖拽外壳。
 *
 * 拖拽用原生 HTML5 DnD（无需第三方库）：
 * - 从物料面板拖出 → dataTransfer 里是 "lc/material"（type）；
 * - 从画布拖节点   → dataTransfer 里是 "lc/node"（node id）；
 * - 只有 container 物料接收落点，drop 后追加子节点（顺序可用属性面板的上移/下移调）。
 */

import { useState } from "react";
import type { DragEvent, ReactNode } from "react";
import type { ComponentNode, PageSchema } from "../schema/types";
import { getMaterial } from "../materials/registry";
import { Renderer } from "../runtime/Renderer";
import type { RuntimeStore } from "../runtime/store";

export interface CanvasProps {
  schema: PageSchema;
  store: RuntimeStore;
  selectedId: string | null;
  onSelect: (id: string) => void;
  onAddMaterial: (type: string, parentId: string | null) => void;
  onMoveNode: (id: string, parentId: string) => void;
}

export function Canvas({
  schema,
  store,
  selectedId,
  onSelect,
  onAddMaterial,
  onMoveNode,
}: CanvasProps) {
  const [overId, setOverId] = useState<string | null>(null);

  const dropOnNode = (event: DragEvent<HTMLDivElement>, parentId: string) => {
    event.preventDefault();
    event.stopPropagation();
    setOverId(null);
    const materialType = event.dataTransfer.getData("lc/material");
    const nodeId = event.dataTransfer.getData("lc/node");
    if (materialType) onAddMaterial(materialType, parentId);
    else if (nodeId && nodeId !== parentId) onMoveNode(nodeId, parentId);
  };

  const dropOnBackground = (event: DragEvent<HTMLDivElement>) => {
    if (event.target !== event.currentTarget) return; // 交给具体节点处理
    event.preventDefault();
    setOverId(null);
    const materialType = event.dataTransfer.getData("lc/material");
    const nodeId = event.dataTransfer.getData("lc/node");
    if (materialType) onAddMaterial(materialType, null);
    else if (nodeId && nodeId !== schema.root.id) onMoveNode(nodeId, schema.root.id);
  };

  const wrapNode = (node: ComponentNode, element: ReactNode) => {
    const meta = getMaterial(node.type);
    const selected = node.id === selectedId;
    const isOver = overId === node.id;
    return (
      <div
        className={`lc-node${selected ? " lc-selected" : ""}${isOver ? " lc-over" : ""}`}
        data-node-id={node.id}
        title={`${meta?.label ?? node.type}（${node.id}）`}
        draggable
        onClick={(event) => {
          event.stopPropagation();
          onSelect(node.id);
        }}
        onDragStart={(event) => {
          event.stopPropagation();
          event.dataTransfer.setData("lc/node", node.id);
          event.dataTransfer.effectAllowed = "move";
        }}
        onDragOver={(event) => {
          if (!meta?.container) return;
          event.preventDefault();
          event.stopPropagation();
          if (overId !== node.id) setOverId(node.id);
        }}
        onDragLeave={() => setOverId((current) => (current === node.id ? null : current))}
        onDrop={(event) => {
          if (meta?.container) dropOnNode(event, node.id);
        }}
      >
        {selected ? <span className="lc-node-badge">{meta?.label ?? node.type}</span> : null}
        {isOver ? <span className="lc-drop-hint">放入「{meta?.label}」</span> : null}
        {element}
      </div>
    );
  };

  return (
    <div
      className="lc-canvas"
      onClick={() => onSelect(schema.root.id)}
      onDragOver={(event) => {
        if (event.target === event.currentTarget) event.preventDefault();
      }}
      onDrop={dropOnBackground}
    >
      <Renderer schema={schema} store={store} mode="edit" wrapNode={wrapNode} />
    </div>
  );
}
