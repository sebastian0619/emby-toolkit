# web_parser.py
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse
from typing import List, Dict, Optional
import re
import logging
# 从你的项目中导入 logger 和 utils
from utils import clean_character_name_static

logger = logging.getLogger(__name__)
class ParserError(Exception):
    pass

def _get_soup_from_url(url: str, custom_headers: Optional[Dict[str, str]] = None) -> BeautifulSoup:
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36'
    }
    if custom_headers:
        headers.update(custom_headers)
        logger.info(f"正在使用自定义请求头: {headers}")
    else:
        logger.info(f"正在使用默认请求头: {headers}")
    try:
        response = requests.get(url, headers=headers, timeout=20)
        response.raise_for_status()
        response.encoding = 'utf-8' 
        return BeautifulSoup(response.text, 'html.parser')
    except requests.exceptions.RequestException as e:
        logger.error(f"抓取网页失败: {url}, 错误: {e}")
        raise ParserError(f"无法访问URL: {e}")

def _parse_wikipedia(soup: BeautifulSoup) -> List[Dict[str, str]]:
    """
    智能解析维基百科页面的演员表，能处理表格(table)和列表(ul)两种常见格式。
    """
    CAST_SECTION_KEYWORDS = ["演员阵容", "演員陣容", "主要演員", "主要演员", "演出", "演员列表", "演員列表", "Cast"]
    
    all_headlines = soup.find_all('span', class_='mw-headline')
    for headline in all_headlines:
        if headline.get_text(strip=True) in CAST_SECTION_KEYWORDS:
            logger.info(f"找到演员表章节标题: '{headline.get_text(strip=True)}'。")
            heading_tag = headline.parent
            
            target_table = heading_tag.find_next('table', class_='wikitable')
            if target_table:
                logger.info("标题后找到 'wikitable'，将按表格格式解析。")
                return _parse_wikitable_format(target_table)

            target_list = heading_tag.find_next('ul')
            if target_list:
                logger.info("标题后未找到表格，但找到了 'ul' 列表，将按列表格式解析。")
                return _parse_ul_list_format(target_list)

    logger.warning("未能通过章节标题定位到演员表。将进行全局搜索作为后备。")
    
    all_wikitables = soup.find_all('table', class_='wikitable')
    for table in all_wikitables:
        header_text = table.find('tr').get_text().lower() if table.find('tr') else ""
        if ('演员' in header_text or '演員' in header_text) and '角色' in header_text:
            logger.info("后备方案: 全局搜索找到一个高度疑似的演员表格。")
            return _parse_wikitable_format(table)
            
    all_lists = soup.find_all('ul')
    for ul in all_lists:
        if "飾演" in ul.get_text() or "饰演" in ul.get_text():
             logger.info("后备方案: 全局搜索找到一个包含'饰演'关键词的列表。")
             return _parse_ul_list_format(ul)

    logger.error("所有解析策略均失败，未能在页面上找到可识别的演员表。")
    return []

def _parse_wikitable_format(table: BeautifulSoup) -> List[Dict[str, str]]:
    cast_list = []
    header_row = table.find('tr')
    headers = [th.get_text(strip=True) for th in header_row.find_all('th')] if header_row else []
    actor_col_idx, role_col_idx = -1, -1
    for i, header_text in enumerate(headers):
        if any(keyword in header_text for keyword in ["演员", "演員", "Actor", "飾演", "饰演"]):
            actor_col_idx = i
        if any(keyword in header_text for keyword in ["角色", "Role"]):
            role_col_idx = i
    if actor_col_idx == -1 or role_col_idx == -1:
        logger.warning(f"未能从表头 {headers} 中识别出'演员'和'角色'列，将使用默认的第1列和第2列。")
        actor_col_idx, role_col_idx = 0, 1
    logger.info(f"表格列索引确定: 演员列={actor_col_idx}, 角色列={role_col_idx}")
    rows = table.find_all('tr')
    for row in rows[1:]:
        cells = row.find_all('td')
        if len(cells) > max(actor_col_idx, role_col_idx):
            actor_name = cells[actor_col_idx].get_text(strip=True)
            character_name_raw = cells[role_col_idx].get_text(strip=True)
            character_name = clean_character_name_static(character_name_raw)
            if actor_name and character_name:
                cast_list.append({'actor': actor_name, 'character': character_name})
    logger.info(f"从表格格式成功解析到 {len(cast_list)} 位演员。")
    return cast_list

def _parse_ul_list_format(ul_element: BeautifulSoup) -> List[Dict[str, str]]:
    cast_list = []
    list_items = ul_element.find_all('li')
    
    for item in list_items:
        text = item.get_text()
        match = re.search(r'^(.*?)\s*(?:飾演|饰演|飾|饰|演)\s*(.*)$', text)
        if not match:
            match = re.search(r'^(.*?)\s*[:：]\s*(.*)$', text)

        if match:
            actor_name = match.group(1).strip()
            character_name_raw = match.group(2).strip()
            
            character_name = clean_character_name_static(character_name_raw)
            
            if actor_name and character_name:
                cast_list.append({'actor': actor_name, 'character': character_name})
        else:
            logger.debug(f"列表项 '{text[:50]}...' 不符合预期的演员表格式，跳过。")

    logger.info(f"从列表格式成功解析到 {len(cast_list)} 位演员。")
    return cast_list

def parse_cast_from_url(url: str, custom_headers: Optional[Dict[str, str]] = None) -> List[Dict[str, str]]:
    """
    主分发函数：根据 URL 调用合适的解析器。
    """
    if not url or not url.strip():
        raise ParserError("URL 不能为空。")

    parsed_url = urlparse(url)
    hostname = parsed_url.hostname

    logger.info(f"准备从 URL ({hostname}) 解析演员表...")
    
    soup = _get_soup_from_url(url, custom_headers=custom_headers)

    # ✨✨✨ 只保留对维基百科的支持 ✨✨✨
    if 'wikipedia.org' in hostname:
        return _parse_wikipedia(soup)
    else:
        logger.error(f"不支持的网站域名: {hostname}")
        raise ParserError(f"当前不支持从 '{hostname}' 解析。请使用维基百科链接。")