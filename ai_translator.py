# ai_translator.py
from typing import Optional, Dict, Any
from logger_setup import logger

# 动态导入，哪个需要用哪个
try:
    from openai import OpenAI, APIError
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

try:
    from zhipuai import ZhipuAI
    ZHIPUAI_AVAILABLE = True
except ImportError:
    ZHIPUAI_AVAILABLE = False

class AITranslator:
    def __init__(self, config: Dict[str, Any]):
        self.provider = config.get("ai_provider", "openai").lower()
        self.api_key = config.get("ai_api_key")
        self.model = config.get("ai_model_name")
        self.base_url = config.get("ai_base_url")
        self.prompt = config.get("ai_translation_prompt")
        
        if not self.api_key:
            raise ValueError("AI Translator: API Key 未配置。")
            
        self.client = None
        self._initialize_client()

    def _initialize_client(self):
        """根据提供商初始化对应的客户端"""
        if self.provider == 'openai':
            if not OPENAI_AVAILABLE:
                raise ImportError("OpenAI SDK 未安装，请运行 'pip install openai'")
            try:
                # OpenAI的base_url是可选的，只有在用户提供了代理时才使用
                self.client = OpenAI(api_key=self.api_key, base_url=self.base_url if self.base_url else None)
                logger.info(f"OpenAI client 初始化成功 (Model: {self.model}, Base URL: {self.base_url or '默认'})。")
            except Exception as e:
                logger.error(f"OpenAI client 初始化失败: {e}")
                raise
        
        elif self.provider == 'zhipuai':
            if not ZHIPUAI_AVAILABLE:
                raise ImportError("智谱AI SDK 未安装，请运行 'pip install zhipuai'")
            try:
                # 智谱AI的客户端初始化方式
                self.client = ZhipuAI(api_key=self.api_key)
                logger.info(f"智谱AI (ZhipuAI) client 初始化成功 (Model: {self.model})。")
            except Exception as e:
                logger.error(f"智谱AI client 初始化失败: {e}")
                raise
        
        else:
            raise ValueError(f"不支持的AI提供商: {self.provider}")

    def translate(self, text: str) -> Optional[str]:
        if not text or not text.strip():
            return text
            
        # 根据提供商调用不同的翻译方法
        if self.provider == 'openai':
            return self._translate_with_openai(text)
        elif self.provider == 'zhipuai':
            return self._translate_with_zhipuai(text)
        else:
            logger.error(f"没有为提供商 '{self.provider}' 实现翻译方法。")
            return None

    def _translate_with_openai(self, text: str) -> Optional[str]:
        if not self.client: return None
        try:
            logger.debug(f"使用 (OpenAI) 翻译: '{text}'")
            chat_completion = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": self.prompt},
                    {"role": "user", "content": text}
                ],
                temperature=0.0,
                max_tokens=100,
                timeout=30.0,
            )
            result = chat_completion.choices[0].message.content
            cleaned_result = result.strip().strip('"').strip("'") if result else ""
            logger.info(f"(OpenAI) 翻译成功: '{text}' -> '{cleaned_result}'")
            return cleaned_result
        except APIError as e:
            logger.error(f"OpenAI API 错误: {e.status_code} - {e.response.text}")
            return None
        except Exception as e:
            logger.error(f"调用 OpenAI API 时发生未知错误: {e}", exc_info=True)
            return None

    def _translate_with_zhipuai(self, text: str) -> Optional[str]:
        if not self.client: return None
        try:
            logger.info(f"使用 智谱AI ({self.model}) 翻译: '{text}'")
            response = self.client.chat.completions.create(
                model=self.model, # 例如 "glm-4" 或 "glm-3-turbo"
                messages=[
                    {"role": "system", "content": self.prompt},
                    {"role": "user", "content": text},
                ],
                temperature=0.0,
            )
            result = response.choices[0].message.content
            cleaned_result = result.strip().strip('"').strip("'") if result else ""
            logger.info(f"智谱AI 翻译成功: '{text}' -> '{cleaned_result}'")
            return cleaned_result
        except Exception as e:
            logger.error(f"调用 智谱AI API 时发生未知错误: {e}", exc_info=True)
            return None