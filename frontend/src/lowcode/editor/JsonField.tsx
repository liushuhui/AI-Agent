/**
 * JSON 编辑控件：本地维护文本，失焦时才尝试解析提交（解析失败标红、不覆盖）。
 * 父组件用 key（节点 + 字段 + 修订号）重建它，保证撤销等外部变更能回填。
 */

import { useState } from "react";
import { Input } from "antd";

export interface JsonFieldProps {
  value: unknown;
  onChange: (value: unknown) => void;
  placeholder?: string;
}

export function JsonField({ value, onChange, placeholder }: JsonFieldProps) {
  const [text, setText] = useState(() =>
    value === undefined || value === null ? "" : JSON.stringify(value, null, 2),
  );
  const [invalid, setInvalid] = useState(false);

  const commit = () => {
    if (text.trim() === "") {
      setInvalid(false);
      onChange(undefined);
      return;
    }
    try {
      onChange(JSON.parse(text));
      setInvalid(false);
    } catch {
      setInvalid(true);
    }
  };

  return (
    <>
      <Input.TextArea
        value={text}
        onChange={(event) => setText(event.target.value)}
        onBlur={commit}
        status={invalid ? "error" : undefined}
        autoSize={{ minRows: 2, maxRows: 10 }}
        placeholder={placeholder}
      />
      {invalid ? <div className="lc-tip lc-tip-error">JSON 解析失败（未保存，修正后重新失焦）</div> : null}
    </>
  );
}
