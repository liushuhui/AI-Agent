import { useState } from "react";
import { Alert, Button, Flex, Input, Modal, Spin, Typography } from "antd";

import { fetchDirs } from "../api/workspace";
import type { DirListing } from "../types";

/**
 * 「浏览服务端目录」选择器（备选方式）。
 *
 * 首选是 WorkspacePanel 上的「选择本机文件夹」：由服务端弹系统选择框，
 * 能拿到真实绝对路径，也是最自然的操作。只有后端跑在没有图形界面的环境
 * （或前后端不在同一台机器）时，才需要退化成在网页里逐级浏览。
 */
export function FolderPicker({
  open,
  onClose,
  onPick,
}: {
  open: boolean;
  onClose: () => void;
  /** 返回 true 表示选中成功，弹窗由调用方关闭 */
  onPick: (path: string) => Promise<boolean>;
}) {
  const [listing, setListing] = useState<DirListing | null>(null);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const go = async (path?: string) => {
    setLoading(true);
    setError(null);
    try {
      const next = await fetchDirs(path);
      setListing(next);
      setInput(next.path);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  // 每次打开从用户主目录开始。用 Modal 的 afterOpenChange 而不是 useEffect：
  // effect 里不允许同步调 setState，而这个回调本来就是「打开后」才触发的。

  const pick = async () => {
    if (!listing) return;
    if (await onPick(listing.path)) onClose();
  };

  return (
    <Modal
      title="浏览服务端目录"
      open={open}
      onCancel={onClose}
      width={560}
      okText="选择此文件夹"
      cancelText="取消"
      confirmLoading={loading}
      onOk={pick}
      okButtonProps={{ disabled: !listing }}
      afterOpenChange={(visible) => {
        if (visible) void go();
      }}
    >
      <Flex vertical gap={10}>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          选中的是服务端上的文件夹，Agent 之后只能在它内部读写文件。
        </Typography.Text>

        <Flex gap={8}>
          <Button
            size="small"
            disabled={!listing?.parent}
            onClick={() => go(listing?.parent ?? undefined)}
          >
            上一级
          </Button>
          <Input
            size="small"
            value={input}
            placeholder="也可以直接粘贴完整路径，回车前往"
            onChange={(e) => setInput(e.target.value)}
            onPressEnter={() => go(input.trim() || undefined)}
          />
          <Button size="small" onClick={() => go(input.trim() || undefined)}>
            前往
          </Button>
        </Flex>

        {error && (
          <Alert type="error" title={error} style={{ padding: "4px 10px", fontSize: 13 }} />
        )}

        <div className="ws-dirs">
          {loading && !listing ? (
            <Flex justify="center" style={{ padding: 24 }}>
              <Spin />
            </Flex>
          ) : listing && listing.dirs.length === 0 && listing.files.length === 0 ? (
            <Typography.Text type="secondary" style={{ fontSize: 13 }}>
              这个文件夹下没有子文件夹，可以直接选定它
            </Typography.Text>
          ) : (
            <>
              {listing?.dirs.map((dir) => (
                <div key={dir.path} className="ws-dir" onClick={() => go(dir.path)}>
                  📁 {dir.name}
                </div>
              ))}
              {/* 文件列出来只是为了让人看清目录里不是空的；这一层只选目录，所以不可点 */}
              {listing && listing.files.length > 0 && (
                <div className="ws-dir-files">
                  🗎 本层 {listing.files.length} 个文件：
                  {listing.files.map((f) => f.name).join("、")}
                </div>
              )}
            </>
          )}
        </div>
      </Flex>
    </Modal>
  );
}
