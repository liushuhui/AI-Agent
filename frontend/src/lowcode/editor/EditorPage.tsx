/**
 * 页面编辑器：左（物料 + 组件树）/ 中（画布）/ 右（AI / 属性 / 数据源 / 事件）。
 *
 * 所有修改都调用 schema/commands 的纯函数（命令层），历史栈存整份 schema 快照；
 * AI 生成的 schema 走同一个入口，所以「AI 改坏了」直接 Ctrl+Z 就能退回。
 */

import { useEffect, useReducer, useState } from "react";
import { useNavigate, useParams } from "react-router";
import { App as AntdApp, Button, Input, Modal, Space, Tabs, Tag } from "antd";
import { logout, useCurrentUser } from "../../api/auth";
import {
  clearDraft,
  getPage,
  publishPage,
  rollbackPage,
  savePage,
  writeDraft,
} from "../../api/lowcode";
import type { ComponentNode, PageDetail, PageSchema } from "../schema/types";
import {
  findNode,
  findParent,
  insertChild,
  makeNode,
  moveNode,
  moveSibling,
  removeNode,
  updateNode,
} from "../schema/commands";
import { getMaterial } from "../materials/registry";
import { RuntimeStore } from "../runtime/store";
import { AiPanel } from "./AiPanel";
import { Canvas } from "./Canvas";
import { MaterialPanel } from "./MaterialPanel";
import { OutlinePanel } from "./OutlinePanel";
import { PropertyPanel } from "./PropertyPanel";
import { DataSourcePanel } from "./DataSourcePanel";
import { EventPanel } from "./EventPanel";
import { VersionDrawer } from "./VersionDrawer";
import "../lowcode.css";

export function EditorPage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const { message } = AntdApp.useApp();
  const user = useCurrentUser();

  // 服务端页面详情（status / version 等）；保存、发布、回滚后都拿它刷新
  const [page, setPage] = useState<PageDetail | null>(null);
  // 当前画布结构；所有编辑都基于它做「旧树 → 新树」的替换
  const [schema, setSchema] = useState<PageSchema | null>(null);
  const [name, setName] = useState("");
  // 运行时（表达式求值 / 数据源 / 组件方法）：画布渲染与事件执行都靠它
  const [store, setStore] = useState<RuntimeStore | null>(null);
  // 当前选中的节点 id：决定属性 / 事件面板内容，以及「新物料放哪儿」
  const [selectedId, setSelectedId] = useState("root");
  // 有未保存修改：工具栏显示「未保存」，发布前会据此自动保存
  const [dirty, setDirty] = useState(false);
  // 手动递增的修订号：撤销/重做直接 setSchema 不经过命令层，
  // 子面板（属性/数据源/事件）靠它感知变化并强制刷新
  const [revision, bumpRevision] = useReducer((n: number) => n + 1, 0);
  // 历史栈存整份 schema 快照（撤销/重做）；用 state 而不是 ref，因为渲染时要读长度
  const [history, setHistory] = useState<{ past: PageSchema[]; future: PageSchema[] }>({
    past: [],
    future: [],
  });
  // 发布弹窗：开关、说明文字、提交中的 loading
  const [publishOpen, setPublishOpen] = useState(false);
  const [publishComment, setPublishComment] = useState("");
  const [publishing, setPublishing] = useState(false);
  // 版本抽屉（历史版本对比 / 回滚）
  const [versionsOpen, setVersionsOpen] = useState(false);

  // 打开编辑器时按路由参数 id 拉取页面详情，并初始化运行时
  useEffect(() => {
    // 竞态保护：请求还没回来时切了 id / 卸载组件，就丢弃这次结果，别再 setState
    let alive = true;
    getPage(id)
      .then((detail) => {
        if (!alive) return;
        setPage(detail);
        setSchema(detail.schema);
        setName(detail.name);
        setSelectedId(detail.schema.root.id);
        setDirty(false);
        setHistory({ past: [], future: [] });
        // 运行时宿主：把组件的 notify / navigate 映射到 antd 提示与路由跳转，
        // 第三个参数是表达式可读的环境变量（当前用户、角色、接口前缀）
        const runtime = new RuntimeStore(
          detail.schema,
          {
            notify: (text, level) => message.open({ type: level ?? "info", content: text }),
            navigate: (to) => navigate(to),
          },
          { user: user?.id ?? "", role: user?.role ?? "", baseUrl: "/api" },
        );
        runtime.autoLoad(); // 画布也加载 autoLoad 数据源：编辑器里直接看到真实数据
        setStore(runtime);
      })
      .catch((error: unknown) => {
        message.error(error instanceof Error ? error.message : String(error));
      });
    return () => {
      alive = false;
    };
  }, [id, message, navigate, user?.id, user?.role]);

  // 编辑中的 schema 同步给运行时（例如刚改完数据源 URL，点「运行」立即用新的）
  useEffect(() => {
    if (store && schema) store.setSchema(schema);
  }, [store, schema]);

  /** 提交一份新 schema：当前版本进「过去」栈（最多留 50 步），并清空「未来」栈。 */
  const pushSchema = (next: PageSchema) => {
    if (!schema) return;
    // slice(-49) 只保留最近 49 条，加上这次的 schema 正好 50 步上限；
    // future 清空，因为「撤销后又做了新修改」就不该再能重做旧分支
    setHistory((h) => ({ past: [...h.past.slice(-49), schema], future: [] }));
    setSchema(next);
    setDirty(true);
  };

  /** 以「整棵树 → 新树」的形式提交一次结构修改（无变化时不进历史栈）。 */
  const rootOp = (op: (root: ComponentNode) => ComponentNode, selectId?: string) => {
    if (!schema) return;
    const root = op(schema.root);
    // 命令层遇到非法操作（如拖进自己的子树）会原样返回旧树 —— 直接忽略，别记进历史
    if (root === schema.root) return;
    pushSchema({ ...schema, root });
    if (selectId) setSelectedId(selectId); // 新增节点后顺手选中它
  };

  /** 撤销：弹出 past 栈顶，当前版本压入 future 栈。 */
  const undo = () => {
    if (!schema || history.past.length === 0) return;
    const previous = history.past[history.past.length - 1];
    setHistory({ past: history.past.slice(0, -1), future: [...history.future, schema] });
    setSchema(previous);
    setDirty(true);
    bumpRevision(); // 撤销结果也要刷新右侧面板
  };

  /** 重做：与 undo 对称，从 future 栈取回。 */
  const redo = () => {
    if (!schema || history.future.length === 0) return;
    const next = history.future[history.future.length - 1];
    setHistory({ past: [...history.past, schema], future: history.future.slice(0, -1) });
    setSchema(next);
    setDirty(true);
    bumpRevision();
  };

  /** 推断新节点放哪儿：选中容器就放进它内部，否则放到其父级（相当于兄弟位置）。 */
  const defaultParentId = (): string | null => {
    if (!schema) return null;
    const selected = selectedId ? findNode(schema.root, selectedId) : null;
    if (!selected) return schema.root.id;
    if (getMaterial(selected.type)?.container) return selected.id;
    return findParent(schema.root, selected.id)?.id ?? schema.root.id;
  };

  /** 添加物料：显式给了 parentId 用它（画布拖拽落点），否则按当前选中推断。 */
  const addMaterial = (type: string, parentId?: string | null) => {
    if (!schema) return;
    // undefined = 没指定落点（点物料面板添加），按选中推断；
    // null / 具体 id = 拖拽落点，null 表示落在根画布上
    const target = parentId === undefined ? defaultParentId() : parentId;
    const node = makeNode(type, getMaterial(type));
    rootOp((root) => insertChild(root, target, node), node.id); // 插入后选中新节点
  };

  // 当前选中节点（每次渲染现算，schema 变了自动是最新的）
  const selectedNode = schema && selectedId ? findNode(schema.root, selectedId) : null;

  /** 把属性/事件面板的修改写到选中节点上。 */
  const patchSelected = (patch: Partial<ComponentNode>) => {
    if (!selectedNode) return;
    rootOp((root) => updateNode(root, selectedNode.id, patch));
  };

  /** 删除选中节点；根节点不允许删。删完把选中态挪到父级，避免面板悬空。 */
  const removeSelected = () => {
    if (!schema || !selectedNode || selectedNode.id === schema.root.id) return;
    const parent = findParent(schema.root, selectedNode.id);
    rootOp((root) => removeNode(root, selectedNode.id));
    setSelectedId(parent?.id ?? schema.root.id);
  };

  /** 保存到服务端；返回是否成功，发布流程要拿它决定是否继续。 */
  const save = async (): Promise<boolean> => {
    if (!schema || !page) return false;
    try {
      // baseVersion 传的是「我这一版基于哪个版本」：
      // 别人先改过的话版本对不上，后端会拒绝并报版本冲突，防止互相覆盖
      const updated = await savePage(page.id, { schema, name, baseVersion: page.version });
      setPage(updated);
      setDirty(false);
      clearDraft(page.id); // 已入库，本地草稿（预览用）可以清掉
      message.success("已保存");
      return true;
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error));
      return false;
    }
  };

  /** 发布 = 保存（如有未保存修改）+ 服务端把当前快照固化成新版本。 */
  const publish = async () => {
    if (!page) return;
    setPublishing(true);
    try {
      if (dirty && !(await save())) return; // 有未保存修改：先保存再发布
      const updated = await publishPage(page.id, publishComment.trim());
      setPage(updated);
      setPublishOpen(false);
      setPublishComment("");
      message.success(`已发布 v${updated.version}`);
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error));
    } finally {
      setPublishing(false); // 无论成败都要收掉 loading
    }
  };

  /** 回滚到指定历史版本（由版本抽屉触发）。 */
  const rollback = async (version: number) => {
    if (!page) return;
    try {
      const updated = await rollbackPage(page.id, version);
      setPage(updated);
      setSchema(updated.schema);
      store?.setSchema(updated.schema); // 运行时也要换上回滚后的 schema
      setSelectedId(updated.schema.root.id);
      setHistory({ past: [], future: [] }); // 旧历史栈对新版本没意义，清空
      setDirty(false);
      message.success(`已回滚到 v${version}（当前 v${updated.version}）`);
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error));
    }
  };

  /** AI 生成的整页 schema 入口：与手动编辑走同一条提交路径。 */
  const applyAiSchema = (next: PageSchema) => {
    pushSchema(next); // 进历史栈：不满意直接撤销
    if (next.name && next.name !== name) setName(next.name); // AI 起了新页面名就同步过来
  };

  /** 新标签页预览：先把当前 schema 写进 localStorage 草稿，预览页带 ?draft=1 读它。 */
  const preview = () => {
    if (!schema || !page) return;
    writeDraft(page.id, schema); // 预览页优先读本地草稿，看到的正是当前编辑内容
    window.open(`/preview/${page.id}?draft=1`, "_blank");
  };

  const signOut = async () => {
    await logout();
    navigate("/login", { replace: true }); // 用 replace：退出后不应回退到编辑器
  };

  // 页面数据与运行时都就绪前先占位，避免面板拿到 null 报错
  if (!schema || !store) return <div className="lc-loading">加载中…</div>;

  return (
    <div className="lc-editor">
      {/* 顶栏：左 = 返回 / 页面名 / 状态标签，右 = 历史操作与保存发布 */}
      <header className="lc-toolbar">
        <Space size={8}>
          <Button size="small" onClick={() => navigate("/lowcode")}>
            ← 列表
          </Button>
          {/* 页面名：改名也算一次修改（标脏，保存时和 schema 一起提交） */}
          <Input
            size="small"
            value={name}
            style={{ width: 200 }}
            onChange={(event) => {
              setName(event.target.value);
              setDirty(true);
            }}
          />
          {dirty ? <span className="lc-dirty">未保存</span> : null}
          <Tag color={page?.status === "published" ? "green" : "default"}>
            {page?.status === "published" ? "已发布" : "草稿"} v{page?.version}
          </Tag>
        </Space>
        <Space size={8}>
          {/* 没得撤销/重做时按钮置灰：直接看历史栈长度 */}
          <Button size="small" disabled={history.past.length === 0} onClick={undo}>
            撤销
          </Button>
          <Button size="small" disabled={history.future.length === 0} onClick={redo}>
            重做
          </Button>
          <Button size="small" onClick={() => setVersionsOpen(true)}>
            版本
          </Button>
          <Button size="small" onClick={() => setPublishOpen(true)}>
            发布
          </Button>
          <Button size="small" onClick={preview}>
            预览
          </Button>
          <Button size="small" type="primary" onClick={() => void save()}>
            保存
          </Button>
          <span className="lc-muted">{user?.name}</span>
          <Button size="small" type="text" onClick={() => void signOut()}>
            退出
          </Button>
        </Space>
      </header>

      <div className="lc-body">
        {/* 左栏：物料面板（点击添加）+ 组件大纲树 */}
        <aside className="lc-side">
          <MaterialPanel onAdd={(type) => addMaterial(type)} />
          <OutlinePanel root={schema.root} selectedId={selectedId} onSelect={setSelectedId} />
        </aside>

        {/* 中栏：画布；选中、拖入、拖动节点分别映射到对应命令 */}
        <main className="lc-main">
          <Canvas
            schema={schema}
            store={store}
            selectedId={selectedId}
            onSelect={setSelectedId}
            onAddMaterial={(type, parentId) => addMaterial(type, parentId)}
            onMoveNode={(nodeId, parentId) => rootOp((root) => moveNode(root, nodeId, parentId))}
          />
        </main>

        {/* 右栏：AI 生成 / 属性 / 数据源 / 事件，四个面板各管一类编辑 */}
        <aside className="lc-right">
          <Tabs
            size="small"
            items={[
              {
                key: "ai",
                label: "✨ AI 生成",
                // 产出整份新 schema，经 applyAiSchema 进历史栈，和手动编辑同一入口
                children: <AiPanel schema={schema} onApply={applyAiSchema} />,
              },
              {
                key: "props",
                label: "属性",
                children: (
                  <PropertyPanel
                    node={selectedNode}
                    revision={revision}
                    onPatch={patchSelected}
                    onDelete={removeSelected}
                    onMove={(delta) => {
                      // delta = -1 / +1：把选中节点在同级里上移 / 下移一位
                      if (selectedNode) rootOp((root) => moveSibling(root, selectedNode.id, delta));
                    }}
                  />
                ),
              },
              {
                key: "data",
                label: "数据源",
                children: (
                  <DataSourcePanel
                    schema={schema}
                    revision={revision}
                    store={store}
                    onChange={pushSchema} // 数据源的增删改直接提交整份新 schema
                  />
                ),
              },
              {
                key: "events",
                label: "事件",
                children: (
                  <EventPanel
                    node={selectedNode}
                    schema={schema}
                    revision={revision}
                    onPatch={patchSelected}
                  />
                ),
              },
            ]}
          />
        </aside>
      </div>

      {/* 发布弹窗：填发布说明，确认后先保存再发布 */}
      <Modal
        title="发布当前版本"
        open={publishOpen}
        onOk={() => void publish()}
        onCancel={() => setPublishOpen(false)}
        okText="发布"
        cancelText="取消"
        confirmLoading={publishing}
      >
        <div className="lc-field">
          <label className="lc-field-label">发布说明（可选）</label>
          <Input
            value={publishComment}
            placeholder="例如：新增搜索与分页"
            onChange={(event) => setPublishComment(event.target.value)}
          />
        </div>
        <div className="lc-tip">
          发布会把当前画布快照存成 v{page?.version} 并标记「已发布」
          {dirty ? "；检测到未保存修改，会先自动保存再发布。" : "。"}
        </div>
      </Modal>

      {/* 版本抽屉：列出历史版本、与当前 schema 对比，可一键回滚 */}
      <VersionDrawer
        pageId={id}
        open={versionsOpen}
        currentSchema={schema}
        onClose={() => setVersionsOpen(false)}
        onRollback={rollback}
      />
    </div>
  );
}
