/**
 * 两份 Schema 的差异比较：给「发布版本对比当前画布」用。
 * 纯函数、不依赖 UI（组件名通过 labelOf 注入），按 页面 / 变量 / 数据源 / 组件 分组产出条目。
 */

import type { ComponentNode, PageSchema } from "./types";

export interface DiffItem {
  kind: "add" | "remove" | "change";
  scope: string;
  target: string;
  detail: string;
}

export interface DiffResult {
  items: DiffItem[];
  added: number;
  removed: number;
  changed: number;
}

type Push = (kind: DiffItem["kind"], scope: string, target: string, detail: string) => void;

export function diffSchema(
  oldSchema: PageSchema,
  newSchema: PageSchema,
  labelOf: (node: ComponentNode) => string,
): DiffResult {
  const items: DiffItem[] = [];
  const push: Push = (kind, scope, target, detail) => items.push({ kind, scope, target, detail });

  if ((oldSchema.name ?? "") !== (newSchema.name ?? "")) {
    push("change", "页面", "页面名", `「${oldSchema.name ?? ""}」→「${newSchema.name ?? ""}」`);
  }

  // ----- 页面变量 -----
  const oldState = oldSchema.state ?? {};
  const newState = newSchema.state ?? {};
  for (const key of new Set([...Object.keys(oldState), ...Object.keys(newState)])) {
    const before = JSON.stringify(oldState[key] ?? null);
    const after = JSON.stringify(newState[key] ?? null);
    if (!(key in oldState)) push("add", "变量", key, `新增，初始值 ${truncate(after)}`);
    else if (!(key in newState)) push("remove", "变量", key, `删除，原值 ${truncate(before)}`);
    else if (before !== after) push("change", "变量", key, `${truncate(before)} → ${truncate(after)}`);
  }

  // ----- 数据源 -----
  const oldSources = new Map((oldSchema.dataSources ?? []).map((item) => [item.id, item]));
  const newSources = new Map((newSchema.dataSources ?? []).map((item) => [item.id, item]));
  for (const [id, source] of oldSources) {
    if (!newSources.has(id)) push("remove", "数据源", id, `删除（${source.method} ${source.url}）`);
  }
  for (const [id, source] of newSources) {
    const before = oldSources.get(id);
    if (!before) {
      push("add", "数据源", id, `${source.method} ${source.url}`);
      continue;
    }
    if (before.method !== source.method || before.url !== source.url) {
      push("change", "数据源", id, `${before.method} ${before.url} → ${source.method} ${source.url}`);
    }
    if (JSON.stringify(before.params ?? null) !== JSON.stringify(source.params ?? null)) {
      push(
        "change",
        "数据源",
        id,
        `参数：${truncate(JSON.stringify(before.params ?? null))} → ${truncate(JSON.stringify(source.params ?? null))}`,
      );
    }
    if (Boolean(before.autoLoad) !== Boolean(source.autoLoad)) {
      push("change", "数据源", id, `自动加载：${before.autoLoad ? "是" : "否"} → ${source.autoLoad ? "是" : "否"}`);
    }
  }

  // ----- 组件树（按节点 id 对比） -----
  const oldNodes = collectNodes(oldSchema.root);
  const newNodes = collectNodes(newSchema.root);
  for (const [id, entry] of oldNodes) {
    if (newNodes.has(id)) continue;
    const parentId = entry.parentId;
    const parent = (parentId && (newNodes.get(parentId) ?? oldNodes.get(parentId))) || null;
    push(
      "remove",
      "组件",
      describe(entry.node, labelOf),
      `从「${parent ? describe(parent.node, labelOf) : "根"}」移除`,
    );
  }
  for (const [id, entry] of newNodes) {
    const before = oldNodes.get(id);
    if (!before) {
      const parent = entry.parentId ? newNodes.get(entry.parentId) : null;
      push(
        "add",
        "组件",
        describe(entry.node, labelOf),
        `添加到「${parent ? describe(parent.node, labelOf) : "根"}」`,
      );
      continue;
    }
    if (before.parentId !== entry.parentId) {
      push("change", "组件", describe(entry.node, labelOf), `移动了位置（新父节点：${entry.parentId ?? "root"}）`);
    }
    diffProps(before.node, entry.node, labelOf, push);
    if ((before.node.visible ?? "") !== (entry.node.visible ?? "")) {
      push(
        "change",
        "组件",
        describe(entry.node, labelOf),
        `显示条件：「${before.node.visible ?? "无"}」→「${entry.node.visible ?? "无"}」`,
      );
    }
    if (JSON.stringify(before.node.events ?? {}) !== JSON.stringify(entry.node.events ?? {})) {
      push("change", "组件", describe(entry.node, labelOf), "事件/动作有变化");
    }
    const beforeIds = (before.node.children ?? []).map((child) => child.id);
    const afterIds = (entry.node.children ?? []).map((child) => child.id);
    const keptBefore = beforeIds.filter((childId) => afterIds.includes(childId));
    const keptAfter = afterIds.filter((childId) => beforeIds.includes(childId));
    if (keptBefore.join(",") !== keptAfter.join(",")) {
      push("change", "组件", describe(entry.node, labelOf), "子组件顺序变了");
    }
  }

  return {
    items,
    added: items.filter((item) => item.kind === "add").length,
    removed: items.filter((item) => item.kind === "remove").length,
    changed: items.filter((item) => item.kind === "change").length,
  };
}

function diffProps(
  before: ComponentNode,
  after: ComponentNode,
  labelOf: (node: ComponentNode) => string,
  push: Push,
): void {
  const oldProps = before.props ?? {};
  const newProps = after.props ?? {};
  for (const key of new Set([...Object.keys(oldProps), ...Object.keys(newProps)])) {
    const a = JSON.stringify(oldProps[key] ?? null);
    const b = JSON.stringify(newProps[key] ?? null);
    if (a !== b) {
      push("change", "组件", describe(after, labelOf), `属性「${key}」：${truncate(a)} → ${truncate(b)}`);
    }
  }
}

interface NodeEntry {
  node: ComponentNode;
  parentId: string | null;
}

function collectNodes(root: ComponentNode): Map<string, NodeEntry> {
  const map = new Map<string, NodeEntry>();
  const walk = (node: ComponentNode, parentId: string | null) => {
    map.set(node.id, { node, parentId });
    for (const child of node.children ?? []) walk(child, node.id);
  };
  walk(root, null);
  return map;
}

const describe = (node: ComponentNode, labelOf: (node: ComponentNode) => string): string =>
  `${labelOf(node)}（${node.id}）`;

function truncate(text: string, max = 70): string {
  return text.length > max ? `${text.slice(0, max)}…` : text;
}
