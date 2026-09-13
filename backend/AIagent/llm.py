"""对话模型：全项目共用一个实例（主模型 + 可选的备用模型）。

生产上模型调用必须有三样东西，这里都补齐了：
  - timeout   ：单次请求超时，避免网络挂死把 Flask 线程占满；
  - max_tokens：单次输出上限，挡住「模型刹不住车」导致的天价账单；
  - 备用模型  ：主模型限流/抖动时自动降级（由 ModelFallbackMiddleware 使用）。

模型名支持环境变量覆盖（AGENT_MODEL / FALLBACK_MODEL），换模型不用改代码。
"""

import os

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model

import config
from logging_setup import get_logger

log = get_logger("llm")

load_dotenv(override=True)

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_API_BASE = os.getenv("DEEPSEEK_API_BASE")

MODEL_NAME = config.AGENT_MODEL


def _build(name: str):
    """按统一参数构造一个模型实例。"""
    return init_chat_model(
        model=name,
        api_key=DEEPSEEK_API_KEY,
        base_url=DEEPSEEK_API_BASE,
        temperature=config.AGENT_MODEL_TEMPERATURE,
        timeout=config.AGENT_MODEL_TIMEOUT,
        max_tokens=config.AGENT_MODEL_MAX_TOKENS,
    )


# 主模型：全项目共用（同参数构造出来的实例没必要重复创建）
model = _build(MODEL_NAME)


def build_fallback_model():
    """构造备用模型；未启用/与主模型相同/构造失败时返回 None。

    返回 None 时调用方直接不加 ModelFallbackMiddleware ——
    塞一个「必然失败」的降级链，只会在真出事时把失败原因藏起来。
    """
    if not config.AGENT_FALLBACK_ENABLED:
        return None
    fallback_name = config.FALLBACK_MODEL
    if not fallback_name or fallback_name == MODEL_NAME:
        return None
    try:
        fallback = _build(fallback_name)
    except Exception as exc:  # noqa: BLE001 - 降级模型配错不该拦住服务启动
        log.warning("fallback model unavailable: %s", exc)
        return None
    log.info("fallback model ready: %s", fallback_name)
    return fallback

