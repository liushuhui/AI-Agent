/**
 * 版本抽屉：发布快照列表（对比当前画布 / 回滚）。
 * 对比视图直接替换列表显示（见 VersionDiffView），避免嵌套弹窗的层级问题。
 */

import { useEffect, useState } from "react";
import { App as AntdApp, Button, Drawer, Popconfirm, Space, Tag } from "antd";
import { listVersions } from "../../api/lowcode";
import type { VersionInfo } from "../../api/lowcode";
import type { PageSchema } from "../schema/types";
import { VersionDiffView } from "./VersionDiffView";

export interface VersionDrawerProps {
  pageId: string;
  open: boolean;
  /** 当前画布（用于与历史版本对比）。 */
  currentSchema: PageSchema;
  onClose: () => void;
  /** 返回 Promise：完成后抽屉自己刷新列表。 */
  onRollback: (version: number) => Promise<void>;
}

export function VersionDrawer({
  pageId,
  open,
  currentSchema,
  onClose,
  onRollback,
}: VersionDrawerProps) {
  const { message } = AntdApp.useApp();
  const [versions, setVersions] = useState<VersionInfo[] | null>(null);
  const [reloadToken, setReloadToken] = useState(0);
  const [diffVersion, setDiffVersion] = useState<VersionInfo | null>(null);

  useEffect(() => {
    if (!open) return;
    let alive = true;
    listVersions(pageId)
      .then((data) => {
        if (alive) setVersions(data.versions);
      })
      .catch((error: unknown) => {
        if (!alive) return;
        setVersions([]);
        message.error(error instanceof Error ? error.message : String(error));
      });
    return () => {
      alive = false;
    };
  }, [open, pageId, reloadToken, message]);

  // 关闭时回到列表视图，下次打开不残留上次的对比页
  const handleClose = () => {
    setDiffVersion(null);
    onClose();
  };

  return (
    <Drawer
      title={diffVersion ? "版本对比" : "发布版本"}
      open={open}
      onClose={handleClose}
      size={400}
    >
      {diffVersion ? (
        <VersionDiffView
          pageId={pageId}
          version={diffVersion}
          currentSchema={currentSchema}
          onBack={() => setDiffVersion(null)}
          onRollback={async (version) => {
            await onRollback(version);
            setReloadToken((token) => token + 1);
            setDiffVersion(null);
          }}
        />
      ) : (
        <>
          <div className="lc-tip" style={{ marginBottom: 12 }}>
            发布时会把当时的画布快照存下来；回滚会以该快照生成一个新版本（历史不会被覆盖）。
          </div>
          {versions === null ? (
            <div className="lc-loading">加载中…</div>
          ) : versions.length === 0 ? (
            <div className="lc-panel-tip">还没有发布过。点右上角「发布」创建第一个版本。</div>
          ) : (
            versions.map((version) => (
              <div className="lc-version-row" key={version.version}>
                <div className="lc-version-main">
                  <div className="lc-version-head">
                    <Tag color="purple">v{version.version}</Tag>
                    <span className="lc-muted">{version.created_at}</span>
                  </div>
                  <div className="lc-version-comment">{version.comment || "（无发布说明）"}</div>
                </div>
                <Space size={4}>
                  <Button size="small" onClick={() => setDiffVersion(version)}>
                    对比
                  </Button>
                  <Popconfirm
                    title={`回滚到 v${version.version}？`}
                    description="将用该版本覆盖当前画布（保存后生效）"
                    onConfirm={async () => {
                      await onRollback(version.version);
                      setReloadToken((token) => token + 1);
                    }}
                  >
                    <Button size="small">回滚</Button>
                  </Popconfirm>
                </Space>
              </div>
            ))
          )}
        </>
      )}
    </Drawer>
  );
}
