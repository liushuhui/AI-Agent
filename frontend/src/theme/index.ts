import { theme, type ThemeConfig } from "antd";

/**
 * 亮色主题：白底 + 靛紫主色 + 玻璃质感的侧栏/工具栏。
 *
 * 页面里非 antd 的部分走 CSS 变量（定义在 Demo.css 的 :root），
 * 两边的取色保持一致：--accent #5b5bf6 / --accent-2 #a855f7。
 */
export const lightTheme: ThemeConfig = {
  algorithm: theme.defaultAlgorithm,
  token: {
    colorPrimary: "#5b5bf6",
    colorInfo: "#5b5bf6",
    colorSuccess: "#16a34a",
    colorWarning: "#d97706",
    colorError: "#e5484d",
    colorBgBase: "#f6f7fb",
    colorBgContainer: "#ffffff",
    colorBgElevated: "#ffffff",
    colorText: "#1e2430",
    colorTextSecondary: "#667085",
    colorTextHeading: "#141a24",
    colorBorder: "#e5e8f2",
    colorBorderSecondary: "#eef0f7",
    colorLink: "#5b5bf6",
    borderRadius: 12,
    fontFamily: '-apple-system, "Segoe UI", "Microsoft YaHei", sans-serif',
    fontSize: 15,
    lineHeight: 1.7,
    controlHeightLG: 52,
    boxShadow: "0 16px 40px rgba(24, 28, 50, .14)",
    boxShadowSecondary: "0 10px 28px rgba(24, 28, 50, .10)",
  },
  components: {
    Layout: {
      bodyBg: "transparent", // 露出 body 的浅色渐变背景
      siderBg: "transparent", // 侧栏的玻璃效果在 App.css 里画
      headerBg: "transparent",
    },
    Menu: {
      itemBg: "transparent",
      itemSelectedBg: "rgba(91, 91, 246, .12)",
      itemSelectedColor: "#4338ca",
      itemBorderRadius: 10,
    },
    Collapse: {
      headerBg: "#ffffff",
      contentBg: "#ffffff",
      headerPadding: "8px 12px",
      contentPadding: "8px 12px",
      headerPaddingSM: "8px 12px",
      contentPaddingSM: "8px 12px",
    },
    Input: {
      paddingBlock: 8,
      paddingInline: 14,
    },
    Card: {
      boxShadowTertiary: "0 2px 10px rgba(24, 28, 50, .06)",
    },
  },
};
