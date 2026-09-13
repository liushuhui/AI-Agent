import { Button, Flex, Popconfirm, Spin, Typography } from "antd";

import type { Conversation } from "../types";

/**
 * 左侧会话列表：新建对话 + 历史对话（点击切换、✎ 重命名、✕ 删除）。
 * 纯展示：数据与动作都由 ChatPanel 传进来，自己不碰网络。
 */
export function ConversationList({
  list,
  currentId,
  loading,
  onSelect,
  onCreate,
  onRename,
  onRemove,
}: {
  list: Conversation[];
  /** 当前打开的会话 id；空白新对话时为 null */
  currentId: string | null;
  /** 首次加载列表 / 正在拉某个会话的历史 */
  loading: boolean;
  onSelect: (id: string) => void;
  onCreate: () => void;
  onRename: (id: string, title: string) => void;
  onRemove: (id: string) => void;
}) {
  return (
    <Flex vertical gap={10} className="conv-side">
      <Button block disabled={loading} onClick={onCreate}>
        ＋ 新建对话
      </Button>

      {loading && list.length === 0 && (
        <Flex justify="center" style={{ padding: 16 }}>
          <Spin size="small" />
        </Flex>
      )}

      {!loading && list.length === 0 && (
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          还没有历史对话。聊聊看，记录会自动保存。
        </Typography.Text>
      )}

      <Flex vertical gap={4} className="conv-list">
        {list.map((item) => (
          <Flex
            key={item.id}
            align="center"
            gap={6}
            className={`conv-item${item.id === currentId ? " conv-item-active" : ""}`}
            onClick={(e) => {
              // Popconfirm 的弹层挂在 body 上，事件是沿 React 树冒泡上来的：
              // 用 DOM 包含关系判断「是不是真的点在行内」，否则点删除/取消
              // 会顺带把这一行也当作被选中（切到刚删掉的会话 → 404）。
              if (!e.currentTarget.contains(e.target as Node)) return;
              if (!loading) onSelect(item.id);
            }}
          >
            <Flex vertical className="conv-box">
              {/* 改标题：antd 的可编辑文本，回车/失焦提交，空标题不提交 */}
              <Typography.Text
                className="conv-title"
                ellipsis={{ tooltip: item.title }}
                editable={{
                  icon: <span className="conv-edit">✎</span>,
                  tooltip: "重命名",
                  onChange: (value) => {
                    const title = value.trim();
                    if (title && title !== item.title) onRename(item.id, title);
                  },
                }}
              >
                {item.title}
              </Typography.Text>
              <span className="conv-meta">
                {item.updated_at.slice(0, 16)} · {item.message_count} 条
              </span>
            </Flex>

            <Popconfirm
              title="删除这个对话？"
              description="聊天记录会一并删除"
              okText="删除"
              cancelText="取消"
              okButtonProps={{ danger: true }}
              onConfirm={() => onRemove(item.id)}
            >
              {/* 点删除不能顺带切换会话，所以拦掉冒泡 */}
              <a className="conv-del" title="删除" onClick={(e) => e.stopPropagation()}>
                ✕
              </a>
            </Popconfirm>
          </Flex>
        ))}
      </Flex>
    </Flex>
  );
}
