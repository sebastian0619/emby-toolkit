# local_data_handler.py
import os
import json
import re # 用于从文件夹名称中提取 IMDb ID
import logging
from typing import Optional, Dict, Any, List
from utils import clean_character_name_static # 假设 utils.py 中有这个函数

logger = logging.getLogger(__name__)

def find_local_json_path(
    local_data_root_path: str,
    media_type: str, # "movie" or "tv"
    imdb_id: Optional[str] = None,
) -> Optional[str]:
    logger.debug(f"find_local_json_path called with: root='{local_data_root_path}', type='{media_type}', imdb='{imdb_id}'")

    # 1. 参数校验
    if not local_data_root_path or not os.path.isdir(local_data_root_path):
        logger.warning(f"本地数据源根路径 '{local_data_root_path}' 无效或不存在。")
        return None

    if not imdb_id or not imdb_id.startswith("tt"):
        logger.debug(f"未提供有效的 IMDb ID ('{imdb_id}')，无法通过ID精确查找本地文件夹。")
        return None

    # 2. 根据 media_type 确定 subdir
    subdir = "douban-movies" if media_type == "movie" else ("douban-tv" if media_type == "tv" else None)
    logger.debug(f"Determined subdir: '{subdir}'")
    if not subdir:
        logger.warning(f"未知的媒体类型 '{media_type}' 无法确定本地子目录。")
        return None

    # 3. 构造 base_path
    base_path = os.path.join(local_data_root_path, subdir)
    logger.debug(f"Constructed base_path: '{base_path}'")

    # 4. 检查 base_path 是否为有效目录
    if not os.path.isdir(base_path):
        logger.info(f"本地数据子目录 '{base_path}' 不是一个有效的目录或不存在。")
        return None

    # 5. 准备查找
    target_imdb_suffix = f"_{imdb_id}" # 例如 "_tt0111495"
    logger.debug(f"将在 '{base_path}' 中查找文件夹名包含 '{target_imdb_suffix}' 的目录...")
    found_folder_path = None

    # 6. 遍历 base_path 下的目录进行查找
    try:
        for folder_name in os.listdir(base_path):
            full_folder_path = os.path.join(base_path, folder_name)
            if os.path.isdir(full_folder_path):
                # 6a. 检查是否为占位目录 (以 "0_" 开头)
                if folder_name.startswith("0_"):
                    logger.info(f"文件夹 '{folder_name}' 是一个占位目录 (以 '0_' 开头)，将跳过。")
                    continue # 跳过这个文件夹，继续查找下一个

                # 6b. 检查文件夹名称是否以 _<imdb_id> 结尾
                # （或者其他你确定的神医插件命名规则，例如直接等于 imdb_id）
                if folder_name.endswith(target_imdb_suffix):
                    found_folder_path = full_folder_path
                    logger.info(f"找到完全匹配 IMDb ID 后缀的本地文件夹: {found_folder_path}")
                    break # 找到后就跳出循环
                # (可选) 如果神医插件有时只用 IMDb ID 作为文件夹名 (不带豆瓣ID前缀)
                # elif folder_name == imdb_id:
                #     found_folder_path = full_folder_path
                #     logger.info(f"找到直接以 IMDb ID 命名的本地文件夹: {found_folder_path}")
                #     break
    except FileNotFoundError: # os.listdir 可能因为 base_path 突然消失而报错
        logger.error(f"查找本地文件夹时发生 FileNotFoundError: 目录 '{base_path}' 可能不存在或无权限访问。")
        return None
    except PermissionError:
        logger.error(f"查找本地文件夹时发生 PermissionError: 没有权限访问目录 '{base_path}'。")
        return None
    except Exception as e: # 其他 os.listdir 可能的错误
        logger.error(f"查找本地文件夹时发生未知错误 (os.listdir on '{base_path}'): {e}", exc_info=True)
        return None


    if not found_folder_path:
        logger.info(f"在 '{base_path}' 中未找到与 IMDb ID '{imdb_id}' 相关的文件夹 (期望文件夹名包含 '{target_imdb_suffix}')。")
        return None

    # 7. 确定 JSON 文件名并检查文件是否存在
    json_filename = "all.json" if media_type == "movie" else ("series.json" if media_type == "tv" else None)
    # subdir 已经保证了 media_type 是 movie 或 tv，所以 json_filename 不会是 None
    # if not json_filename: return None # 这行理论上不需要了

    json_file_path = os.path.join(found_folder_path, json_filename)
    if os.path.isfile(json_file_path):
        logger.info(f"找到本地元数据文件: {json_file_path}")
        return json_file_path
    else:
        logger.warning(f"本地元数据文件 '{json_file_path}' 未找到 (在文件夹 '{found_folder_path}' 中)。")
        return None


def parse_local_actor_data(json_file_path: str) -> Optional[Dict[str, Any]]:
    """
    解析本地神医JSON文件 (all.json 或 series.json)，提取演员信息。
    返回一个类似豆瓣API get_acting 返回的结构: {"cast": [...], "title": "...", "year": "...", "douban_id": "..."}
    """
    try:
        with open(json_file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except FileNotFoundError:
        logger.error(f"解析本地数据：文件未找到 {json_file_path}")
        return None
    except json.JSONDecodeError:
        logger.error(f"解析本地数据：JSON解码失败 {json_file_path}")
        return None
    except Exception as e:
        logger.error(f"解析本地数据时发生未知错误 {json_file_path}: {e}", exc_info=True)
        return None

    # 从JSON中提取基本信息
    title = data.get("title", "未知标题")
    year = data.get("year", "")
    douban_id = data.get("id", "") # 这是豆瓣 subject ID

    cast_list = []
    raw_actors = data.get("actors") # 根据你的JSON示例，演员在 "actors" 键下

    if not raw_actors or not isinstance(raw_actors, list):
        logger.warning(f"在本地文件 '{json_file_path}' 中未找到有效的 'actors' 列表。")
        # 即使没有演员，也返回包含标题等信息的字典，但 cast 为空
        return {"title": title, "year": year, "douban_id": douban_id, "cast": []}

    for actor_info in raw_actors:
        if not isinstance(actor_info, dict): continue

        name = actor_info.get("name")
        character_raw = actor_info.get("character")
        # 使用我们已有的清理函数来处理 "饰 " 前缀
        cleaned_character = clean_character_name_static(character_raw)

        if name: # 必须有演员名
            cast_list.append({
                "name": name,                                 # 演员中文名
                "character": cleaned_character,               # 清理后的角色名
                "id": str(actor_info.get("id", "")),          # 演员的豆瓣 celebrity ID (转为字符串以保持一致性)
                "latin_name": actor_info.get("latin_name"),   # 演员外文名
                "profile_path": actor_info.get("avatar", {}).get("large"), # 演员头像
                "source_comment": "From local data file"      # 标记数据来源
            })

    logger.info(f"从本地文件 '{json_file_path}' (标题: {title}) 解析到 {len(cast_list)} 位演员。")
    return {"title": title, "year": year, "douban_id": douban_id, "cast": cast_list}

if __name__ == '__main__':
    # --- 测试代码 ---
    # 创建一些临时的测试文件和目录结构来模拟你的神医数据
    test_root = "_test_local_data"
    test_movie_path = os.path.join(test_root, "douban-movies")
    test_tv_path = os.path.join(test_root, "douban-tv")

    # 模拟电影文件夹和文件
    movie_folder_name = "1291557_tt0120338" # 泰坦尼克号 (豆瓣ID_IMDbID)
    movie_json_content = { # 简化版，只包含必要字段
        "title": "泰坦尼克号 (本地)", "year": "1997", "id": "1291557", "is_tv": False,
        "actors": [
            {"name": "莱昂纳多·迪卡普里奥 (本地)", "character": "饰 Jack Dawson", "id": "1041029", "latin_name": "Leonardo DiCaprio", "avatar": {"large": "url_leo.jpg"}},
            {"name": "凯特·温丝莱特 (本地)", "character": "饰 Rose DeWitt Bukater", "id": "1054446", "latin_name": "Kate Winslet", "avatar": {"large": "url_kate.jpg"}}
        ]
    }
    os.makedirs(os.path.join(test_movie_path, movie_folder_name), exist_ok=True)
    with open(os.path.join(test_movie_path, movie_folder_name, "all.json"), 'w', encoding='utf-8') as f:
        json.dump(movie_json_content, f, ensure_ascii=False, indent=4)

    logger.info(f"测试文件已创建在 '{test_root}' 目录下。")

    # 1. 测试 find_local_json_path
    logger.info("\n--- 测试 find_local_json_path ---")
    found_path_movie = find_local_json_path(test_root, "movie", imdb_id="tt0120338")
    logger.info(f"查找电影 (tt0120338): {found_path_movie}")
    assert found_path_movie and "all.json" in found_path_movie

    found_path_nonexist = find_local_json_path(test_root, "movie", imdb_id="tt0000000")
    logger.info(f"查找不存在的电影 (tt0000000): {found_path_nonexist}")
    assert found_path_nonexist is None

    # 2. 测试 parse_local_actor_data
    logger.info("\n--- 测试 parse_local_actor_data ---")
    if found_path_movie:
        parsed_data_movie = parse_local_actor_data(found_path_movie)
        logger.info(f"解析电影数据: {json.dumps(parsed_data_movie, indent=2, ensure_ascii=False)}")
        assert parsed_data_movie and len(parsed_data_movie.get("cast", [])) == 2
        assert parsed_data_movie.get("title") == "泰坦尼克号 (本地)"

    logger.info("\n--- 测试结束，请手动删除 _test_local_data 文件夹 ---")