import type { Attachment, AttachmentKind } from "../types";
import { getToken } from "./auth";

/** 上传接口：经 Vite 代理 /api → 后端 /attachments */
export const UPLOAD_API = "/api/attachments";

/** 单文件大小上限（MB），与后端 MAX_UPLOAD_MB 保持一致 */
export const MAX_FILE_MB = 20;

/** 图片原始大小上限（MB），与后端 MAX_IMAGE_MB 保持一致 */
export const MAX_IMAGE_MB = 8;

const IMAGE_EXTS = ["png", "jpg", "jpeg", "webp", "bmp", "gif"];
const PDF_EXTS = ["pdf"];
const WORD_EXTS = ["docx"];
const SHEET_EXTS = ["xlsx", "csv"];
const TEXT_EXTS = [
  "txt", "md", "markdown", "json", "log", "yaml", "yml", "xml", "html", "htm",
  "py", "js", "jsx", "ts", "tsx", "css", "sql", "ini", "conf", "toml", "sh",
];

const EXT_KIND: Record<string, AttachmentKind> = {
  ...Object.fromEntries(IMAGE_EXTS.map((e) => [e, "image" as const])),
  ...Object.fromEntries(PDF_EXTS.map((e) => [e, "pdf" as const])),
  ...Object.fromEntries(WORD_EXTS.map((e) => [e, "word" as const])),
  ...Object.fromEntries(SHEET_EXTS.map((e) => [e, "sheet" as const])),
  ...Object.fromEntries(TEXT_EXTS.map((e) => [e, "text" as const])),
};

/** <input accept>：与后端 attachment.py 的白名单保持一致 */
export const ACCEPT_ATTR = Object.keys(EXT_KIND)
  .map((ext) => `.${ext}`)
  .join(",");

/** 类型对应的中文标签，用于 UI 展示 */
export const KIND_LABELS: Record<AttachmentKind, string> = {
  image: "图片",
  pdf: "PDF",
  word: "Word",
  sheet: "表格",
  text: "文本",
  other: "文件",
};

/** 取扩展名（小写，不带点） */
const extOf = (name: string): string => {
  const index = name.lastIndexOf(".");
  return index >= 0 ? name.slice(index + 1).toLowerCase() : "";
};

/** 选文件时先在本地判断类型，给出一致的分组（不依赖服务端） */
export const guessKind = (name: string, mime = ""): AttachmentKind => {
  if (mime.startsWith("image/")) return "image";
  return EXT_KIND[extOf(name)] ?? "other";
};

/** 本地校验：类型白名单 + 大小上限，尽量在上传前就拦掉 */
export const validateFile = (file: File): string | null => {
  const kind = guessKind(file.name, file.type);
  if (kind === "other") {
    return `不支持的文件类型（支持 图片 / PDF / Word / Excel / CSV / 常见文本）`;
  }
  const limit = (kind === "image" ? MAX_IMAGE_MB : MAX_FILE_MB) * 1024 * 1024;
  if (file.size > limit) {
    return `文件超过上限（${kind === "image" ? MAX_IMAGE_MB : MAX_FILE_MB}MB）`;
  }
  return null;
};

/** 人类可读的大小 */
export const formatSize = (bytes: number): string => {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
};

const parseJson = (text: string): unknown => {
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
};

/**
 * 上传单个附件。用 XHR 而不是 fetch，是为了拿到上传进度。
 * 失败时抛出的 Error.message 已是服务端给的中文提示，可直接展示。
 */
export const uploadAttachment = (
  file: File,
  onProgress?: (percent: number) => void,
  signal?: AbortSignal
): Promise<Attachment> =>
  new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", UPLOAD_API);
    const token = getToken();
    if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);

    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable && onProgress) {
        onProgress(Math.round((event.loaded / event.total) * 100));
      }
    };

    xhr.onload = () => {
      const data = parseJson(xhr.responseText) as
        | (Attachment & { error?: string })
        | null;
      if (xhr.status >= 200 && xhr.status < 300 && data?.id) {
        resolve(data);
      } else {
        reject(new Error(data?.error || `上传失败（HTTP ${xhr.status}）`));
      }
    };
    xhr.onerror = () => reject(new Error("网络异常，上传失败"));
    xhr.onabort = () => reject(new DOMException("已取消上传", "AbortError"));

    if (signal) {
      if (signal.aborted) {
        xhr.abort();
        return;
      }
      signal.addEventListener("abort", () => xhr.abort(), { once: true });
    }

    const form = new FormData();
    form.append("file", file);
    xhr.send(form);
  });
