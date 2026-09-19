/**
 * 版本对比视图：把某个发布快照与「当前画布」做差异比较。
 * 直接渲染在版本抽屉里（不套弹窗：窄屏/后台标签页下嵌套遮罩的层级与动画都容易出意外）。
 */

import { useEffect, useMemo, useState } from "react";
import { App as AntdApp, Button, Popconfirm, Tag } from "antd";
import { getVersion } from "../../api/lowcode";
import type { VersionDetail, VersionInfo } from "../../api/lowcode";
import { diffSchema } from "../schema/diff";
import type { DiffItem } from "../schema/diff";
import { getMaterial } from "../materials/registry";
import type { ComponentNode, PageSchema } from "../schema/types";

export interface VersionDiffViewProps {
  pageId: string;
  version: VersionInfo;
  currentSchema: PageSchema;
  onBack: () => void;
  onRollback: (version: number) => Promise<void>;
}

const KIND_META: Record<DiffItem["kind"], { color: string; label: string }> = {
  add: { color: "green", label: "新增" },
  remove: { color: "red", label: "删除" },
  change: { color: "orange", label: "修改" },
};

export function VersionDiffView({
  pageId,
  version,
  currentSchema,
  onBack,
  onRollback,
}: VersionDiffViewProps) {
  const { message } = AntdApp.useApp();
  const [detail, setDetail] = useState<VersionDetail | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    getVersion(pageId, version.version)
      .then((data) => {
        if (alive) setDetail(data);
      })
      .catch((error: unknown) => {
        if (!alive) return;
        setFailed(true);
        message.error(error instanceof Error ? error.message : String(error));
      });
    return () => {
      alive = false;
    };
  }, [pageId, version.version, message]);

  const diff = useMemo(
    () =>
      detail
        ? diffSchema(
            detail.schema,
            currentSchema,
            (node: ComponentNode) => getMaterial(node.type)?.label ?? node.type,
          )
        : null,
    [detail, currentSchema],
  );

  return (
    <div className="lc-diff">
      <div className="lc-diff-head">
        <Button size="small" onClick={onBack}>
          ← 版本列表
        </Button>
        <span className="lc-diff-title">
          v{version.version} → 当前画布
        </span>
      </div>
      <div className="lc-tip">
        基准 v{version.version}（{version.created_at}，「{version.comment || "无发布说明"}」）；
        对比对象是当前画布（含未保存的修改）。
      </div>

      {failed ? <div className="lc-panel-tip">版本快照加载失败</div> : null}
      {!failed && !detail ? <div className="lc-loading">加载中…</div> : null}

      {detail && diff ? (
        <>
          <div className="lc-diff-summary">
            <Tag color="green">新增 {diff.added}</Tag>
            <Tag color="red">删除 {diff.removed}</Tag>
            <Tag color="orange">修改 {diff.changed}</Tag>
          </div>
          {diff.items.length === 0 ? (
            <div className="lc-panel-tip">这个版本与当前画布没有差异。</div>
          ) : (
            <div className="lc-diff-list">
              {diff.items.map((item, index) => (
                <div
                  className={`lc-diff-row lc-diff-${item.kind}`}
                  key={`${item.scope}-${item.target}-${index}`}
                >
                  <Tag color={KIND_META[item.kind].color}>{KIND_META[item.kind].label}</Tag>
                  <div className="lc-diff-body">
                    <div className="lc-diff-target">
                      <span className="lc-diff-scope">{item.scope}</span>
                      {item.target}
                    </div>
                    <div className="lc-diff-detail">{item.detail}</div>
                  </div>
                </div>
              ))}
            </div>
          )}
          <div className="lc-diff-actions">
            <Popconfirm
              title={`回滚到 v${version.version}？`}
              description="将用该版本覆盖当前画布（保存后生效）"
              onConfirm={() => onRollback(version.version)}
            >
              <Button type="primary" size="small">
                回滚到此版本
              </Button>
            </Popconfirm>
          </div>
        </>
      ) : null}
    </div>
  );
}
