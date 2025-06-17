# utils.py (最终智能匹配版)

import re
from typing import Optional, List, Dict, Any
from urllib.parse import quote_plus
import unicodedata

# 尝试导入 pypinyin，如果失败则创建一个模拟函数
try:
    from pypinyin import pinyin, Style
    PYPINYIN_AVAILABLE = True
except ImportError:
    PYPINYIN_AVAILABLE = False
    def pinyin(*args, **kwargs):
        # 如果库不存在，这个模拟函数将导致中文名无法转换为拼音进行匹配
        return []

# 尝试导入 translators
try:
    from translators import translate_text as translators_translate_text
    TRANSLATORS_LIB_AVAILABLE = True
except ImportError:
    TRANSLATORS_LIB_AVAILABLE = False
    def translators_translate_text(*args, **kwargs):
        raise NotImplementedError("translators 库未安装")

def contains_chinese(text: Optional[str]) -> bool:
    """检查字符串是否包含中文字符。"""
    if not text:
        return False
    for char in text:
        if '\u4e00' <= char <= '\u9fff' or \
           '\u3400' <= char <= '\u4dbf' or \
           '\uf900' <= char <= '\ufaff':
            return True
    return False

def clean_character_name_static(character_name: Optional[str]) -> str:
    """
    【V3 - 终极清理版】清理和格式化角色名。
    """
    if not character_name:
        return ""
    
    name = str(character_name).strip()

    # --- 第一阶段：预处理，移除明确的垃圾信息 ---

    # 1. 移除括号及其内容，例如 "(voice)", "(uncredited)"
    name = re.sub(r'\(.*?\)|\[.*?\]', '', name).strip()
    
    # 2. 移除 "as " 前缀 (不区分大小写)
    if name.lower().startswith('as '):
        name = name[3:].strip()
        
    # 3. 移除 "饰 " 等中文前缀
    prefixes_to_remove = ["饰 ", "饰", "配音 ", "配音", "配 "]
    for prefix in prefixes_to_remove:
        if name.startswith(prefix):
            name = name[len(prefix):].strip()
            break # 找到一个就够了

    # 4. 处理 " / " 分隔符，只取第一个角色
    if ' / ' in name:
        name = name.split(' / ')[0].strip()

    # --- 第二阶段：核心处理，分离中英文 ---

    # ★★★ 核心升级：使用更强大的正则表达式来分离中英文 ★★★
    # 这个表达式寻找以“一个或多个中文字符”开头，后面可能紧跟着（或用空格隔开）其他任何字符的模式
    # 它会捕获开头的连续中文部分
    match = re.match(r'^([\u4e00-\u9fa5·]+)', name)
    
    if match:
        # 如果字符串以中文开头
        chinese_part = match.group(1).strip('· ')
        
        # 获取整个字符串中所有中文字符
        all_chinese_chars = re.findall(r'[\u4e00-\u9fa5]', name)
        
        # 如果开头的中文部分，就包含了整个字符串里几乎所有的中文字符
        # 这可以很好地处理 "绮莉 Kiri" -> "绮莉"
        # 以及 "洛克 Lo'ak" -> "洛克"
        # 同时避免错误处理 "彼得·潘" 这种本身就是完整中文译名的情况
        if len(chinese_part) >= len(all_chinese_chars) * 0.8:
             # 检查是否是像 "彼得·潘" 这样的名字，如果是，则保留
            if '·' in name and not re.search(r'[a-zA-Z]', name.split('·')[-1]):
                 return name # 保留 "彼得·潘"
            
            # 否则，我们确信开头的中文就是我们想要的角色名
            return chinese_part

    # 如果以上规则都不适用（比如纯英文名），返回清理过的原始字符串
    return name.strip()

def translate_text_with_translators(
    query_text: str,
    to_language: str = 'zh',
    engine_order: Optional[List[str]] = None,
    from_language: str = 'auto'
) -> Optional[Dict[str, str]]:
    """使用指定的翻译引擎顺序尝试翻译文本。"""
    if not query_text or not query_text.strip() or not TRANSLATORS_LIB_AVAILABLE:
        return None
    if engine_order is None:
        engine_order = ['bing', 'google', 'baidu']
    for engine_name in engine_order:
        try:
            translated_text = translators_translate_text(
                query_text, translator=engine_name, to_language=to_language,
                from_language=from_language, timeout=10.0
            )
            if translated_text and translated_text.strip().lower() != query_text.strip().lower():
                return {"text": translated_text.strip(), "engine": engine_name}
        except Exception:
            continue
    return None

def generate_search_url(site: str, title: str, year: Optional[int] = None) -> str:
    """为指定网站生成搜索链接。"""
    query = f'"{title}"'
    if year: query += f' {year}'
    final_query = f'site:zh.wikipedia.org {query} 演员表' if site == 'wikipedia' else query + " 演员表 cast"
    return f"https://www.google.com/search?q={quote_plus(final_query)}"

# --- ★★★ 全新的智能名字匹配核心逻辑 ★★★ ---

def normalize_name_for_matching(name: Optional[str]) -> str:
    """
    将名字极度标准化，用于模糊比较。
    转小写、移除所有非字母数字字符、处理 Unicode 兼容性。
    例如 "Chloë Grace Moretz" -> "chloegracemoretz"
    """
    if not name:
        return ""
    # NFKD 分解可以将 'ë' 分解为 'e' 和 '̈'
    nfkd_form = unicodedata.normalize('NFKD', str(name))
    # 只保留基本字符，去除重音等组合标记
    ascii_name = u"".join([c for c in nfkd_form if not unicodedata.combining(c)])
    # 转小写并只保留字母和数字
    return ''.join(filter(str.isalnum, ascii_name.lower()))

def get_name_variants(name: Optional[str]) -> set:
    """
    根据一个名字生成所有可能的变体集合，用于匹配。
    处理中文转拼音、英文名姓/名顺序。
    """
    if not name:
        return set()
    
    name_str = str(name).strip()
    
    # 检查是否包含中文字符
    if contains_chinese(name_str):
        if PYPINYIN_AVAILABLE:
            # 如果是中文，转换为无音调拼音
            pinyin_list = pinyin(name_str, style=Style.NORMAL)
            pinyin_flat = "".join([item[0] for item in pinyin_list if item])
            return {pinyin_flat.lower()}
        else:
            # 如果 pypinyin 不可用，无法处理中文名，返回空集合
            return set()

    # 如果是英文/拼音，处理姓和名顺序
    parts = name_str.split()
    if not parts:
        return set()
        
    # 标准化并移除所有空格和特殊字符
    normalized_direct = normalize_name_for_matching(name_str)
    variants = {normalized_direct}
    
    # 如果有多于一个部分，尝试颠倒顺序
    if len(parts) > 1:
        reversed_name = " ".join(parts[::-1])
        normalized_reversed = normalize_name_for_matching(reversed_name)
        variants.add(normalized_reversed)
        
    return variants

def are_names_match(name1_a: Optional[str], name1_b: Optional[str], name2_a: Optional[str], name2_b: Optional[str]) -> bool:
    """
    【智能版】比较两组名字是否可能指向同一个人。
    """
    # 为第一组名字（通常是 TMDb）生成变体集合
    variants1 = get_name_variants(name1_a)
    if name1_b:
        variants1.update(get_name_variants(name1_b))
    
    # 为第二组名字（通常是豆瓣）生成变体集合
    variants2 = get_name_variants(name2_a)
    if name2_b:
        variants2.update(get_name_variants(name2_b))

    # 移除可能产生的空字符串，避免错误匹配
    if "" in variants1: variants1.remove("")
    if "" in variants2: variants2.remove("")
        
    # 如果任何一个集合为空，则无法匹配
    if not variants1 or not variants2:
        return False
            
    # 检查两个集合是否有任何共同的元素（交集不为空）
    return not variants1.isdisjoint(variants2)

# --- ★★★ 智能匹配逻辑结束 ★★★ ---

if __name__ == '__main__':
    # 测试新的 are_names_match
    print("\n--- Testing are_names_match ---")
    # 测试1: 张子枫
    print(f"张子枫 vs Zhang Zifeng: {are_names_match('Zhang Zifeng', 'Zhang Zifeng', '张子枫', 'Zifeng Zhang')}") # 应该为 True
    # 测试2: 姓/名顺序
    print(f"Jon Hamm vs Hamm Jon: {are_names_match('Jon Hamm', None, 'Hamm Jon', None)}") # 应该为 True
    # 测试3: 特殊字符和大小写
    print(f"Chloë Moretz vs chloe moretz: {are_names_match('Chloë Moretz', None, 'chloe moretz', None)}") # 应该为 True
    # 测试4: 中文 vs 拼音
    print(f"张三 vs zhang san: {are_names_match('zhang san', None, '张三', None)}") # 应该为 True
    # 测试5: 不匹配
    print(f"Zhang San vs Li Si: {are_names_match('Zhang San', None, 'Li Si', None)}") # 应该为 False