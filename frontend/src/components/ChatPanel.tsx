import { useEffect, useRef, useState } from "react";
import { App as AntdApp, Flex, Input, Typography } from "antd";

import { useAttachments } from "../hooks/useAttachments";
import { useChat } from "../hooks/useChat";
import { useConversations } from "../hooks/useConversations";
import { useWorkspace } from "../hooks/useWorkspace";
import { AttachmentChip } from "./AttachmentChip";
import { Composer } from "./Composer";
import { ConversationList } from "./ConversationList";
import { MessageList } from "./MessageList";
import { TodoPanel } from "./TodoPanel";
import { WorkspacePanel } from "./WorkspacePanel";

/** 把错误对象翻成能给用户看的一句话 */
const errText = (err: unknown) => (err instanceof Error ? err.message : String(err));

/**
 * 页面装配层：把「会话列表」「对话状态机」「附件队列」「工作目录」接起来。
 *
 * 职责分得很清：会话列表（useConversations）只管有哪些对话，
 * 消息状态（useChat）只管当前这一屏；这里负责把两边接上，
 * 并处理「发消息前先确保有会话」这类跨模块的动作。
 */
export function ChatPanel() {
  const { message } = AntdApp.useApp();

  const chat = useChat();
  const conv = useConversations();
  const workspace = useWorkspace();
  // 附件队列需要知道「某个附件是否已进入历史消息」，所以放在 chat 之后构造
  const files = useAttachments(chat.isAttachedToMessage);

  const [input, setInput] = useState("");
  const logRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<React.ComponentRef<typeof Input.TextArea>>(null);
  const bootedRef = useRef(false);

  // 首次进入：拉一次会话列表，并回到最近聊过的那一条
  // （刷新页面不丢上下文；一条都没有就停在空白新对话）
  useEffect(() => {
    if (bootedRef.current) return;
    bootedRef.current = true;
    void (async () => {
      try {
        const items = await conv.refresh();
        if (items.length > 0) {
          const rows = await conv.open(items[0].id);
          chat.openConversation(items[0].id, rows);
        }
      } catch (err) {
        message.error(`加载历史对话失败：${errText(err)}`);
      }
    })();
  }, [chat, conv, message]);

  /** 打开历史会话：拉回消息填进对话区（生成中切换会先中止当前流） */
  const openConversation = async (id: string) => {
    try {
      const rows = await conv.open(id);
      chat.openConversation(id, rows);
    } catch (err) {
      message.error(`打开对话失败：${errText(err)}`);
    }
  };

  /** 新建对话：先落一行（列表里立刻可见），标题会在发出第一条消息时自动改 */
  const newConversation = async () => {
    try {
      const created = await conv.create();
      chat.openConversation(created.id, []);
      inputRef.current?.focus();
    } catch (err) {
      message.error(`新建对话失败：${errText(err)}`);
    }
  };

  const selectConversation = (id: string) => {
    if (id === chat.conversationId) return; // 点自己不用重新拉一遍
    void openConversation(id);
  };

  /** 删除对话；删的正是当前这条时，落到剩下的最近一条（没有就回到空白） */
  const removeConversation = async (id: string) => {
    try {
      await conv.remove(id);
      if (chat.conversationId !== id) return;
      const rest = await conv.refresh();
      if (rest.length > 0) {
        const rows = await conv.open(rest[0].id);
        chat.openConversation(rest[0].id, rows);
      } else {
        chat.startNewConversation();
      }
    } catch (err) {
      message.error(`删除对话失败：${errText(err)}`);
    }
  };

  /** 重命名会话（列表行里的 ✎ 入口） */
  const renameConversation = async (id: string, title: string) => {
    try {
      await conv.rename(id, title);
    } catch (err) {
      message.error(`重命名失败：${errText(err)}`);
    }
  };

  /** 发送 / 停止共用一个按钮 */
  const submit = async () => {
    // 生成中 → 按钮变为「停止」
    if (chat.streaming) {
      chat.stop();
      return;
    }

    const text = input.trim();
    if (files.uploading) {
      message.warning("附件还在上传中，请稍候再发送");
      return;
    }

    if (!text && !files.pending.some((p) => p.status === "ready")) {
      if (files.failed.length) message.warning("附件不可用，请移除后重试");
      return;
    }
    if (files.failed.length) {
      message.warning(`${files.failed.length} 个附件不可用，本次发送已跳过`);
    }

    // 还没有会话就先建一个（刚打开页面、或历史对话被删光了）
    if (!chat.conversationId) {
      try {
        const created = await conv.create();
        chat.openConversation(created.id, []);
      } catch (err) {
        message.error(`创建对话失败：${errText(err)}`);
        return;
      }
    }

    const ready = files.takeReady();
    setInput("");
    await chat.send(text, ready);
    // 首条消息会自动改标题、更新排序，发完刷一下列表；
    // 刷新失败不影响这轮对话，静默即可（下次操作还会再拉）
    void conv.refresh().catch(() => {});
    inputRef.current?.focus();
  };

  return (
    <Flex
      gap={18}
      align="flex-start"
      className={files.dragging ? "chat drop-active" : "chat"}
      style={{ width: "min(1180px, 100%)", padding: "32px 20px" }}
      {...files.dragProps}
    >
      <ConversationList
        list={conv.list}
        currentId={chat.conversationId}
        loading={conv.loading || conv.opening}
        onSelect={selectConversation}
        onCreate={newConversation}
        onRename={renameConversation}
        onRemove={removeConversation}
      />

      <Flex vertical style={{ flex: 1, minWidth: 0 }}>
        <Typography.Title
          level={1}
          style={{ fontSize: 20, fontWeight: 600, letterSpacing: ".3px", margin: "0 0 20px" }}
        >
          流式对话 Demo
          <Typography.Text type="secondary" style={{ fontSize: 13, fontWeight: 400, marginLeft: 10 }}>
            SSE · 附件 · 人工审批 · 任务规划 · 代码读写
          </Typography.Text>
        </Typography.Title>

        <WorkspacePanel ws={workspace} />

        <TodoPanel todos={chat.todos} />

        <MessageList chat={chat} logRef={logRef} />

        {files.pending.length > 0 && (
          <Flex gap={8} wrap style={{ marginBottom: 10 }}>
            {files.pending.map((item) => (
              <AttachmentChip
                key={item.uid}
                item={item}
                onRemove={files.removePending}
                onRetry={files.retryPending}
              />
            ))}
          </Flex>
        )}

        <Composer
          value={input}
          onChange={setInput}
          onSend={submit}
          onAddFiles={files.addFiles}
          streaming={chat.streaming}
          inputRef={inputRef}
        />
      </Flex>
    </Flex>
  );
}
