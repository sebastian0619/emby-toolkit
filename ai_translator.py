# ai_translator.py
import json
from typing import Optional, Dict, Any, List
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
        # 这个prompt现在只用于单文本翻译，作为向后兼容
        self.single_translation_prompt = config.get("ai_translation_prompt", "Translate the following text to Chinese:")
        
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
                self.client = OpenAI(api_key=self.api_key, base_url=self.base_url if self.base_url else None)
                logger.info(f"OpenAI client 初始化成功 (Model: {self.model}, Base URL: {self.base_url or '默认'})。")
            except Exception as e:
                logger.error(f"OpenAI client 初始化失败: {e}")
                raise
        
        elif self.provider == 'zhipuai':
            if not ZHIPUAI_AVAILABLE:
                raise ImportError("智谱AI SDK 未安装，请运行 'pip install zhipuai'")
            try:
                self.client = ZhipuAI(api_key=self.api_key)
                logger.info(f"智谱AI (ZhipuAI) client 初始化成功 (Model: {self.model})。")
            except Exception as e:
                logger.error(f"智谱AI client 初始化失败: {e}")
                raise
        
        else:
            raise ValueError(f"不支持的AI提供商: {self.provider}")

    # --- 单文本翻译 (保留，但内部可以调用批量方法以统一逻辑) ---
    def translate(self, text: str) -> Optional[str]:
        if not text or not text.strip():
            return text
        
        # 单文本翻译现在可以简单地调用批量翻译，代码更简洁
        # 如果翻译失败，返回原文
        batch_result = self.batch_translate([text])
        return batch_result.get(text, text)

    # --- ✨✨✨【新增】批量翻译核心方法✨✨✨ ---
    def batch_translate(self, texts: List[str]) -> Dict[str, str]:
        """
        批量翻译一个字符串列表。

        Args:
            texts: 需要翻译的字符串列表。

        Returns:
            一个字典，键是原文，值是译文。对于翻译失败的条目，不会包含在字典中。
        """
        if not texts:
            return {}
        
        # 去重，避免重复翻译消耗Token
        unique_texts = list(set(texts))
        logger.info(f"开始批量翻译 {len(unique_texts)} 个独立词条...")

        # 根据提供商调用不同的批量翻译方法
        if self.provider == 'openai':
            return self._batch_translate_with_openai(unique_texts)
        elif self.provider == 'zhipuai':
            return self._batch_translate_with_zhipuai(unique_texts)
        else:
            logger.error(f"没有为提供商 '{self.provider}' 实现批量翻译方法。")
            return {}

    def _batch_translate_with_openai(self, texts: List[str]) -> Dict[str, str]:
        if not self.client: return {}
        
        # 专门为批量翻译设计的、高效的Prompt
        system_prompt = "You are an expert translator. Your task is to translate a list of English names and roles into Chinese. Return the result as a single, valid JSON object where keys are the original English strings and values are their Chinese translations. Follow standard translation conventions."
        user_prompt = f"Please translate the following items:\n{json.dumps(texts, ensure_ascii=False)}"

        try:
            logger.debug(f"使用 (OpenAI) 批量翻译 {len(texts)} 个词条...")
            chat_completion = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.0,
                # ✨ 关键：强制模型返回JSON格式，非常稳定
                response_format={"type": "json_object"},
                timeout=60.0, # 批量任务可能需要更长的超时时间
            )
            response_content = chat_completion.choices[0].message.content
            translated_dict = json.loads(response_content)

            if not isinstance(translated_dict, dict):
                logger.error(f"OpenAI 批量翻译未返回有效的JSON对象，而是返回了: {type(translated_dict)}")
                return {}
            
            logger.debug(f"(OpenAI) 批量翻译成功，返回 {len(translated_dict)} 个结果。")
            return translated_dict
            
        except APIError as e:
            logger.error(f"OpenAI API 错误: {e.status_code} - {e.response.text}")
            return {}
        except Exception as e:
            logger.error(f"调用 OpenAI API 进行批量翻译时发生未知错误: {e}", exc_info=True)
            return {}

    def _batch_translate_with_zhipuai(self, texts: List[str]) -> Dict[str, str]:
        if not self.client: return {}

        # 智谱AI的Prompt可以和OpenAI保持一致
        system_prompt = "你是一位专业的翻译家。你的任务是将一个英文的姓名和角色列表翻译成中文。请将结果作为一个单一、合法的JSON对象返回，其中键是原始的英文字符串，值是对应的中文翻译。请遵循标准的翻译惯例。"
        user_prompt = f"请翻译以下条目：\n{json.dumps(texts, ensure_ascii=False)}"

        try:
            logger.debug(f"使用 (智谱AI) 批量翻译 {len(texts)} 个词条...")
            response = self.client.chat.completions.create(
                model=self.model, # 例如 "glm-4"
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.0,
                # ✨ 关键：智谱AI同样支持工具调用和JSON模式，这里我们请求它返回JSON
                # 注意：对于旧模型可能不支持，但GLM-4支持良好
                tool_choice="auto",
                tools=[{
                    "type": "function",
                    "function": {
                        "name": "translation_output",
                        "description": "The JSON object containing all translations.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "translations": {
                                    "type": "object",
                                    "description": "A dictionary mapping original English text to its Chinese translation."
                                }
                            },
                            "required": ["translations"]
                        }
                    }
                }]
            )
            
            # 解析智谱AI的工具调用返回
            tool_call = response.choices[0].message.tool_calls[0]
            if tool_call.function.name == "translation_output":
                arguments = json.loads(tool_call.function.arguments)
                translated_dict = arguments.get("translations", {})

                if not isinstance(translated_dict, dict):
                    logger.error(f"智谱AI 批量翻译未返回有效的JSON对象，而是返回了: {type(translated_dict)}")
                    return {}
                
                logger.debug(f"(智谱AI) 批量翻译成功，返回 {len(translated_dict)} 个结果。")
                return translated_dict
            else:
                logger.error("智谱AI 未按预期调用 'translation_output' 工具。")
                return {}

        except Exception as e:
            logger.error(f"调用 智谱AI API 进行批量翻译时发生未知错误: {e}", exc_info=True)
            return {}