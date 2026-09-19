import { useState } from "react";
import { App as AntdApp, Button, ConfigProvider, Layout, Menu } from "antd";
import { Navigate, Route, Routes, useLocation, useNavigate } from "react-router";

import Demo from "./Demo";
import { VirtualListPage } from "@/pages/VirtualListPage";
import { RequireAuth } from "@/lowcode/components/RequireAuth";
import { LoginPage } from "@/lowcode/pages/LoginPage";
import { PreviewPage } from "@/lowcode/preview/PreviewPage";
import { logout, useCurrentUser } from "./api/auth";

import "./App.css";
import { LowcodeListPage } from "./lowcode/pages/LowcodeListPage";
import { EditorPage } from "./lowcode/editor/EditorPage";
import { lightTheme } from "./theme";

const { Sider, Content } = Layout;

/** 侧栏折叠状态持久化的 key（与 lc:token 等保持同一命名风格） */
const COLLAPSED_KEY = "lc:sider-collapsed";

/** 侧边菜单项：key 就是路由路径；icon 用于折叠后（只剩图标）显示 */
const NAV_ITEMS: { key: string; label: string; icon: string }[] = [
  { key: "/", label: "对话 Demo", icon: "💬" },
  { key: "/virtual-list", label: "虚拟滚动", icon: "📜" },
  { key: "/lowcode", label: "低代码", icon: "🧩" },
];

/**
 * 应用外壳：主题 + antd 全局上下文 + 路由。
 *
 * 路由用 react-router（history 模式）；左侧 Sider 里的 Menu 只负责 navigate，
 * 真正的页面切换仍然交给 Routes（URL 可直达、可回退）。
 * 登录页是整屏设计（不带外壳）；未知路径一律重定向回对话页。
 */
export default function App() {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  // 侧栏折叠状态：从 localStorage 恢复，折叠后菜单只显示图标
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem(COLLAPSED_KEY) === "1");

  const toggleCollapsed = () => {
    setCollapsed((prev) => {
      localStorage.setItem(COLLAPSED_KEY, prev ? "0" : "1"); // 下次打开保持同样状态
      return !prev;
    });
  };

  // 子路由（如 /lowcode/abc）高亮所属的顶级菜单项；未知路径回退 "/"
  const activeKey =
    NAV_ITEMS.find(
      (item) => item.key === pathname || (item.key !== "/" && pathname.startsWith(`${item.key}/`)),
    )?.key ?? "/";

  const isBare = pathname === "/login";
  // 登录态走订阅钩子（直接读 localStorage 会被 React Compiler 缓存住）
  const user = useCurrentUser();

  const signOut = async () => {
    await logout();
    navigate("/login", { replace: true });
  };

  const routes = (
    <Routes>
      <Route
        path="/"
        element={
          <RequireAuth>
            <Demo />
          </RequireAuth>
        }
      />
      <Route path="/virtual-list" element={<VirtualListPage />} />
      <Route path="/login" element={<LoginPage />} />
      <Route
        path="/lowcode"
        element={
          <RequireAuth>
            <LowcodeListPage />
          </RequireAuth>
        }
      />
      <Route
        path="/lowcode/:id"
        element={
          <RequireAuth>
            <EditorPage />
          </RequireAuth>
        }
      />
      <Route
        path="/preview/:id"
        element={
          <RequireAuth>
            <PreviewPage />
          </RequireAuth>
        }
      />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );

  return (
    <ConfigProvider theme={lightTheme} button={{ autoInsertSpace: false }}>
      <AntdApp>
        {isBare ? (
          routes
        ) : (
          <Layout hasSider className="app-shell">
            <Sider
              width={208}
              collapsedWidth={64}
              collapsible
              collapsed={collapsed}
              trigger={null} // 关掉自带的长条触发器，改用品牌行里的自定义按钮
              className={collapsed ? "app-sider app-sider-collapsed" : "app-sider"}
            >
              <div className="app-brand">
                {!collapsed && (
                  <div className="app-brand-text">
                    <span className="app-brand-name">AI Agent</span>
                    <span className="app-brand-sub">前端功能演示</span>
                  </div>
                )}
                <Button
                  type="text"
                  size="small"
                  className="app-collapse-btn"
                  title={collapsed ? "展开菜单" : "折叠菜单"}
                  aria-label={collapsed ? "展开菜单" : "折叠菜单"}
                  onClick={toggleCollapsed}
                >
                  {collapsed ? "»" : "«"}
                </Button>
              </div>
              <Menu
                className="app-menu"
                mode="inline"
                items={NAV_ITEMS}
                selectedKeys={[activeKey]}
                onClick={({ key }) => navigate(key)}
              />
              <div className="app-user">
                {user ? (
                  <>
                    <span className="app-user-name" title={user.name}>
                      {user.name}
                    </span>
                    <Button size="small" type="text" onClick={() => void signOut()}>
                      退出
                    </Button>
                  </>
                ) : (
                  <Button size="small" type="text" onClick={() => navigate("/login")}>
                    去登录
                  </Button>
                )}
              </div>
            </Sider>

            <Content className="app-content">{routes}</Content>
          </Layout>
        )}
      </AntdApp>
    </ConfigProvider>
  );
}
