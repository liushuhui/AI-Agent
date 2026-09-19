/**
 * 低代码页面列表：新建 / 编辑 / 预览 / 删除 + 当前用户与退出登录。
 * 权限由后端裁决：列表只返回当前用户可见的页面，越权操作返回 403。
 */

import { useEffect, useState } from "react";
import { useNavigate } from "react-router";
import { App as AntdApp, Button, Input, Modal, Popconfirm, Space, Table, Tag } from "antd";
import { logout } from "../../api/auth";
import { useCurrentUser } from "../../api/auth";
import { createPage, deletePage, listPages } from "../../api/lowcode";
import type { PageSummary } from "../schema/types";
import "../lowcode.css";

const ROLE_LABEL: Record<string, string> = { admin: "管理员", editor: "编辑", viewer: "只读" };

export function LowcodeListPage() {
  const navigate = useNavigate();
  const { message } = AntdApp.useApp();
  const user = useCurrentUser();
  const [pages, setPages] = useState<PageSummary[] | null>(null);
  const [reloadToken, setReloadToken] = useState(0);
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [newVisibility, setNewVisibility] = useState("private");

  // 列表加载：setState 都放在 promise 回调里（effect 体内同步 setState 会被 react-hooks v7 拦）
  useEffect(() => {
    let alive = true;
    listPages()
      .then((data) => {
        if (alive) setPages(data.pages);
      })
      .catch((error: unknown) => {
        if (!alive) return;
        setPages([]);
        message.error(error instanceof Error ? error.message : String(error));
      });
    return () => {
      alive = false;
    };
  }, [reloadToken, message]);

  const reload = () => setReloadToken((token) => token + 1);

  const submitCreate = async () => {
    if (!newName.trim()) {
      message.warning("请填写页面名");
      return;
    }
    try {
      const page = await createPage({ name: newName.trim(), visibility: newVisibility });
      setCreating(false);
      setNewName("");
      navigate(`/lowcode/${page.id}`);
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error));
    }
  };

  const remove = async (id: string) => {
    try {
      await deletePage(id);
      message.success("已删除");
      reload();
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error));
    }
  };

  const signOut = async () => {
    await logout();
    navigate("/login", { replace: true });
  };

  return (
    <div className="lc-list">
      <header className="lc-toolbar">
        <Space size={8}>
          <span className="lc-title">低代码页面</span>
          <Button size="small" type="primary" onClick={() => setCreating(true)}>
            新建页面
          </Button>
        </Space>
        <Space size={8}>
          {user ? (
            <Tag color="purple">
              {user.name} · {ROLE_LABEL[user.role] ?? user.role}
            </Tag>
          ) : null}
          <Button size="small" onClick={() => void signOut()}>
            退出登录
          </Button>
        </Space>
      </header>

      <Table
        rowKey="id"
        size="small"
        loading={pages === null}
        dataSource={pages ?? []}
        pagination={false}
        columns={[
          {
            title: "名称",
            dataIndex: "name",
            render: (value: string, record) => (
              <a onClick={() => navigate(`/lowcode/${record.id}`)}>{value}</a>
            ),
          },
          {
            title: "可见性",
            dataIndex: "visibility",
            width: 90,
            render: (value: string) => <Tag>{value === "public" ? "公开" : "私有"}</Tag>,
          },
          { title: "负责人", dataIndex: "owner_id", width: 100 },
          {
            title: "状态",
            dataIndex: "status",
            width: 90,
            render: (value: string) => (
              <Tag color={value === "published" ? "green" : "default"}>
                {value === "published" ? "已发布" : "草稿"}
              </Tag>
            ),
          },
          { title: "版本", dataIndex: "version", width: 70 },
          { title: "更新时间", dataIndex: "updated_at", width: 170 },
          {
            title: "操作",
            width: 190,
            render: (_: unknown, record) => (
              <Space size={4}>
                <Button size="small" type="link" onClick={() => navigate(`/lowcode/${record.id}`)}>
                  编辑
                </Button>
                <Button
                  size="small"
                  type="link"
                  onClick={() => window.open(`/preview/${record.id}`, "_blank")}
                >
                  预览
                </Button>
                <Popconfirm title="删除该页面？" onConfirm={() => void remove(record.id)}>
                  <Button size="small" type="link" danger>
                    删除
                  </Button>
                </Popconfirm>
              </Space>
            ),
          },
        ]}
      />

      <Modal
        title="新建页面"
        open={creating}
        onOk={() => void submitCreate()}
        onCancel={() => setCreating(false)}
        okText="创建"
        cancelText="取消"
      >
        <div className="lc-field">
          <label className="lc-field-label">页面名</label>
          <Input
            value={newName}
            placeholder="例如：订单管理"
            onChange={(event) => setNewName(event.target.value)}
          />
        </div>
        <div className="lc-field">
          <label className="lc-field-label">可见性</label>
          <select
            className="lc-select"
            value={newVisibility}
            onChange={(event) => setNewVisibility(event.target.value)}
          >
            <option value="private">私有（仅负责人可见）</option>
            <option value="public">公开（所有人可看，只有负责人能改）</option>
          </select>
        </div>
      </Modal>
    </div>
  );
}
