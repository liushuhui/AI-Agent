"""全局配置：所有环境变量在这里集中读一次，其它模块统一 import 本模块。

为什么要有这个文件
------------------
1. 之前每个模块各自 `os.getenv`，默认值散落在各处，调一个参数要翻好几个文件；
2. 开发/生产两套行为（调试、日志、CORS、限流、安全头）需要一个总开关 `APP_ENV`；
3. 本项目是 demo，所以「预算类」参数（token 阈值、调用次数、限流）都刻意取了
   很小的值 —— 目的是几轮对话就能触发摘要、上下文清理、限额保护这些中间件，
   方便验证。上线时按注释里的「生产建议」调大即可。

约定
----
- 本模块只负责「读配置」，不 import 任何项目内其它模块，避免循环依赖。
- 所有配置都能用环境变量覆盖，`.env` 由 `load_dotenv` 加载（见 `.env.example`）。
"""

import os

from dotenv import load_dotenv

# 本模块在 import 期就要读环境变量，所以必须在这里先加载 .env。
# load_dotenv 只对「它之后」执行的 os.getenv 生效，晚一步就读不到。
load_dotenv(override=True)


# ---------------- 读取助手 ----------------

def _env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is None:
        return default
    value = value.strip()
    return value or default


def _flag(name: str, default: bool) -> bool:
    """布尔开关：1/true/yes/on 为真，0/false/no/off 为假，未设置取默认值。"""
    raw = _env(name)
    if raw is None:
        return default
    return raw.lower() not in ("0", "false", "no", "off")


def _int(name: str, default: int) -> int:
    raw = _env(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    raw = _env(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _csv(name: str, default: list[str] | None = None) -> list[str]:
    raw = _env(name)
    if raw is None:
        return list(default or [])
    return [item.strip() for item in raw.split(",") if item.strip()]


# ---------------- 运行环境 ----------------

APP_ENV = (_env("APP_ENV", "dev") or "dev").lower()  # dev / test / prod
IS_PROD = APP_ENV in ("prod", "production")

HOST = _env("HOST", "0.0.0.0")
PORT = _int("PORT", 5000)
# 生产环境绝不允许开 debug（会泄露源码 + 任意代码执行），这里由 APP_ENV 强制收敛
DEBUG = _flag("APP_DEBUG", not IS_PROD)
# SSE 是长连接，Flask 自带服务器必须开多线程，否则一个对话就把服务占满
THREADED = _flag("APP_THREADED", True)


# ---------------- 日志 ----------------

LOG_LEVEL = (_env("LOG_LEVEL", "DEBUG" if not IS_PROD else "INFO") or "INFO").upper()
# text=人看的单行日志（含 request_id）；json=一行一条 JSON，交给采集器
LOG_FORMAT = (_env("LOG_FORMAT", "text") or "text").lower()


# ---------------- 跨域 ----------------

# 逗号分隔的来源白名单；"*" 表示放行全部（仅建议本地开发用）。
# 生产建议：CORS_ORIGINS=https://your-app.com,https://admin.your-app.com
CORS_ORIGINS = _csv("CORS_ORIGINS", ["*"])
CORS_ALLOW_CREDENTIALS = _flag("CORS_ALLOW_CREDENTIALS", False)
CORS_ALLOW_HEADERS = _csv("CORS_ALLOW_HEADERS", ["Content-Type", "X-Request-ID"])
CORS_MAX_AGE = _int("CORS_MAX_AGE", 600)  # 预检结果缓存秒数，减少 OPTIONS 往返


# ---------------- 限流（进程内计数，够单机 demo 用）----------------

# 生产建议：多实例部署时换成 Redis 版（flask-limiter + Redis），否则每个进程各算一份。
RATE_LIMIT_ENABLED = _flag("RATE_LIMIT_ENABLED", True)
RATE_LIMIT_DEFAULT_PER_MIN = _int("RATE_LIMIT_DEFAULT_PER_MIN", 120)  # 普通接口/分钟/IP
RATE_LIMIT_SSE_PER_MIN = _int("RATE_LIMIT_SSE_PER_MIN", 20)  # 对话（最贵的接口）/分钟/IP
RATE_LIMIT_UPLOAD_PER_MIN = _int("RATE_LIMIT_UPLOAD_PER_MIN", 30)  # 上传/分钟/IP


# ---------------- 响应压缩 / SSE ----------------

GZIP_ENABLED = _flag("GZIP_ENABLED", True)
GZIP_MIN_BYTES = _int("GZIP_MIN_BYTES", 512)  # 太小的响应压缩反而更慢
# SSE 心跳：长时间没有 token 时下发一行注释，防止中间代理按空闲超时掐断连接。
# demo 取 15 秒，方便观察；生产一般 15~30 秒。
SSE_HEARTBEAT_SECONDS = _float("SSE_HEARTBEAT_SECONDS", 15.0)

# 请求 ID 头：前端/网关带进来就沿用，否则服务端生成，便于串联链路日志
REQUEST_ID_HEADER = _env("REQUEST_ID_HEADER", "X-Request-ID") or "X-Request-ID"


# ---------------- Agent（智能体）中间件参数 ----------------

# 关掉任何一个中间件都能单独排查问题，不用改代码。
AGENT_SUMMARY_ENABLED = _flag("AGENT_SUMMARY_ENABLED", True)
AGENT_CONTEXT_EDITING_ENABLED = _flag("AGENT_CONTEXT_EDITING_ENABLED", True)
AGENT_PII_ENABLED = _flag("AGENT_PII_ENABLED", True)
AGENT_INPUT_GUARD_ENABLED = _flag("AGENT_INPUT_GUARD_ENABLED", True)
AGENT_FALLBACK_ENABLED = _flag("AGENT_FALLBACK_ENABLED", True)
AGENT_TOOL_SELECTOR_ENABLED = _flag("AGENT_TOOL_SELECTOR_ENABLED", False)

# --- 预算：一轮对话最多调用几次模型 / 几次工具 ---
# run_limit 每轮 invoke/stream 重新计数；thread_limit 跨轮累计（同一个 thread_id）。
# demo 取小值，几轮就能试出「超限后模型被强制收尾」的效果。
AGENT_MODEL_RUN_LIMIT = _int("AGENT_MODEL_RUN_LIMIT", 6)
AGENT_MODEL_THREAD_LIMIT = _int("AGENT_MODEL_THREAD_LIMIT", 20)
AGENT_TOOL_RUN_LIMIT = _int("AGENT_TOOL_RUN_LIMIT", 8)
AGENT_TOOL_THREAD_LIMIT = _int("AGENT_TOOL_THREAD_LIMIT", 30)

# --- 长对话治理 ---
# 命中任一条件就触发摘要：token 数或消息条数（两者取先到）。
# 生产建议：tokens 用模型上下文窗口的 60~80%，messages 可以不要。
AGENT_SUMMARY_TRIGGER_TOKENS = _int("AGENT_SUMMARY_TRIGGER_TOKENS", 1500)
AGENT_SUMMARY_TRIGGER_MESSAGES = _int("AGENT_SUMMARY_TRIGGER_MESSAGES", 8)
# 摘要后必须原样保留的尾部消息条数（对话连续性靠它）
AGENT_SUMMARY_KEEP_MESSAGES = _int("AGENT_SUMMARY_KEEP_MESSAGES", 4)
# 交给摘要模型的历史最多多少 token（防止摘要这一步自己又超上下文）
AGENT_SUMMARY_TRIM_TOKENS = _int("AGENT_SUMMARY_TRIM_TOKENS", 2000)

# 上下文清理：把「旧的工具返回」替换成占位符，比摘要更便宜，先跑它。
AGENT_CONTEXT_CLEAR_TRIGGER_TOKENS = _int("AGENT_CONTEXT_CLEAR_TRIGGER_TOKENS", 2500)
AGENT_CONTEXT_CLEAR_KEEP = _int("AGENT_CONTEXT_CLEAR_KEEP", 2)  # 保留最近 N 次工具结果

# --- 韧性：重试与降级 ---
AGENT_MODEL_MAX_RETRIES = _int("AGENT_MODEL_MAX_RETRIES", 2)
AGENT_TOOL_MAX_RETRIES = _int("AGENT_TOOL_MAX_RETRIES", 2)
# 指数退避：第 n 次重试等 initial * factor^(n-1)，再叠加抖动，最大 max_delay
AGENT_RETRY_INITIAL_DELAY = _float("AGENT_RETRY_INITIAL_DELAY", 0.5)
AGENT_RETRY_MAX_DELAY = _float("AGENT_RETRY_MAX_DELAY", 8.0)
AGENT_RETRY_BACKOFF_FACTOR = _float("AGENT_RETRY_BACKOFF_FACTOR", 2.0)

# --- 工具选择（工具很多时才值得开；会多一次模型调用）---
AGENT_TOOL_SELECTOR_MAX_TOOLS = _int("AGENT_TOOL_SELECTOR_MAX_TOOLS", 6)

# --- 输入护栏 ---
# 单条用户消息的字符上限，超了直接拒绝（比让模型烧 token 更划算）
AGENT_MAX_INPUT_CHARS = _int("AGENT_MAX_INPUT_CHARS", 20000)

# --- 模型调用 ---
# 主模型 / 备用模型都用 langchain 的 "provider:model" 写法
AGENT_MODEL = _env("AGENT_MODEL", "deepseek:deepseek-flash")
AGENT_MODEL_TEMPERATURE = _float("AGENT_MODEL_TEMPERATURE", 0.3)
AGENT_MODEL_TIMEOUT = _float("AGENT_MODEL_TIMEOUT", 60.0)  # 单次模型请求超时（秒）
AGENT_MODEL_MAX_TOKENS = _int("AGENT_MODEL_MAX_TOKENS", 2048)
FALLBACK_MODEL = _env("FALLBACK_MODEL", "deepseek:deepseek-chat")
