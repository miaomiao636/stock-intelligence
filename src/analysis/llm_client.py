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

    def chat(self, messages: list, model: str = None, temperature: float = 0.7) -> str:
        """发送聊天请求"""
        if not self.client:
            raise RuntimeError("LLM客户端未初始化，请检查LLM_API_KEY配置")

        # 模型优先用参数传入，其次环境变量，最后默认值
        use_model = model or os.getenv("LLM_MODEL", "mimo-v2.5-pro")
        # 超时：分析类请求默认120秒（完整prompt较大），可通过环境变量调整
        timeout = int(os.getenv("LLM_TIMEOUT", "120"))

        response = self.client.chat.completions.create(
            model=use_model,
            messages=messages,
            temperature=temperature,
            timeout=timeout,
        )

        return response.choices[0].message.content

    def is_available(self) -> bool:
        """检查LLM是否可用"""
        return self.client is not None and self.api_key is not None
