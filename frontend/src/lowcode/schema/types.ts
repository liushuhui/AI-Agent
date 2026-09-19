/**
 * 低代码引擎的类型定义：一份 PageSchema 走全流程
 * （AI 生成 → 拖拽编辑 → 属性配置 → 保存 → 渲染）。
 *
 * 约定：
 * - props 里的值可以是字符串表达式（`{{ state.keyword }}`），渲染时求值；
 * - children 只放「子组件」；antd 里配置型的子结构（Table.columns 等）放 props；
 * - events 的动作在 runtime/expression 的动作白名单里执行，不允许任意代码。
 */

export type PropFieldType = "string" | "number" | "boolean" | "select" | "json" | "expression";

export interface PropField {
  label: string;
  type: PropFieldType;
  options?: { label: string; value: string | number }[];
  placeholder?: string;
}

/** 物料元数据：同一份描述同时驱动属性面板、画布拖拽和（后续的）AI 物料目录。 */
export interface MaterialMeta {
  type: string;
  label: string;
  category: string;
  /** 是否可以作为拖拽容器接收子节点。 */
  container?: boolean;
  /** 表单类组件（受控输入控件）：编辑态不绑定 value/checked，避免只读告警。 */
  formControl?: boolean;
  props?: Record<string, PropField>;
  /** 支持的事件名（如 onClick / onChange），属性面板据此生成事件配置。 */
  events?: string[];
  defaultProps?: Record<string, unknown>;
  /** 拖入时附带的初始子节点（创建时会重新分配 id）。 */
  defaultChildren?: ComponentNode[];
}

export interface ComponentNode {
  id: string;
  type: string;
  props?: Record<string, unknown>;
  /** 显示条件表达式，留空 = 总是显示。 */
  visible?: string;
  children?: ComponentNode[];
  events?: Record<string, Action | Action[]>;
}

export interface DataSourceDef {
  id: string;
  kind?: "rest";
  method: "GET" | "POST" | "PUT" | "DELETE" | "PATCH";
  url: string;
  params?: Record<string, unknown>;
  headers?: Record<string, unknown>;
  /** 进页面自动加载（否则只能靠事件里的 callDataSource 触发）。 */
  autoLoad?: boolean;
}

export type Action =
  | { kind: "setState"; key: string; value: unknown }
  | { kind: "navigate"; to: string }
  | { kind: "notify"; text: string; level?: "info" | "success" | "warning" | "error" }
  | {
      kind: "callDataSource";
      dataSource: string;
      params?: Record<string, unknown>;
      refresh?: string[];
      onSuccess?: Action[];
      onError?: Action[];
    };

/**
 * AI 增量修改的语义化操作（与后端 lowcode_patch.py 一一对应）。
 * 不整页重生成，只提交「改哪个节点、怎么改」的指令。
 */
export type PatchOp =
  | { op: "update_props"; nodeId: string; props: Record<string, unknown> }
  | { op: "set_visible"; nodeId: string; visible: string | null }
  | { op: "set_event"; nodeId: string; event: string; action: Action | null }
  | { op: "add_node"; parentId: string; node: ComponentNode; index?: number }
  | { op: "remove_node"; nodeId: string }
  | { op: "move_node"; nodeId: string; parentId: string; index?: number }
  | { op: "set_state"; key: string; value?: unknown }
  | { op: "upsert_data_source"; dataSource: DataSourceDef }
  | { op: "remove_data_source"; id: string }
  | { op: "set_page_name"; name: string };

export interface PageSchema {
  schemaVersion: string;
  name?: string;
  state?: Record<string, unknown>;
  dataSources?: DataSourceDef[];
  root: ComponentNode;
}

// ---------------- 后端接口返回的页面元数据 ----------------

export interface PageSummary {
  id: string;
  name: string;
  description: string;
  visibility: "private" | "public";
  owner_id: string;
  status: "draft" | "published";
  version: number;
  created_at: string;
  updated_at: string;
}

export interface PageDetail extends PageSummary {
  schema: PageSchema;
}

export interface LowcodeUser {
  id: string;
  name: string;
  role: "admin" | "editor" | "viewer";
}
