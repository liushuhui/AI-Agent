/**
 * 自定义组件接入示例 —— 业务要有自己的组件，照这个文件抄即可：
 *
 * 1. 写一个普通 React 组件，props 用 `Record<string, unknown>`（引擎传进来的是动态数据）；
 * 2. 调 registerMaterial 注册：声明 props 表单字段（属性面板自动生成）、
 *    是否容器（container）、支持哪些事件、默认值；
 * 3. 在 Renderer 之前 import 一次这个模块（Renderer.tsx 里已 import "./custom"）。
 *
 * 注册后：物料面板会出现它、可以拖到画布、属性面板能改、Schema 保存后预览一致。
 */

import { registerMaterial } from "./registry";

// 导出成组件，保持「文件只导出组件」，否则 react-refresh 规则会报错
export function StatCard(props: Record<string, unknown>) {
  const title = String(props.title ?? "指标");
  const value = props.value == null ? "-" : String(props.value);
  const hint = props.hint ? String(props.hint) : "";
  const color = String(props.color ?? "#4f8cff");
  return (
    <div className="lc-stat">
      <div className="lc-stat-title">{title}</div>
      <div className="lc-stat-value" style={{ color }}>
        {value}
      </div>
      {hint ? <div className="lc-stat-hint">{hint}</div> : null}
    </div>
  );
}

registerMaterial({
  type: "StatCard",
  label: "统计卡片",
  category: "自定义",
  component: StatCard,
  props: {
    title: { label: "标题", type: "string" },
    value: { label: "数值（可写表达式）", type: "expression" },
    hint: { label: "备注", type: "string" },
    color: { label: "强调色", type: "string" },
  },
  defaultProps: {
    title: "用户总数",
    value: "{{ dataSources.userList.data.length }}",
    hint: "来自 userList 数据源",
  },
});
