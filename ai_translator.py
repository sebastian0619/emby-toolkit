# ai_translator.py
import json
from typing import Optional, Dict, Any, List
import logging

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
        self.base_url = config.get("ai_base_url")
        # 这个prompt现在只用于单文本翻译，作为向后兼容
        
        if not self.api_key:
            raise ValueError("AI Translator: API Key 未配置。")
            
        self.client = None
        self._initialize_client()

    def _initialize_client(self):
        """根据提供商初始化对应的客户端"""
        try:
            if self.provider == 'openai':
                if not OPENAI_AVAILABLE: raise ImportError("OpenAI SDK 未安装")
                self.client = OpenAI(api_key=self.api_key, base_url=self.base_url if self.base_url else None)
                logger.info(f"OpenAI 初始化成功")
            
            elif self.provider == 'zhipuai':
                if not ZHIPUAI_AVAILABLE: raise ImportError("智谱AI SDK 未安装")
                self.client = ZhipuAI(api_key=self.api_key)
                logger.info(f"智谱AI 初始化成功")
            
            elif self.provider == 'gemini':
                if not GEMINI_AVAILABLE: raise ImportError("Google Gemini SDK 未安装")
                genai.configure(api_key=self.api_key)
                self.client = genai.GenerativeModel(self.model)
                logger.info(f"Google Gemini 初始化成功")

            else:
                raise ValueError(f"不支持的AI提供商: {self.provider}")
        except Exception as e:
            logger.error(f"{self.provider.capitalize()} client 初始化失败: {e}")
            raise

    # --- 单文本翻译 (保留，但内部可以调用批量方法以统一逻辑) ---
    def translate(self, text: str) -> Optional[str]:
        if not text or not text.strip():
            return text
        
        # 单文本翻译现在可以简单地调用批量翻译，代码更简洁
        # 如果翻译失败，返回原文
        batch_result = self.batch_translate([text])
        return batch_result.get(text, text)

    # --- ✨✨✨【新增】批量翻译核心方法✨✨✨ ---
    def batch_translate(self, 
                        texts: List[str], 
                        title: Optional[str] = None, 
                        year: Optional[int] = None) -> Dict[str, str]:
        if not texts: return {}
        
        unique_texts = list(set(texts))
        
        # 构造日志信息，包含上下文
        context_log = f" (上下文: {title}"
        if year:
            context_log += f" {year}"
        context_log += ")" if title else ""
        
        logger.info(f"开始批量翻译 {len(unique_texts)} 个独立词条 (提供商: {self.provider}){context_log}...")

        # 总调度室，将上下文信息传递给具体实现
        if self.provider == 'openai':
            return self._batch_translate_with_openai(unique_texts, title, year)
        elif self.provider == 'zhipuai':
            return self._batch_translate_with_zhipuai(unique_texts, title, year)
        elif self.provider == 'gemini':
            return self._batch_translate_with_gemini(unique_texts, title, year)
        else:
            logger.error(f"没有为提供商 '{self.provider}' 实现批量翻译方法。")
            return {}

    # --- ✨✨✨【核心改造】重写 System Prompt，并适配所有实现✨✨✨ ---
    def _get_system_prompt(self) -> str:
        """
        生成统一的、面向“影视顾问”角色的系统提示词。
        """
        return """
You are a world-class film and television expert, acting as a JSON-only API. Your primary goal is to accurately identify and translate English or Pinyin names of actors and characters into standard Chinese, leveraging the provided movie/series context.

**Your Task & Strict Rules:**

1.  **Input Format:** You will receive a JSON object containing:
    -   `context`: An object with `title` and `year` of the movie/series.
    -   `terms`: A JSON array of strings (names/roles) to be translated.

2.  **Your Core Mission (Translation Strategy):**
    -   **Step 1: Contextual Lookup (Highest Priority):** Use the `title` and `year` to identify the specific film or TV show. First, try to find the **official or most recognized Chinese translation** for the `terms` within the context of that specific show. This is crucial for character names that are common words (e.g., "Riddler" in "The Batman" vs. a generic "riddler").
    -   **Step 2: Pinyin/Romanization Translation:** If a term is clearly Pinyin or another romanization of a Chinese name (e.g., "Yoon Se-ri", "Zhang San"), translate it into the correct Chinese characters ("尹世理", "张三"). This is a major pain point to solve.
    -   **Step 3: Standard Translation:** If the above steps fail, perform a high-quality, standard translation for famous individuals or general terms (e.g., "Peter Parker" -> "彼得·帕克", "The Night King" -> "夜王").
    -   **Step 4: Preserve Context:** For mixed content (e.g., "Maj. Sophie E. Jean"), translate correctly while preserving titles ("苏菲·E·让少校").
    -   **Step 5: Fallback:** If a term cannot or should not be translated (e.g., it's already in Chinese, or it's a nonsensical string), you **MUST** use the original string as its value in the output.

3.  **Output Format (ABSOLUTELY MANDATORY):**
    -   You MUST return a single, valid JSON object that maps each original string from the `terms` array to its Chinese translation.
    -   DO NOT add any explanations, introductory text, markdown formatting (`json` tags), or any text outside of the final JSON object. Your entire response must be only the JSON object itself.

**Example:**
User Input:
```json
{
  "context": {
    "title": "The Batman",
    "year": 2022
  },
  "terms": [
    "Riddler",
    "Zhang San",
    "The Night King"
  ]
}
{
  "Riddler": "谜语人",
  "Zhang San": "张三",
  "The Night King": "夜王"
}
"""
    def _batch_translate_with_openai(self, texts: List[str], title: Optional[str], year: Optional[int]) -> Dict[str, str]:
        if not self.client: return {}
        # OpenAI 的实现保持分块逻辑，因为即使上下文不大，输入token也有限制
        chunk_size = 50
        all_translated_results = {}
        text_chunks = [texts[i:i + chunk_size] for i in range(0, len(texts), chunk_size)]
        if len(text_chunks) > 1: logger.info(f"数据量过大 ({len(texts)} > {chunk_size})，已自动分块。")
        
        system_prompt = self._get_system_prompt()

        for i, chunk in enumerate(text_chunks):
            if len(text_chunks) > 1: logger.info(f"--- 正在处理批次 {i + 1}/{len(text_chunks)} ---")
            
            # 构建包含上下文的 User Prompt
            user_payload = {
                "context": {"title": title, "year": year},
                "terms": chunk
            }
            user_prompt = json.dumps(user_payload, ensure_ascii=False)

            try:
                chat_completion = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    temperature=0.0,
                    response_format={"type": "json_object"}, # 请求JSON输出，更可靠
                    timeout=300, 
                )
                # ... (后续的错误处理和解析逻辑与之前基本一致) ...
                response_content = chat_completion.choices[0].message.content
                translated_dict = json.loads(response_content)
                all_translated_results.update(translated_dict)

            except Exception as e:
                logger.error(f"批次 {i + 1} (OpenAI) 发生错误: {e}", exc_info=True)
                continue
        
        logger.info(f"所有批次处理完成，总共成功翻译 {len(all_translated_results)} 个词条。")
        return all_translated_results

    def _batch_translate_with_zhipuai(self, texts: List[str], title: Optional[str], year: Optional[int]) -> Dict[str, str]:
        # 智谱AI的实现可以一次性处理，因为它通常支持更长的上下文
        if not self.client: return {}
        
        system_prompt = self._get_system_prompt()
        user_payload = {
            "context": {"title": title, "year": year},
            "terms": texts
        }
        user_prompt = json.dumps(user_payload, ensure_ascii=False)

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.0,
                # 请求JSON模式，让模型直接返回JSON字符串
                response_format={"type": "json_object"}
            )
            response_content = response.choices[0].message.content
            translated_dict = json.loads(response_content)
            return translated_dict
        except Exception as e:
            logger.error(f"调用 智谱AI API 进行批量翻译时发生未知错误: {e}", exc_info=True)
            return {}

    def _batch_translate_with_gemini(self, texts: List[str], title: Optional[str], year: Optional[int]) -> Dict[str, str]:
        # Gemini 的实现也通常可以一次性处理
        if not self.client: return {}
        
        system_prompt = self._get_system_prompt()
        user_payload = {
            "context": {"title": title, "year": year},
            "terms": texts
        }
        user_prompt = json.dumps(user_payload, ensure_ascii=False)
        
        generation_config = genai.types.GenerationConfig(
            response_mime_type="application/json",
            temperature=0.0
        )

        try:
            response = self.client.generate_content(
                [system_prompt, user_prompt],
                generation_config=generation_config,
                request_options={'timeout': 300}
            )
            response_text = response.text
            translated_dict = json.loads(response_text)
            return translated_dict
        except Exception as e:
            logger.error(f"调用 Gemini API 进行批量翻译时发生错误: {e}", exc_info=True)
            return {}