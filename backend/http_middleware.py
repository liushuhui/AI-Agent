"""HTTP 层中间件：请求 ID、访问日志、安全响应头、CORS、限流、压缩、统一错误、健康检查。

【实现方式】
Flask 没有 express 那种 `app.use()` 链式写法，本模块用原生钩子实现全部功能，
不引入额外依赖（够用）：
  - `@app.before_request`：请求进视图函数之前执行。返回 None = 放行；
    返回 Response 会「短路」——后面的钩子和视图函数都不再执行。
  - `@app.after_request`：视图（或 errorhandler）产出 Response 后执行，用来改响应。
  - `@app.teardown_request`：请求彻底结束后执行，用来做清理。

【注册顺序的坑】
after_request 的执行顺序是「注册顺序的逆序」（后注册的先执行），
所以这里刻意让 `_install_request_context` 最先注册 —— 它最后执行，
能看到其它中间件改完的最终响应（比如压缩后的字节数、限流加的 429 头）。

【本模块的 7 个中间件】
  1. 请求上下文 + 访问日志：分配 request_id，记录每请求的耗时/状态码。
  2. 安全响应头：nosniff、禁止 iframe 嵌套等浏览器侧加固。
  3. CORS：按白名单给浏览器跨域放行（开发时前端 5173 → 后端 5000）。
  4. 限流：单进程内存版滑动窗口，按 IP+路径计数。
  5. 响应压缩：JSON/文本类响应做 gzip（SSE 流式响应必须跳过）。
  6. 统一错误：所有异常/HTTP 错误转成同格式 JSON，方便前端处理。
  7. 健康检查：/healthz（存活）与 /readyz（就绪），供容器探针使用。

`init_app(app)` 是唯一入口，负责把以上全部注册上。
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

# 本模块专用 logger：访问日志、限流告警、未捕获异常都从这里出去
log = get_logger("http")

# 走「贵」限流桶的路径：对话是 SSE 长连接 + 模型调用，最该限量
# （/send-message/* 是老的无状态接口，留给 stream_demo.html 用）
_SSE_PATHS = ("/send-message/stream", "/send-message/resume")
# 会话内的发消息路径中间夹着会话 id（如 /conversations/12/messages），
# 没办法用前缀匹配，改用后缀识别
_SSE_PATH_SUFFIXES = ("/messages", "/messages/resume")
# 上传接口单独一档限流：附件要落盘 + 解析，比普通读接口重得多
_UPLOAD_PATHS = ("/attachments",)
# 不需要计数的请求方法：OPTIONS 是浏览器跨域前的预检探路（自动发起，用户无感）
_SKIP_RATE_LIMIT_METHODS = ("OPTIONS",)
# 不需要计数的路径：健康检查会被监控探针高频轮询，计入限流会误伤
_SKIP_RATE_LIMIT_PATHS = ("/healthz", "/readyz")

# 浏览器侧安全加固头：对每个响应统一下发
_SECURITY_HEADERS = {
    # 禁止浏览器猜测 MIME（防止上传的 txt 被当成 html 执行 → XSS）
    "X-Content-Type-Options": "nosniff",
    # 不允许被 iframe 嵌套（防点击劫持）
    "X-Frame-Options": "DENY",
    # 不把带路径的完整 URL 通过 Referer 泄漏给外部站点
    "Referrer-Policy": "no-referrer",
    # 纯 JSON API，不加载任何外部资源；frame-ancestors 兜底点击劫持
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
    # 允许其它源（前端 5173）读取本 API 的响应
    "Cross-Origin-Resource-Policy": "cross-origin",
}


# ---------------- 请求上下文 + 访问日志 ----------------

def _install_request_context(app) -> None:
    """给每个请求分配 request_id，并输出一行结构化访问日志。

    request_id 的三个作用：
      1. 通过响应头回传，前端报错时可直接发给后端定位问题；
      2. 服务端所有日志自动带上它（logging_setup 用 contextvar 实现）；
      3. 网关/上游带过来的 ID 优先沿用，跨服务调用链能对上。
    """

    @app.before_request
    def _begin():
        # 网关/前端带过来的请求 ID 优先沿用，这样跨服务日志能对上；
        # 没带就自己生成一个 12 位短 ID（比完整 uuid 好读）
        request_id = (
            request.headers.get(config.REQUEST_ID_HEADER) or uuid.uuid4().hex[:12]
        )
        # g 是「本次请求」的临时存储，请求结束即销毁
        g.request_id = request_id
        g.started_at = time.perf_counter()  # 单调时钟，专门用来算耗时
        # 记下 token，请求结束时还原，避免上下文变量在长连接里串味
        g.request_id_token = bind_request_id(request_id)

    @app.after_request
    def _finish(response):
        started = getattr(g, "started_at", None)
        elapsed_ms = (time.perf_counter() - started) * 1000 if started else 0.0
        request_id = getattr(g, "request_id", "-")

        # 把 request_id 回传给调用方（前端/网关），便于对照服务端日志排查
        response.headers[config.REQUEST_ID_HEADER] = request_id
        # 方便前端/网关直接看到服务端处理耗时
        response.headers["X-Process-Time"] = f"{elapsed_ms:.1f}ms"

        # 静态文件和预检不写访问日志，否则日志被淹掉
        if request.method != "OPTIONS":
            log.info(
                "request",
                extra={
                    "method": request.method,  # 请求方法
                    "path": request.path,  # 请求路径
                    "status": response.status_code,  # 响应状态码
                    "ms": round(elapsed_ms, 1),  # 处理耗时（毫秒）
                    "ip": request.remote_addr,  # 客户端 IP
                    "bytes": response.calculate_content_length() or 0,  # 响应体字节数
                },
            )
        return response

    @app.teardown_request
    def _end(_exc=None):
        # 必须在 after_request 之后还原：访问日志还要用这个 request_id
        reset_request_id(getattr(g, "request_id_token", None))


# ---------------- 安全响应头 ----------------

def _install_security_headers(app) -> None:
    """给每个响应统一追加浏览器安全加固头（取值见 _SECURITY_HEADERS）。"""
    @app.after_request
    def _headers(response):
        # setdefault：已存在的头不覆盖，业务代码有特殊需要时仍可自行设置
        for key, value in _SECURITY_HEADERS.items():
            response.headers.setdefault(key, value)
        return response


# ---------------- CORS ----------------

def _install_cors(app) -> None:
    """跨域资源共享（Cross-Origin Resource Sharing）。

    开发时前端在 5173 端口、后端在 5000 端口，属于「跨域」；不返回这些头，
    浏览器会拦截响应（用 curl 测不出来——跨域是浏览器的限制，不是 HTTP 的）。
    """
    allow_any = "*" in config.CORS_ORIGINS  # 配置里写了 * 即「允许任何来源」

    @app.after_request
    def _cors(response):
        origin = request.headers.get("Origin")  # 浏览器自动带上的请求来源
        if allow_any:
            allowed = "*"
        else:
            # 白名单校验：Origin 不在配置里就置 None（不加跨域头）
            allowed = origin if origin in config.CORS_ORIGINS else None

        # 白名单没命中就不加跨域头：浏览器会自己拦下来，比服务端放行安全
        if allowed:
            response.headers["Access-Control-Allow-Origin"] = allowed
            if allowed != "*":
                # 同一 URL 对不同 Origin 要返回不同的头，
                # 必须告诉缓存按 Origin 分开存，否则会串缓存
                response.headers.add("Vary", "Origin")
            # 通配来源 + 携带 Cookie 是非法组合，浏览器会直接拒绝
            if config.CORS_ALLOW_CREDENTIALS and allowed != "*":
                response.headers["Access-Control-Allow-Credentials"] = "true"
            # 以下几项主要给浏览器的预检请求（OPTIONS）读取：
            # 声明真实请求允许使用的方法
            response.headers["Access-Control-Allow-Methods"] = (
                "GET, POST, PUT, DELETE, OPTIONS"
            )
            # 声明允许携带的请求头（如 Authorization、Content-Type）
            response.headers["Access-Control-Allow-Headers"] = ", ".join(
                config.CORS_ALLOW_HEADERS
            )
            # 默认前端只能读到少数几个「安全头」，自定义响应头必须显式暴露
            response.headers["Access-Control-Expose-Headers"] = (
                f"{config.REQUEST_ID_HEADER}, X-Process-Time"
            )
            # 预检结果的缓存时长（秒），减少 OPTIONS 请求次数
            response.headers["Access-Control-Max-Age"] = str(config.CORS_MAX_AGE)
        return response


# ---------------- 限流 ----------------

class _SlidingWindowLimiter:
    """滑动窗口计数器，按 key（IP / IP+路径）限流。

    原理：每个 key 维护一个命中时间戳队列，每次访问先踢掉窗口之外的旧记录，
    再看窗口内剩余条数是否达到上限。
      - 比「固定窗口」（每整分钟清零）平滑，不会在整点被薅双倍流量；
      - 比「令牌桶」实现简单，demo 场景够用。

    单进程内存版：本地 demo、单 worker 部署够用。
    生产多实例必须换 Redis（否则 N 个进程 = N 倍额度），见 config 里的注释。
    """

    def __init__(self) -> None:
        # key → 该 key 在窗口内每次命中的时间戳（双端队列，左旧右新）
        self._hits: dict[str, deque] = defaultdict(deque)
        # 多线程环境下必须加锁，否则对队列的并发读写会互相破坏
        self._lock = Lock()

    def hit(self, key: str, limit: int, window: float = 60.0) -> tuple[bool, int]:
        """记一次访问。返回 (是否放行, 建议的 Retry-After 秒数)。"""
        now = time.monotonic()  # 单调时钟：系统时间被调整也不受影响
        with self._lock:
            queue = self._hits[key]
            # 1) 踢掉窗口（默认 60 秒）之外的旧记录
            while queue and now - queue[0] > window:
                queue.popleft()
            # 2) 窗口内条数已达上限 → 拒绝；重试建议 = 最老那条记录还需多久过期
            if len(queue) >= limit:
                retry_after = max(1, int(window - (now - queue[0])) + 1)
                return False, retry_after
            # 3) 放行：记下本次命中的时间戳
            queue.append(now)
            if len(self._hits) > 10_000:  # 兜底：防止 IP 爆炸把内存撑满
                self._sweep(now, window)
            return True, 0

    def _sweep(self, now: float, window: float) -> None:
        """清理长时间没再访问过的 key，防止字典无限膨胀（内存兜底）。"""
        for key in [k for k, q in self._hits.items() if not q or now - q[-1] > window]:
            self._hits.pop(key, None)


# 模块级单例：全进程共用一个限流器
limiter = _SlidingWindowLimiter()


def _limit_for(path: str) -> int:
    """按路径挑配额：SSE 最严格 → 上传次之 → 其余用默认值。"""
    if path.startswith(_SSE_PATHS) or path.endswith(_SSE_PATH_SUFFIXES):
        return config.RATE_LIMIT_SSE_PER_MIN
    if path.startswith(_UPLOAD_PATHS):
        return config.RATE_LIMIT_UPLOAD_PER_MIN
    return config.RATE_LIMIT_DEFAULT_PER_MIN


def _install_rate_limit(app) -> None:
    """注册限流钩子：在 before_request 里按 IP+路径走滑动窗口，超限直接返回 429 短路视图。"""
    @app.before_request
    def _rate_limit():
        # 返回 None = 放行；返回 Response 会短路掉视图函数
        if not config.RATE_LIMIT_ENABLED:
            return None
        # 预检和健康检查不计入限流（原因见模块顶部的常量注释）
        if request.method in _SKIP_RATE_LIMIT_METHODS:
            return None
        if request.path.startswith(_SKIP_RATE_LIMIT_PATHS):
            return None

        limit = _limit_for(request.path)
        key = f"{request.remote_addr}|{request.path}"  # 限流维度：IP + 路径
        allowed, retry_after = limiter.hit(key, limit)
        if allowed:
            return None

        log.warning(
            "rate limited",
            extra={"path": request.path, "ip": request.remote_addr, "limit": limit},
        )
        # 429 Too Many Requests 是限流的标准状态码；
        # Retry-After 告诉客户端「等几秒再试」
        response = jsonify(
            {"error": f"请求过于频繁，请 {retry_after} 秒后重试", "limit_per_minute": limit}
        )
        response.status_code = 429
        response.headers["Retry-After"] = str(retry_after)
        return response


# ---------------- 响应压缩 ----------------

# 可压缩的 MIME：文本类压缩收益大；图片/zip 本身已压缩，再压纯属白费 CPU
_COMPRESSIBLE_MIMES = ("application/json", "text/plain", "text/html", "text/css")


def _install_compression(app) -> None:
    """注册 gzip 钩子：在 after_request 里对文本类响应压缩，SSE 流式响应一律跳过。"""
    @app.after_request
    def _gzip(response):
        # 一串「一票否决」式检查：任何一条不满足就原样返回
        if not config.GZIP_ENABLED or request.method == "HEAD":
            return response
        # 流式响应（SSE）必须边产生边下发，压缩会把它重新攒起来，绝对不能压；
        # direct_passthrough=True 是 Flask 对流式响应的标记
        if response.direct_passthrough or response.mimetype not in _COMPRESSIBLE_MIMES:
            return response
        if response.headers.get("Content-Encoding"):
            return response  # 已被压缩过，避免压两次
        # 客户端声明不支持 gzip 就不能压（HTTP 协议约定）
        if "gzip" not in (request.headers.get("Accept-Encoding") or "").lower():
            return response

        data = response.get_data()
        # 太小的响应压缩后可能反而更大，不划算
        if len(data) < config.GZIP_MIN_BYTES:
            return response

        response.set_data(gzip.compress(data, compresslevel=6))  # 6 是性价比档位
        response.headers["Content-Encoding"] = "gzip"
        # 压缩后长度变了，必须重算 Content-Length，否则浏览器读不全数据
        response.headers["Content-Length"] = str(response.calculate_content_length() or 0)
        # 响应内容随 Accept-Encoding 变化，告诉缓存按该头分开存
        response.headers.add("Vary", "Accept-Encoding")
        return response


# ---------------- 统一错误处理 ----------------

def _error(message: str, status: int, **extra):
    """统一的 JSON 错误响应，格式：{error: 提示语, request_id, ...额外字段}。

    带上 request_id，用户截图报错时服务端能直接按 ID 搜到对应日志。
    返回元组的第二项是 HTTP 状态码，Flask 会自动识别这种写法。
    """
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
    """注册两类错误处理器：HTTPException 转中文 JSON；未捕获 Exception 兜底 500。"""
    @app.errorhandler(HTTPException)
    def _http_exception(exc: HTTPException):
        """处理 Flask/Werkzeug 主动抛出的 HTTP 异常，如 404 / 405 / 413。"""
        # 413 给一句人话：光看 "Request Entity Too Large" 用户不知道怎么办
        if exc.code == 413:
            from attachment.config import MAX_UPLOAD_MB  # 延迟导入，避免与附件包互相牵扯

            return _error(f"请求体过大，单次上传请控制在 {MAX_UPLOAD_MB:g}MB 以内", 413)
        # 先查中文映射表；没收录的用 Werkzeug 自带描述兜底
        message = _HTTP_MESSAGES.get(exc.code) or exc.description or exc.name
        return _error(message, exc.code or 500)

    @app.errorhandler(Exception)
    def _unhandled(exc: Exception):
        """兜底：任何没被上面拦住的异常都转成 500 JSON。"""
        # 500 只给用户一句通用提示：异常细节（可能含 SQL、密钥）只进服务端日志
        log.exception("unhandled error", extra={"path": request.path})
        return _error("服务器内部错误，请稍后重试或联系管理员", 500)


# ---------------- 健康检查 ----------------

def _install_health_routes(app, readiness_probe) -> None:
    """注册容器/K8s 探针用的两个接口，职责严格分开：
      - /healthz（liveness）：只回答「进程还活着吗」，用于决定是否重启容器；
      - /readyz（readiness）：回答「依赖都通吗」，用于决定是否把流量接进来。
    """

    @app.get("/healthz")
    def healthz():
        """存活探针：进程还在就返回 200，不查外部依赖（否则数据库抖动会被误杀）。"""
        return jsonify({"status": "ok", "env": config.APP_ENV})

    @app.get("/readyz")
    def readyz():
        """就绪探针：外部依赖不可用时返回 503，让负载均衡把流量摘走。"""
        if readiness_probe is None:
            # 没有注入探针（如单元测试/轻量启动）时，直接视为就绪
            return jsonify({"status": "ready", "env": config.APP_ENV})
        try:
            detail = readiness_probe()  # 由 app.py 注入，通常是「查一下数据库」
        except Exception as exc:  # noqa: BLE001 - 探针绝不能再抛异常
            log.warning("readiness failed", extra={"reason": str(exc)})
            return jsonify({"status": "not-ready", "reason": str(exc)}), 503
        return jsonify({"status": "ready", "env": config.APP_ENV, "checks": detail})


# ---------------- 装配入口 ----------------

def init_app(app, readiness_probe=None) -> None:
    """给 Flask app 装上全部 HTTP 中间件（app.py 启动时调用一次）。

    readiness_probe：无参可调用对象，正常返回即可（可返回 dict 作为详情），
    抛异常表示「未就绪」。由 app.py 注入，这样本模块不依赖具体的数据层。
    """
    # 注册顺序即「执行逆序」（after_request 后注册的先跑）；
    # request_context 排第一 → 它的 after_request 最后执行，
    # 此时其它中间件（压缩、限流…）已改完响应，日志里的字节数才是最终值
    _install_request_context(app)
    _install_security_headers(app)
    _install_cors(app)
    _install_rate_limit(app)
    _install_compression(app)
    _install_error_handlers(app)
    _install_health_routes(app, readiness_probe)
