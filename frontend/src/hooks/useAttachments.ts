import { useEffect, useRef, useState } from "react";
import { App } from "antd";

import { guessKind, uploadAttachment, validateFile } from "../api/attachment";
import { newUid } from "../lib/message";
import type { PendingAttachment } from "../types";

/**
 * 待发附件队列：本地校验 → 上传（带进度）→ 失败重试，离开页面时回收预览地址。
 *
 * 它不关心「附件最终挂到哪条消息上」，那部分由调用方决定；
 * 只在移除时通过 isAttachedToMessage 问一句，避免回收掉历史消息还在用的预览。
 */
export function useAttachments(isAttachedToMessage: (uid: string) => boolean) {
  const { message } = App.useApp();

  const [pending, setPending] = useState<PendingAttachment[]>([]);
  const [dragging, setDragging] = useState(false);

  /** uid → 原始 File，仅用于失败重试 */
  const filesRef = useRef<Map<string, File>>(new Map());
  /** 已创建的 objectURL，卸载时统一回收 */
  const previewUrlsRef = useRef<Set<string>>(new Set());
  /** 拖拽进入/离开会连续触发，用计数避免闪烁 */
  const dragDepthRef = useRef(0);

  // 离开页面时回收所有本地预览地址
  useEffect(() => {
    const urls = previewUrlsRef.current;
    return () => {
      urls.forEach((url) => URL.revokeObjectURL(url));
      urls.clear();
    };
  }, []);

  const trackPreview = (url: string) => {
    previewUrlsRef.current.add(url);
    return url;
  };

  const releasePreview = (url?: string) => {
    if (!url) return;
    URL.revokeObjectURL(url);
    previewUrlsRef.current.delete(url);
  };

  /** 真正发起上传；进度与结果都写回队列 */
  const startUpload = (uid: string, file: File) => {
    uploadAttachment(file, (percent) =>
      setPending((prev) =>
        prev.map((x) => (x.uid === uid ? { ...x, progress: percent } : x))
      )
    )
      .then((res) =>
        setPending((prev) =>
          prev.map((x) =>
            x.uid === uid
              ? {
                  ...x,
                  id: res.id,
                  name: res.name ?? x.name,
                  kind: res.kind ?? x.kind,
                  size: res.size ?? x.size,
                  status: res.status === "failed" ? "failed" : "ready",
                  error: res.error ?? undefined,
                  preview: res.preview,
                  progress: 100,
                }
              : x
          )
        )
      )
      .catch((err: unknown) => {
        const text = err instanceof Error ? err.message : String(err);
        setPending((prev) =>
          prev.map((x) =>
            x.uid === uid ? { ...x, status: "failed", error: text, progress: 0 } : x
          )
        );
      });
  };

  /** 选文件 / 拖拽 / 粘贴的统一入口：先本地校验，再立刻开始上传 */
  const addFiles = (files: File[]) => {
    if (files.length === 0) return;

    const items: PendingAttachment[] = [];
    const queue: { uid: string; file: File }[] = [];

    for (const file of files) {
      const uid = newUid();
      const name = file.name || `粘贴的图片-${items.length + 1}.png`;
      const invalid = validateFile(file);

      items.push({
        uid,
        name,
        size: file.size,
        kind: guessKind(name, file.type),
        status: invalid ? "failed" : "uploading",
        progress: 0,
        error: invalid ?? undefined,
        previewUrl: file.type.startsWith("image/")
          ? trackPreview(URL.createObjectURL(file))
          : undefined,
      });

      if (!invalid) {
        filesRef.current.set(uid, file);
        queue.push({ uid, file });
      }
    }

    setPending((prev) => [...prev, ...items]);
    queue.forEach(({ uid, file }) => startUpload(uid, file));
  };

  const removePending = (uid: string) => {
    const target = pending.find((x) => x.uid === uid);
    // 已经进了消息气泡的附件不能回收，否则历史里的缩略图会失效
    if (target && !isAttachedToMessage(uid)) releasePreview(target.previewUrl);
    filesRef.current.delete(uid);
    setPending((prev) => prev.filter((x) => x.uid !== uid));
  };

  const retryPending = (item: PendingAttachment) => {
    const file = filesRef.current.get(item.uid);
    if (!file) {
      message.warning("原始文件已失效，请重新选择");
      return;
    }
    setPending((prev) =>
      prev.map((x) =>
        x.uid === item.uid
          ? { ...x, status: "uploading", error: undefined, progress: 0 }
          : x
      )
    );
    startUpload(item.uid, file);
  };

  /** 发送时把已就绪的附件摘出队列，交给消息使用 */
  const takeReady = () => {
    const ready = pending.filter((p) => p.status === "ready");
    setPending((prev) => prev.filter((p) => p.status !== "ready"));
    return ready;
  };

  const hasTransferFiles = (e: React.DragEvent) =>
    Array.from(e.dataTransfer?.types ?? []).includes("Files");

  /** 挂到最外层容器上，实现「拖到窗口任意位置即可添加」 */
  const dragProps = {
    onDragOver: (e: React.DragEvent) => {
      if (hasTransferFiles(e)) e.preventDefault();
    },
    onDragEnter: (e: React.DragEvent) => {
      if (!hasTransferFiles(e)) return;
      e.preventDefault();
      dragDepthRef.current += 1;
      setDragging(true);
    },
    onDragLeave: () => {
      dragDepthRef.current = Math.max(0, dragDepthRef.current - 1);
      if (dragDepthRef.current === 0) setDragging(false);
    },
    onDrop: (e: React.DragEvent) => {
      e.preventDefault();
      dragDepthRef.current = 0;
      setDragging(false);
      const files = Array.from(e.dataTransfer?.files ?? []);
      if (files.length) addFiles(files);
    },
  };

  return {
    pending,
    dragging,
    /** 还有附件在上传 → 调用方应拦住发送 */
    uploading: pending.some((p) => p.status === "uploading"),
    failed: pending.filter((p) => p.status === "failed"),
    addFiles,
    removePending,
    retryPending,
    takeReady,
    dragProps,
  };
}

export type UseAttachments = ReturnType<typeof useAttachments>;
