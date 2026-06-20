"""统一 Claude API 调用入口。

模型、API Key、Base URL 均从 .env / config.json 读取，换代理只改配置不改代码。
所有模块通过 claude_call() / claude_call_tool() / claude_stream() / get_client() 调用，
不要直接创建 anthropic.Anthropic client。

- claude_call: 普通文本生成
- claude_call_tool: 用 tool_use 强制结构化输出（适合 reasoning 代理模型，避免思考链占满 max_tokens）
- claude_stream: 流式输出
"""
import json
import logging
import os
import re
import time
import anthropic
from anthropic.types import TextBlock, ThinkingBlock, ToolUseBlock

from utils.config import get_claude_config

logger = logging.getLogger(__name__)


def _strip_markdown_code_block(raw: str) -> str:
    """去除 markdown 代码块包裹（支持多个代码块，逐行过滤）。"""
    lines = raw.strip().splitlines()
    cleaned = [
        line for line in lines
        if not re.match(r'^\s*```(?:json)?\s*$', line)
    ]
    return "\n".join(cleaned)


def _find_last_json_object(text: str) -> tuple[int, int]:
    """在文本中找到最后一个完整的 JSON 对象的 (start, end) 位置。

    从后往前扫描，用括号深度匹配找到正确的 {…} 对。
    """
    end = text.rfind("}")
    if end == -1:
        return -1, -1
    depth = 0
    for i in range(end, -1, -1):
        if text[i] == "}":
            depth += 1
        elif text[i] == "{":
            depth -= 1
            if depth == 0:
                return i, end + 1
    return -1, -1


def extract_json(raw: str) -> dict:
    """从 Claude 响应中提取最后一个 JSON 对象，处理 markdown 代码块包裹。"""
    cleaned = _strip_markdown_code_block(raw)
    start, end = _find_last_json_object(cleaned)
    if start == -1:
        raise ValueError(f"无 JSON 对象: {raw[:100]}")
    return json.loads(cleaned[start:end])


def _find_last_json_array(text: str) -> tuple[int, int]:
    """在文本中找到最后一个完整的 JSON 数组的 (start, end) 位置。"""
    end = text.rfind("]")
    if end == -1:
        return -1, -1
    depth = 0
    for i in range(end, -1, -1):
        if text[i] == "]":
            depth += 1
        elif text[i] == "[":
            depth -= 1
            if depth == 0:
                return i, end + 1
    return -1, -1


def extract_json_array(raw: str) -> list:
    """从 Claude 响应中提取最后一个 JSON 数组，处理 markdown 代码块包裹。"""
    cleaned = _strip_markdown_code_block(raw)
    start, end = _find_last_json_array(cleaned)
    if start == -1:
        raise ValueError(f"无 JSON 数组: {raw[:100]}")
    return json.loads(cleaned[start:end])


def get_client() -> anthropic.Anthropic:
    """返回配置好的 Anthropic client，供需要复用 client 的场景使用。"""
    api_key, base_url, _ = get_claude_config()

    # anthropic SDK 0.71+ 新增了 auth_token 参数，会读取 ANTHROPIC_AUTH_TOKEN 环境变量
    # 如果该变量存在，SDK 会同时发送 X-Api-Key 和 Authorization: Bearer 两个头，
    # 导致代理（如 MiMo）的 API key 认证被 Bearer token 干扰 → 401
    os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

    return anthropic.Anthropic(api_key=api_key, base_url=base_url)


def _get_model() -> str:
    """返回当前配置的模型名，未配置则报错。"""
    _, _, model = get_claude_config()
    if not model:
        raise RuntimeError("未配置 ANTHROPIC_MODEL，请在 .env 中设置")
    return model


def claude_call(
    prompt: str,
    *,
    max_tokens: int = 1024,
    model: str | None = None,
) -> str:
    """发送单轮对话，返回文本响应。

    Args:
        prompt: 用户消息内容
        max_tokens: 最大输出 token 数
        model: 指定模型则直接用，不走 fallback

    Returns:
        模型返回的文本，所有模型都失败时抛出最后一个异常
    """
    client = get_client()
    m = model or _get_model()

    last_err = None
    for attempt in range(3):
        try:
            msg = client.messages.create(
                model=m,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            u = msg.usage
            logger.info("claude_call: %s %d in / %d out", m, u.input_tokens, u.output_tokens)
            # 取第一个 TextBlock
            for block in msg.content:
                if isinstance(block, TextBlock):
                    return block.text
            # 兜底：无 TextBlock 时尝试从 ThinkingBlock 提取
            for block in msg.content:
                if isinstance(block, ThinkingBlock) and block.thinking:
                    logger.debug("claude_call: 无 TextBlock，从 ThinkingBlock 提取")
                    return block.thinking
            return ""
        except anthropic.RateLimitError as e:
            if attempt < 2:
                wait = 2 ** (attempt + 1)
                logger.warning("claude_call: %s 限流，%ds 后重试", m, wait)
                time.sleep(wait)
                continue
            logger.warning("claude_call: %s 限流 3 次，放弃", m)
            last_err = e
            break
        except Exception as e:
            logger.warning("claude_call: %s 失败（%s）", m, e)
            last_err = e
            break

    raise last_err


def claude_call_tool(
    prompt: str,
    tool_name: str,
    tool_description: str,
    input_schema: dict,
    *,
    max_tokens: int = 2048,
    model: str | None = None,
) -> dict:
    """用 tool_use 强制模型返回结构化数据，比解析 JSON 文本鲁棒得多。

    适用场景：mimo 等代理模型对 prompt 中"output JSON only"指令不稳定，
    但完整支持 Anthropic 的 tool_use 协议（thinking 之后必输出 ToolUseBlock）。

    默认传 thinking={"type":"disabled"} 关闭思考链，避免 reasoning 模型把
    max_tokens 全用在 thinking 上、工具调用被截断。若代理不识别该字段
    返回 BadRequestError，会自动降级为不带 thinking 重试一次。

    Args:
        prompt: 用户消息内容
        tool_name: 工具名（语义化命名，如 "record_analysis"）
        tool_description: 工具用途描述（让模型知道何时该调用）
        input_schema: JSON Schema 定义工具入参结构
        max_tokens: 最大输出 token 数
        model: 指定模型则直接用

    Returns:
        ToolUseBlock 的 input 字段（已是 dict，无需 JSON 解析）

    Raises:
        RuntimeError: 模型未返回 ToolUseBlock（被 max_tokens 截断或代理不支持）
        anthropic.RateLimitError: 3 次指数退避重试后仍限流
    """
    client = get_client()
    m = model or _get_model()

    def _do_call(disable_thinking: bool):
        kwargs = {
            "model": m,
            "max_tokens": max_tokens,
            "tools": [{
                "name": tool_name,
                "description": tool_description,
                "input_schema": input_schema,
            }],
            "tool_choice": {"type": "tool", "name": tool_name},
            "messages": [{"role": "user", "content": prompt}],
        }
        if disable_thinking:
            kwargs["thinking"] = {"type": "disabled"}
        return client.messages.create(**kwargs)

    msg = None
    last_err = None
    for attempt in range(3):
        try:
            msg = _do_call(disable_thinking=True)
            break
        except anthropic.RateLimitError as e:
            if attempt < 2:
                wait = 2 ** (attempt + 1)
                logger.warning("claude_call_tool: %s 限流，%ds 后重试", m, wait)
                time.sleep(wait)
                continue
            logger.warning("claude_call_tool: %s 限流 3 次，放弃", m)
            last_err = e
            raise
        except anthropic.BadRequestError as e:
            # 代理不识别 thinking 字段：降级一次重试
            logger.warning("claude_call_tool: 带 thinking 调用失败（%s），降级重试", e)
            try:
                msg = _do_call(disable_thinking=False)
                break
            except Exception as fallback_err:
                last_err = fallback_err
                raise

    if msg is None:
        raise last_err if last_err else RuntimeError("claude_call_tool: 未拿到响应")  # pragma: no cover

    u = msg.usage
    logger.info(
        "claude_call_tool: %s %d in / %d out (stop=%s)",
        m, u.input_tokens, u.output_tokens, msg.stop_reason,
    )

    for block in msg.content:
        if isinstance(block, ToolUseBlock):
            return block.input

    # 没拿到 tool_use：通常是 thinking 占满 max_tokens 还没输出工具调用就被截
    block_types = [type(b).__name__ for b in msg.content]
    raise RuntimeError(
        f"未收到 ToolUseBlock（stop_reason={msg.stop_reason}, "
        f"output_tokens={u.output_tokens}/{max_tokens}, blocks={block_types}），"
        f"可能 max_tokens 不够留给思考链 + 工具调用，或代理不支持 tool_use"
    )


def claude_stream(
    prompt: str,
    *,
    max_tokens: int = 8192,
    model: str | None = None,
):
    """流式输出，返回 context manager，用法：

    with claude_stream(prompt) as stream:
        for text in stream.text_stream:
            ...
    """
    client = get_client()
    return client.messages.stream(
        model=model or _get_model(),
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
