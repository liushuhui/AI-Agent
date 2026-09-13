"""日志：统一出口 + 结构化格式 + 请求 ID 串联。

设计要点
--------
1. 只用标准库 `logging`，不引入额外依赖；`setup_logging()` 在 app 启动时调一次。
2. 每条日志自动带上当前请求的 `request_id`（`contextvar` 传递，无需到处传参），
   排查问题时可以用一个 id 把「接口日志」和「Agent 内部日志」串起来。
3. `LOG_FORMAT=json` 时输出一行一条 JSON，方便直接喂给 Loki/ELK；
   默认 text 模式带颜色，本地开发好读。
"""

import json
import logging
import sys
from contextvars import ContextVar
from datetime import datetime, timezone

import config

# 当前请求 id：http_middleware 在每个请求开始时 set，请求结束时 reset。
_request_id: ContextVar[str] = ContextVar("request_id", default="-")


def bind_request_id(request_id: str):
    """绑定请求 id，返回 token（用于请求结束时还原）。"""
    return _request_id.set(request_id or "-")


def reset_request_id(token) -> None:
    try:
        _request_id.reset(token)
    except ValueError:
        # 跨上下文 reset 会抛 ValueError；此时直接置回默认值即可
        _request_id.set("-")


def current_request_id() -> str:
    return _request_id.get()


# ---------------- 格式化 ----------------

# 需要脱敏的字段：日志里绝不能出现密钥/密码。
# 注意必须用「精确匹配」：早期用「包含」判断时，input_tokens / total_tokens
# 因为含 token 被一并抹成 ***，反倒把有用的用量统计弄没了。
_SENSITIVE_KEYS = {
    "token",
    "access_token",
    "refresh_token",
    "id_token",
    "api_key",
    "apikey",
    "api_secret",
    "password",
    "passwd",
    "secret",
    "authorization",
    "cookie",
}


def _sanitize(extra: dict) -> dict:
    cleaned = {}
    for key, value in extra.items():
        cleaned[key] = "***" if key.lower() in _SENSITIVE_KEYS else value
    return cleaned


class _ExtraFilter(logging.Filter):
    """把 extra 里非标准字段收敛到 `record.extras`，方便格式化器统一处理。"""

    _STANDARD = set(
        logging.LogRecord("", 0, "", 0, "", (), None).__dict__
    ) | {"message", "asctime", "taskName"}

    def filter(self, record: logging.LogRecord) -> bool:
        record.extras = _sanitize(
            {k: v for k, v in record.__dict__.items() if k not in self._STANDARD}
        )
        record.request_id = current_request_id()
        return True


class _TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        stamp = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        head = f"{stamp} {record.levelname:<7} [{record.request_id}] {record.name}: "
        line = head + record.getMessage()
        extras = getattr(record, "extras", None)
        if extras:
            pairs = " ".join(f"{k}={v}" for k, v in extras.items())
            line = f"{line} | {pairs}"
        if record.exc_info:
            line = f"{line}\n{self.formatException(record.exc_info)}"
        return line


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "request_id": getattr(record, "request_id", "-"),
            "message": record.getMessage(),
        }
        payload.update(getattr(record, "extras", None) or {})
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


_configured = False


def setup_logging() -> None:
    """初始化根 logger；重复调用只生效一次（Flask reloader 会 import 两次）。"""
    global _configured
    if _configured:
        return
    _configured = True

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        _JsonFormatter() if config.LOG_FORMAT == "json" else _TextFormatter()
    )
    handler.addFilter(_ExtraFilter())

    root = logging.getLogger()
    root.handlers.clear()  # 去掉 Flask/werkzeug 默认处理器，避免一条日志打两遍
    root.addHandler(handler)
    root.setLevel(config.LOG_LEVEL)

    # 这几个库在 INFO/DEBUG 级别话太多（连接池、握手、重定向全打出来），
    # 压到 WARNING；同时让它们复用我们的 handler。
    # 注意 httpx2/httpcore2 是 httpx/httpcore 的新版本包名，两套都要盖住。
    for name in (
        "werkzeug",
        "httpx",
        "httpx2",
        "httpcore",
        "httpcore2",
        "openai",
        "urllib3",
        "pymysql",
        "langsmith",
        "charset_normalizer",
        "PIL",
    ):
        logging.getLogger(name).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
