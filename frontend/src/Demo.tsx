import { App as AntdApp, ConfigProvider } from "antd";

import { darkTheme } from "./theme";
import { ChatPanel } from "./components/ChatPanel";

import "./Demo.css";

/**
 * 演示页外壳：只提供主题与 antd 的 App 上下文。
 *
 * 具体实现按职责拆在下面几处：
 *   components/  纯展示组件（消息列表、审批面板、附件卡片、输入区）
 *   hooks/       状态逻辑（useChat 对话与审批、useAttachments 附件队列）
 *   api/         与后端交互（chat.ts 走 SSE、attachment.ts 走上传）
 *   lib/         与 UI 无关的纯函数
 */
export default function Demo() {
  return (
    <ConfigProvider theme={darkTheme} button={{ autoInsertSpace: false }}>
      <AntdApp>
        <ChatPanel />
      </AntdApp>
    </ConfigProvider>
  );
}
