import { Button, Flex, Input, Upload } from "antd";

import { ACCEPT_ATTR } from "../api/attachment";

/**
 * 输入区：文本框 + 附件按钮 + 发送/停止。
 * 自己不持有输入内容，值由父组件管（发送后清空、附件仍在队列里）。
 */
export function Composer({
  value,
  onChange,
  onSend,
  onAddFiles,
  streaming,
  inputRef,
}: {
  value: string;
  onChange: (value: string) => void;
  onSend: () => void;
  onAddFiles: (files: File[]) => void;
  streaming: boolean;
  inputRef: React.RefObject<React.ComponentRef<typeof Input.TextArea> | null>;
}) {
  return (
    <Flex gap={10} align="flex-end">
      <Input.TextArea
        ref={inputRef}
        value={value}
        autoSize={{ minRows: 2, maxRows: 6 }}
        placeholder="输入内容，Enter 发送，Shift + Enter 换行；可直接粘贴截图"
        onChange={(e) => onChange(e.target.value)}
        onPaste={(e) => {
          const files = Array.from(e.clipboardData?.files ?? []);
          if (files.length === 0) return;
          e.preventDefault();
          onAddFiles(files);
        }}
        onKeyDown={(e) => {
          // 输入法组合中的回车不当作发送；Shift + Enter 用于换行
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) onSend();
        }}
        styles={{ root: { flex: 1 } }}
      />

      <Upload
        multiple
        accept={ACCEPT_ATTR}
        showUploadList={false}
        fileList={[]}
        beforeUpload={(file) => {
          onAddFiles([file]);
          return false; // 交给自己的上传流程，不用 antd 的
        }}
      >
        <Button size="large" style={{ height: 52 }}>
          附件
        </Button>
      </Upload>

      <Button
        type="primary"
        size="large"
        danger={streaming}
        onClick={onSend}
        style={{ height: 52 }}
      >
        {streaming ? "停止" : "发送"}
      </Button>
    </Flex>
  );
}
