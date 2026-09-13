"""HTTP 层中间件：请求 ID、访问日志、安全响应头、CORS、限流、压缩、统一错误、健康检查。

Flask 没有 express 那种 `app.use()`，中间件都靠 `before_request` /
`after_request` / `errorhandler` 钩子实现（够用，不用额外依赖）。
`init_app(app)` 负责全部注册。

after_request 的执行顺序是「注册顺序的逆序」，所以这里刻意让
`_install_request_context` 最先注册 —— 它最后执行，能看到其它中间件改完的最终响应。
"""

import gzip
import time
import uuid
from collections import defaultdict, deque
from threading import Lock

from flask import g, jsonify, request
from werkzeug.exceptions import HTTPException

import config
from logging_setup import (
    bind_request_id,
    get_logger,
    reset_request_id,
)

log = get_logger("http")

# 走「贵」限流桶的路径：对话是 SSE 长连接 + 模型调用，最该限量
_SSE_PATHS = ("/send-message/stream", "/send-message/resume")
# 会话内的发消息路径中间夹着会话 id，没办法用前缀匹配，改用后缀识别
_SSE_PATH_SUFFIXES = ("/messages", "/messages/resume")
_UPLOAD_PATHS = ("/attachments",)
# 不需要计数的请求：预检只是浏览器探路，健康检查会被探针高频调用
_SKIP_RATE_LIMIT_METHODS = ("OPTIONS",)
_SKIP_RATE_LIMIT_PATHS = ("/healthz", "/readyz")

_SECURITY_HEADERS = {
    # 禁止浏览器猜测 MIME（防止上传的 txt 被当成 html 执行）
    "X-Content-Type-Options": "nosniff",
    # 不允许被 iframe 嵌套（防点击劫持）
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    # 纯 JSON API，不加载任何外部资源；frame-ancestors 兜底点击劫持
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
    "Cross-Origin-Resource-Policy": "cross-origin",
}


# ---------------- 请求上下文 + 访问日志 ----------------

def _install_request_context(app) -> None:
    @app.before_request
    def _begin():
        # 网关/前端带过来的请求 ID 优先沿用，这样跨服务日志能对上
        request_id = (
            request.headers.get(config.REQUEST_ID_HEADER) or uuid.uuid4().hex[:12]
        )
        g.request_id = request_id
        g.started_at = time.perf_counter()
        # 记下 token，请求结束时还原，避免上下文变量在长连接里串味
        g.request_id_token = bind_request_id(request_id)

    @app.after_request
    def _finish(response):
        started = getattr(g, "started_at", None)
        elapsed_ms = (time.perf_counter() - started) * 1000 if started else 0.0
        request_id = getattr(g, "request_id", "-")

        response.headers[config.REQUEST_ID_HEADER] = request_id
        # 方便前端/网关直接看到服务端处理耗时
        response.headers["X-Process-Time"] = f"{elapsed_ms:.1f}ms"

        # 静态文件和预检不写访问日志，否则日志被淹掉
        if request.method != "OPTIONS":
            log.info(
                "request",
                extra={
                    "method": request.method,
                    "path": request.path,
                    "status": response.status_code,
                    "ms": round(elapsed_ms, 1),
                    "ip": request.remote_addr,
                    "bytes": response.calculate_content_length() or 0,
                },
            )
        return response

    @app.teardown_request
    def _end(_exc=None):
        # 必须在 after_request 之后还原：日志还要用这个 request_id
        reset_request_id(getattr(g, "request_id_token", None))


# ---------------- 安全响应头 ----------------

def _install_security_headers(app) -> None:
    @app.after_request
    def _headers(response):
        for key, value in _SECURITY_HEADERS.items():
            response.headers.setdefault(key, value)
        return response


# ---------------- CORS ----------------

def _install_cors(app) -> None:
    allow_any = "*" in config.CORS_ORIGINS

    @app.after_request
    def _cors(response):
        origin = request.headers.get("Origin")
        if allow_any:
            allowed = "*"
        else:
            allowed = origin if origin in config.CORS_ORIGINS else None

        # 白名单没命中就不加跨域头：浏览器会自己拦下来，比服务端放行安全
        if allowed:
            response.headers["Access-Control-Allow-Origin"] = allowed
            if allowed != "*":
                # 同一 URL 对不同 Origin 返回不同头，必须告诉缓存分层存储
                response.headers.add("Vary", "Origin")
            # 通配来源 + 携带 Cookie 是非法组合，浏览器会直接拒绝
            if config.CORS_ALLOW_CREDENTIALS and allowed != "*":
                response.headers["Access-Control-Allow-Credentials"] = "true"
            response.headers["Access-Control-Allow-Methods"] = (
                "GET, POST, PUT, DELETE, OPTIONS"
            )
            response.headers["Access-Control-Allow-Headers"] = ", ".join(
                config.CORS_ALLOW_HEADERS
            )
            response.headers["Access-Control-Expose-Headers"] = (
                f"{config.REQUEST_ID_HEADER}, X-Process-Time"
            )
            response.headers["Access-Control-Max-Age"] = str(config.CORS_MAX_AGE)
        return response


# ---------------- 限流 ----------------

class _SlidingWindowLimiter:
    """滑动窗口计数器，按 key（IP / IP+路径）限流。

    单进程内存版：本地 demo、单 worker 部署够用。
    生产多实例必须换 Redis（否则 N 个进程 = N 倍额度），见 config 里的注释。
    """

    def __init__(self) -> None:
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = Lock()

    def hit(self, key: str, limit: int, window: float = 60.0) -> tuple[bool, int]:
        """记一次访问。返回 (是否放行, 建议的 Retry-After 秒数)。"""
        now = time.monotonic()
        with self._lock:
            queue = self._hits[key]
            while queue and now - queue[0] > window:
                queue.popleft()
            if len(queue) >= limit:
                retry_after = max(1, int(window - (now - queue[0])) + 1)
                return False, retry_after
            queue.append(now)
            if len(self._hits) > 10_000:  # 兜底：防止 IP 爆炸把内存撑满
                self._sweep(now, window)
            return True, 0

    def _sweep(self, now: float, window: float) -> None:
        for key in [k for k, q in self._hits.items() if not q or now - q[-1] > window]:
            self._hits.pop(key, None)


limiter = _SlidingWindowLimiter()


def _limit_for(path: str) -> int:
    if path.startswith(_SSE_PATHS) or path.endswith(_SSE_PATH_SUFFIXES):
        return config.RATE_LIMIT_SSE_PER_MIN
    if path.startswith(_UPLOAD_PATHS):
        return config.RATE_LIMIT_UPLOAD_PER_MIN
    return config.RATE_LIMIT_DEFAULT_PER_MIN


def _install_rate_limit(app) -> None:
    @app.before_request
    def _rate_limit():
        if not config.RATE_LIMIT_ENABLED:
            return None
        if request.method in _SKIP_RATE_LIMIT_METHODS:
            return None
        if request.path.startswith(_SKIP_RATE_LIMIT_PATHS):
            return None

        limit = _limit_for(request.path)
        key = f"{request.remote_addr}|{request.path}"
        allowed, retry_after = limiter.hit(key, limit)
        if allowed:
            return None

        log.warning(
            "rate limited",
            extra={"path": request.path, "ip": request.remote_addr, "limit": limit},
        )
        response = jsonify(
            {"error": f"请求过于频繁，请 {retry_after} 秒后重试", "limit_per_minute": limit}
        )
        response.status_code = 429
        response.headers["Retry-After"] = str(retry_after)
        return response


# ---------------- 响应压缩 ----------------

_COMPRESSIBLE_MIMES = ("application/json", "text/plain", "text/html", "text/css")


def _install_compression(app) -> None:
    @app.after_request
    def _gzip(response):
        if not config.GZIP_ENABLED or request.method == "HEAD":
            return response
        # 流式响应（SSE）必须边产生边下发，压缩会把它重新攒起来，绝对不能压
        if response.direct_passthrough or response.mimetype not in _COMPRESSIBLE_MIMES:
            return response
        if response.headers.get("Content-Encoding"):
            return response
        if "gzip" not in (request.headers.get("Accept-Encoding") or "").lower():
            return response

        data = response.get_data()
        if len(data) < config.GZIP_MIN_BYTES:
            return response

        response.set_data(gzip.compress(data, compresslevel=6))
        response.headers["Content-Encoding"] = "gzip"
        response.headers["Content-Length"] = str(response.calculate_content_length() or 0)
        response.headers.add("Vary", "Accept-Encoding")
        return response


# ---------------- 统一错误处理 ----------------

def _error(message: str, status: int, **extra):
    return (
        jsonify(
            {
                "error": message,
                "request_id": getattr(g, "request_id", "-"),
                **extra,
            }
        ),
        status,
    )


# Werkzeug 默认给的是英文长句（还会附带一堆排查建议），
# 直接透出去既不统一也不友好，这里换成一句中文。
_HTTP_MESSAGES = {
    400: "请求参数有误",
    401: "未认证，请先登录",
    403: "没有权限执行该操作",
    404: "接口不存在，请检查请求地址",
    405: "该接口不支持此请求方法",
    406: "不接受该响应格式",
    408: "请求超时",
    409: "请求与服务器当前状态冲突",
    415: "不支持的请求内容类型",
    422: "请求参数校验失败",
    429: "请求过于频繁，请稍后重试",
    500: "服务器内部错误",
    502: "上游服务不可用",
    503: "服务暂时不可用，请稍后重试",
    504: "上游服务超时",
}


def _install_error_handlers(app) -> None:
    @app.errorhandler(HTTPException)
    def _http_exception(exc: HTTPException):
        # 413 给一句人话：光看 "Request Entity Too Large" 用户不知道怎么办
        if exc.code == 413:
            from attachment.config import MAX_UPLOAD_MB  # 延迟导入，避免与附件包互相牵扯

            return _error(f"请求体过大，单次上传请控制在 {MAX_UPLOAD_MB:g}MB 以内", 413)
        message = _HTTP_MESSAGES.get(exc.code) or exc.description or exc.name
        return _error(message, exc.code or 500)

    @app.errorhandler(Exception)
    def _unhandled(exc: Exception):
        # 500 只给用户一句通用提示：异常细节（可能含 SQL、密钥）只进服务端日志
        log.exception("unhandled error", extra={"path": request.path})
        return _error("服务器内部错误，请稍后重试或联系管理员", 500)


# ---------------- 健康检查 ----------------

def _install_health_routes(app, readiness_probe) -> None:
    @app.get("/healthz")
    def healthz():
        """存活探针：进程还在就返回 200，不查外部依赖（否则数据库抖动会被误杀）。"""
        return jsonify({"status": "ok", "env": config.APP_ENV})

    @app.get("/readyz")
    def readyz():
        """就绪探针：外部依赖不可用时返回 503，让负载均衡把流量摘走。"""
        if readiness_probe is None:
            return jsonify({"status": "ready", "env": config.APP_ENV})
        try:
            detail = readiness_probe()
        except Exception as exc:  # noqa: BLE001 - 探针绝不能再抛异常
            log.warning("readiness failed", extra={"reason": str(exc)})
            return jsonify({"status": "not-ready", "reason": str(exc)}), 503
        return jsonify({"status": "ready", "env": config.APP_ENV, "checks": detail})


# ---------------- 装配入口 ----------------

def init_app(app, readiness_probe=None) -> None:
    """给 Flask app 装上全部 HTTP 中间件。

    readiness_probe：无参可调用对象，正常返回即可（可返回 dict 作为详情），
    抛异常表示「未就绪」。由 app.py 注入，这样本模块不依赖具体的数据层。
    """
    # 注册顺序即「执行逆序」（after_request 后注册的先跑）
    _install_request_context(app)
    _install_security_headers(app)
    _install_cors(app)
    _install_rate_limit(app)
    _install_compression(app)
    _install_error_handlers(app)
    _install_health_routes(app, readiness_probe)
