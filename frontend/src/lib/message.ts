import type { AttachmentRef, Message, PendingAttachment, StoredMessage } from "../types";

/** 生成前端本地唯一键 */
export const newUid = () =>
  `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;

/** 只把「已上传成功」的附件转成引用，失败/上传中的不发给后端 */
export const readyRefs = (list: PendingAttachment[]): AttachmentRef[] =>
  list
    .filter(
      (a): a is PendingAttachment & { id: string } =>
        a.status === "ready" && !!a.id
    )
    // 带上 size：历史回填时不用再逐条回查附件接口，附件卡片也能显示大小
    .map(({ id, name, kind, size }) => ({ id, name, kind, size }));

/**
 * 服务端历史 → 界面消息。
 *
 * localId 由调用方统一分配（与流式新增的消息共用一套 id），否则会和
 * 「正在生成的那条」撞号。
 * 空助手消息（生成被打断留下的占位行）不还原：那是库里的半成品，
 * 画出来只会多一个空气泡。
 */
export const storedToMessages = (
  rows: StoredMessage[],
  startId: number
): Message[] =>
  rows
    .filter((row) => row.role === "user" || row.content !== "" || !!row.reasoning)
    .map((row, index) => ({
      id: startId + index,
      role: row.role,
      content: row.content,
      thinking: row.reasoning ?? "",
      hasThinking: !!row.reasoning,
      thinkingOpen: false,
      errors: [],
      attachments: (row.attachments ?? []).map((ref) => ({
        uid: newUid(),
        id: ref.id,
        name: ref.name,
        kind: ref.kind,
        size: ref.size ?? 0,
        status: "ready" as const,
        progress: 100,
      })),
    }));
