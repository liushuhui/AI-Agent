import { Flex, Image, Tooltip } from "antd";

import { formatSize, KIND_LABELS, UPLOAD_API } from "../api/attachment";
import type { PendingAttachment } from "../types";

/**
 * 附件卡片：图片显示缩略图（可点开预览），其余显示类型徽标 + 文件名。
 *
 * 只读展示 + 两个可选动作（重试 / 移除）：
 * 传了 onRetry 才显示「重试」，传了 onRemove 才显示「✕」。
 */
export function AttachmentChip({
  item,
  onRemove,
  onRetry,
}: {
  item: PendingAttachment;
  onRemove?: (uid: string) => void;
  onRetry?: (item: PendingAttachment) => void;
}) {
  const failed = item.status === "failed";
  const uploading = item.status === "uploading";
  // 本地 objectURL 优先（立即可见），发送后本地地址失效则回退到服务端原图接口
  const src = item.previewUrl ?? (item.id ? `${UPLOAD_API}/${item.id}/raw` : undefined);

  return (
    <Tooltip title={item.error ?? item.preview ?? item.name}>
      <Flex
        align="center"
        gap={8}
        className={`att-chip${failed ? " att-chip-failed" : ""}`}
      >
        {item.kind === "image" && src ? (
          <Image
            src={src}
            alt={item.name}
            width={40}
            height={40}
            className="att-thumb"
            preview={{ mask: null }}
          />
        ) : (
          <span className="att-badge">{KIND_LABELS[item.kind]}</span>
        )}

        <Flex vertical className="att-meta">
          <span className="att-name">{item.name}</span>
          <span className="att-sub">
            {uploading
              ? `上传中 ${item.progress}%`
              : failed
                ? item.error ?? "上传失败"
                : // 历史附件只有服务端存的引用，更早的数据没记大小：留空好过显示 0 B
                  item.size > 0
                  ? formatSize(item.size)
                  : ""}
          </span>
          {uploading && (
            <span className="att-bar">
              <i style={{ width: `${item.progress}%` }} />
            </span>
          )}
        </Flex>

        {failed && item.id === undefined && (
          <a onClick={() => onRetry?.(item)} className="att-act">
            重试
          </a>
        )}
        {onRemove && (
          <a onClick={() => onRemove(item.uid)} className="att-act" title="移除">
            ✕
          </a>
        )}
      </Flex>
    </Tooltip>
  );
}
