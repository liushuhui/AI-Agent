export type StreamEvent = {
  type: "reasoning" | "content" | "error" | "usage" | "interrupt" | "todos";
  content?: string;
  error?: string;
  usage?: unknown;
  /** type === "interrupt" 时带回的会话 id，提交审批时要原样传回 */
  thread_id?: string;
  /** type === "interrupt" 时带回的待审批工具调用 */
  actions?: ApprovalAction[];
  /** type === "interrupt" 时带回的助手消息 id（历史在服务端）：续跑时原样传回 */
  message_id?: number;
  /** type === "todos" 时带回的待办清单 */
  todos?: Todo[];
};

/** 待办清单的一项，由后端 TodoListMiddleware 产出 */
export type Todo = {
  content: string;
  status: "pending" | "in_progress" | "completed";
};

/** 工作区内的一个文件 */
export type WorkspaceFile = {
  /** 相对工作目录的路径 */
  path: string;
  name: string;
  size: number;
};

/** GET /workspace 的返回：当前工作目录 + 文件清单 */
export type WorkspaceState = {
  /** 未选择工作目录时为 null */
  root: string | null;
  entries: WorkspaceFile[];
  /** 文件过多被截断了 */
  truncated: boolean;
};

/**
 * POST /workspace/pick 的返回。
 * 用户在系统选择框里点了取消时 cancelled 为 true，此时 root 保持原样。
 */
export type PickResult = WorkspaceState & { cancelled: boolean };

/** GET /fs/dirs 的返回：某个目录下的子文件夹与文件，供「浏览服务端目录」使用 */
export type DirListing = {
  path: string;
  /** 已经是根（如 C:\\）时为 null */
  parent: string | null;
  dirs: { name: string; path: string }[];
  /** 一并返回文件名，否则「只有文件的目录」看起来像空的 */
  files: { name: string; size: number }[];
};

/** GET /workspace/file 的返回 */
export type FileContent = {
  path: string;
  size: number;
  content: string;
  truncated: boolean;
};

/** 会话（左侧列表的一项），与后端 conversations 表对应 */
export type Conversation = {
  id: string;
  title: string;
  /** 已累计的消息条数（含助手回复），列表里展示 */
  message_count: number;
  created_at: string;
  updated_at: string;
};

/** 服务端存的聊天记录（打开历史会话时用它回填界面） */
export type StoredMessage = {
  id: number;
  conversation_id: string;
  role: "user" | "assistant";
  content: string;
  /** 思维链；没有则为 null */
  reasoning: string | null;
  /** 附件引用（只存 id/name/kind/size，文件本身在附件表里） */
  attachments: AttachmentRef[];
  /** 这一轮用的 LangGraph 线程 id（挂起状态在服务端检查点里，删会话时据此清理） */
  thread_id: string | null;
  created_at: string;
};

/** GET /conversations/<id> 的返回：会话记录 + 全部消息 */
export type ConversationDetail = {
  conversation: Conversation;
  messages: StoredMessage[];
};

/** 前端能给出的审批选项，与后端 allowed_decisions 一一对应 */
export type ApprovalDecisionType = "approve" | "edit" | "reject" | "respond";

/** 后端中断事件里的一项待审批工具调用 */
export type ApprovalAction = {
  name: string;
  args: Record<string, unknown>;
  description?: string;
  /** 该工具允许的决策，后端决定；前端据此决定渲染哪几个选项 */
  allowed_decisions: ApprovalDecisionType[];
};

/** 审批面板里的一项：比后端多了本地选择态 */
export type ApprovalItem = ApprovalAction & {
  /** 用户选中的决策；undefined = 还没选 */
  decision?: ApprovalDecisionType;
  /** 选了 edit 时展示的参数文本（JSON），提交前解析 */
  argsText: string;
  /** 选了 respond 时由人代答给模型的工具返回值 */
  respondMessage?: string;
};

/** 挂在助手消息上的审批面板状态 */
export type ApprovalState = {
  threadId: string;
  /** 服务端返回的助手消息 id：续跑审批时原样传回，续写内容接到同一条回复上 */
  messageId?: number;
  items: ApprovalItem[];
  /** pending=等用户选择；submitting=已提交等后端；done=已处理完 */
  status: "pending" | "submitting" | "done";
  error?: string;
};

/** 附件类型，与后端 attachment.py 的 kind 一一对应 */
export type AttachmentKind = "image" | "pdf" | "word" | "sheet" | "text" | "other";

/** 上传成功后服务端返回的附件元数据 */
export type Attachment = {
  id: string;
  name: string;
  kind: AttachmentKind;
  mime: string;
  size: number;
  /** ready=已解析可用；failed=文件已存但解析失败（如扫描版 PDF） */
  status: "ready" | "failed";
  error?: string | null;
  created_at?: string;
  /** 服务端解析出的文本预览（截断） */
  preview?: string;
};

/** 前端附件队列里的一项：比服务端多了本地 UI 状态与预览地址 */
export type PendingAttachment = {
  /** 前端本地唯一键，用于列表 diff */
  uid: string;
  name: string;
  size: number;
  kind: AttachmentKind;
  status: "uploading" | "ready" | "failed";
  /** 上传进度 0-100 */
  progress: number;
  /** 上传成功后的服务端 id */
  id?: string;
  error?: string;
  /** 图片的本地预览地址（objectURL，仅用于渲染） */
  previewUrl?: string;
  /** 服务端解析出的文本预览 */
  preview?: string;
};

/** 发给后端的附件引用：只传 id，服务端自己去查文件和解析结果 */
export type AttachmentRef = {
  id: string;
  name: string;
  kind: AttachmentKind;
  /** 文件大小；历史回填时也用它显示大小（更早的数据可能没有） */
  size?: number;
};

/** 界面上的消息：比服务端的多本地渲染状态（思维链展开、错误、审批面板） */
export type Message = {
  id: number;
  role: "user" | "assistant";
  content: string;
  thinking: string;
  hasThinking: boolean;
  thinkingOpen: boolean;
  errors: string[];
  /** 本轮携带的附件（含上传态，用于气泡内渲染） */
  attachments: PendingAttachment[];
  /** 工具调用等待人工审批时的面板状态；没有则为 undefined */
  approval?: ApprovalState;
};