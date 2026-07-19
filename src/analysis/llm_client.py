# -*- coding: utf-8 -*-
"""LLM客户端模块"""

import os
from typing import Dict, Optional

# 安全加载.env（防止直接import时环境变量未加载）
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    import openai
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False


class LLMClient:
    """LLM客户端"""

    def __init__(self):
        self.provider = os.getenv("LLM_PROVIDER", "custom")
        self.api_key = os.getenv("LLM_API_KEY")
        self.base_url = self._get_base_url()

        if OPENAI_AVAILABLE and self.api_key:
            self.client = openai.OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
            )
        else:
            self.client = None

    def _get_base_url(self) -> str:
        """获取API基础URL"""
        if self.provider == "deepseek":
            return "https://api.deepseek.com"
        elif self.provider == "openai":
            return "https://api.openai.com/v1"
        else:
            return os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1")

    def chat(
        self,
        messages: list,
        model: str = None,
        temperature: float = 0.7,
        timeout: int = None,
        json_mode: bool = False,
        max_tokens: int = None,
    ) -> str:
        """发送聊天请求"""
        if not self.client:
            raise RuntimeError("LLM客户端未初始化，请检查LLM_API_KEY配置")

        # 模型优先用参数传入，其次环境变量，最后默认值
        use_model = model or os.getenv("LLM_MODEL", "mimo-v2.5-pro")
        # 超时：分析类请求默认120秒（完整prompt较大），可通过环境变量调整
        request_timeout = timeout or int(os.getenv("LLM_TIMEOUT", "120"))
        output_token_limit = max_tokens or int(os.getenv("LLM_MAX_TOKENS", "8192"))
        request = dict(
            model=use_model,
            messages=messages,
            temperature=temperature,
            timeout=request_timeout,
            max_tokens=output_token_limit,
        )
        use_json_mode = json_mode and os.getenv("LLM_JSON_MODE", "auto").lower() != "false"
        if use_json_mode:
            request["response_format"] = {"type": "json_object"}

        try:
            response = self.client.chat.completions.create(**request)
        except Exception as exc:
            # 部分 OpenAI 兼容服务暂不支持 response_format；只对明确的参数不兼容回退。
            message = str(exc).lower()
            unsupported_json_mode = use_json_mode and any(
                marker in message
                for marker in ("response_format", "json_object", "unsupported parameter")
            )
            if not unsupported_json_mode:
                raise
            request.pop("response_format", None)
            response = self.client.chat.completions.create(**request)

        choice = response.choices[0]
        if getattr(choice, "finish_reason", None) == "length":
            raise RuntimeError(
                f"LLM输出被截断：已达到max_tokens={output_token_limit}，未使用残缺JSON"
            )
        return choice.message.content

    def is_available(self) -> bool:
        """检查LLM是否可用"""
        return self.client is not None and self.api_key is not None
