import { Badge, Card, Flex, Typography } from "antd";

import type { Todo } from "../types";

/** 状态标记：符号 + 颜色，避免为了三个图标引入 icon 包 */
const STATUS_MARK: Record<Todo["status"], { mark: string; color: string; label: string }> = {
  completed: { mark: "✓", color: "#5cba7d", label: "已完成" },
  in_progress: { mark: "▶", color: "#4f8cff", label: "进行中" },
  pending: { mark: "○", color: "#8b93a7", label: "待开始" },
};

/**
 * Agent 的任务清单与进度（由后端 TodoListMiddleware 产出）。
 *
 * 每轮工具审批前后后端都会下发最新的 todos，所以这里的进度会跟着走。
 */
export function TodoPanel({ todos }: { todos: Todo[] }) {
  if (todos.length === 0) return null;

  const done = todos.filter((t) => t.status === "completed").length;
  const running = todos.some((t) => t.status === "in_progress");

  return (
    <Card
      variant="outlined"
      className="todo"
      style={{ boxShadow: "none", marginBottom: 16 }}
      styles={{ body: { padding: "10px 14px" } }}
    >
      <Flex vertical gap={6}>
        <Flex align="center" gap={8}>
          <Typography.Text strong style={{ fontSize: 13 }}>
            任务清单
          </Typography.Text>
          <Badge
            count={`${done}/${todos.length}`}
            style={{
              backgroundColor: done === todos.length ? "#5cba7d" : "#4f8cff",
              fontSize: 11,
            }}
          />
          {running && (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              执行中…
            </Typography.Text>
          )}
        </Flex>

        {todos.map((todo, index) => {
          const { mark, color, label } = STATUS_MARK[todo.status];
          return (
            <Flex key={`${index}-${todo.content}`} gap={8} align="flex-start">
              <span style={{ color, flex: "none", lineHeight: "20px" }}>{mark}</span>
              <Typography.Text
                type={todo.status === "pending" ? "secondary" : undefined}
                style={{
                  fontSize: 13,
                  lineHeight: "20px",
                  textDecoration: todo.status === "completed" ? "line-through" : undefined,
                }}
              >
                {todo.content}
                <Typography.Text type="secondary" style={{ fontSize: 11, marginLeft: 6 }}>
                  {label}
                </Typography.Text>
              </Typography.Text>
            </Flex>
          );
        })}
      </Flex>
    </Card>
  );
}
