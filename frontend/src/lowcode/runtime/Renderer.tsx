/**
 * 递归渲染器：把 PageSchema 渲染成 React 树。
 *
 * 三种形态共用它，只换外壳：
 * - 编辑器画布：mode="edit" + wrapNode（选中/悬停/拖拽指示，事件不触发）
 * - 预览页：mode="view"（可交互，可调数据源）
 * - 以后发布页：同 mode="view"
 */

import { Fragment } from "react";
import type { ReactNode } from "react";
import type { Action, ComponentNode, PageSchema } from "../schema/types";
import { getMaterial } from "../materials/registry";
import "../materials/custom"; // 副作用导入：注册示例自定义组件（业务自己加组件也放这类模块）
import { evalRecord, evalValue, truthy } from "./expression";
import type { RuntimeStore } from "./store";
import { useRuntime } from "./store";

export type WrapNode = (node: ComponentNode, element: ReactNode) => ReactNode;

export interface RendererProps {
  schema: PageSchema;
  store: RuntimeStore;
  mode?: "view" | "edit";
  /** 编辑态外壳：给每个节点套选中/拖拽交互。 */
  wrapNode?: WrapNode;
}

export function Renderer({ schema, store, mode = "view", wrapNode }: RendererProps) {
  const snapshot = useRuntime(store);

  const nodeEvents = (node: ComponentNode, extra: Record<string, unknown>) => {
    const handlers: Record<string, (arg: unknown) => void> = {};
    for (const [name, action] of Object.entries(node.events ?? {})) {
      handlers[name] = (arg: unknown) => {
        void store.handleAction(action as Action, { ...extra, event: normalizeEvent(arg) });
      };
    }
    return handlers;
  };

  const renderNode = (node: ComponentNode, extra: Record<string, unknown>): ReactNode => {
    const scope = { state: snapshot.state, dataSources: snapshot.ds, env: store.env, ...extra };

    if (node.visible && !truthy(evalValue(node.visible, scope))) {
      if (mode !== "edit") return null;
      // 编辑态保留占位，否则条件不满足的组件在画布上无法选中、无法改回来
      const placeholder = <div className="lc-invisible">隐藏中：{node.visible}</div>;
      return wrapNode ? wrapNode(node, placeholder) : placeholder;
    }

    const meta = getMaterial(node.type);
    const props = evalRecord(node.props, scope);
    const children = (node.children ?? []).map((child) => (
      <Fragment key={child.id}>{renderNode(child, extra)}</Fragment>
    ));

    let element: ReactNode;
    if (!meta) {
      element = <div className="lc-unknown">未知组件：{node.type}</div>;
    } else {
      if (mode === "edit" && meta.formControl) {
        // 编辑态不触发交互（点按钮别真的跳走）；表单控件也不绑定受控值（否则只读告警）
        props.value = undefined;
        props.checked = undefined;
      }
      const handlers = mode === "view" ? nodeEvents(node, extra) : {};
      const Component = meta.component;
      element = (
        <Component {...props} {...handlers} children={children.length ? children : undefined} />
      );
    }
    return wrapNode ? wrapNode(node, element) : element;
  };

  return <>{renderNode(schema.root, {})}</>;
}

/** 把事件参数统一成 { value, raw }：兼容 antd 直接给值（Select/Switch）和 DOM 事件两种形态。 */
function normalizeEvent(arg: unknown): Record<string, unknown> {
  const target = (arg as { target?: { value?: unknown; checked?: unknown } } | null)?.target;
  const value =
    target && target.value !== undefined
      ? target.value
      : target && target.checked !== undefined
        ? target.checked
        : arg;
  return { value, raw: arg };
}
