import type { Conversation, ConversationDetail } from "../types";
import { request } from "./http";

/** 会话：列表 / 详情 / 增删改（发消息走 SSE，走下面两个地址） */
export const CONVERSATIONS_API = "/api/conversations";

/** 会话内发消息（SSE）的地址 */
export const conversationMessagesApi = (id: string) =>
  `${CONVERSATIONS_API}/${id}/messages`;

/** 会话内提交审批、续跑被挂起的回复（SSE）的地址 */
export const conversationResumeApi = (id: string) =>
  `${conversationMessagesApi(id)}/resume`;

/**
 * 历史消息存在服务端，前端只做「读」和「写这一轮」：
 * 打开老会话拉全量，发消息只传本轮新内容——不再每轮把全部历史传一遍。
 */
export const listConversations = async (): Promise<Conversation[]> => {
  const data = await request<{ conversations: Conversation[] }>(CONVERSATIONS_API);
  return data.conversations;
};

/** 新建会话；不传标题时服务端用「新对话」，发出第一条带文字的消息后自动改成内容摘要 */
export const createConversation = (title?: string) =>
  request<Conversation>(CONVERSATIONS_API, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(title ? { title } : {}),
  });

/** 会话详情 + 全部消息（点开历史对话时回填界面） */
export const fetchConversation = (id: string) =>
  request<ConversationDetail>(`${CONVERSATIONS_API}/${id}`);

/** 重命名会话（界面暂未接入改名入口；后端 PUT 已就绪，需要时直接接上） */
export const renameConversation = (id: string, title: string) =>
  request<Conversation>(`${CONVERSATIONS_API}/${id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });

/** 删除会话及其全部消息（附件不属于会话，不受影响） */
export const deleteConversation = (id: string) =>
  request<{ message: string }>(`${CONVERSATIONS_API}/${id}`, { method: "DELETE" });
