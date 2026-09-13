import { useState } from "react";
import { Button, Collapse, Flex, Modal, Spin, Typography } from "antd";

import { fetchFile } from "../api/workspace";
import type { UseWorkspace } from "../hooks/useWorkspace";
import type { FileContent } from "../types";
import { FolderPicker } from "./FolderPicker";

/** 把字节数变成人看得懂的大小 */
const formatSize = (bytes: number) =>
  bytes < 1024 ? `${bytes} B` : `${(bytes / 1024).toFixed(1)} KB`;

/**
 * 工作区面板：显示/切换工作目录，列出里面的文件，点文件名可预览内容。
 *
 * Agent 改完文件后清单不会自动更新（前端并不知道它写了哪些文件），
 * 所以留了一个「刷新」按钮。
 */
export function WorkspacePanel({ ws }: { ws: UseWorkspace }) {
  const [browsing, setBrowsing] = useState(false);
  const [preview, setPreview] = useState<FileContent | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);

  const openFile = async (path: string) => {
    setPreviewLoading(true);
    setPreviewError(null);
    setPreview({ path, size: 0, content: "", truncated: false });
    try {
      setPreview(await fetchFile(path));
    } catch (err) {
      setPreviewError(err instanceof Error ? err.message : String(err));
    } finally {
      setPreviewLoading(false);
    }
  };

  return (
    <>
      <Flex align="center" gap={8} wrap className="ws-bar">
        <span className="ws-label">工作目录</span>
        <span className="ws-path" title={ws.root ?? undefined}>
          {ws.root ?? "未选择 —— Agent 无法读写代码，请先选一个文件夹"}
        </span>
        <Button size="small" type="primary" loading={ws.picking} onClick={ws.pick}>
          {ws.picking ? "等待选择…" : "选择本机文件夹"}
        </Button>
        <Button size="small" loading={ws.loading} disabled={!ws.root} onClick={ws.refresh}>
          刷新
        </Button>
        <Button size="small" type="text" onClick={() => setBrowsing(true)}>
          浏览服务端目录
        </Button>
      </Flex>

      {ws.root && (
        <Collapse
          size="small"
          defaultActiveKey={["files"]}
          items={[
            {
              key: "files",
              label: `文件（${ws.entries.length}${ws.truncated ? "+" : ""}）`,
              children:
                ws.entries.length === 0 ? (
                  <Typography.Text type="secondary" style={{ fontSize: 13 }}>
                    这个文件夹里（还没有）可展示的文件
                  </Typography.Text>
                ) : (
                  <Flex vertical gap={2} className="ws-files">
                    {ws.entries.map((file) => (
                      <Flex
                        key={file.path}
                        gap={8}
                        justify="space-between"
                        className="ws-file"
                        onClick={() => openFile(file.path)}
                      >
                        <span className="ws-file-name">{file.path}</span>
                        <span className="ws-file-size">{formatSize(file.size)}</span>
                      </Flex>
                    ))}
                    {ws.truncated && (
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        文件过多，仅列出前 {ws.entries.length} 个
                      </Typography.Text>
                    )}
                  </Flex>
                ),
            },
          ]}
          styles={{ title: { fontSize: 13 }, body: { fontSize: 13 } }}
          style={{ border: "1px solid #262b38", borderRadius: 10, marginBottom: 16 }}
        />
      )}

      <FolderPicker
        open={browsing}
        onClose={() => setBrowsing(false)}
        onPick={async (path) => ws.open(path)}
      />

      <Modal
        title={preview?.path}
        open={preview !== null}
        onCancel={() => setPreview(null)}
        footer={null}
        width={760}
      >
        {previewError ? (
          <Typography.Text type="danger">{previewError}</Typography.Text>
        ) : previewLoading ? (
          <Flex justify="center" style={{ padding: 24 }}>
            <Spin />
          </Flex>
        ) : (
          <>
            <pre className="ws-preview">{preview?.content}</pre>
            {preview?.truncated && (
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                文件过大，仅预览了前一部分
              </Typography.Text>
            )}
          </>
        )}
      </Modal>
    </>
  );
}
