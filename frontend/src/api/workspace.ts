import type { DirListing, FileContent, PickResult, WorkspaceState } from "../types";
import { request } from "./http";

/** 工作目录：查询 / 设置 */
export const WORKSPACE_API = "/api/workspace";

/** 工作目录内单个文件的内容 */
export const WORKSPACE_FILE_API = "/api/workspace/file";

/** 浏览服务端目录（「打开文件夹」选择器用） */
export const FS_DIRS_API = "/api/fs/dirs";

/** 当前工作目录 + 文件清单 */
export const fetchWorkspace = () => request<WorkspaceState>(WORKSPACE_API);

/** 设置工作目录，返回新的文件清单 */
export const openWorkspace = (path: string) =>
  request<WorkspaceState>(WORKSPACE_API, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });

/**
 * 让服务端在它所在的机器上弹出系统「选择文件夹」对话框，返回选中的目录。
 * 会阻塞到用户选完（或取消），所以前端要把按钮置为等待态。
 */
export const pickWorkspace = () =>
  request<PickResult>(`${WORKSPACE_API}/pick`, { method: "POST" });

/** 列出某个目录下的子文件夹；path 留空从用户主目录开始 */
export const fetchDirs = (path?: string) =>
  request<DirListing>(`${FS_DIRS_API}?path=${encodeURIComponent(path ?? "")}`);

/** 读取工作区内某个文件的内容 */
export const fetchFile = (path: string) =>
  request<FileContent>(`${WORKSPACE_FILE_API}?path=${encodeURIComponent(path)}`);
