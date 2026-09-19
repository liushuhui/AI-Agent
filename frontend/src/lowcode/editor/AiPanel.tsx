/**
 * AI 生成面板：自然语言 → 页面 Schema（SSE 流式）。
 *
 * - 物料目录从物料注册表实时导出，随请求发给后端（单一事实来源，不写死副本）；
 * - 生成过程中流式显示模型解说；拿到 schema 后自动应用到画布（进历史栈、可撤销）；
 * - 支持中止（AbortController）。
 */

import { useRef, useState } from "react";
import { App as AntdApp, Button, Input } from "antd";
import { agentStream } from "../../api/lowcode";
import type { MaterialBrief } from "../../api/lowcode";
import { applyPatches } from "../schema/patch";
import { listMaterials } from "../materials/registry";
import type { PageSchema } from "../schema/types";

export interface AiPanelProps {
  schema: PageSchema;
  onApply: (schema: PageSchema) => void;
}

const EXAMPLES = [
  "做一个用户管理页：搜索输入框 + 查询按钮 + 用户表格，接口 GET /users（keyword 参数）",
  "做一个数据看板：统计卡片展示用户总数，下面放用户表格",
  "把当前页面标题改成「客户管理」，表格列改成姓名、邮箱、创建时间",
];

function buildMaterialCatalog(): MaterialBrief[] {
  return listMaterials().map((material) => ({
    type: material.type,
    label: material.label,
    category: material.category,
    container: material.container ?? false,
    formControl: material.formControl ?? false,
    events: material.events ?? [],
    props: Object.entries(material.props ?? {}).map(([name, field]) => ({
      name,
      label: field.label,
      type: field.type,
      options: field.options,
    })),
    defaultProps: material.defaultProps,
  }));
}

export function AiPanel({ schema, onApply }: AiPanelProps) {
  const { message } = AntdApp.useApp();
  const [instruction, setInstruction] = useState("");
  const [running, setRunning] = useState(false);
  const [status, setStatus] = useState("");
  const [words, setWords] = useState("");
  const controllerRef = useRef<AbortController | null>(null);

  const generate = async () => {
    const text = instruction.trim();
    if (!text) {
      message.warning("先描述一下要生成什么");
      return;
    }
    setRunning(true);
    setWords("");
    setStatus("准备中…");
    const controller = new AbortController();
    controllerRef.current = controller;
    try {
      await agentStream(
        { instruction: text, schema, materials: buildMaterialCatalog() },
        (event) => {
          if (event.type === "text") {
            setWords((prev) => prev + event.content);
          } else if (event.type === "status") {
            setStatus(event.text);
          } else if (event.type === "schema") {
            onApply(event.schema);
            setStatus("✅ 已生成整页并应用到画布（可 Ctrl+Z 撤销，记得保存）");
            message.success("AI 已更新画布");
          } else if (event.type === "patch") {
            // 优先在前端命令层应用（能合到最新画布上）；失败再回退到后端已应用好的结果
            const local = applyPatches(schema, event.patches);
            const next = local.errors.length === 0 && local.applied > 0 ? local.schema : event.schema;
            if (next) {
              onApply(next);
              const how =
                local.errors.length === 0
                  ? `已增量修改 ${local.applied} 处`
                  : `已应用 ${event.count} 处修改`;
              setStatus(`✅ ${how}（可 Ctrl+Z 撤销，记得保存）`);
              message.success("AI 已完成增量修改");
            } else {
              setStatus("");
              message.error(`增量修改未生效：${local.errors.join("；")}`);
            }
          } else if (event.type === "error") {
            setStatus("");
            message.error(event.message);
          }
        },
        controller.signal,
      );
    } catch (error) {
      if (!(error instanceof DOMException && error.name === "AbortError")) {
        message.error(error instanceof Error ? error.message : String(error));
      }
    } finally {
      setRunning(false);
      controllerRef.current = null;
    }
  };

  const stop = () => controllerRef.current?.abort();

  return (
    <div className="lc-panel">
      <div className="lc-panel-head">
        <span className="lc-panel-title">✨ AI 生成页面</span>
        {running ? (
          <Button size="small" danger onClick={stop}>
            停止
          </Button>
        ) : null}
      </div>
      <div className="lc-tip" style={{ marginBottom: 8 }}>
        描述你想要的页面或改动：新建走整页生成，修改走增量 Patch（只改相关组件，不整页重生成）。
      </div>

      <Input.TextArea
        value={instruction}
        placeholder="例如：做一个用户管理页，带搜索和表格，接口是 /users"
        autoSize={{ minRows: 4, maxRows: 8 }}
        disabled={running}
        onChange={(event) => setInstruction(event.target.value)}
      />

      <div className="lc-login-chips" style={{ margin: "8px 0" }}>
        {EXAMPLES.map((example) => (
          <button
            key={example}
            type="button"
            className="lc-chip"
            disabled={running}
            onClick={() => setInstruction(example)}
          >
            {example.length > 22 ? `${example.slice(0, 22)}…` : example}
          </button>
        ))}
      </div>

      <Button type="primary" block loading={running} onClick={() => void generate()}>
        生成并应用到画布
      </Button>

      {status ? <div className="lc-tip">{status}</div> : null}
      {words ? <pre className="lc-ai-log">{words}</pre> : null}
    </div>
  );
}
