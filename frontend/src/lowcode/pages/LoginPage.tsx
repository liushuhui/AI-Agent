/**
 * 登录页：左侧渐变品牌区 + 右侧玻璃卡片表单。
 * 演示账号点一下就填充（种子账号见后端 lowcode_auth.SEED_PASSWORDS）。
 */

import { useState } from "react";
import { useLocation, useNavigate } from "react-router";
import { App as AntdApp, Button, Input } from "antd";
import { login, setSession } from "../../api/auth";
import "../lowcode.css";

const DEMO_ACCOUNTS = [
  { username: "admin", password: "admin123", label: "管理员" },
  { username: "alice", password: "alice123", label: "编辑" },
  { username: "bob", password: "bob123", label: "只读" },
];

export function LoginPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const { message } = AntdApp.useApp();
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("admin123");
  const [loading, setLoading] = useState(false);

  const from = (location.state as { from?: string } | null)?.from ?? "/lowcode";

  const submit = async () => {
    if (!username.trim() || !password) {
      message.warning("请输入用户名和密码");
      return;
    }
    setLoading(true);
    try {
      const session = await login(username.trim(), password);
      setSession(session);
      message.success(`欢迎回来，${session.user.name}`);
      navigate(from, { replace: true });
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="lc-login">
      <div className="lc-login-hero">
        <div className="lc-login-orb lc-orb-a" />
        <div className="lc-login-orb lc-orb-b" />
        <div className="lc-login-orb lc-orb-c" />
        <div className="lc-login-hero-inner">
          <div className="lc-login-logo">AI</div>
          <h1>AI Agent · 低代码平台</h1>
          <p>一句话生成后台页面，拖拽微调，多用户协作发布。</p>
          <ul>
            <li>自然语言 → 页面结构，流式生成、可撤销</li>
            <li>物料拖拽 + 属性 / 数据源 / 事件可视化配置</li>
            <li>乐观锁保存 + 发布版本 + 一键回滚</li>
          </ul>
        </div>
      </div>

      <div className="lc-login-panel">
        <div className="lc-login-card">
          <h2>登录</h2>
          <p className="lc-login-sub">使用平台账号登录后进入低代码工作台</p>

          <label className="lc-field-label">用户名</label>
          <Input
            size="large"
            value={username}
            placeholder="admin"
            onChange={(event) => setUsername(event.target.value)}
            onPressEnter={() => void submit()}
          />

          <label className="lc-field-label lc-login-gap">密码</label>
          <Input.Password
            size="large"
            value={password}
            placeholder="admin123"
            onChange={(event) => setPassword(event.target.value)}
            onPressEnter={() => void submit()}
          />

          <Button
            type="primary"
            size="large"
            block
            loading={loading}
            className="lc-login-submit"
            onClick={() => void submit()}
          >
            登 录
          </Button>

          <div className="lc-login-demo">
            <div className="lc-muted">演示账号（点击填充）</div>
            <div className="lc-login-chips">
              {DEMO_ACCOUNTS.map((account) => (
                <button
                  key={account.username}
                  type="button"
                  className="lc-chip"
                  onClick={() => {
                    setUsername(account.username);
                    setPassword(account.password);
                  }}
                >
                  {account.label} · {account.username}
                </button>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
