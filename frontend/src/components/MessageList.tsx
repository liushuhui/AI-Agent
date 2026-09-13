import { useEffect } from "react";
import { Alert, Card, Collapse, Flex, Typography } from "antd";

import type { UseChat } from "../hooks/useChat";
import type { Message } from "../types";
import { ApprovalCard } from "./ApprovalCard";
import { AttachmentChip } from "./AttachmentChip";

/** 思维链折叠面板的 key */
const THINK_KEY = "think";

/** 一轮消息：附件 → 思维链 → 正文气泡 → 审批面板 → 错误 */
function MessageRow({ m, chat }: { m: Message; chat: UseChat }) {
  const isUser = m.role === "user";
  // 只发了附件的用户消息不再画一个空气泡；
  // 助手消息在「等审批且还没正文」时也用审批面板代替空气泡
  const showCard = isUser
    ? m.content !== ""
    : m.content !== "" || (chat.streaming && !m.approval);

  return (
    <Flex vertical gap={6} align={isUser ? "flex-end" : undefined}>
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        {isUser ? "你" : "助手"}
      </Typography.Text>

      {m.attachments.length > 0 && (
        <Flex
          gap={8}
          wrap
          justify={isUser ? "flex-end" : "flex-start"}
          style={{ maxWidth: "88%" }}
        >
          {m.attachments.map((a) => (
            <AttachmentChip key={a.uid} item={a} />
          ))}
        </Flex>
      )}

      {m.hasThinking && (
        <Collapse
          size="small"
          activeKey={m.thinkingOpen ? [THINK_KEY] : []}
          onChange={() => chat.toggleThinking(m.id)}
          items={[
            {
              key: THINK_KEY,
              // 生成期间推理链可能还在追加，标签统一显示「思考中…」更准确
              label: chat.streaming ? "思考中…" : "已深度思考",
              children: (
                <Typography.Paragraph
                  type="secondary"
                  style={{ margin: 0, fontSize: 13, whiteSpace: "pre-wrap" }}
                >
                  {m.thinking}
                </Typography.Paragraph>
              ),
            },
          ]}
          styles={{ title: { fontSize: 13 }, body: { fontSize: 13 } }}
          style={{ maxWidth: "88%", border: "1px dashed #262b38", borderRadius: 10 }}
        />
      )}

      {showCard && (
        <Card
          variant="outlined"
          styles={{
            body: {
              padding: "10px 14px",
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
            },
          }}
          style={{
            maxWidth: "88%",
            boxShadow: "none",
            ...(isUser ? { background: "#4f8cff", borderColor: "#4f8cff" } : null),
          }}
        >
          {m.content ? (
            <Typography.Text style={{ color: isUser ? "#fff" : undefined }}>
              {m.content}
            </Typography.Text>
          ) : (
            // 等待首个 token 时的闪烁光标
            <Typography.Text className="caret">▍</Typography.Text>
          )}
        </Card>
      )}

      {m.approval && m.approval.status !== "done" && (
        <ApprovalCard
          approval={m.approval}
          streaming={chat.streaming}
          onPatch={(index, patchItem) => chat.patchApprovalItem(m.id, index, patchItem)}
          onApproveAll={() => chat.approveAll(m.id)}
          onSubmit={() => chat.submitApproval(m.id)}
        />
      )}

      {m.errors.map((e, i) => (
        <Alert
          key={i}
          type="error"
          title={e}
          style={{ maxWidth: "88%", padding: "4px 10px", fontSize: 13 }}
        />
      ))}
    </Flex>
  );
}

/** 消息列表：有新消息或新内容时自动滚到底部 */
export function MessageList({
  chat,
  logRef,
}: {
  chat: UseChat;
  logRef: React.RefObject<HTMLDivElement | null>;
}) {
  const { messages } = chat;

  useEffect(() => {
    const log = logRef.current;
    if (log) log.scrollTop = log.scrollHeight;
  }, [messages, logRef]);

  return (
    <Flex ref={logRef} vertical gap={16} style={{ flex: 1, marginBottom: 20 }}>
      {messages.length === 0 && (
        <Typography.Text type="secondary" style={{ fontSize: 13 }}>
          可以拖拽文件到这里、直接粘贴截图，或点「附件」选择：图片 / PDF / Word / Excel / CSV / 文本
        </Typography.Text>
      )}

      {messages.map((m) => (
        <MessageRow key={m.id} m={m} chat={chat} />
      ))}
    </Flex>
  );
}
