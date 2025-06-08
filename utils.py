# utils.py
import re
from typing import Optional, List, Dict, Any
from urllib.parse import quote_plus
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
    """检查字符串是否包含中文字符。"""
    if not text:
        return False
    for char in text:
        # 基本的 Unicode 中文字符范围判断
        # CJK Unified Ideographs: U+4E00 to U+9FFF
        # CJK Compatibility Ideographs: U+F900 to U+FAFF
        # CJK Unified Ideographs Extension A: U+3400 to U+4DBF
        # 还有其他扩展区，但这些是核心
        if '\u4e00' <= char <= '\u9fff' or \
           '\u3400' <= char <= '\u4dbf' or \
           '\uf900' <= char <= '\ufaff':
            return True
    return False

def clean_character_name_static(character_name: Optional[str]) -> str:
    """
    清理角色名，移除常见的前缀、后缀，并处理中英混合及国家代码的情况。
    """
    if not character_name:
        return ""
    
    name = str(character_name).strip()
    
    # 1. 移除复杂的后缀模式，如 (voice UK)
    # 这个模式已经包含了 (voice XX) 的情况
    country_code_pattern = r'\s*\((?:voice|配音)[,\s]*[A-Z]{2}\)\s*$'
    name = re.sub(country_code_pattern, '', name, flags=re.IGNORECASE).strip()

    # 2. ✨✨✨ 修复核心：集中处理所有已知的前缀 ✨✨✨
    # 按照从长到短的顺序检查，防止 "配音" 被 "配" 错误处理
    prefixes_to_remove = ["饰 ", "饰", "配音 ", "配音", "配 "]
    # 循环检查，直到没有前缀匹配为止，这可以处理 "饰 配音 角色名" 这样的情况
    cleaned_in_loop = True
    while cleaned_in_loop:
        cleaned_in_loop = False
        for prefix in prefixes_to_remove:
            if name.lower().startswith(prefix): # 使用 lower() 使匹配不区分大小写（如果需要）
                name = name[len(prefix):].strip()
                cleaned_in_loop = True
                break # 找到并移除一个前缀后，重新开始循环

    # 3. 处理中英混合的核心逻辑 (例如 "中文名 / English Name")
    match = re.match(r'^([\u4e00-\u9fa5·\s]+)[\s/]+[a-zA-Z0-9\s\.]+$', name)
    if match:
        name = match.group(1).strip()

    # 再次处理简单的斜杠分隔
    if '/' in name:
        name = name.split('/')[0].strip()
        
    # 4. 移除通用的后缀标记 (voice), [voice], (v.o.), 配音
    # 注意：这里的 '配音' 和 '配' 只作为后缀处理
    voice_patterns_suffix = [
        r'\s*\((?:voice|v\.o\.)\)\s*$', # 整合了 (voice) 和 (v.o.)
        r'\s*\[voice\]\s*$',
        r'\s*配\s*音\s*$', # 只在结尾时移除
        r'\s*配\s*$',      # 只在结尾时移除
    ]
    for pattern in voice_patterns_suffix:
        name = re.sub(pattern, '', name, flags=re.IGNORECASE).strip()
        
    return name.strip()

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

def generate_search_url(site: str, title: str, year: Optional[int] = None) -> str:
    """
    为指定网站生成搜索链接。
    
    :param site: 'google' 或 'baidu'
    :param title: 影视标题
    :param year: 影视年份 (可选)
    :return: 完整的搜索 URL
    """
    query = f"{title} {year}" if year else title
    # 添加 " 演员表" 或 " cast" 可以获得更精确的结果
    query += " 演员表 cast"
    
    encoded_query = quote_plus(query)
    
    if site == 'google':
        return f"https://www.google.com/search?q={encoded_query}"
    elif site == 'baidu':
        return f"https://www.baidu.com/s?wd={encoded_query}"
    else:
        raise ValueError(f"不支持的网站: {site}")

if __name__ == '__main__':
    # 测试 contains_chinese
    print(f"'Hello': {contains_chinese('Hello')}")  # False
    print(f"'你好': {contains_chinese('你好')}")    # True
    print(f"'Hello 你好': {contains_chinese('Hello 你好')}") # True
    print(f"None: {contains_chinese(None)}")      # False
    print(f"Empty string: {contains_chinese('')}") # False

    # 测试 clean_character_name_static
    test_roles = [
        "饰 蝙蝠侠 / 布鲁斯·韦恩", "饰蝙蝠侠", "蝙蝠侠 (voice)", "蝙蝠侠 [VOICE]",
        "超人 (v.o.)", "神奇女侠", None, "", "  饰 小丑  ", "配音", "饰 配音", "饰 钢铁侠 配音"
    ]
    for role in test_roles:
        print(f"Original: '{role}' -> Cleaned: '{clean_character_name_static(role)}'")