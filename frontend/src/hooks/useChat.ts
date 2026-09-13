import { useRef, useState } from "react";
import { App } from "antd";

import { postStream } from "../api/chat";
import { conversationMessagesApi, conversationResumeApi } from "../api/conversation";
import { readyRefs, storedToMessages } from "../lib/message";
import type {
  ApprovalItem,
  AttachmentRef,
  Message,
  PendingAttachment,
  StoredMessage,
  StreamEvent,
  Todo,
} from "../types";

/**
 * 对话状态机：消息列表、流式生成、人工审批。
 *
 * 历史存在服务端（conversation_messages 表），这里只持有「当前这一屏」：
 * 打开会话时把历史拉回来渲染（openConversation），发消息只传本轮新内容。
 * 两次请求（发消息 / 提交审批后继续）的响应格式完全一致，
 * 所以共用一份收流逻辑；附件队列不在这里管，发送时由调用方把
 * 「已就绪的附件」传进来。
 */
export function useChat() {
  const { message } = App.useApp();

  const [messages, setMessages] = useState<Message[]>([]);
  const [streaming, setStreaming] = useState(false);
  /** 当前会话 id；还没建会话（空白新对话）时为 null */
  const [conversationId, setConversationId] = useState<string | null>(null);
  /** Agent 当前的任务清单（每轮工具审批前后都会被后端刷新） */
  const [todos, setTodos] = useState<Todo[]>([]);

  const controllerRef = useRef<AbortController | null>(null);
  const nextIdRef = useRef(0);
  /** conversationId 的同步副本：发消息时立刻要用，不能等 setState 生效 */
  const conversationIdRef = useRef<string | null>(null);

  // id 存在 ref 里，热更新后不会与已有消息撞号
  const createMessage = (
    role: Message["role"],
    content = "",
    thinkingOpen = false
  ): Message => ({
    id: nextIdRef.current++,
    role,
    content,
    thinking: "",
    hasThinking: false,
    thinkingOpen,
    errors: [],
    attachments: [],
  });

  const patch = (id: number, updater: (m: Message) => Message) =>
    setMessages((prev) => prev.map((m) => (m.id === id ? updater(m) : m)));

  /** 把一条 SSE 事件写回指定的助手消息 */
  const applyEvent = (replyId: number, evt: StreamEvent) => {
    if (evt.type === "reasoning") {
      patch(replyId, (m) => ({
        ...m,
        hasThinking: true,
        thinking: m.thinking + (evt.content ?? ""),
      }));
    } else if (evt.type === "content") {
      patch(replyId, (m) => ({
        ...m,
        content: m.content + (evt.content ?? ""), // 追加渲染，实现打字机效果
        thinkingOpen: m.content === "" ? false : m.thinkingOpen, // 正文开始 → 收起思维链
      }));
    } else if (evt.type === "interrupt") {
      // 有工具调用被挂起，等用户点选。整轮可能被中断多次，
      // 所以每次都用最新的 actions 覆盖掉上一块面板。
      const actions = evt.actions ?? [];
      if (actions.length === 0) return;
      patch(replyId, (m) => ({
        ...m,
        approval: {
          threadId: evt.thread_id ?? "",
          // 助手消息在库里的 id：续跑时原样回传，续写内容接到同一条回复上
          messageId: evt.message_id,
          items: actions.map((a) => ({
            ...a,
            argsText: JSON.stringify(a.args, null, 2),
          })),
          status: "pending",
        },
      }));
    } else if (evt.type === "todos") {
      setTodos(evt.todos ?? []);
    } else if (evt.type === "error") {
      patch(replyId, (m) => ({ ...m, errors: [...m.errors, evt.error ?? ""] }));
    } else if (evt.type === "usage") {
      console.log("用量：", evt.usage);
    }
  };

  /** 统一的「发请求 + 收流」流程：负责 loading 态、可中止、错误兜底 */
  const runStream = async (url: string, body: unknown, replyId: number) => {
    const controller = new AbortController();
    controllerRef.current = controller;
    setStreaming(true);
    // 记下这次流属于哪个会话：中途切走（或新建对话）后，迟到的分片不能再写界面
    const scope = conversationIdRef.current;
    try {
      await postStream(url, body, controller.signal, (evt) => {
        if (conversationIdRef.current !== scope) return;
        applyEvent(replyId, evt);
      });
    } catch (err) {
      const aborted = err instanceof Error && err.name === "AbortError";
      const text = err instanceof Error ? err.message : String(err);
      patch(replyId, (m) => ({
        ...m,
        errors: [...m.errors, aborted ? "已停止生成" : `请求失败：${text}`],
        // 审批提交到一半被打断时退回可操作状态，否则面板会卡在「提交中」
        approval:
          m.approval?.status === "submitting"
            ? { ...m.approval, status: "pending" }
            : m.approval,
      }));
    } finally {
      controllerRef.current = null;
      setStreaming(false);
      // 在等审批时先收起思维链，把注意力留给审批面板
      patch(replyId, (m) => ({ ...m, thinkingOpen: !m.approval }));
    }
  };

  /**
   * 打开一个会话：把服务端拿回的历史消息填进界面。
   * 正在生成时先中止，避免旧流的内容写进新打开的会话。
   */
  const openConversation = (id: string, rows: StoredMessage[]) => {
    controllerRef.current?.abort();
    controllerRef.current = null;
    const loaded = storedToMessages(rows, nextIdRef.current);
    nextIdRef.current += loaded.length;
    conversationIdRef.current = id;
    setConversationId(id);
    setTodos([]);
    setMessages(loaded);
  };

  /** 回到空白新对话（当前会话被删掉时用；发出下一条消息前不会落库） */
  const startNewConversation = () => {
    controllerRef.current?.abort();
    controllerRef.current = null;
    conversationIdRef.current = null;
    setConversationId(null);
    setTodos([]);
    setMessages([]);
  };

  /** 发送一轮新对话；ready 是本次要带上的附件（历史存在服务端，只传这一轮） */
  const send = async (text: string, ready: PendingAttachment[]) => {
    const cid = conversationIdRef.current;
    if (!cid) {
      message.error("会话还未就绪，请重新发送");
      return;
    }
    // 每轮对话都是新的 thread_id，后端的待办清单也跟着重来，前端同步清空
    setTodos([]);
    const userMsg: Message = { ...createMessage("user", text), attachments: ready };
    const reply = createMessage("assistant", "", true);
    setMessages((prev) => [...prev, userMsg, reply]);

    // 只传本轮内容：历史由服务端保存，不再每轮回传全量（附件只传引用）
    const body: { content: string; attachments?: AttachmentRef[] } = { content: text };
    const refs = readyRefs(ready);
    if (refs.length) body.attachments = refs;

    await runStream(conversationMessagesApi(cid), body, reply.id);
  };

  /** 生成中 → 中止本次生成 */
  const stop = () => controllerRef.current?.abort();

  const toggleThinking = (id: number) =>
    patch(id, (m) => ({ ...m, thinkingOpen: !m.thinkingOpen }));

  /** 修改某一项待审批工具调用的本地选择（选项 / 参数 / 代答内容） */
  const patchApprovalItem = (
    replyId: number,
    index: number,
    patchItem: Partial<ApprovalItem>
  ) =>
    patch(replyId, (m) =>
      m.approval
        ? {
            ...m,
            approval: {
              ...m.approval,
              error: undefined,
              items: m.approval.items.map((item, i) =>
                i === index ? { ...item, ...patchItem } : item
              ),
            },
          }
        : m
    );

  /** 给审批面板提示错误（校验不过时用） */
  const setApprovalError = (replyId: number, error: string) =>
    patch(replyId, (m) =>
      m.approval ? { ...m, approval: { ...m.approval, error } } : m
    );

  /** 一键全部批准（不支持 approve 的项保持原样） */
  const approveAll = (replyId: number) =>
    patch(replyId, (m) =>
      m.approval
        ? {
            ...m,
            approval: {
              ...m.approval,
              error: undefined,
              items: m.approval.items.map((item) =>
                item.allowed_decisions.includes("approve")
                  ? { ...item, decision: "approve" as const }
                  : item
              ),
            },
          }
        : m
    );

  /**
   * 把审批面板的选项拼成后端要的 decisions。
   * 失败时返回 null，并已把错误写到面板上。
   */
  const buildDecisions = (replyId: number, items: ApprovalItem[]) => {
    // decisions 必须与后端 actions 按位置一一对应，顺序不能乱、数量不能少
    const decisions: unknown[] = [];
    for (const item of items) {
      if (item.decision === "edit") {
        let args: unknown;
        try {
          args = JSON.parse(item.argsText);
        } catch {
          setApprovalError(
            replyId,
            `「${item.name}」的参数不是合法 JSON，请修正后再提交`
          );
          return null;
        }
        decisions.push({ type: "edit", edited_action: { name: item.name, args } });
      } else if (item.decision === "respond") {
        // 后端要求 respond 必须带 message，缺了会 KeyError
        if (!item.respondMessage?.trim()) {
          setApprovalError(replyId, `「${item.name}」由你代答，请先填写内容`);
          return null;
        }
        decisions.push({ type: "respond", message: item.respondMessage });
      } else {
        decisions.push({ type: item.decision });
      }
    }
    return decisions;
  };

  /** 提交审批结果：把 decisions 发回后端续跑，后续内容接到同一条消息上 */
  const submitApproval = async (replyId: number) => {
    const approval = messages.find((m) => m.id === replyId)?.approval;
    if (!approval || approval.status !== "pending") return;

    if (approval.items.some((it) => !it.decision)) {
      message.warning("请先为每个工具调用选择一个处理方式");
      return;
    }

    const decisions = buildDecisions(replyId, approval.items);
    if (!decisions) return;

    const cid = conversationIdRef.current;
    if (!cid || approval.messageId === undefined) {
      // 理论上不会发生（interrupt 事件必带这两个值），兜底提示用户重开对话
      setApprovalError(replyId, "会话上下文已丢失，请重新打开这个对话再试");
      return;
    }

    patch(replyId, (m) =>
      m.approval
        ? { ...m, approval: { ...m.approval, status: "submitting", error: undefined } }
        : m
    );
    await runStream(
      conversationResumeApi(cid),
      {
        thread_id: approval.threadId,
        decisions,
        // 续写接到同一条助手回复上，刷新后看到的是拼接完整的整条回复
        message_id: approval.messageId,
      },
      replyId
    );
    // 续跑结束：若期间又触发了新一轮审批，status 已被 interrupt 事件重置为
    // pending，这里就别覆盖；否则标记完成，收起面板。
    patch(replyId, (m) =>
      m.approval?.status === "submitting"
        ? { ...m, approval: { ...m.approval, status: "done" } }
        : m
    );
  };

  return {
    messages,
    streaming,
    todos,
    /** 当前会话 id；null = 空白新对话（还没落库） */
    conversationId,
    openConversation,
    startNewConversation,
    send,
    stop,
    toggleThinking,
    patchApprovalItem,
    approveAll,
    submitApproval,
    /** 该附件是否已进入历史消息（决定移除时能不能回收本地预览） */
    isAttachedToMessage: (uid: string) =>
      messages.some((m) => m.attachments.some((a) => a.uid === uid)),
  };
}

export type UseChat = ReturnType<typeof useChat>;
