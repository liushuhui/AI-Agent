import { theme, type ThemeConfig } from "antd";

export const darkTheme: ThemeConfig = {
  algorithm: theme.darkAlgorithm,
  token: {
    colorPrimary: "#4f8cff",
    colorBgBase: "#0f1117",
    colorBgContainer: "#171a23",
    colorBgElevated: "#171a23",
    colorText: "#e6e8ee",
    colorTextSecondary: "#8b93a7",
    colorTextHeading: "#e6e8ee",
    colorBorder: "#262b38",
    colorBorderSecondary: "#262b38",
    colorError: "#ff8b8b",
    borderRadius: 12,
    fontFamily: '-apple-system, "Segoe UI", "Microsoft YaHei", sans-serif',
    fontSize: 15,
    lineHeight: 1.7,
    controlHeightLG: 52,
  },
  components: {
    Collapse: {
      headerBg: "#12151d",
      contentBg: "#12151d",
      headerPadding: "8px 12px",
      contentPadding: "8px 12px",
      headerPaddingSM: "8px 12px",
      contentPaddingSM: "8px 12px",
    },
    Input: {
      paddingBlock: 14,
      paddingInline: 14,
    },
  },
};