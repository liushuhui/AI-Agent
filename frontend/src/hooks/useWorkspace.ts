import { useCallback, useEffect, useState } from "react";
import { App } from "antd";

import { fetchWorkspace, openWorkspace, pickWorkspace } from "../api/workspace";
import type { WorkspaceState } from "../types";

const EMPTY: WorkspaceState = { root: null, entries: [], truncated: false };

/**
 * 工作目录状态：当前选中的文件夹 + 里面的文件清单。
 *
 * 工作目录是「服务端」的概念（Agent 要读写它），所以前端只是显示与切换；
 * 目录内容会随 Agent 改文件而变化，需要手动或操作后刷新。
 */
export function useWorkspace() {
  const { message } = App.useApp();

  const [state, setState] = useState<WorkspaceState>(EMPTY);
  const [loading, setLoading] = useState(false);
  /** 系统选择框正开着（请求会被阻塞到用户选完） */
  const [picking, setPicking] = useState(false);

  /** 重新拉一次清单（Agent 改过文件后、用户点刷新时用） */
  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      setState(await fetchWorkspace());
    } catch (err) {
      // 多半是后端没起，控制台留个记录即可，不用打断页面
      console.warn("读取工作目录失败", err);
    } finally {
      setLoading(false);
    }
  }, []);

  // 进页面时同步一次后端已有的工作目录。
  // 这里用 .then() 而不是 await：effect 里不允许同步调 setState（会引发级联渲染），
  // 放到 Promise 回调里就不是同步调用了。
  useEffect(() => {
    let cancelled = false;
    fetchWorkspace()
      .then((next) => {
        if (!cancelled) setState(next);
      })
      .catch((err: unknown) => console.warn("读取工作目录失败", err));
    return () => {
      cancelled = true;
    };
  }, []);

  /** 选定工作目录 */
  const open = useCallback(
    async (path: string) => {
      setLoading(true);
      try {
        const next = await openWorkspace(path);
        setState(next);
        message.success(`工作目录已切换：${next.root}`);
        return true;
      } catch (err) {
        const text = err instanceof Error ? err.message : String(err);
        message.error(text);
        return false;
      } finally {
        setLoading(false);
      }
    },
    [message]
  );

  /**
   * 让服务端在本机弹出系统「选择文件夹」对话框，选完自动设为工作目录。
   * 浏览器拿不到本地真实路径，所以只能由服务端来弹框——本地开发时前后端
   * 在同一台机器上，体验上就是「在页面里选本机文件夹」。
   */
  const pick = useCallback(async () => {
    setPicking(true);
    try {
      const res = await pickWorkspace();
      if (res.cancelled) {
        message.info("已取消选择");
        return false;
      }
      setState({ root: res.root, entries: res.entries, truncated: res.truncated });
      message.success(`工作目录已切换：${res.root}`);
      return true;
    } catch (err) {
      const text = err instanceof Error ? err.message : String(err);
      message.error(text);
      return false;
    } finally {
      setPicking(false);
    }
  }, [message]);

  return { ...state, loading, picking, refresh, open, pick };
}

export type UseWorkspace = ReturnType<typeof useWorkspace>;
