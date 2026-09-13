import { Alert, Button, Card, Flex, Input, Radio, Tag, Typography } from "antd";

import type { ApprovalDecisionType, ApprovalItem, ApprovalState } from "../types";

/** 审批选项的中文标签 */
const DECISION_LABELS: Record<ApprovalDecisionType, string> = {
  approve: "批准执行",
  edit: "改参数后执行",
  reject: "拒绝执行",
  respond: "由我代答",
};

/**
 * 人工审批面板：工具调用被后端的 HumanInTheLoopMiddleware 挂起时，
 * 由用户在这里决定「批准 / 改参数 / 拒绝 / 代答」，提交后才真正执行。
 *
 * 选项不是写死的，而是按后端下发的 allowed_decisions 渲染，
 * 这样在后端调整某工具的可选动作时，前端不用跟着改。
 */
export function ApprovalCard({
  approval,
  streaming,
  onPatch,
  onApproveAll,
  onSubmit,
}: {
  approval: ApprovalState;
  streaming: boolean;
  onPatch: (index: number, patch: Partial<ApprovalItem>) => void;
  onApproveAll: () => void;
  onSubmit: () => void;
}) {
  const busy = streaming || approval.status === "submitting";
  const undecided = approval.items.filter((it) => !it.decision).length;

  return (
    <Card
      variant="outlined"
      className="approval"
      style={{ maxWidth: "88%", boxShadow: "none" }}
      styles={{ body: { padding: "12px 14px" } }}
    >
      <Flex vertical gap={12}>
        <Flex align="center" gap={8} wrap>
          <Typography.Text strong style={{ fontSize: 13 }}>
            工具调用需要你确认
          </Typography.Text>
          <Tag color="gold" style={{ marginInlineEnd: 0 }}>
            {approval.items.length} 项
          </Tag>
          {!busy && (
            <a className="approval-act" onClick={onApproveAll}>
              全部批准
            </a>
          )}
        </Flex>

        {approval.items.map((item, index) => (
          <Flex key={`${item.name}-${index}`} vertical gap={6} className="approval-item">
            <Flex align="center" gap={8} wrap>
              <Tag color="blue" style={{ marginInlineEnd: 0 }}>
                {item.name}
              </Tag>
              {/* 文件类工具把目标路径单独拎出来，比埋在 JSON 里好认 */}
              {typeof item.args.path === "string" && (
                <span className="approval-file">{item.args.path}</span>
              )}
              {item.description && (
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  {item.description}
                </Typography.Text>
              )}
            </Flex>

            <Radio.Group
              size="small"
              optionType="button"
              value={item.decision}
              disabled={busy}
              onChange={(e) =>
                onPatch(index, { decision: e.target.value as ApprovalDecisionType })
              }
              options={item.allowed_decisions.map((d) => ({
                value: d,
                label: DECISION_LABELS[d],
              }))}
            />

            {item.decision === "edit" && (
              // 选「改参数后执行」才给编辑框，避免平时占地方
              <Input.TextArea
                value={item.argsText}
                disabled={busy}
                autoSize={{ minRows: 3, maxRows: 8 }}
                onChange={(e) => onPatch(index, { argsText: e.target.value })}
                style={{ fontFamily: "Consolas, monospace", fontSize: 12.5 }}
              />
            )}

            {item.decision === "respond" && (
              // 选「由我代答」时填的就是要回给模型的工具返回值，工具本身不会执行
              <Input.TextArea
                value={item.respondMessage ?? ""}
                disabled={busy}
                autoSize={{ minRows: 2, maxRows: 6 }}
                placeholder="填写要回给模型的工具结果"
                onChange={(e) => onPatch(index, { respondMessage: e.target.value })}
              />
            )}

            {item.decision !== "edit" && item.decision !== "respond" && (
              // 写文件的参数可能很长（整份文件内容），给个固定高度的滚动区，别把页面撑开
              <pre className="approval-args">{JSON.stringify(item.args, null, 2)}</pre>
            )}
          </Flex>
        ))}

        {approval.error && (
          <Alert
            type="warning"
            title={approval.error}
            style={{ padding: "4px 10px", fontSize: 13 }}
          />
        )}

        <Flex align="center" gap={10} wrap>
          <Button
            type="primary"
            size="small"
            loading={approval.status === "submitting"}
            disabled={busy || undecided > 0}
            onClick={onSubmit}
          >
            提交审批
          </Button>
          {undecided > 0 && (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              还有 {undecided} 项未选择
            </Typography.Text>
          )}
        </Flex>
      </Flex>
    </Card>
  );
}
