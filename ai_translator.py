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
# ★★★ 说明书一：给“翻译官”看的（翻译模式） ★★★
FAST_MODE_SYSTEM_PROMPT = """
You are a translation API that only returns JSON.
Your task is to translate a list of English terms into Chinese.
You MUST return a single, valid JSON object mapping each original English term to its Chinese translation.
If a term cannot be translated, use the original term as its value.
Do not add any explanations or text outside the JSON object.
"""

# ★★★ 说明书二：给“影视顾问”看的（顾问模式） ★★★
QUALITY_MODE_SYSTEM_PROMPT = """
You are a world-class film and television expert, acting as a JSON-only API.
Your mission is to accurately translate English or Pinyin names of actors and characters into standard Chinese, using the provided movie/series context.

**Input Format:**
You will receive a JSON object with `context` (containing `title` and `year`) and `terms` (a list of strings to translate).

**Your Strategy:**
1.  **Use Context:** Use the `title` and `year` to identify the show. Find the official or most recognized Chinese translation for the `terms` in that specific show's context. This is crucial for character names.
2.  **Translate Pinyin:** If a term is Pinyin (e.g., "Zhang San"), translate it to Chinese characters ("张三").
3.  **Fallback:** If a term cannot or should not be translated, you MUST use the original string as its value.

**Output Format (MANDATORY):**
You MUST return a single, valid JSON object mapping each original term to its Chinese translation. NO other text or markdown.
"""
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

    # --- ✨✨✨ 翻译调度 ✨✨✨ ---
    def batch_translate(self, 
                        texts: List[str], 
                        mode: str = 'fast', # 新增一个“模式”参数，默认是“快速度”
                        title: Optional[str] = None, 
                        year: Optional[int] = None) -> Dict[str, str]:
        
        if not texts: 
            return {}
        
        unique_texts = list(set(texts))
        
        # 调度员开始看指令
        if mode == 'quality':
            # 如果指令是“高质量”，就喊“顾问组”来干活
            logger.info(f"[顾问模式] 开始上下文翻译 {len(unique_texts)} 个词条...")
            return self._translate_quality_mode(unique_texts, title, year)
        else:
            # 其他所有情况（包括默认的'fast'），都喊“翻译组”来干活
            logger.info(f"[翻译模式] 开始快速翻译 {len(unique_texts)} 个词条...")
            return self._translate_fast_mode(unique_texts)
    # ★★★ “翻译快做”小组长 ★★★
    def _translate_fast_mode(self, texts: List[str]) -> Dict[str, str]:
        # 小组长根据公司（provider）选择不同的员工干活
        if self.provider == 'openai':
            return self._fast_openai(texts)
        elif self.provider == 'zhipuai':
            return self._fast_zhipuai(texts)
        elif self.provider == 'gemini':
            return self._fast_gemini(texts)
        else:
            logger.error(f"未知的提供商: {self.provider}")
            return {}

    # ★★★ “顾问精做”小组长 ★★★
    def _translate_quality_mode(self, texts: List[str], title: Optional[str], year: Optional[int]) -> Dict[str, str]:
        # 小组长根据公司（provider）选择不同的员工干活
        if self.provider == 'openai':
            return self._quality_openai(texts, title, year)
        elif self.provider == 'zhipuai':
            return self._quality_zhipuai(texts, title, year)
        elif self.provider == 'gemini':
            return self._quality_gemini(texts, title, year)
        else:
            logger.error(f"未知的提供商: {self.provider}")
            return {}
    # --- 底层员工：具体实现各种模式和提供商的组合 ---
    # --- OpenAI 员工 ---
    def _fast_openai(self, texts: List[str]) -> Dict[str, str]:
        if not self.client: return {}
        system_prompt = FAST_MODE_SYSTEM_PROMPT
        user_prompt = json.dumps(texts, ensure_ascii=False)
        try:
            chat_completion = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.0,
                response_format={"type": "json_object"},
                timeout=300
            )
            response_content = chat_completion.choices[0].message.content
            return json.loads(response_content)
        except Exception as e:
            logger.error(f"[翻译模式-OpenAI] 翻译时发生错误: {e}", exc_info=True)
            return {}

    def _quality_openai(self, texts: List[str], title: Optional[str], year: Optional[int]) -> Dict[str, str]:
        if not self.client: return {}
        system_prompt = QUALITY_MODE_SYSTEM_PROMPT
        user_payload = {"context": {"title": title, "year": year}, "terms": texts}
        user_prompt = json.dumps(user_payload, ensure_ascii=False)
        try:
            chat_completion = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.0,
                response_format={"type": "json_object"},
                timeout=300
            )
            response_content = chat_completion.choices[0].message.content
            return json.loads(response_content)
        except Exception as e:
            logger.error(f"[顾问模式-OpenAI] 翻译时发生错误: {e}", exc_info=True)
            return {}

    # --- 智谱AI 员工 ---
    def _fast_zhipuai(self, texts: List[str]) -> Dict[str, str]:
        if not self.client: return {}
        system_prompt = FAST_MODE_SYSTEM_PROMPT
        user_prompt = json.dumps(texts, ensure_ascii=False)
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.0,
                response_format={"type": "json_object"}
            )
            response_content = response.choices[0].message.content
            return json.loads(response_content)
        except Exception as e:
            logger.error(f"[翻译模式-智谱AI] 翻译时发生错误: {e}", exc_info=True)
            return {}

    def _quality_zhipuai(self, texts: List[str], title: Optional[str], year: Optional[int]) -> Dict[str, str]:
        if not self.client: return {}
        system_prompt = QUALITY_MODE_SYSTEM_PROMPT
        user_payload = {"context": {"title": title, "year": year}, "terms": texts}
        user_prompt = json.dumps(user_payload, ensure_ascii=False)
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.0,
                response_format={"type": "json_object"}
            )
            response_content = response.choices[0].message.content
            return json.loads(response_content)
        except Exception as e:
            logger.error(f"[顾问模式-智谱AI] 翻译时发生错误: {e}", exc_info=True)
            return {}

    # --- Gemini 员工 ---
    def _fast_gemini(self, texts: List[str]) -> Dict[str, str]:
        if not self.client: return {}
        system_prompt = FAST_MODE_SYSTEM_PROMPT
        user_prompt = json.dumps(texts, ensure_ascii=False)
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
            return json.loads(response.text)
        except Exception as e:
            logger.error(f"[翻译模式-Gemini] 翻译时发生错误: {e}", exc_info=True)
            return {}

    def _quality_gemini(self, texts: List[str], title: Optional[str], year: Optional[int]) -> Dict[str, str]:
        if not self.client: return {}
        system_prompt = QUALITY_MODE_SYSTEM_PROMPT
        user_payload = {"context": {"title": title, "year": year}, "terms": texts}
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
            return json.loads(response.text)
        except Exception as e:
            logger.error(f"[顾问模式-Gemini] 翻译时发生错误: {e}", exc_info=True)
            return {}