# utils.py
import re
from typing import Optional, List, Dict, Any

# 假设你的日志记录器已经设置好，并且可以全局导入
# from logger_setup import logger
# 如果没有全局 logger，你可能需要在这里初始化一个简单的 logger 或从其他地方传入
# 为了简单，我这里假设 logger 存在或暂时不用它打印 utils 内部的日志

# 假设你使用的翻译库是 'translators'
# 如果不是，你需要修改下面的导入和调用
try:
    from translators import translate_text as translators_translate_text
    # from translators import apis as translators_apis # 如果需要检查引擎是否存在
    TRANSLATORS_LIB_AVAILABLE = True
except ImportError:
    TRANSLATORS_LIB_AVAILABLE = False
    def translators_translate_text(*args, **kwargs): # 模拟一个空函数
        # print("[UTILS_WARN] 'translators' 库未安装，翻译功能不可用。")
        raise NotImplementedError("translators 库未安装")

# 你在 core_processor.py 中用到的常量，如果它们在 constants.py 中定义，确保能访问
# 例如： DEFAULT_TRANSLATOR_ENGINES_ORDER = ['bing', 'google'] # 来自 constants.py

def contains_chinese(text: Optional[str]) -> bool:
    """检查字符串是否包含中文字符"""
    if not text:
        return False
    for char in text:
        if '\u4e00' <= char <= '\u9fff':
            return True
    return False

def clean_character_name_static(character_name: Optional[str]) -> str:
    """
    静态辅助函数：移除角色名前的 '饰 ' 或 '饰' 及前后空格。
    同时处理 "角色名1 / 角色名2" 的情况，只取第一个。
    并移除角色名末尾的 "(voice)" 或类似配音标记（如果翻译后需要中文配音标记，则在翻译后添加）。
    """
    if not character_name:
        return ""
    name = str(character_name).strip()

    # 移除 "饰 " 或 "饰"
    if name.startswith("饰 "):
        name = name[2:].strip()
    elif name.startswith("饰"):
        name = name[1:].strip()

    # 处理 "角色名1 / 角色名2"，只取第一个
    if '/' in name:
        name = name.split('/')[0].strip()

    # 移除末尾的常见配音标记 (英文)
    # 注意：如果希望翻译后有中文配音标记，例如 " (配音)"，
    # 那么这个移除应该在翻译前做，翻译后再根据需要添加中文标记。
    # 或者，如果翻译引擎能智能处理，也可以保留。
    # 为简单起见，这里先移除常见的英文配音标记。
    voice_patterns = [
        r'\s*\((voice|Voice|VOICE)\)\s*$',
        r'\s*\[voice\]\s*$',
        r'\s*\(v\.o\.\)\s*$',
        r'\s*\(V\.O\.\)\s*$',
    ]
    for pattern in voice_patterns:
        name = re.sub(pattern, '', name, flags=re.IGNORECASE).strip()

    return name

def translate_text_with_translators(
    query_text: str,
    to_language: str = 'zh', # 目标语言通常是中文 'zh' 或 'zh-CN'
    engine_order: Optional[List[str]] = None,
    from_language: str = 'auto' # 源语言自动检测
) -> Optional[Dict[str, str]]:
    """
    使用指定的翻译引擎顺序尝试翻译文本。
    返回一个包含翻译结果和所用引擎的字典，或在失败时返回None。
    返回格式: {"text": "翻译后的文本", "engine": "使用的引擎名"}
    """
    if not query_text or not query_text.strip():
        # print("[UTILS_DEBUG] translate_text_with_translators: query_text 为空，跳过翻译。")
        return None

    if not TRANSLATORS_LIB_AVAILABLE:
        print("[UTILS_ERROR] 'translators' 库不可用，无法执行翻译。")
        return None

    if engine_order is None or not engine_order:
        # print("[UTILS_WARN] translate_text_with_translators: 未提供翻译引擎顺序，使用默认。")
        engine_order = ['bing', 'google', 'baidu'] # 确保有默认值

    # print(f"[UTILS_INFO] 翻译 '{query_text}': 使用引擎顺序 {engine_order}")

    for engine_name in engine_order:
        # print(f"[UTILS_DEBUG] 尝试引擎 [{engine_name}] 翻译: '{query_text}'")
        try:
            # 实际调用 translators 库
            # 注意：你需要确认你的 translators 库版本和用法
            # to_language='zh' 或 'zh-CN' 或 'zh-Hans' 取决于库和引擎支持
            translated_text = translators_translate_text(
                query_text,
                translator=engine_name,
                to_language=to_language,
                from_language=from_language, # 'auto' 通常是默认值
                timeout=10.0 # 设置超时
            )

            if translated_text and translated_text.strip() and translated_text.strip().lower() != query_text.strip().lower():
                # print(f"[UTILS_SUCCESS] 引擎 [{engine_name}] 成功: '{query_text}' -> '{translated_text}'")
                return {"text": translated_text.strip(), "engine": engine_name}
            elif translated_text and translated_text.strip().lower() == query_text.strip().lower():
                # print(f"[UTILS_INFO] 引擎 [{engine_name}] 返回结果与原文相同: '{query_text}'")
                # 可以选择视为翻译失败并尝试下一个引擎，或者接受这个结果
                # 如果接受，也应该返回引擎名
                # return {"text": translated_text.strip(), "engine": engine_name}
                continue # 视为无效翻译，尝试下一个
            else:
                # print(f"[UTILS_WARN] 引擎 [{engine_name}] 返回空结果 for '{query_text}'")
                continue # 尝试下一个引擎

        except Exception as e:
            # print(f"[UTILS_WARN] 引擎 [{engine_name}] 翻译 '{query_text}' 失败: {e}")
            continue # 尝试下一个引擎

    # print(f"[UTILS_ERROR] 所有引擎都未能翻译: '{query_text}'")
    return None # 所有引擎都失败了

# 你可能还有其他辅助函数，比如格式化演员名、角色名等
def format_actor_display_name(name_cn: Optional[str], name_en: Optional[str]) -> str:
    if name_cn and name_en and name_cn != name_en:
        return f"{name_cn} ({name_en})"
    elif name_cn:
        return name_cn
    elif name_en:
        return name_en
    return "未知演员"

def format_character_display_name(char_cn: Optional[str], char_en: Optional[str]) -> str:
    # 角色名通常不需要同时显示中英文，除非特殊需求
    if char_cn:
        return char_cn
    elif char_en: # 如果没有中文，显示英文
        return char_en
    return "" # 或者 "未知角色"

if __name__ == '__main__':
    # 测试 contains_chinese
    print(f"'Hello World' contains chinese: {contains_chinese('Hello World')}")
    print(f"'你好，世界' contains chinese: {contains_chinese('你好，世界')}")

    # 测试 clean_character_name_static
    print(f"Clean '饰 小明': '{clean_character_name_static('饰 小明')}'")
    print(f"Clean '小红 (voice)': '{clean_character_name_static('小红 (voice)')}'")
    print(f"Clean '角色1 / 角色2 [VOICE]': '{clean_character_name_static('角色1 / 角色2 [VOICE]')}'")

    # 测试翻译 (前提是 translators 库已安装且网络通畅)
    if TRANSLATORS_LIB_AVAILABLE:
        test_text = "Hello World"
        # 确保 constants.DEFAULT_TRANSLATOR_ENGINES_ORDER 在这里可用，或者硬编码一个列表
        default_engines = ['bing', 'google', 'baidu'] # 假设这是你的默认顺序
        translation_result = translate_text_with_translators(test_text, engine_order=default_engines)
        if translation_result:
            print(f"Translate '{test_text}': '{translation_result['text']}' (Engine: {translation_result['engine']})")
        else:
            print(f"Failed to translate '{test_text}'")

        test_text_jp = "こんにちは世界" # 日语
        translation_result_jp = translate_text_with_translators(test_text_jp, from_language='ja', engine_order=default_engines)
        if translation_result_jp:
            print(f"Translate '{test_text_jp}' (from ja): '{translation_result_jp['text']}' (Engine: {translation_result_jp['engine']})")
        else:
            print(f"Failed to translate '{test_text_jp}'")
    else:
        print("Skipping translation test as 'translators' library is not available.")