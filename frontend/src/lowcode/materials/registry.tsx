/* eslint-disable react-refresh/only-export-components -- 注册表是「组件 + 元数据/函数」的混合模块，HMR 降级可接受 */
/**
 * 物料注册表：Schema 里的 type 在这里找到「组件实现 + 能力描述」。
 *
 * 一份元数据同时驱动三个消费者：
 * - 编辑器：物料面板分组、属性面板自动生成表单、画布拖拽容器判断；
 * - 渲染器：type → React 组件；
 * - （下一阶段的）AI：物料目录，让模型知道有哪些组件、每个组件能配什么。
 *
 * 自定义组件接入（见 custom.tsx）：调 registerMaterial 即可，不用改引擎。
 */

import type { ComponentType, ElementType, ReactNode } from "react";
import {
  Button,
  Card,
  Col,
  DatePicker,
  Input,
  InputNumber,
  Row,
  Select,
  Space,
  Switch,
  Table,
  Typography,
} from "antd";
import type { MaterialMeta, PropField, PropFieldType } from "../schema/types";

export interface Material extends MaterialMeta {
  component: ComponentType<Record<string, unknown>>;
}

const registry = new Map<string, Material>();

export function registerMaterial(material: Material): void {
  registry.set(material.type, material);
}

export function getMaterial(type: string): Material | undefined {
  return registry.get(type);
}

export function listMaterials(): Material[] {
  return [...registry.values()];
}

/** 按分类分组，物料面板直接照着渲染。 */
export function materialCategories(): { category: string; items: Material[] }[] {
  const groups = new Map<string, Material[]>();
  for (const material of registry.values()) {
    const items = groups.get(material.category) ?? [];
    items.push(material);
    groups.set(material.category, items);
  }
  return [...groups.entries()].map(([category, items]) => ({ category, items }));
}

/** 把 antd 组件宽化成「动态 props 组件」：Schema 里的 props 是运行期数据，TS 无法静态校验。 */
function dynamic(component: ElementType): ComponentType<Record<string, unknown>> {
  return component as unknown as ComponentType<Record<string, unknown>>;
}

const UI = {
  Text: dynamic(Typography.Text),
  Title: dynamic(Typography.Title),
  Button: dynamic(Button),
  Card: dynamic(Card),
  Row: dynamic(Row),
  Col: dynamic(Col),
  Space: dynamic(Space),
  Input: dynamic(Input),
  InputNumber: dynamic(InputNumber),
  Select: dynamic(Select),
  Switch: dynamic(Switch),
  DatePicker: dynamic(DatePicker),
  Table: dynamic(Table),
};

// 需要把 text 变成 children 的组件写薄包装（children 用 attribute 形式传）
const TextView = (props: Record<string, unknown>) => {
  const { text, ...rest } = props;
  return <UI.Text {...rest} children={text as ReactNode} />;
};

const TitleView = (props: Record<string, unknown>) => {
  const { text, ...rest } = props;
  return <UI.Title {...rest} children={text as ReactNode} />;
};

const ButtonView = (props: Record<string, unknown>) => {
  const { text, children, ...rest } = props;
  return <UI.Button {...rest} children={(text ?? children) as ReactNode} />;
};

const field = (label: string, type: PropFieldType, extra: Partial<PropField> = {}): PropField => ({
  label,
  type,
  ...extra,
});

const options = (...values: (string | number)[]): PropField["options"] =>
  values.map((value) => ({ label: String(value), value }));

// ---------------- 基础 ----------------

registerMaterial({
  type: "Text",
  label: "文本",
  category: "基础",
  component: TextView,
  props: {
    text: field("文本内容（可写表达式）", "string"),
    strong: field("加粗", "boolean"),
    code: field("代码样式", "boolean"),
  },
  defaultProps: { text: "一段文本" },
});

registerMaterial({
  type: "Title",
  label: "标题",
  category: "基础",
  component: TitleView,
  props: {
    text: field("标题内容", "string"),
    level: field("层级", "select", { options: options("1", "2", "3", "4", "5") }),
  },
  defaultProps: { text: "标题", level: "3" },
});

registerMaterial({
  type: "Button",
  label: "按钮",
  category: "基础",
  component: ButtonView,
  props: {
    text: field("按钮文字", "string"),
    type: field("样式", "select", {
      options: options("default", "primary", "dashed", "text", "link"),
    }),
    danger: field("危险色", "boolean"),
    disabled: field("禁用", "boolean"),
  },
  events: ["onClick"],
  defaultProps: { text: "按钮", type: "primary" },
});

// ---------------- 布局（container: 可接收拖拽子节点） ----------------

registerMaterial({
  type: "Card",
  label: "卡片",
  category: "布局",
  container: true,
  component: UI.Card,
  props: {
    title: field("标题", "string"),
    variant: field("边框样式", "select", { options: options("outlined", "borderless") }),
  },
  defaultProps: { title: "卡片" },
  defaultChildren: [{ id: "seed", type: "Text", props: { text: "卡片内容" } }],
});

registerMaterial({
  type: "Row",
  label: "栅格行",
  category: "布局",
  container: true,
  component: UI.Row,
  props: { gutter: field("列间距", "number") },
  defaultProps: { gutter: 12 },
});

registerMaterial({
  type: "Col",
  label: "栅格列",
  category: "布局",
  container: true,
  component: UI.Col,
  props: { span: field("占宽（1-24）", "number") },
  defaultProps: { span: 12 },
});

registerMaterial({
  type: "Space",
  label: "间距容器",
  category: "布局",
  container: true,
  component: UI.Space,
  props: {
    orientation: field("方向", "select", { options: options("horizontal", "vertical") }),
    size: field("间距", "select", { options: options("small", "middle", "large") }),
  },
  defaultProps: { orientation: "horizontal", size: "middle" },
});

// ---------------- 表单 ----------------

registerMaterial({
  type: "Input",
  label: "输入框",
  category: "表单",
  formControl: true,
  component: UI.Input,
  props: {
    placeholder: field("占位文案", "string"),
    allowClear: field("可清除", "boolean"),
    value: field("值（绑定表达式）", "expression"),
    style: field("样式（JSON）", "json"),
  },
  events: ["onChange", "onPressEnter"],
  defaultProps: { placeholder: "请输入", style: { width: 220 } },
});

registerMaterial({
  type: "InputNumber",
  label: "数字输入",
  category: "表单",
  formControl: true,
  component: UI.InputNumber,
  props: {
    placeholder: field("占位文案", "string"),
    min: field("最小值", "number"),
    max: field("最大值", "number"),
    value: field("值（绑定表达式）", "expression"),
  },
  events: ["onChange"],
  defaultProps: { placeholder: "请输入数字" },
});

registerMaterial({
  type: "Select",
  label: "下拉选择",
  category: "表单",
  formControl: true,
  component: UI.Select,
  props: {
    placeholder: field("占位文案", "string"),
    options: field("选项（JSON 数组）", "json"),
    allowClear: field("可清除", "boolean"),
    value: field("值（绑定表达式）", "expression"),
  },
  events: ["onChange"],
  defaultProps: {
    placeholder: "请选择",
    options: [
      { label: "选项 A", value: "a" },
      { label: "选项 B", value: "b" },
    ],
    style: { width: 180 },
  },
});

registerMaterial({
  type: "Switch",
  label: "开关",
  category: "表单",
  formControl: true,
  component: UI.Switch,
  props: { checked: field("选中（绑定表达式）", "expression") },
  events: ["onChange"],
});

registerMaterial({
  type: "DatePicker",
  label: "日期选择",
  category: "表单",
  formControl: true,
  component: UI.DatePicker,
  props: {
    placeholder: field("占位文案", "string"),
    value: field("值（绑定表达式）", "expression"),
  },
  events: ["onChange"],
  defaultProps: { placeholder: "选择日期" },
});

// ---------------- 数据展示 ----------------

registerMaterial({
  type: "Table",
  label: "表格",
  category: "数据展示",
  component: UI.Table,
  props: {
    columns: field("列定义（JSON 数组）", "json"),
    dataSource: field("数据（绑定表达式）", "expression"),
    loading: field("加载中（表达式）", "expression"),
    rowKey: field("行主键字段", "string"),
    pagination: field("分页", "boolean"),
  },
  defaultProps: {
    rowKey: "id",
    pagination: true,
    dataSource: "{{ [] }}",
    columns: [{ key: "name", title: "名称", dataIndex: "name" }],
  },
});
