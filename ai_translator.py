# ai_translator.py
import json
from typing import Optional, Dict, Any, List
import logging
import re # ✨ 1. 引入 re 模块，用于清理 Gemini 的返回

logger = logging.getLogger(__name__)

# --- 动态导入所有需要的 SDK ---
try:
    from openai import OpenAI, APIError, APITimeoutError
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

try:
    from zhipuai import ZhipuAI
    ZHIPUAI_AVAILABLE = True
except ImportError:
    ZHIPUAI_AVAILABLE = False

# ✨ 2. 动态导入 Gemini 的 SDK ✨
try:
    import google.generativeai as genai
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False

class AITranslator:
    def __init__(self, config: Dict[str, Any]):
        self.provider = config.get("ai_provider", "openai").lower()
        self.api_key = config.get("ai_api_key")
        self.model = config.get("ai_model_name")
        self.base_url = config.get("ai_base_url") # Gemini 通常不需要 base_url
        
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
                logger.info(f"OpenAI 初始化成功")
            except Exception as e:
                logger.error(f"OpenAI client 初始化失败: {e}")
                raise
        
        elif self.provider == 'zhipuai':
            if not ZHIPUAI_AVAILABLE:
                raise ImportError("智谱AI SDK 未安装，请运行 'pip install zhipuai'")
            try:
                self.client = ZhipuAI(api_key=self.api_key)
                logger.info(f"智谱AI 初始化成功")
            except Exception as e:
                logger.error(f"智谱AI client 初始化失败: {e}")
                raise
        
        # ✨ 3. 增加 Gemini 的初始化逻辑 ✨
        elif self.provider == 'gemini':
            if not GEMINI_AVAILABLE:
                raise ImportError("Google Gemini SDK 未安装，请运行 'pip install google-generativeai'")
            try:
                genai.configure(api_key=self.api_key)
                # Gemini 的 "client" 就是它的模型对象
                self.client = genai.GenerativeModel(self.model)
                logger.info(f"Google Gemini 初始化成功 (模型: {self.model})")
            except Exception as e:
                logger.error(f"Google Gemini client 初始化失败: {e}")
                raise

        else:
            raise ValueError(f"不支持的AI提供商: {self.provider}")

    # --- 单文本翻译 (保留，但内部可以调用批量方法以统一逻辑) ---
    def translate(self, text: str) -> Optional[str]:
        if not text or not text.strip():
            return text
        
        batch_result = self.batch_translate([text])
        return batch_result.get(text, text)

    # --- 批量翻译核心方法 ---
    def batch_translate(self, texts: List[str]) -> Dict[str, str]:
        """
        批量翻译一个字符串列表。
        """
        if not texts:
            return {}
        
        unique_texts = list(set(texts))
        logger.info(f"开始批量翻译 {len(unique_texts)} 个独立词条 (使用引擎: {self.provider})...")

        # ✨ 4. 在主分发逻辑中加入 Gemini ✨
        if self.provider == 'openai':
            return self._batch_translate_with_openai(unique_texts)
        elif self.provider == 'zhipuai':
            return self._batch_translate_with_zhipuai(unique_texts)
        elif self.provider == 'gemini':
            return self._batch_translate_with_gemini(unique_texts)
        else:
            logger.error(f"没有为提供商 '{self.provider}' 实现批量翻译方法。")
            return {}

    # --- OpenAI 的批量翻译实现 (保持不变) ---
    def _batch_translate_with_openai(self, texts: List[str]) -> Dict[str, str]:
        if not self.client: return {}

        chunk_size = 50
        all_translated_results = {}

        # 智能判断是否需要分块和打印相应日志
        if len(texts) > chunk_size:
            logger.info(f"数据量过大 ({len(texts)} > {chunk_size})，已自动分块。")
            text_chunks = [texts[i:i + chunk_size] for i in range(0, len(texts), chunk_size)]
        else:
            text_chunks = [texts]

        total_chunks = len(text_chunks)

        for i, chunk in enumerate(text_chunks):
            # 智能判断是否需要打印批次进度日志
            if total_chunks > 1:
                logger.info(f"--- 正在处理批次 {i + 1}/{total_chunks} ---")

            # ✨✨✨ 完整的、无法违抗的终极提示词 ✨✨✨
            system_prompt = """
    You are a professional film and television translation expert, acting as a JSON-only API. Your primary goal is to translate English names and roles into Chinese, adhering to the common practices of the Chinese-speaking film community.

    **Your Task & Strict Rules:**
    1.  You will receive a JSON array of English strings as input.
    2.  You MUST return a single, valid JSON object that maps each original English string to its Chinese translation.
    3.  **Translation Quality:**
        -   For famous people or characters, use the most common, official, or universally accepted Chinese names (e.g., "Peter Parker" -> "彼得·帕克").
        -   For pinyin or romanized names (e.g., "Yoon Se-ri", "Zhang San"), translate them into standard Chinese names (e.g., "尹世理", "张三").
        -   For mixed content (e.g., "Maj. Sophie E. Jean"), translate correctly while preserving context like titles ("苏菲·E·让少校").
    4.  **Crucially:** If a string cannot or should not be translated, use the original English string as its value in the output JSON.
    5.  **Output Format:** DO NOT add any explanations, introductory text, markdown formatting, or any text outside of the final JSON object. Your response MUST be only the JSON object itself.

    **Example:**
    User Input:
    ["Peter Parker", "The Night King", "Maj. Sophie E. Jean"]

    Your Output (MUST be in this exact format):
    {
    "Peter Parker": "彼得·帕克",
    "The Night King": "夜王",
    "Maj. Sophie E. Jean": "苏菲·E·让少校"
    }
    """
            user_prompt = json.dumps(chunk, ensure_ascii=False)

            try:
                chat_completion = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    temperature=0.0,
                    timeout=300, 
                )

                if not chat_completion.choices:
                    logger.error(f"批次 {i + 1} 返回了空的 choices 列表，跳过此批次。")
                    continue

                choice = chat_completion.choices[0]
                response_content = choice.message.content

                if choice.finish_reason == 'content_filter':
                    logger.error(f"批次 {i + 1} 因触发内容安全策略而被拦截。跳过此批次。")
                    logger.warning(f"  -> 被拦截的批次内容: {chunk}")
                    continue

                if not response_content:
                    logger.error(f"批次 {i + 1} 返回了空的内容。跳过此批次。")
                    continue

                translated_dict = json.loads(response_content)

                if not isinstance(translated_dict, dict):
                    logger.error(f"批次 {i + 1} 翻译未返回有效的JSON对象，跳过此批次。")
                    continue

                all_translated_results.update(translated_dict)

                # 智能成功日志
                if total_chunks > 1:
                    logger.info(f"批次 {i + 1} 处理成功，已翻译 {len(translated_dict)} 个词条。")

            except json.JSONDecodeError as e:
                logger.error(f"批次 {i + 1} 发生JSON解析错误: {e}。跳过此批次。")
                logger.warning(f"  -> 无法解析的原始响应内容: '{response_content}'")
                continue
            except APIError as e:
                logger.error(f"批次 {i + 1} 发生API错误: Status {e.status_code} - {e.message}。跳过此批次。")
                continue
            except APITimeoutError as e:
                logger.error(f"批次 {i + 1} 请求超时: {e}。跳过此批次。")
                continue
            except Exception as e:
                logger.error(f"批次 {i + 1} 发生未知错误: {e}。跳过此批次。", exc_info=True)
                continue

        logger.info(f"所有批次处理完成，总共成功翻译 {len(all_translated_results)} 个词条。")
        return all_translated_results

    def _batch_translate_with_zhipuai(self, texts: List[str]) -> Dict[str, str]:
        if not self.client: return {}

        # 智谱AI的Prompt可以和OpenAI保持一致
        system_prompt = """
    You are a professional film and television translation expert, acting as a JSON-only API. Your primary goal is to translate English names and roles into Chinese, adhering to the common practices of the Chinese-speaking film community.

    **Your Task & Strict Rules:**
    1.  You will receive a JSON array of English strings as input.
    2.  You MUST return a single, valid JSON object that maps each original English string to its Chinese translation.
    3.  **Translation Quality:**
        -   For famous people or characters, use the most common, official, or universally accepted Chinese names (e.g., "Peter Parker" -> "彼得·帕克").
        -   For pinyin or romanized names (e.g., "Yoon Se-ri", "Zhang San"), translate them into standard Chinese names (e.g., "尹世理", "张三").
        -   For mixed content (e.g., "Maj. Sophie E. Jean"), translate correctly while preserving context like titles ("苏菲·E·让少校").
    4.  **Crucially:** If a string cannot or should not be translated, use the original English string as its value in the output JSON.
    5.  **Output Format:** DO NOT add any explanations, introductory text, markdown formatting, or any text outside of the final JSON object. Your response MUST be only the JSON object itself.

    **Example:**
    User Input:
    ["Peter Parker", "The Night King", "Maj. Sophie E. Jean"]

    Your Output (MUST be in this exact format):
    {
    "Peter Parker": "彼得·帕克",
    "The Night King": "夜王",
    "Maj. Sophie E. Jean": "苏菲·E·让少校"
    }
    """
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
                    logger.error(f"智谱AI 批量翻译未返回有效的JSON对象，而是返回了: {type(translated_dict)}")More actions
                    return {}

                logger.debug(f"(智谱AI) 批量翻译成功，返回 {len(translated_dict)} 个结果。")
                return translated_dict
            else:
                logger.error("智谱AI 未按预期调用 'translation_output' 工具。")
                return {}

        except Exception as e:
            logger.error(f"调用 智谱AI API 进行批量翻译时发生未知错误: {e}", exc_info=True)
        
    # ✨ 5. 新增 Gemini 的批量翻译实现 ✨
    def _batch_translate_with_gemini(self, texts: List[str]) -> Dict[str, str]:
        if not self.client: return {}

        # Gemini 的 Prompt 可以和 OpenAI 保持一致，它对 JSON 的遵循能力很强
        # 我们甚至可以简化一下，因为 Gemini 的 JSON 模式更直接
        system_prompt = """
You are a translation API that only returns JSON. Translate the given English film/TV names and roles into standard Chinese.
Your task is to return a single JSON object mapping each original English string to its Chinese translation.
If a string cannot be translated, use the original English string as its value.
Do not add any text outside of the final JSON object.

Example Input:
["Peter Parker", "The Night King"]

Example Output:
{
  "Peter Parker": "彼得·帕克",
  "The Night King": "夜王"
}
"""
        user_prompt = json.dumps(texts, ensure_ascii=False)
        
        # Gemini 的 GenerationConfig
        generation_config = genai.types.GenerationConfig(
            # 强制要求输出 JSON
            response_mime_type="application/json",
            temperature=0.0
        )

        try:
            # Gemini 的调用方式
            response = self.client.generate_content(
                [system_prompt, user_prompt],
                generation_config=generation_config
            )
            
            response_text = response.text
            
            # Gemini 有时会在 JSON 前后加上 ```json ... ```，需要清理
            # 使用正则表达式匹配被 ```json ... ``` 包裹的内容
            match = re.search(r"```json\s*([\s\S]*?)\s*```", response_text)
            if match:
                json_str = match.group(1)
            else:
                json_str = response_text

            translated_dict = json.loads(json_str)

            if not isinstance(translated_dict, dict):
                logger.error(f"Gemini 批量翻译未返回有效的JSON对象，而是返回了: {type(translated_dict)}")
                return {}
            
            logger.info(f"(Gemini) 批量翻译成功，返回 {len(translated_dict)} 个结果。")
            return translated_dict

        except json.JSONDecodeError as e:
            logger.error(f"Gemini 发生JSON解析错误: {e}。")
            logger.warning(f"  -> 无法解析的原始响应内容: '{response.text}'")
            return {}
        except Exception as e:
            logger.error(f"调用 Google Gemini API 进行批量翻译时发生未知错误: {e}", exc_info=True)
            return {}

# --- 为了让代码块能独立运行，我把之前省略的代码补回来 ---
    def _batch_translate_with_openai(self, texts: List[str]) -> Dict[str, str]:
        if not self.client: return {}
        chunk_size = 50
        all_translated_results = {}
        if len(texts) > chunk_size:
            logger.info(f"数据量过大 ({len(texts)} > {chunk_size})，已自动分块。")
            text_chunks = [texts[i:i + chunk_size] for i in range(0, len(texts), chunk_size)]
        else:
            text_chunks = [texts]
        total_chunks = len(text_chunks)
        for i, chunk in enumerate(text_chunks):
            if total_chunks > 1:
                logger.info(f"--- 正在处理批次 {i + 1}/{total_chunks} ---")
            system_prompt = "..." # 省略和原来一样的prompt
            user_prompt = json.dumps(chunk, ensure_ascii=False)
            try:
                chat_completion = self.client.chat.completions.create(model=self.model, messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}], temperature=0.0, timeout=300)
                if not chat_completion.choices: continue
                choice = chat_completion.choices[0]
                response_content = choice.message.content
                if choice.finish_reason == 'content_filter': continue
                if not response_content: continue
                translated_dict = json.loads(response_content)
                if not isinstance(translated_dict, dict): continue
                all_translated_results.update(translated_dict)
                if total_chunks > 1: logger.info(f"批次 {i + 1} 处理成功。")
            except Exception as e:
                logger.error(f"批次 {i + 1} 发生错误: {e}。跳过此批次。", exc_info=True)
                continue
        logger.info(f"所有批次处理完成，总共成功翻译 {len(all_translated_results)} 个词条。")
        return all_translated_results

    def _batch_translate_with_zhipuai(self, texts: List[str]) -> Dict[str, str]:
        if not self.client: return {}
        system_prompt = "..." # 省略和原来一样的prompt
        user_prompt = f"请翻译以下条目：\n{json.dumps(texts, ensure_ascii=False)}"
        try:
            response = self.client.chat.completions.create(model=self.model, messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}], temperature=0.0, tool_choice="auto", tools=[{"type": "function", "function": {"name": "translation_output", "description": "The JSON object containing all translations.", "parameters": {"type": "object", "properties": {"translations": {"type": "object", "description": "A dictionary mapping original English text to its Chinese translation."}}, "required": ["translations"]}}}])
            tool_call = response.choices[0].message.tool_calls[0]
            if tool_call.function.name == "translation_output":
                arguments = json.loads(tool_call.function.arguments)
                translated_dict = arguments.get("translations", {})
                if not isinstance(translated_dict, dict): return {}
                return translated_dict
            else:
                return {}
        except Exception as e:
            logger.error(f"调用 智谱AI API 进行批量翻译时发生未知错误: {e}", exc_info=True)
            return {}