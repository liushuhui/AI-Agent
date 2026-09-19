/**
 * 应用 AI 的语义化 Patch（与后端 lowcode_patch.py 同一套操作）。
 *
 * 在 structuredClone 的副本上操作，不修改入参；单条失败不影响其他条，
 * 错误收集后由调用方决定是否回退到「后端已应用好的 schema」。
 */

import { containsNode, findNode, findParent } from "./commands";
import type { PatchOp, PageSchema } from "./types";

export interface PatchResult {
  schema: PageSchema;
  applied: number;
  errors: string[];
}

export function applyPatches(schema: PageSchema, patches: PatchOp[]): PatchResult {
  if (!Array.isArray(patches) || patches.length === 0) {
    return { schema, applied: 0, errors: ["patches 为空"] };
  }
  let work = schema;
  let applied = 0;
  const errors: string[] = [];
  patches.forEach((patch, index) => {
    try {
      work = applyOne(work, patch);
      applied += 1;
    } catch (error) {
      const op = typeof patch === "object" && patch !== null ? (patch as { op?: string }).op : "?";
      const reason = error instanceof Error ? error.message : String(error);
      errors.push(`第 ${index + 1} 条（${op ?? "?"}）：${reason}`);
    }
  });
  return { schema: work, applied, errors };
}

function applyOne(schema: PageSchema, patch: PatchOp): PageSchema {
  const next = structuredClone(schema);
  const root = next.root;
  const mustFind = (nodeId: string) => {
    const node = findNode(root, nodeId);
    if (!node) throw new Error(`节点不存在（${nodeId}）`);
    return node;
  };

  switch (patch.op) {
    case "update_props": {
      const node = mustFind(patch.nodeId);
      const props = (node.props ??= {});
      for (const [key, value] of Object.entries(patch.props ?? {})) {
        if (value === null) delete props[key];
        else props[key] = value;
      }
      break;
    }
    case "set_visible": {
      const node = mustFind(patch.nodeId);
      if (patch.visible === null) delete node.visible;
      else node.visible = patch.visible;
      break;
    }
    case "set_event": {
      const node = mustFind(patch.nodeId);
      const events = (node.events ??= {});
      if (patch.action === null) delete events[patch.event];
      else events[patch.event] = patch.action;
      if (Object.keys(events).length === 0) delete node.events;
      break;
    }
    case "add_node": {
      if (findNode(root, patch.node.id)) throw new Error(`节点 id 重复（${patch.node.id}）`);
      const parent = patch.parentId ? mustFind(patch.parentId) : root;
      const children = (parent.children ??= []);
      children.splice(clampIndex(patch.index, children.length), 0, structuredClone(patch.node));
      break;
    }
    case "remove_node": {
      if (patch.nodeId === root.id) throw new Error("不能删除根节点");
      mustFind(patch.nodeId);
      const parent = findParent(root, patch.nodeId);
      if (!parent?.children) throw new Error(`找不到 ${patch.nodeId} 的父节点`);
      parent.children = parent.children.filter((child) => child.id !== patch.nodeId);
      break;
    }
    case "move_node": {
      if (patch.nodeId === root.id) throw new Error("不能移动根节点");
      const node = mustFind(patch.nodeId);
      if (containsNode(root, patch.nodeId, patch.parentId)) {
        throw new Error("不能移动到自己的子树里");
      }
      const newParent = mustFind(patch.parentId);
      const oldParent = findParent(root, patch.nodeId);
      if (!oldParent?.children) throw new Error(`找不到 ${patch.nodeId} 的父节点`);
      oldParent.children = oldParent.children.filter((child) => child.id !== patch.nodeId);
      const children = (newParent.children ??= []);
      children.splice(clampIndex(patch.index, children.length), 0, node);
      break;
    }
    case "set_state": {
      const state = (next.state ??= {});
      if (patch.value === undefined || patch.value === null) delete state[patch.key];
      else state[patch.key] = patch.value;
      break;
    }
    case "upsert_data_source": {
      const sources = (next.dataSources ??= []);
      const index = sources.findIndex((item) => item.id === patch.dataSource.id);
      if (index >= 0) sources[index] = { ...sources[index], ...structuredClone(patch.dataSource) };
      else sources.push(structuredClone(patch.dataSource));
      break;
    }
    case "remove_data_source": {
      next.dataSources = (next.dataSources ?? []).filter((item) => item.id !== patch.id);
      break;
    }
    case "set_page_name": {
      next.name = patch.name;
      break;
    }
  }
  return next;
}

function clampIndex(index: number | undefined, length: number): number {
  if (typeof index !== "number" || Number.isNaN(index)) return length;
  return Math.max(0, Math.min(Math.trunc(index), length));
}
