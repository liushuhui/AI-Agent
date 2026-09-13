import { useCallback, useState } from "react";

import {
  createConversation,
  deleteConversation,
  fetchConversation,
  listConversations,
  renameConversation,
} from "../api/conversation";
import type { Conversation, StoredMessage } from "../types";

/**
 * 会话列表：加载 / 新建 / 删除，以及「打开某个会话」时把历史消息拉回来。
 *
 * 它不持有消息状态（消息渲染是 useChat 的事）：打开会话拿到历史后，
 * 由调用方交给 useChat。这样滚动列表这类操作不会牵动正在流式的对话。
 */
export function useConversations() {
  const [list, setList] = useState<Conversation[]>([]);
  /** 首次列表还没拉回来；拉过一次后不再翻转（避免每次刷新都闪一下加载态） */
  const [loading, setLoading] = useState(true);
  /** 正在拉某个会话的历史，用于列表置灰防连点 */
  const [opening, setOpening] = useState(false);

  /** 拉最新列表；返回最新数据，方便调用方接着做「选中第一条」之类的动作 */
  const refresh = useCallback(async (): Promise<Conversation[]> => {
    try {
      const items = await listConversations();
      setList(items);
      return items;
    } finally {
      setLoading(false);
    }
  }, []);

  /** 新建会话：空会话也先落一行（发出第一条带文字的消息时，服务端会把标题改成内容） */
  const create = async (): Promise<Conversation> => {
    const conversation = await createConversation();
    setList((prev) => [conversation, ...prev]);
    return conversation;
  };

  /** 删除会话（消息一并删除；附件不属于会话，不会动） */
  const remove = async (id: string): Promise<void> => {
    await deleteConversation(id);
    setList((prev) => prev.filter((item) => item.id !== id));
  };

  /** 重命名：只换这一条的标题（服务端不动 updated_at，列表排序不变） */
  const rename = async (id: string, title: string): Promise<void> => {
    const updated = await renameConversation(id, title);
    setList((prev) => prev.map((item) => (item.id === id ? updated : item)));
  };

  /** 打开会话：返回全部历史消息，顺带用服务端的最新值刷新这一行（标题可能刚变过） */
  const open = async (id: string): Promise<StoredMessage[]> => {
    setOpening(true);
    try {
      const { conversation, messages } = await fetchConversation(id);
      setList((prev) => prev.map((item) => (item.id === id ? conversation : item)));
      return messages;
    } finally {
      setOpening(false);
    }
  };

  return { list, loading, opening, refresh, create, remove, rename, open };
}

export type UseConversations = ReturnType<typeof useConversations>;
