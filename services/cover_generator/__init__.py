# services/cover_generator/__init__.py

import logging
import os
import shutil
import time
import yaml
import base64
import random
import re
import requests # 使用标准的 requests 库
from pathlib import Path
from typing import Dict, Any, List, Tuple
from collections import defaultdict

# 从您的项目中导入您确认存在的模块
import config_manager
import emby_handler 

# 从我们自己的包中进行相对导入
from .styles.style_single_1 import create_style_single_1
from .styles.style_single_2 import create_style_single_2
from .styles.style_multi_1 import create_style_multi_1

# 使用标准的Python日志记录方式
logger = logging.getLogger(__name__)

class CoverGeneratorService:
    """
    一个独立的媒体库封面生成服务，从MoviePilot插件移植而来。
    """
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        
        # 从配置中解析服务设置
        self._sort_by = self.config.get("sort_by", "Random")
        self._covers_output = self.config.get("covers_output")
        self._covers_input = self.config.get("covers_input")
        self._title_config_str = self.config.get("title_config", "")
        self._cover_style = self.config.get("cover_style", "single_1")
        
        # 多图风格的特定配置
        self._multi_1_blur = self.config.get("multi_1_blur", False)
        self._multi_1_use_primary = self.config.get("multi_1_use_primary", True)
        
        # 单图风格的特定配置
        self._single_use_primary = self.config.get("single_use_primary", False)

        # 初始化路径
        self.data_path = Path(config_manager.PERSISTENT_DATA_PATH) / "cover_generator"
        self.covers_path = self.data_path / "covers"
        self.font_path = self.data_path / "fonts"
        self.covers_path.mkdir(parents=True, exist_ok=True)
        self.font_path.mkdir(parents=True, exist_ok=True)
        
        # 字体路径将在使用时动态获取
        self.zh_font_path = None
        self.en_font_path = None
        self.zh_font_path_multi_1 = None
        self.en_font_path_multi_1 = None

    # --- 核心公开方法 ---
    def generate_for_library(self, emby_server_id: str, library: Dict[str, Any]):
        """
        为指定的媒体库生成并上传封面。
        这是从外部调用的主入口。
        """
        logger.info(f"开始为媒体库 '{library['Name']}' (服务器: {emby_server_id}) 生成封面...")
        
        # 1. 确保字体文件已准备好 (已修改为自动下载)
        self.__get_fonts()

        # 2. 生成封面图片数据
        image_data = self.__generate_image_data(emby_server_id, library)
        if not image_data:
            logger.error(f"为媒体库 '{library['Name']}' 生成封面图片失败。")
            return False

        # 3. 上传封面到媒体服务器
        success = self.__set_library_image(emby_server_id, library, image_data)
        if success:
            logger.info(f"成功更新媒体库 '{library['Name']}' 的封面！")
        else:
            logger.error(f"上传封面到媒体库 '{library['Name']}' 失败。")
            
        return success

    # --- 私有逻辑方法 (从原插件移植和修改) ---

    def __generate_image_data(self, server_id: str, library: Dict[str, Any]) -> bytes:
        """根据配置和媒体库内容，生成最终的封面图片二进制数据"""
        library_name = library['Name']
        title = self.__get_library_title_from_yaml(library_name)
        
        # 检查是否有自定义图片源
        custom_image_paths = self.__check_custom_image(library_name)
        if custom_image_paths:
            logger.info(f"发现媒体库 '{library_name}' 的自定义图片，将使用路径模式生成。")
            return self.__generate_image_from_path(library_name, title, custom_image_paths)

        # 如果没有自定义图片，则从服务器获取
        logger.info(f"未发现自定义图片，将从服务器 '{server_id}' 获取媒体项作为封面来源。")
        return self.__generate_from_server(server_id, library, title)

    def __generate_from_server(self, server_id: str, library: Dict[str, Any], title: Tuple[str, str]) -> bytes:
        """从媒体服务器获取项目并生成封面"""
        required_items_count = 1 if self._cover_style.startswith('single') else 9
        
        # 获取媒体库中的有效媒体项 (已修改为支持随机)
        items = self.__get_valid_items_from_library(server_id, library, required_items_count)
        if not items:
            logger.warning(f"在媒体库 '{library['Name']}' 中找不到任何带有可用图片的媒体项。")
            return None

        # 根据风格调用不同的处理函数
        if self._cover_style.startswith('single'):
            image_url = self.__get_image_url(items[0])
            if not image_url: return None
            
            image_path = self.__download_image(server_id, image_url, library['Name'], 1)
            if not image_path: return None
            
            return self.__generate_image_from_path(library['Name'], title, [image_path])
        else: # multi style
            image_paths = []
            for i, item in enumerate(items[:9]):
                image_url = self.__get_image_url(item)
                if image_url:
                    path = self.__download_image(server_id, image_url, library['Name'], i + 1)
                    if path:
                        image_paths.append(path)
            
            if not image_paths:
                logger.warning(f"为多图模式下载图片失败。")
                return None
            
            return self.__generate_image_from_path(library['Name'], title, image_paths)

    # ★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★
    # ★ 核心修复 1: 修复封面始终相同的问题 ★
    # ★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★
    def __get_valid_items_from_library(self, server_id: str, library: Dict[str, Any], limit: int) -> List[Dict]:
        """从媒体库中获取足够数量的、包含有效图片的媒体项 (已修复随机逻辑)"""
        library_id = library.get("Id") or library.get("ItemId")
        
        base_url = config_manager.APP_CONFIG.get('emby_server_url')
        api_key = config_manager.APP_CONFIG.get('emby_api_key')
        user_id = config_manager.APP_CONFIG.get('emby_user_id')

        all_items = emby_handler.get_emby_library_items(
            base_url=base_url,
            api_key=api_key,
            user_id=user_id,
            library_ids=[library_id],
            media_type_filter="Movie,Series,MusicAlbum"
        )
        
        if not all_items:
            return []
            
        # 1. 先收集所有带图片的有效项目
        valid_items = []
        for item in all_items:
            if self.__get_image_url(item):
                valid_items.append(item)
        
        if not valid_items:
            return []

        # 2. 现在，对所有有效的项目应用排序逻辑
        if self._sort_by == "Random":
            logger.debug(f"正在对 {len(valid_items)} 个有效项目进行随机排序...")
            random.shuffle(valid_items)
        # else:
        #     # 在这里可以添加其他排序逻辑，例如按添加日期或发布日期
        #     # valid_items.sort(key=lambda x: x.get('DateCreated'), reverse=True)
        #     pass

        # 3. 最后，返回所需数量的项目
        return valid_items[:limit]

    def __generate_image_from_path(self, library_name: str, title: Tuple[str, str], image_paths: List[str]) -> bytes:
        """使用本地图片路径列表生成封面"""
        logger.info(f"正在为 '{library_name}' 从本地路径生成封面...")
        
        # 字体和尺寸配置
        zh_font_size = self.config.get("zh_font_size", 1)
        en_font_size = self.config.get("en_font_size", 1)
        blur_size = self.config.get("blur_size", 50)
        color_ratio = self.config.get("color_ratio", 0.8)
        font_size = (float(zh_font_size), float(en_font_size))

        if self._cover_style == 'single_1':
            return create_style_single_1(image_paths[0], title, (self.zh_font_path, self.en_font_path), 
                                         font_size=font_size, blur_size=blur_size, color_ratio=color_ratio)
        elif self._cover_style == 'single_2':
            return create_style_single_2(image_paths[0], title, (self.zh_font_path, self.en_font_path), 
                                         font_size=font_size, blur_size=blur_size, color_ratio=color_ratio)
        elif self._cover_style == 'multi_1':
            zh_font_path_multi = self.zh_font_path_multi_1 or self.zh_font_path
            en_font_path_multi = self.en_font_path_multi_1 or self.en_font_path
            font_path_multi = (zh_font_path_multi, en_font_path_multi)
            
            zh_font_size_multi = self.config.get("zh_font_size_multi_1", 1)
            en_font_size_multi = self.config.get("en_font_size_multi_1", 1)
            font_size_multi = (float(zh_font_size_multi), float(en_font_size_multi))
            
            blur_size_multi = self.config.get("blur_size_multi_1", 50)
            color_ratio_multi = self.config.get("color_ratio_multi_1", 0.8)

            # 准备9张图
            library_dir = self.covers_path / library_name
            self.__prepare_multi_images(library_dir, image_paths)
            
            return create_style_multi_1(library_dir, title, font_path_multi, 
                                      font_size=font_size_multi, 
                                      is_blur=self._multi_1_blur, 
                                      blur_size=blur_size_multi, 
                                      color_ratio=color_ratio_multi)
        return None

    def __set_library_image(self, server_id: str, library: Dict[str, Any], image_data: bytes) -> bool:
        """上传封面到媒体库"""
        library_id = library.get("Id") or library.get("ItemId")
        
        base_url = config_manager.APP_CONFIG.get('emby_server_url')
        api_key = config_manager.APP_CONFIG.get('emby_api_key')
        
        upload_url = f"{base_url.rstrip('/')}/Items/{library_id}/Images/Primary?api_key={api_key}"
        headers = {"Content-Type": "image/jpeg"}

        if self._covers_output:
            try:
                save_path = Path(self._covers_output) / f"{library['Name']}.jpg"
                save_path.parent.mkdir(parents=True, exist_ok=True)
                with open(save_path, "wb") as f:
                    f.write(image_data)
                logger.info(f"封面已另存到: {save_path}")
            except Exception as e:
                logger.error(f"另存封面失败: {e}")

        try:
            response = requests.post(upload_url, data=image_data, headers=headers, timeout=30)
            response.raise_for_status()
            logger.info(f"成功上传封面到媒体库 '{library['Name']}'。")
            return True
        except requests.exceptions.RequestException as e:
            logger.error(f"上传封面到媒体库 '{library['Name']}' 时发生网络错误: {e}")
            if e.response is not None:
                logger.error(f"  -> 响应状态: {e.response.status_code}, 响应内容: {e.response.text[:200]}")
            return False

    # --- 以下是辅助函数 ---

    def __get_library_title_from_yaml(self, library_name: str) -> Tuple[str, str]:
        zh_title, en_title = library_name, ''
        if not self._title_config_str:
            return zh_title, en_title
        try:
            title_config = yaml.safe_load(self._title_config_str)
            if isinstance(title_config, dict) and library_name in title_config:
                titles = title_config[library_name]
                if isinstance(titles, list) and len(titles) >= 2:
                    zh_title, en_title = titles[0], titles[1]
        except yaml.YAMLError as e:
            logger.error(f"解析标题配置失败: {e}")
        return zh_title, en_title

    def __get_image_url(self, item: Dict[str, Any]) -> str:
        item_id = item.get("Id")
        if self._cover_style.startswith('single') and not self._single_use_primary:
            if item.get("BackdropImageTags"):
                return f'/emby/Items/{item_id}/Images/Backdrop/0?tag={item["BackdropImageTags"][0]}'
        if item.get("ImageTags", {}).get("Primary"):
            return f'/emby/Items/{item_id}/Images/Primary?tag={item["ImageTags"]["Primary"]}'
        if item.get("BackdropImageTags"):
            return f'/emby/Items/{item_id}/Images/Backdrop/0?tag={item["BackdropImageTags"][0]}'
        return None

    def __download_image(self, server_id: str, api_path: str, library_name: str, count: int) -> Path:
        subdir = self.covers_path / library_name
        subdir.mkdir(parents=True, exist_ok=True)
        filepath = subdir / f"{count}.jpg"
        
        try:
            base_url = config_manager.APP_CONFIG.get('emby_server_url')
            api_key = config_manager.APP_CONFIG.get('emby_api_key')

            path_parts = api_path.strip('/').split('/')
            if len(path_parts) >= 4 and path_parts[1] == 'Items' and path_parts[3] == 'Images':
                item_id = path_parts[2]
                image_type = path_parts[4].split('?')[0]

                success = emby_handler.download_emby_image(
                    item_id=item_id,
                    image_type=image_type,
                    save_path=str(filepath),
                    emby_server_url=base_url,
                    emby_api_key=api_key
                )
                
                if success:
                    return filepath
            else:
                logger.error(f"无法从API路径解析有效的项目ID和图片类型: {api_path}")

        except Exception as e:
            logger.error(f"下载图片失败 ({api_path}): {e}", exc_info=True)
            
        return None
        
    def __prepare_multi_images(self, library_dir: Path, source_paths: List[str]):
        """为多图模式准备9张图片"""
        library_dir.mkdir(parents=True, exist_ok=True)
        for i in range(1, 10):
            target_path = library_dir / f"{i}.jpg"
            if not target_path.exists():
                source_to_copy = random.choice(source_paths)
                shutil.copy(source_to_copy, target_path)

    def __check_custom_image(self, library_name: str) -> List[str]:
        if not self._covers_input: return []
        library_dir = Path(self._covers_input) / library_name
        if not library_dir.is_dir(): return []
        images = sorted([
            str(p) for p in library_dir.iterdir()
            if p.suffix.lower() in [".jpg", ".jpeg", ".png"]
        ])
        return images

    def __download_file(self, url: str, dest_path: Path):
        """下载文件到指定路径，如果文件已存在则跳过。"""
        if dest_path.exists():
            logger.debug(f"字体文件已存在，跳过下载: {dest_path.name}")
            return
        
        logger.info(f"字体文件不存在，正在从URL下载: {dest_path.name}...")
        try:
            response = requests.get(url, stream=True, timeout=60)
            response.raise_for_status()
            with open(dest_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            logger.info(f"字体 '{dest_path.name}' 下载成功。")
        except requests.RequestException as e:
            logger.error(f"下载字体 '{dest_path.name}' 失败: {e}")
            # 如果下载失败，可以考虑删除不完整的文件
            if dest_path.exists():
                dest_path.unlink()

    # ★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★
    # ★ 核心修复 2: 实现自动字体下载 ★
    # ★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★
    def __get_fonts(self):
        """检查并使用配置中提供的URL自动下载所需的字体文件。"""
        
        # 1. 定义本地文件名与配置项key的映射关系
        #    这里的key (如 'zh_font_url') 必须与您UI保存到 cover_generator.json 中的字段名完全一致
        font_config_map = {
            "zh_font.ttf": "zh_font_url",
            "en_font.ttf": "en_font_url",
            "zh_font_multi_1.ttf": "zh_font_multi_1_url",
            "en_font_multi_1.otf": "en_font_multi_1_url"
        }

        # 2. 遍历映射，根据用户配置执行下载
        for filename, config_key in font_config_map.items():
            # 从传入的 self.config 字典中获取用户填写的URL
            url = self.config.get(config_key)

            # 如果用户没有填写该URL，则记录日志并跳过
            if not url:
                logger.debug(f"未在配置中找到 '{config_key}' 的URL，将跳过下载 {filename}。")
                continue

            # 定义本地保存路径
            dest_path = self.font_path / filename
            
            # 调用下载辅助函数
            self.__download_file(url, dest_path)

        # 3. 在尝试下载后，设置实例的字体路径属性，供后续方法使用
        self.zh_font_path = self.font_path / "zh_font.ttf"
        self.en_font_path = self.font_path / "en_font.ttf"
        self.zh_font_path_multi_1 = self.font_path / "zh_font_multi_1.ttf"
        self.en_font_path_multi_1 = self.font_path / "en_font_multi_1.otf"
        
        # 4. 最后检查核心字体是否存在，并给出统一警告
        if not self.zh_font_path.exists() or not self.en_font_path.exists():
             logger.warning("一个或多个核心字体文件缺失。请检查UI中的下载链接是否正确填写，并确保网络通畅。")