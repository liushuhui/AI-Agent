/**
 * Schema 的命令层：**所有写操作都从这里走**（拖拽、属性面板、以后 AI 的 patch）。
 *
 * 实现上用 structuredClone 复制整棵树再改——页面规模在几百节点内，
 * 换来实现简单、历史栈（撤销/重做）直接存快照即可。
 */

import type { ComponentNode, MaterialMeta } from "./types";

export function createNodeId(): string {
  return `n_${Math.random().toString(36).slice(2, 8)}`;
}

/** 按物料元数据创建节点（默认 props、默认子节点），子节点 id 会重新分配。 */
export function makeNode(type: string, meta?: MaterialMeta): ComponentNode {
  const node: ComponentNode = {
    id: createNodeId(),
    type,
    props: { ...(meta?.defaultProps ?? {}) },
  };
  if (meta?.defaultChildren?.length) {
    node.children = structuredClone(meta.defaultChildren).map(reassignIds);
  }
  return node;
}

function reassignIds(node: ComponentNode): ComponentNode {
  node.id = createNodeId();
  node.children?.forEach(reassignIds);
  return node;
}

export function findNode(root: ComponentNode, id: string): ComponentNode | null {
  if (root.id === id) return root;
  for (const child of root.children ?? []) {
    const hit = findNode(child, id);
    if (hit) return hit;
  }
  return null;
}

export function findParent(root: ComponentNode, id: string): ComponentNode | null {
  for (const child of root.children ?? []) {
    if (child.id === id) return root;
    const hit = findParent(child, id);
    if (hit) return hit;
  }
  return null;
}

/** 判断 targetId 是否位于 ancestorId 的子树内（含自身）。 */
export function containsNode(root: ComponentNode, ancestorId: string, targetId: string): boolean {
  const ancestor = findNode(root, ancestorId);
  return ancestor !== null && findNode(ancestor, targetId) !== null;
}

export function walk(
  root: ComponentNode,
  visit: (node: ComponentNode, depth: number) => void,
  depth = 0,
): void {
  visit(root, depth);
  for (const child of root.children ?? []) walk(child, visit, depth + 1);
}

export function updateNode(
  root: ComponentNode,
  id: string,
  patch: Partial<ComponentNode>,
): ComponentNode {
  const next = structuredClone(root);
  const node = findNode(next, id);
  if (node) Object.assign(node, patch);
  return next;
}

/** 把节点追加为 parentId 的子节点；parentId 为空/不存在时挂到根上。 */
export function insertChild(
  root: ComponentNode,
  parentId: string | null,
  node: ComponentNode,
): ComponentNode {
  const next = structuredClone(root);
  const parent = (parentId ? findNode(next, parentId) : null) ?? next;
  (parent.children ??= []).push(node);
  return next;
}

export function removeNode(root: ComponentNode, id: string): ComponentNode {
  if (id === root.id) return root; // 根节点不允许删除
  const next = structuredClone(root);
  const parent = findParent(next, id);
  if (parent?.children) parent.children = parent.children.filter((child) => child.id !== id);
  return next;
}

export function moveNode(root: ComponentNode, id: string, parentId: string): ComponentNode {
  // 不能把节点拖进自己的子树里
  if (id === root.id || containsNode(root, id, parentId)) return root;
  const next = structuredClone(root);
  const node = findNode(next, id);
  const oldParent = findParent(next, id);
  if (!node || !oldParent?.children) return root;
  oldParent.children = oldParent.children.filter((child) => child.id !== id);
  const newParent = findNode(next, parentId) ?? next;
  (newParent.children ??= []).push(node);
  return next;
}

/** 在同级里上/下移动（delta = -1 / +1），越界返回原树。 */
export function moveSibling(root: ComponentNode, id: string, delta: number): ComponentNode {
  const next = structuredClone(root);
  const parent = findParent(next, id);
  if (!parent?.children) return root;
  const from = parent.children.findIndex((child) => child.id === id);
  const to = from + delta;
  if (from < 0 || to < 0 || to >= parent.children.length) return root;
  const list = parent.children;
  [list[from], list[to]] = [list[to], list[from]];
  return next;
}
