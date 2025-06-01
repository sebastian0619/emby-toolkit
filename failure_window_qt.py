# failure_window_qt.py
import json
import os
import csv
import io
import webbrowser
import urllib.parse
import time
import re
import emby_handler
import requests # 需要导入
from typing import Optional, List, Dict

from PyQt6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QLineEdit, QPushButton, QTextEdit, QProgressBar, QCheckBox,
    QFileDialog, QMessageBox, QFrame, QScrollArea, QStatusBar, QSizePolicy,
    QSpacerItem, QListWidget, QListWidgetItem, QTreeWidget, QTreeWidgetItem,
    QAbstractItemView, QSplitter, QHeaderView,QInputDialog,
    QApplication
)
from PyQt6.QtWidgets import QProgressDialog, QApplication # 确保导入
from PyQt6.QtWidgets import QDialogButtonBox, QListWidget, QListWidgetItem
from PyQt6.QtGui import QPixmap, QIcon
from PyQt6.QtCore import Qt, pyqtSlot, QSize, QCoreApplication, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QBrush, QFont, QPalette, QPixmap, QIcon
from utils import (
    is_role_name_valid,
    contains_chinese,
    get_tmdb_person_details,
    format_tmdb_person_to_cast_entry,
    search_tmdb_person_by_name,
    search_tmdb_media_by_name,
    translate_text_with_translators,
    should_translate_text 
)
try:
    from douban import clean_character_name_static
except ImportError:
    # Fallback or local definition if douban.py is not available or function moved
    def clean_character_name_static(character_name: Optional[str]) -> str:
        if not character_name:
            return ""
        name = str(character_name)
        if name.startswith("饰 "):
            name = name[2:].strip()
        elif name.startswith("饰"):
            name = name[1:].strip()
        else:
            name = name.strip()
        return name

# Use the imported or defined clean_character_name_static as clean_character_name_dialog
# This was in your original script, keeping it.


def clean_character_name_dialog(character_name: Optional[str]) -> str:
    if not character_name:
        return ""
    name = str(character_name)
    if name.startswith("饰 "):
        name = name[2:].strip()
    elif name.startswith("饰"):
        name = name[1:].strip()
    else:
        name = name.strip()
    return name

class ActorImageDownloaderThread(QThread):
    image_downloaded = pyqtSignal(int, QPixmap) # 发射 (item_row, pixmap)

    def __init__(self, item_row: int, profile_path: str, parent=None):
        super().__init__(parent)
        self.item_row = item_row
        self.profile_path = profile_path
        self.image_base_url = "https://image.tmdb.org/t/p/"
        self.image_size = "w92" # 或者 w45, w185 等

    def run(self):
        pixmap = None
        if self.profile_path:
            try:
                image_url = f"{self.image_base_url}{self.image_size}{self.profile_path}"
                response = requests.get(image_url, stream=True, timeout=5)
                response.raise_for_status()
                image_data = response.content
                temp_pixmap = QPixmap()
                if temp_pixmap.loadFromData(image_data):
                    pixmap = temp_pixmap
            except Exception as e:
                # 在线程中不直接操作GUI logger
                print(f"[ActorImageDownloaderThread] Error downloading image {self.profile_path}: {e}")
        self.image_downloaded.emit(self.item_row, pixmap if pixmap else QPixmap()) # 发射空Pixmap如果失败
        
# failure_window_qt.py -> ActorCandidateSelectionDialog
class ActorCandidateSelectionDialog(QDialog):
    def __init__(self, candidates_data: List[Dict], parent=None): # 参数名清晰化
        super().__init__(parent)
        self.setWindowTitle("选择TMDB匹配演员")
        # --- <<< 关键：确保这一行存在且正确 >>> ---
        self.candidates_internal_list = candidates_data # 将传入的列表赋值给一个实例属性
        # --- <<< 关键结束 >>> ---
        self.selected_candidate_id = None

        layout = QVBoxLayout(self)
        self.list_widget = QListWidget()
        self.list_widget.setIconSize(QSize(70, 70))

        # --- <<< 修改：使用 self.candidates_internal_list 进行遍历 >>> ---
        if self.candidates_internal_list:
            # --- <<< 新增：初始化 image_threads 列表 >>> ---
            self.image_threads = [] # 确保在循环前初始化
            # --- <<< 初始化结束 >>> ---
            for idx, cand in enumerate(self.candidates_internal_list): 
                display_text = f"{cand.get('name', 'N/A')} (ID: {cand.get('id')})"
                if cand.get('known_for_titles'):
                    display_text += f"\n作品: {cand.get('known_for_titles')}"
                
                item = QListWidgetItem(display_text)
                item.setData(Qt.ItemDataRole.UserRole, cand.get('id'))
                self.list_widget.addItem(item)

                # --- <<< 取消注释以下图片加载代码 >>> ---
                profile_path = cand.get("profile_path")
                if profile_path:
                    thread = ActorImageDownloaderThread(idx, profile_path, self) 
                    thread.image_downloaded.connect(self._set_item_icon)
                    thread.finished.connect(thread.deleteLater)
                    self.image_threads.append(thread) # 保持对线程的引用
                    thread.start()
        
        layout.addWidget(QLabel("找到以下可能的TMDB匹配项，请选择一个（双击选择）：")) # Line H
        layout.addWidget(self.list_widget) # ------------------------- Line I

        self.manual_input_item_text = "--- 以上都不是，手动输入TMDB ID ---"
        self.keep_current_item_text = "--- 保持现状，暂不选择 ---"
        
        manual_item = QListWidgetItem(self.manual_input_item_text) # -- Line J
        manual_item.setData(Qt.ItemDataRole.UserRole, "manual_input")
        self.list_widget.addItem(manual_item) # ---------------------- Line K
        
        keep_item = QListWidgetItem(self.keep_current_item_text) # --- Line L
        keep_item.setData(Qt.ItemDataRole.UserRole, "keep_current")
        self.list_widget.addItem(keep_item) # ------------------------ Line M

        self.list_widget.itemDoubleClicked.connect(self.accept_selection)

        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel) # Line N
        button_box.accepted.connect(self.accept_selection)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box) # ------------------------------ Line O

        self.setMinimumWidth(450)
        self.setMinimumHeight(300)
        print("DEBUG: ActorCandidateSelectionDialog __init__ finished.") # Line P

    @pyqtSlot(int, QPixmap)
    def _set_item_icon(self, row: int, pixmap: QPixmap):
        if 0 <= row < self.list_widget.count(): # 确保行号有效
            item = self.list_widget.item(row)
            if item and not pixmap.isNull():
                item.setIcon(QIcon(pixmap))
            elif item: # 下载失败或无图片，可以设置一个默认图标
                # item.setIcon(QIcon("path/to/default_person_icon.png")) # 如果有默认图标
                pass 

    def accept_selection(self): # 这个方法在双击或点击OK时被调用
        current_item = self.list_widget.currentItem()
        if current_item:
            data = current_item.data(Qt.ItemDataRole.UserRole)
            # --- 打印日志确认进入此方法和获取的数据 ---
            # self.parent().main_app.log_to_progress_text_qt(f"DEBUG: accept_selection called. Item data: {data}", "DEBUG")
            # 注意：直接 self.parent().main_app 这样调用可能层级不对或不健壮，
            # 更好的方式是在 __init__ 中传入 main_app_instance
            # 但为了快速调试，可以先这样，或者直接用 print()
            print(f"DEBUG: ActorCandidateSelectionDialog.accept_selection - Item data: {data}")


            if data == "manual_input":
                self.selected_candidate_id = "manual_input"
                self.accept() # 调用QDialog的accept()方法，会使exec()返回True
            elif data == "keep_current":
                self.selected_candidate_id = None 
                self.accept() 
            elif isinstance(data, int): 
                self.selected_candidate_id = data
                self.accept()
            else: 
                # 如果是双击了非选项区域，current_item可能是存在的，但data可能不是预期的
                # 这种情况下不应该关闭对话框，或者至少不设置selected_candidate_id
                # QMessageBox.warning(self, "提示", "请选择一个有效的选项。") # 避免在双击时频繁弹窗
                print(f"DEBUG: ActorCandidateSelectionDialog.accept_selection - Invalid item data: {data}")
                return # 不关闭对话框
        else:
            # 如果没有选中项（例如直接点击OK按钮但列表没选），也不应该设置selected_candidate_id
            # QMessageBox.warning(self, "提示", "请选择一个选项。")
            print(f"DEBUG: ActorCandidateSelectionDialog.accept_selection - No item selected when OK/DoubleClicked.")
            # 这种情况下，如果用户是点击OK按钮，对话框还是会因为 button_box.accepted 连接到 self.accept() 而关闭
            # 但 self.selected_candidate_id 会是 None
            # 如果是双击空白处，current_item 为 None，这里直接返回，对话框不关闭
            if self.sender() == self.list_widget: # 如果是双击事件触发的
                 return
            # 如果是OK按钮触发的，但没选，让它按默认行为关闭（selected_id为None）
            self.accept() # 确保对话框能关闭


    def get_selected_id(self):
        return self.selected_candidate_id

    # 确保线程在对话框关闭时能被妥善处理 (可选，因为用了deleteLater)
    # def closeEvent(self, event):
    #     for thread in self.image_threads:
    #         if thread.isRunning():
    #             thread.quit() # 请求停止
    #             thread.wait(500) # 等待一小会
    #     super().closeEvent(event)
class ManualTranslationThread(QThread):
    # 信号：(原始文本, 翻译后文本, 演员在列表中的索引, 字段类型 'character' 或 'name')
    translation_done = pyqtSignal(str, object, int, str)
    # translation_started = pyqtSignal(str, str, str, int) # 可选

    def __init__(self, text_to_translate: str, original_text_for_signal: str,
                 actor_index: int, field_type: str, parent=None):
        super().__init__(parent)
        self.text_to_translate = text_to_translate
        self.original_text_for_signal = original_text_for_signal
        self.actor_index = actor_index
        self.field_type = field_type
        self.failure_window_parent = parent
        # --- 新增一个唯一的任务标识，方便日志追踪 ---
        # 使用更简单的唯一ID方式，避免浮点数精度问题导致显示过长
        self.task_debug_id = f"Task_{actor_index}_{field_type}_{id(self)}"


    def run(self):
        # self.translation_started.emit(...) # 如果需要启动信号

        translated_text_final_for_emit = None # 用于最终发射的翻译结果
        text_to_actually_translate = self.text_to_translate # 实际发送给翻译引擎的文本
        suffix_to_add_after = "" # 翻译后要加的后缀

        if self.text_to_translate and self.text_to_translate.strip():
            text_stripped_for_processing = self.text_to_translate.strip()

            # --- <<< 新增：处理 (voice) 后缀的逻辑，与 TranslationThread 类似 >>> ---
            if self.field_type == 'character': # 只对角色名处理 (voice)
                # 编译正则表达式以匹配 "(voice)" 及其变体，忽略大小写，并允许前后有空格
                # 确保匹配字符串末尾的 (voice)
                voice_patterns = [
                    re.compile(r'\s*\(\s*voice\s*\)\s*$', re.IGNORECASE), # 匹配 (voice) 在末尾
                    re.compile(r'\s+voice\s*$', re.IGNORECASE),          # 匹配 voice 在末尾（无括号，作为备选）
                ]
                for pattern in voice_patterns:
                    match = pattern.search(text_stripped_for_processing)
                    if match:
                        # 获取匹配开始的位置，并截取前面的部分作为要翻译的文本
                        text_to_actually_translate = text_stripped_for_processing[:match.start()].strip()
                        suffix_to_add_after = " (配音)" # 固定为中文后缀
                        self.failure_window_parent.main_app.log_to_progress_text_qt(
                            f"手动翻译：识别到角色名中的voice模式，将翻译 '{text_to_actually_translate}' 并添加后缀 '{suffix_to_add_after}'", "DEBUG"
                        )
                        break # 找到一个匹配就跳出
            # --- <<< (voice) 后缀处理结束 >>> ---

            # 如果剥离 (voice) 后，text_to_actually_translate 变为空了，但有后缀 (说明原文就是 "(voice)")
            if not text_to_actually_translate.strip() and suffix_to_add_after == " (配音)":
                translated_text_final_for_emit = "配音" # 直接设为“配音”
                self.failure_window_parent.main_app.log_to_progress_text_qt(
                    f"手动翻译：原文仅为voice模式，直接设为 '{translated_text_final_for_emit}'", "INFO"
                )
            elif text_to_actually_translate.strip(): # 确保有实际内容需要翻译
                engine_cfg_to_use = None
                if self.failure_window_parent and \
                   hasattr(self.failure_window_parent, 'main_app') and \
                   hasattr(self.failure_window_parent.main_app, 'cfg_translator_engines_order'):
                    engine_cfg_to_use = self.failure_window_parent.main_app.cfg_translator_engines_order
                
                # print(f"调试演员名翻译[手动-线程]: 准备在线翻译 '{text_to_actually_translate}' (原始字段类型: {self.field_type})")
                online_translation_result = translate_text_with_translators(
                    text_to_actually_translate, # 使用剥离了 (voice) 的文本
                    "zh",
                    engine_order=engine_cfg_to_use
                )
                # print(f"调试演员名翻译[手动-线程]: '{text_to_actually_translate}' 翻译结果: '{online_translation_result}'")

                if online_translation_result and online_translation_result.strip():
                    # 注意：translate_text_with_translators 现在返回的是 "译文（原文）" 格式
                    # 我们需要从中提取纯译文部分来拼接 "(配音)"
                    
                    # 简单的提取：假设 "译文（原文）" 中，"（" 前面的是纯译文
                    # 这可能需要根据 format_translation_with_original 的实际输出进行调整
                    pure_translated_text = online_translation_result
                    if f"（{text_to_actually_translate}）" in online_translation_result: # 如果是标准格式
                        pure_translated_text = online_translation_result.replace(f"（{text_to_actually_translate}）", "").strip()
                    
                    translated_text_final_for_emit = pure_translated_text + suffix_to_add_after
                else: # 翻译失败或返回空
                    # 如果翻译失败，将原文（剥离后的）和后缀（如果有）组合起来
                    translated_text_final_for_emit = text_to_actually_translate + suffix_to_add_after
            else: # 如果剥离 (voice) 后 text_to_actually_translate 为空，且没有后缀（说明原文就是空的）
                 translated_text_final_for_emit = self.text_to_translate # 返回原始的空或空白文本

        else: # 如果原始 self.text_to_translate 就是空或空白
            translated_text_final_for_emit = self.text_to_translate

        # 发射信号时，original_text_for_signal 应该是未处理 (voice) 的原始文本，
        # 而 translated_text_final_for_emit 是处理了 (voice) 并翻译后再拼接的结果。
        self.translation_done.emit(self.original_text_for_signal, translated_text_final_for_emit, self.actor_index, self.field_type)
class FailureProcessingWindowQt(QDialog):
    def _get_cast_from_local_json(self, item_data_from_failure_log):
        media_dir_path_main_cache_ref = item_data_from_failure_log['path'] # 这个path现在是实际找到的媒体项目录
        media_type = item_data_from_failure_log['type']
        
        authoritative_json_source_dir = media_dir_path_main_cache_ref # 直接使用传入的路径作为权威源
        log_source_reason = f"直接使用路径 '{authoritative_json_source_dir}'"

        # if self.main_app.cfg_override_cache_path and \
        #    os.path.isdir(self.main_app.cfg_override_cache_path) and \
        #    os.path.normpath(self.main_app.cfg_override_cache_path).lower() != os.path.normpath(self.main_app.cfg_main_cache_path).lower():
            
        #     media_id_folder_name = os.path.basename(media_dir_path_main_cache)
        #     path_above_media_id_folder = os.path.dirname(media_dir_path_main_cache)
        #     type_subdir_name = os.path.basename(path_above_media_id_folder)

        #     if type_subdir_name in ["tmdb-movies2", "tmdb-tv"]:
        #         potential_override_dir = os.path.join(
        #             self.main_app.cfg_override_cache_path,
        #             type_subdir_name,
        #             media_id_folder_name
        #         )
        #         potential_override_dir = os.path.normpath(potential_override_dir)
                
        #         if os.path.isdir(potential_override_dir):
        #             temp_json_filename = "all.json" if media_type == "movie" else "series.json"
        #             if os.path.exists(os.path.join(potential_override_dir, temp_json_filename)):
        #                 authoritative_json_source_dir = potential_override_dir
        #                 log_source_reason = f"覆盖缓存 '{authoritative_json_source_dir}' (主JSON存在)"
        #             else:
        #                 log_source_reason = f"主缓存 '{authoritative_json_source_dir}' (覆盖缓存中主JSON不存在于 '{potential_override_dir}')"
        #         else:
        #             log_source_reason = f"主缓存 '{authoritative_json_source_dir}' (覆盖缓存对应目录 '{potential_override_dir}' 不存在)"
        #     else:
        #         log_source_reason = f"主缓存 '{authoritative_json_source_dir}' (无法从主缓存路径推断类型子目录)"
        # else:
        #     log_source_reason = f"主缓存 '{authoritative_json_source_dir}' (未配置或未使用独立覆盖缓存)"
        
        # self.main_app.log_to_progress_text_qt(
        #     f"失败项处理：将尝试从 {log_source_reason} 读取JSON。", "DEBUG"
        # )

        json_file_to_check = None
        if media_type == "movie":
            json_file_to_check = os.path.join(authoritative_json_source_dir, "all.json")
        elif media_type == "tv":
            json_file_to_check = os.path.join(authoritative_json_source_dir, "series.json")
        
        if json_file_to_check and os.path.exists(json_file_to_check):
            try:
                with open(json_file_to_check, 'r', encoding='utf-8') as f:
                    json_data = json.load(f)
                
                cast_list_from_json = None
                if "credits" in json_data and isinstance(json_data.get("credits"), dict) and \
                   "cast" in json_data["credits"] and isinstance(json_data["credits"].get("cast"), list):
                    cast_list_from_json = json_data["credits"]["cast"]
                elif "casts" in json_data and isinstance(json_data.get("casts"), dict) and \
                     "cast" in json_data["casts"] and isinstance(json_data["casts"].get("cast"), list):
                    cast_list_from_json = json_data["casts"]["cast"]
                elif "cast" in json_data and isinstance(json_data.get("cast"), list):
                    cast_list_from_json = json_data["cast"]
                elif "guest_stars" in json_data and isinstance(json_data.get("guest_stars"), list):
                    cast_list_from_json = json_data["guest_stars"]
                
                if cast_list_from_json:
                    processed_cast_list = []
                    for actor_dict in cast_list_from_json:
                        if isinstance(actor_dict, dict):
                            actor_dict_copy = actor_dict.copy()
                            actor_dict_copy['original_name'] = actor_dict.get('name') 
                            original_char_name_from_json = actor_dict.get('character') 
                            actor_dict_copy['character'] = clean_character_name_dialog(original_char_name_from_json)
                        
                            if 'name' not in actor_dict_copy: 
                                actor_dict_copy['name'] = actor_dict.get('name', "未知演员")
                            processed_cast_list.append(actor_dict_copy)
                    return processed_cast_list
                else:
                    self.main_app.log_to_progress_text_qt(f"在本地JSON '{os.path.basename(json_file_to_check)}' (源: {authoritative_json_source_dir}) 中未找到可识别的cast结构。", "DEBUG")
                    return None
            except Exception as e:
                self.main_app.log_to_progress_text_qt(f"读取或解析本地JSON '{json_file_to_check}' (源: {authoritative_json_source_dir}) 失败: {e}", "ERROR")
                return None
        else:
            self.main_app.log_to_progress_text_qt(f"未找到预期的本地JSON文件: '{json_file_to_check or '路径未确定'}' (源: {authoritative_json_source_dir})", "DEBUG")
            return None

    def __init__(self, parent_window, main_app_instance, failure_log_path):
        super().__init__(parent_window)
        self.main_app = main_app_instance
        self.failure_log_path = failure_log_path
        self.parsed_failed_items = []
        self.current_selected_item_data = None
        self.current_api_cast_data = None
        self.manual_translation_threads = []
        self.pending_manual_translations = 0

        # --- <<< 修改窗口标题设置逻辑 >>> ---
        if self.failure_log_path:
            window_title = f"失败项处理 - {os.path.basename(self.failure_log_path)}"
        else:
            window_title = "失败项处理 - (无日志加载)"
        self.setWindowTitle(window_title)
        # --- <<< 修改结束 >>> ---

        self.setGeometry(150, 150, 1000, 700)
        self.setMinimumSize(800, 550)
        self._init_ui()
        self._connect_signals()
        self.load_failed_items_from_log()
        self._update_actor_action_buttons_state()
        # --- 初始化按钮状态结束 ---
    def _update_actor_action_buttons_state(self):
        """根据当前演员列表和选中状态更新演员操作按钮的可用性"""
        has_cast_data = bool(self.current_api_cast_data)
        selected_actor_item = self.cast_tree_widget.currentItem()
        is_actor_selected = bool(selected_actor_item)
        
        # 是否有正在进行的翻译
        is_translating = self.pending_manual_translations > 0

        self.btn_add_actor.setEnabled(bool(self.current_selected_item_data) and not is_translating)
        self.btn_delete_actor.setEnabled(is_actor_selected and not is_translating)
        
        # --- <<< 更新翻译按钮状态 >>> ---
        self.btn_translate_selected_roles.setEnabled(is_actor_selected and has_cast_data and not is_translating)
        self.btn_translate_all_roles.setEnabled(has_cast_data and not is_translating)
        # --- <<< 更新结束 >>> ---

        can_move_up = False
        can_move_down = False
        if is_actor_selected and has_cast_data and not is_translating: # 移动按钮在翻译时也禁用
            current_index = self.cast_tree_widget.indexOfTopLevelItem(selected_actor_item)
            if current_index > 0: can_move_up = True
            if current_index != -1 and current_index < (self.cast_tree_widget.topLevelItemCount() - 1): 
                can_move_down = True
        
        self.btn_move_actor_up.setEnabled(can_move_up)
        self.btn_move_actor_down.setEnabled(can_move_down)
    def _init_ui(self):
        main_layout = QHBoxLayout(self)
        splitter = QSplitter(Qt.Orientation.Horizontal)

        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(5, 5, 5, 5)
        left_layout.addWidget(QLabel("待处理失败项:"))
        self.failed_list_widget = QListWidget()
        self.failed_list_widget.setFont(QFont("Consolas", 9))
        self.failed_list_widget.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        left_layout.addWidget(self.failed_list_widget)
        # --- <<< 新增：“按名加载”按钮 >>> ---
        self.btn_load_by_name = QPushButton("搜索编辑本地媒体库")
        left_layout.addWidget(self.btn_load_by_name)
        # --- “按名加载”按钮结束 ---
        splitter.addWidget(left_widget)

        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(5, 5, 5, 5)

        info_groupbox = QFrame()
        info_groupbox.setFrameShape(QFrame.Shape.StyledPanel)
        info_layout = QVBoxLayout(info_groupbox)
        info_layout.setContentsMargins(5, 5, 5, 5)
        self.selected_item_path_label = QLabel("路径: N/A")
        self.selected_item_path_label.setWordWrap(True)
        info_layout.addWidget(self.selected_item_path_label)
        self.selected_item_reason_label = QLabel("原因: N/A")
        self.selected_item_reason_label.setStyleSheet("color: red;")
        self.selected_item_reason_label.setWordWrap(True)
        info_layout.addWidget(self.selected_item_reason_label)
        right_layout.addWidget(info_groupbox)

        cast_groupbox_text = "演员角色详情 (双击演员名或角色名编辑)"  # Modified text
        cast_groupbox = QFrame()
        cast_groupbox.setFrameShape(QFrame.Shape.StyledPanel)
        cast_layout = QVBoxLayout(cast_groupbox)
        cast_layout.setContentsMargins(5, 5, 5, 5)
        cast_layout.addWidget(QLabel(cast_groupbox_text))

        self.cast_tree_widget = QTreeWidget()
        self.cast_tree_widget.setColumnCount(3) # <<< --- 修改为3列 --- <<<
        self.cast_tree_widget.setHeaderLabels(["演员", "饰演角色", "状态"]) # <<< --- 新增列标题 --- <<<
        
        header = self.cast_tree_widget.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive) # 演员名列
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)    # 角色名列拉伸
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents) # <<< --- 状态列根据内容调整 --- <<<
        
        self.cast_tree_widget.setColumnWidth(0, 180) # 演员名列宽度 (可调整)
        self.cast_tree_widget.setAlternatingRowColors(True)
        self.cast_tree_widget.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked |
                                              QAbstractItemView.EditTrigger.SelectedClicked |
                                              QAbstractItemView.EditTrigger.EditKeyPressed)
        cast_layout.addWidget(self.cast_tree_widget)
        # --- <<< 新增：演员操作按钮组 >>> ---
        actor_actions_layout = QHBoxLayout() # 水平布局放按钮
        self.btn_add_actor = QPushButton("新增演员")
        self.btn_delete_actor = QPushButton("删除演员")
        # --- <<< 新增翻译按钮 >>> ---
        self.btn_translate_selected_roles = QPushButton("翻译选中角色")
        self.btn_translate_all_roles = QPushButton("翻译全部角色") # 或者一个更通用的翻译按钮
        # --- <<< 新增结束 >>> ---
        actor_actions_layout.addWidget(self.btn_add_actor)
        actor_actions_layout.addWidget(self.btn_delete_actor)
        # --- 确保翻译按钮被添加到布局中 ---
        actor_actions_layout.addWidget(self.btn_translate_selected_roles) 
        actor_actions_layout.addWidget(self.btn_translate_all_roles)   
        # --- 添加结束 ---
        
        self.btn_move_actor_up = QPushButton("上移")
        self.btn_move_actor_down = QPushButton("下移")

        actor_actions_layout.addWidget(self.btn_add_actor)
        actor_actions_layout.addWidget(self.btn_delete_actor)
        actor_actions_layout.addStretch(1) # 添加弹性空间将上移下移按钮推到右边
        actor_actions_layout.addWidget(self.btn_move_actor_up)
        actor_actions_layout.addWidget(self.btn_move_actor_down)
        
        cast_layout.addLayout(actor_actions_layout) # 将按钮组添加到演员信息框的布局中
        # --- 演员操作按钮组结束 ---
        right_layout.addWidget(cast_groupbox, 1)

        action_buttons_frame = QFrame()
        action_buttons_layout = QHBoxLayout(action_buttons_frame)
        action_buttons_layout.setContentsMargins(0, 0, 0, 0)
        self.btn_research_api = QPushButton("豆瓣搜索")
        action_buttons_layout.addWidget(self.btn_research_api)
        self.btn_search_google = QPushButton("谷歌搜索")
        action_buttons_layout.addWidget(self.btn_search_google)
        self.btn_search_baidu = QPushButton("百度搜索")
        action_buttons_layout.addWidget(self.btn_search_baidu)
        action_buttons_layout.addStretch(1)
        self.btn_remove_from_list = QPushButton("本次忽略")
        action_buttons_layout.addWidget(self.btn_remove_from_list)
        self.btn_save_changes = QPushButton("保存修改并移出")
        self.btn_save_changes.setStyleSheet("font-weight: bold;")
        action_buttons_layout.addWidget(self.btn_save_changes)
        right_layout.addWidget(action_buttons_frame)

        splitter.addWidget(right_widget)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        main_layout.addWidget(splitter)

    def _connect_signals(self):
        self.failed_list_widget.currentItemChanged.connect(
            self.on_failed_item_select_qt)
        self.cast_tree_widget.itemChanged.connect(self.on_tree_item_changed_qt)
        self.cast_tree_widget.currentItemChanged.connect(self._update_actor_action_buttons_state_on_selection)
        self.cast_tree_widget.itemDoubleClicked.connect(self.on_cast_tree_item_double_clicked)
        #self.btn_research_api.clicked.connect(self.research_api_for_selected)
        self.btn_research_api.clicked.connect(self.search_douban_for_selected_item)
        self.btn_search_google.clicked.connect(self.search_google_for_selected)
        self.btn_search_baidu.clicked.connect(self.search_baidu_for_selected)
        self.btn_save_changes.clicked.connect(self.save_changes_and_remove)
        self.btn_remove_from_list.clicked.connect(self.remove_from_list_only)
        self.btn_add_actor.clicked.connect(self.add_actor_manually)
        self.btn_delete_actor.clicked.connect(self.delete_selected_actor)
        self.btn_move_actor_up.clicked.connect(self.move_selected_actor_up)
        self.btn_move_actor_down.clicked.connect(self.move_selected_actor_down)
        self.btn_translate_selected_roles.clicked.connect(self.translate_selected_roles_manually)
        self.btn_translate_all_roles.clicked.connect(self.translate_all_roles_manually)
        self.btn_load_by_name.clicked.connect(self.load_media_by_name_for_editing)
        

    def load_failed_items_from_log(self):
        self.parsed_failed_items.clear()
        self.failed_list_widget.clear()
        self.current_selected_item_data = None
        self.current_api_cast_data = None
        self._update_detail_display_qt(None) # 清空右侧详情

        # --- <<< 新增：处理 self.failure_log_path 为 None 的情况 >>> ---
        if not self.failure_log_path:
            self.main_app.log_to_progress_text_qt("手动处理窗口：未指定失败日志文件路径，不加载任何项目。", "INFO")
            self._update_detail_display_qt(None, "无失败日志加载。请使用“按名加载”或处理新批次生成失败日志。")
            # 确保按钮状态正确
            self._update_actor_action_buttons_state()
            return # 直接返回，不执行后续的文件读取逻辑
        # --- <<< 新增结束 >>> ---

        if not os.path.exists(self.failure_log_path): # 路径存在性检查（理论上app_window已处理）
            QMessageBox.critical(self, "错误", "指定的失败日志文件路径无效或文件不存在。")
            self.main_app.log_to_progress_text_qt(f"错误：尝试加载的失败日志 '{self.failure_log_path}' 不存在。", "ERROR")
            self.close() # 或者显示空列表
            return

        # --- 后续的文件读取和解析逻辑保持不变 ---
        try:
            data_lines = []
            # ... (原有的读取文件内容到 data_lines 的逻辑) ...
            with open(self.failure_log_path, 'r', encoding='utf-8') as f:
                for line in f:
                    stripped_line = line.strip()
                    if not stripped_line or stripped_line.startswith(("#", "="*10)) or "格式: 媒体类型,TMDB_ID" in stripped_line:
                        continue
                    data_lines.append(stripped_line)

            self.main_app.log_to_progress_text_qt(
                f"从失败日志中读取到 {len(data_lines)} 条有效数据行。", "DEBUG")

            if not data_lines: # 文件存在但解析后无数据行
                self._update_detail_display_qt(None, "失败日志文件为空或只包含头部/注释。")
                self.main_app.log_to_progress_text_qt(
                    f"从 '{os.path.basename(self.failure_log_path)}' 加载了 0 个失败项 (文件无数据行)。", "INFO")
                self._update_actor_action_buttons_state() # 确保按钮状态
                return
            
            # ... (原有的 CSV 解析和填充 self.failed_list_widget 的逻辑) ...
            csv_file_like = io.StringIO("\n".join(data_lines))
            csv_reader = csv.reader(csv_file_like, delimiter=',', quotechar='"',
                                    quoting=csv.QUOTE_MINIMAL, skipinitialspace=True)
            parsed_count = 0
            for line_num_data, row_parts in enumerate(csv_reader):
                # self.main_app.log_to_progress_text_qt(f"  CSV解析行 {line_num_data+1}: {row_parts}", "DEBUG") # 这条日志可能过于频繁
                parsed_count += 1
                if len(row_parts) == 4:
                    media_type, tmdb_id, path, reason = [
                        part.strip() for part in row_parts]
                    item_data = {"type": media_type, "tmdb_id": tmdb_id,
                                "path": path, "reason": reason}
                    # ... (获取 display_title_for_list 的逻辑) ...
                    original_mtype = self.main_app.mtype
                    extracted_title, extracted_year, _ = self.main_app.extract_info_from_media_directory(
                        item_data["path"])
                    self.main_app.mtype = original_mtype
                    display_title_text = extracted_title if extracted_title else os.path.basename(
                        item_data["path"])
                    if extracted_title and extracted_year:
                        display_title_text += f" ({extracted_year})"
                    item_data["display_title_for_list"] = display_title_text

                    self.parsed_failed_items.append(item_data)
                    list_display_entry = f"[{item_data['type'].upper()}] {display_title_text}"
                    if item_data['reason']:
                        list_display_entry += f" - {item_data['reason'][:25]}..." if len(
                            item_data['reason']) > 25 else f" - {item_data['reason']}"
                    self.failed_list_widget.addItem(list_display_entry)
                else:
                    self.main_app.log_to_progress_text_qt(
                        f"    警告：解析失败日志行格式错误 (parts: {len(row_parts)}): '{','.join(row_parts)}'", "WARN")
            
            if self.parsed_failed_items:
                self.failed_list_widget.setCurrentRow(0)
            else: # 解析后列表仍为空
                msg = "未在日志中找到有效的失败记录。"
                if parsed_count == 0 and len(data_lines) > 0: # 有数据行但无法解析
                    msg = "失败日志文件数据行无法解析（格式可能不正确）。"
                self._update_detail_display_qt(None, msg)

            self.main_app.log_to_progress_text_qt(
                f"从 '{os.path.basename(self.failure_log_path)}' 加载了 {len(self.parsed_failed_items)} 个失败项。", "INFO")

        except Exception as e:
            import traceback
            self.main_app.log_to_progress_text_qt(
                f"读取或解析失败日志 '{self.failure_log_path}' 失败: {e}\n{traceback.format_exc()}", "ERROR")
            QMessageBox.critical(self, "加载失败", f"读取或解析失败日志文件时发生错误:\n{e}")
            # self.close() # 出错时不一定关闭，可以显示空列表
            self._update_detail_display_qt(None, f"加载失败日志出错: {e}")
        
        self._update_actor_action_buttons_state() # 确保在所有路径后都更新按钮状态

    def _update_detail_display_qt(self, item_data, message=None, cast_list=None):
        self.main_app.log_to_progress_text_qt(f"DEBUG: _update_detail_display_qt called. Message: {message}, Cast list length: {len(cast_list) if cast_list else 'None'}", "DEBUG")
        if cast_list:
            self.main_app.log_to_progress_text_qt(f"DEBUG: First 1 cast item in _update_detail_display_qt: {cast_list[0] if cast_list else 'N/A'}", "DEBUG")
            # 额外打印，检查翻译后的名字是否在传入的数据中
            for i, actor_check in enumerate(cast_list[:3]): # 检查前3个
                self.main_app.log_to_progress_text_qt(f"  Cast item {i} for UI: Name='{actor_check.get('name')}', Char='{actor_check.get('character')}'", "DEBUG")


        self.cast_tree_widget.clear()
        if cast_list:
            self.main_app.log_to_progress_text_qt(f"DEBUG: First 5 cast _tmdb_status: {[actor.get('_tmdb_status') for actor in cast_list[:5]]}", "DEBUG")
        if message:
            QTreeWidgetItem(self.cast_tree_widget, ["---", message])
            if item_data:
                self.selected_item_path_label.setText(
                    f"路径: {item_data.get('path', 'N/A')}")
                self.selected_item_reason_label.setText(
                    f"原因: {item_data.get('reason', 'N/A')}")
            else:
                self.selected_item_path_label.setText("路径: N/A")
                self.selected_item_reason_label.setText("原因: N/A")
        elif item_data:
            self.selected_item_path_label.setText(f"路径: {item_data.get('path', 'N/A')}")
            self.selected_item_reason_label.setText(f"原因: {item_data.get('reason', 'N/A')}")
            if cast_list:
                for i, actor_info in enumerate(cast_list):
                    actor_name = actor_info.get("name", "未知演员")
                    character_name_raw = actor_info.get("character", "未知角色")
                    character_name = clean_character_name_dialog(character_name_raw)

                    tree_item = QTreeWidgetItem(self.cast_tree_widget)
                    tree_item.setText(0, actor_name)
                    tree_item.setText(1, character_name)
                    tree_item.setData(0, Qt.ItemDataRole.UserRole, i)
                    tree_item.setFlags(tree_item.flags() | Qt.ItemFlag.ItemIsEditable)
                    
                    status_text_for_column = ""
                    status_tooltip_for_item = ""

                    is_valid_role = is_role_name_valid(character_name, actor_name)
                    if not is_valid_role:
                        tree_item.setForeground(1, QBrush(QColor("red")))
                        font = tree_item.font(1); font.setBold(True); tree_item.setFont(1, font)
                        if character_name.strip() == "演员": status_text_for_column = "角色名无效('演员')"
                        elif not character_name.strip(): status_text_for_column = "角色名为空"
                        else: status_text_for_column = "角色名待翻译/修正"
                        status_tooltip_for_item = f"角色名 '{character_name}' (演员: {actor_name}) 不符合规范。"
                    else:
                        tmdb_status = actor_info.get("_tmdb_status")
                        # self.main_app.log_to_progress_text_qt(f"Actor: {actor_name}, Role: {character_name}, TMDB Status: {tmdb_status}", "DEBUG") # 这条日志太频繁，可以注释掉

                        if tmdb_status == "tmdb_multiple_matches": tree_item.setForeground(0, QBrush(QColor("orange"))); status_text_for_column = "TMDB多匹配"; status_tooltip_for_item = "TMDB找到多个匹配，请核实。"
                        elif tmdb_status == "tmdb_not_found": tree_item.setForeground(0, QBrush(QColor("gray"))); status_text_for_column = "TMDB未找到"; status_tooltip_for_item = "TMDB未找到匹配此演员。"
                        elif tmdb_status == "tmdb_auto_matched": tree_item.setForeground(0, QBrush(QColor("darkGreen"))); status_text_for_column = "TMDB已匹配"; status_tooltip_for_item = "已通过TMDB名称搜索自动匹配并补充信息。"
                        elif tmdb_status == "tmdb_format_failed": tree_item.setForeground(0, QBrush(QColor("purple"))); status_text_for_column = "TMDB格式化失败"; status_tooltip_for_item = "TMDB信息获取成功但格式化失败。"
                        elif tmdb_status == "tmdb_info_insufficient": tree_item.setForeground(0, QBrush(QColor("darkRed"))); status_text_for_column = "TMDB信息不足"; status_tooltip_for_item = "从TMDB获取的信息不足以创建完整条目。"
                        elif tmdb_status == "tmdb_search_result_used": status_text_for_column = "TMDB部分匹配"; status_tooltip_for_item = "使用了TMDB搜索结果信息（可能不完整）。"
                        elif tmdb_status == "matched_douban": status_text_for_column = "匹配豆瓣数据"; status_tooltip_for_item = "此演员信息已根据豆瓣数据更新。"
                        elif tmdb_status == "tmdb_manual_selected": tree_item.setForeground(0, QBrush(QColor("blue"))); status_text_for_column = "TMDB已手动确认"; status_tooltip_for_item = "此演员的TMDB身份已手动确认。"
                        else:
                            status_text_for_column = "有效"
                            default_fg_brush = QBrush(QApplication.palette().color(QPalette.ColorRole.Text))
                            tree_item.setForeground(0, default_fg_brush)

                    tree_item.setText(2, status_text_for_column)
                    if status_tooltip_for_item:
                        for col_idx in range(self.cast_tree_widget.columnCount()):
                            tree_item.setToolTip(col_idx, status_tooltip_for_item)
            else:
                QTreeWidgetItem(self.cast_tree_widget, ["---", "无演员数据。可尝试“豆瓣搜索”或“新增演员”。"])
        else:
            self.selected_item_path_label.setText("路径: N/A")
            self.selected_item_reason_label.setText("原因: N/A")
            QTreeWidgetItem(self.cast_tree_widget, ["---", "请从左侧选择一个条目"])

        self.cast_tree_widget.resizeColumnToContents(0) # 调整第0列宽度
        self.cast_tree_widget.resizeColumnToContents(2) # 调整第2列（状态列）宽度
        self._update_actor_action_buttons_state()

        # --- 新增强制更新 ---
        self.cast_tree_widget.viewport().update()
        QApplication.processEvents()
        # --- 新增结束 ---
        self.main_app.log_to_progress_text_qt("DEBUG: _update_detail_display_qt finished.", "DEBUG")
    @pyqtSlot(QListWidgetItem, QListWidgetItem)
    def on_failed_item_select_qt(self, current_item, previous_item):
        if not current_item:
            self.current_selected_item_data = None
            self.current_api_cast_data = None
            self._update_detail_display_qt(None, "请从左侧选择一个条目。")
            return

        selected_index = self.failed_list_widget.row(current_item)
        if 0 <= selected_index < len(self.parsed_failed_items):
            self.current_selected_item_data = self.parsed_failed_items[selected_index]
            self.selected_item_path_label.setText(f"路径: {self.current_selected_item_data.get('path', 'N/A')}")
            self.selected_item_reason_label.setText(f"原因: {self.current_selected_item_data.get('reason', 'N/A')}")
            self.main_app.log_to_progress_text_qt(f"选中失败项: {self.current_selected_item_data.get('tmdb_id')} - {self.current_selected_item_data.get('type')}", "DEBUG")

            self.current_api_cast_data = None 
            
            local_cast_data = self._get_cast_from_local_json(self.current_selected_item_data) 
            
            if local_cast_data:
                self.main_app.log_to_progress_text_qt(f"已从本地JSON为失败项 '{self.current_selected_item_data.get('tmdb_id')}' 加载 {len(local_cast_data)} 位演员信息。", "INFO")
                self.current_api_cast_data = local_cast_data 
                self._update_detail_display_qt(self.current_selected_item_data, cast_list=self.current_api_cast_data)
            else:
                self.main_app.log_to_progress_text_qt(f"失败项 '{self.current_selected_item_data.get('tmdb_id')}'：本地JSON未找到演员信息或解析失败。", "WARN")
                self._update_detail_display_qt(self.current_selected_item_data, cast_list=None)
        else:
            self.current_selected_item_data = None
            self.current_api_cast_data = None
            self._update_detail_display_qt(None, "选择索引无效。")
    # ... (其他方法) ...

    @pyqtSlot(QTreeWidgetItem, int)
    def on_tree_item_changed_qt(self, item: QTreeWidgetItem, column: int):
        if not self.current_api_cast_data:
            return

        # Disconnect signal to prevent recursion during programmatic changes
        try:
            self.cast_tree_widget.itemChanged.disconnect(
                self.on_tree_item_changed_qt)
        except TypeError:
            # This can happen if the signal was already disconnected or never connected properly
            # self.main_app.log_to_progress_text_qt("DEBUG: itemChanged signal (on_tree_item_changed_qt) not connected or already disconnected.", "DEBUG")
            pass  # Usually safe to ignore this specific error here

        try:
            row_index = self.cast_tree_widget.indexOfTopLevelItem(item)
            if not (0 <= row_index < len(self.current_api_cast_data)):
                self.main_app.log_to_progress_text_qt(
                    f"编辑提交错误：行索引 {row_index} 无效。", "ERROR")
                return

            actor_data_entry = self.current_api_cast_data[row_index]

            # --- Store the value that was in the cell *before* this edit ---
            # We need to get this BEFORE we update actor_data_entry with the new text
            # One way is to assume actor_data_entry still holds the pre-edit value for the edited field.
            # This depends on when QTreeWidget updates its underlying model vs when this signal is emitted.
            # For robustness, it's better if this original value was stored when editing began,
            # but for now, we'll try to infer it.

            original_ui_text_in_cell = ""
            if column == 0:
                original_ui_text_in_cell = actor_data_entry.get("name", "")
            elif column == 1:
                original_ui_text_in_cell = actor_data_entry.get(
                    "character", "")

            # Get the new text from the UI cell after edit
            new_text_from_ui_cell = item.text(column).strip()

            # --- Update the self.current_api_cast_data ---
            text_to_set_in_model = new_text_from_ui_cell
            if column == 0:  # Actor name column
                if actor_data_entry.get("name") != new_text_from_ui_cell:
                    actor_data_entry["name"] = new_text_from_ui_cell
                    self.main_app.log_to_progress_text_qt(
                        f"演员名在行 {row_index} 更新为: '{new_text_from_ui_cell}'", "DEBUG")

            elif column == 1:  # Character name column
                cleaned_new_role = clean_character_name_dialog(
                    new_text_from_ui_cell)
                # Use cleaned version for model and cache
                text_to_set_in_model = cleaned_new_role
                if actor_data_entry.get("character") != cleaned_new_role:
                    actor_data_entry["character"] = cleaned_new_role
                    self.main_app.log_to_progress_text_qt(
                        f"角色名在行 {row_index} 更新为: '{cleaned_new_role}' (原始输入: '{new_text_from_ui_cell}')", "DEBUG")

                if new_text_from_ui_cell != cleaned_new_role:
                    # Update the UI cell if cleaning changed it
                    item.setText(1, cleaned_new_role)

            # --- Attempt to update the global translation cache ---
            # Use original_ui_text_in_cell as the key (what was there before the edit)
            # Use text_to_set_in_model as the value (the new, possibly cleaned, text)
            if original_ui_text_in_cell and isinstance(original_ui_text_in_cell, str) and \
               text_to_set_in_model and isinstance(text_to_set_in_model, str) and \
               original_ui_text_in_cell.strip() and text_to_set_in_model.strip() and \
               not contains_chinese(original_ui_text_in_cell) and contains_chinese(text_to_set_in_model):

                key_for_cache = original_ui_text_in_cell.strip()

                if hasattr(self.main_app, '_douban_api_instance') and \
                   hasattr(self.main_app._douban_api_instance, '_translation_cache'):

                    current_cache_val = self.main_app._douban_api_instance._translation_cache.get(
                        key_for_cache)

                    if current_cache_val is None or current_cache_val != text_to_set_in_model:
                        self.main_app._douban_api_instance._translation_cache[
                            key_for_cache] = text_to_set_in_model
                        self.main_app.log_to_progress_text_qt(
                            f"翻译缓存已更新 (手动修改): '{key_for_cache}' -> '{text_to_set_in_model}'", "INFO"
                        )
            # --- End of cache update logic ---

            # Update role name validity highlighting in the tree
            actor_name_val = actor_data_entry.get(
                "name", "")  # Use current value from model
            char_name_val = actor_data_entry.get(
                "character", "")  # Use current value from model

            is_valid_after_edit = is_role_name_valid(
                char_name_val, actor_name_val)
            default_color = QApplication.palette().text().color()
            item.setForeground(1, QBrush(default_color))
            font = item.font(1)
            font.setBold(False)
            item.setFont(1, font)

            if not is_valid_after_edit:
                item.setForeground(1, QBrush(QColor("red")))
                font = item.font(1)
                font.setBold(True)
                item.setFont(1, font)

        finally:
            # Reconnect signal
            try:
                self.cast_tree_widget.itemChanged.connect(
                    self.on_tree_item_changed_qt)
            except TypeError:
                # self.main_app.log_to_progress_text_qt("DEBUG: Error reconnecting itemChanged (on_tree_item_changed_qt).", "DEBUG")
                pass

    @pyqtSlot()
    def search_douban_for_selected_item(self):
        if not self.current_selected_item_data:
            QMessageBox.information(self, "提示", "请先从左侧列表选择一个媒体项。")
            return

        item = self.current_selected_item_data
        media_dir_path = item['path']
        original_main_app_mtype = self.main_app.mtype
        name_from_dir, year_from_dir, item_imdb_id = self.main_app.extract_info_from_media_directory(media_dir_path)
        self.main_app.mtype = original_main_app_mtype

        title_to_search = name_from_dir if name_from_dir else os.path.basename(media_dir_path)
        year_to_search = year_from_dir if year_from_dir else None
        media_type_to_search = item['type']
        
        if not title_to_search:
            QMessageBox.warning(self, "信息不足", "无法获取有效的标题进行豆瓣搜索。")
            return

        self.main_app.log_to_progress_text_qt(f"失败项 '{item.get('tmdb_id')}': 开始豆瓣搜索 '{title_to_search}' ...", "INFO")
        self.main_app.api_type_label.setText("豆瓣搜索中...")

        # --- <<< 显示忙碌提示对话框 >>> ---
        progress_dialog = QProgressDialog("正在从豆瓣获取演员信息...", "取消", 0, 0, self) # 父窗口是self
        progress_dialog.setWindowModality(Qt.WindowModality.ApplicationModal) # 设置为应用程序模态，阻止与其他窗口交互
        progress_dialog.setMinimumDuration(0) # 立即显示
        progress_dialog.setValue(0) # 表示不确定进度
        progress_dialog.setAutoClose(True) # 操作完成时自动关闭（如果setValue达到maximum）
        progress_dialog.setAutoReset(True) # 重置以备下次使用
        # progress_dialog.setCancelButton(None) # 如果不希望显示取消按钮

        # QApplication.processEvents() # 确保对话框立即显示，有时需要

        api_response = None
        try:
            # --- 执行耗时的API调用 ---
            # (这里的 QApplication.processEvents() 是为了让进度对话框有机会绘制出来)
            # (在实际的阻塞操作前调用一次，确保UI更新)
            QApplication.processEvents() # 允许UI事件处理，以便对话框显示

            api_response = self.main_app._douban_api_instance.get_acting(
                name=title_to_search,
                imdbid=item_imdb_id,
                mtype=media_type_to_search,
                year=year_to_search
            )
        finally:
            # --- <<< 关闭忙碌提示对话框 >>> ---
            # 如果操作很快，progress_dialog 可能还没来得及显示就被关闭了
            # setValue(maximum) 会使其自动关闭（如果setAutoClose(True)）
            # 或者直接调用 close() 或 accept() / reject()
            if progress_dialog.isVisible(): # 确保它可见再操作
                progress_dialog.close() # 或者 progress_dialog.accept()
            # QApplication.restoreOverrideCursor() # 如果之前用了 setOverrideCursor

        # --- 处理API响应 (这部分逻辑不变，但现在它在对话框关闭后执行) ---
        # self.cast_tree_widget.clear() # 这行可以移到 _process_and_display_cast_response 开头

        # 调用 _process_and_display_cast_response 来处理结果
        # 注意：_process_and_display_cast_response 内部如果还有耗时操作（如TMDB演员名称搜索），
        # 那些操作也会阻塞UI，除非它们也被类似地处理或异步化。
        self.current_api_cast_data = self._get_cast_from_local_json(item) or [] # 获取基础
        self._process_and_display_cast_response(api_response, item, 来源="豆瓣搜索")        
        media_dir_path = item['path']
        original_main_app_mtype = self.main_app.mtype # 保存主应用原来的mtype
        name_from_dir, year_from_dir, _ = self.main_app.extract_info_from_media_directory(media_dir_path)
        self.main_app.mtype = original_main_app_mtype # 恢复主应用原来的mtype

        title_to_search = name_from_dir if name_from_dir else os.path.basename(media_dir_path) # 如果没有标题用文件夹名
        year_to_search = year_from_dir if year_from_dir else None # 年份可以是None
        media_type_to_search = item['type'] # "movie" or "tv"

        if not title_to_search:
            QMessageBox.warning(self, "信息不足", "无法获取有效的标题进行豆瓣搜索。")
            return

        self.main_app.log_to_progress_text_qt(f"失败项 '{item.get('tmdb_id')}': 开始豆瓣名称搜索 '{title_to_search}' ({year_to_search or '任意年份'}, {media_type_to_search})", "INFO")
        self.main_app.api_type_label.setText("豆瓣名称搜索中...") # 更新UI提示

        # 调用 get_acting，不传递 imdbid，强制走名称搜索
        # douban_id_override 在这里应该是 None
        api_response = self.main_app._douban_api_instance.get_acting(
            name=title_to_search, 
            mtype=media_type_to_search, 
            year=year_to_search
        )

        self.cast_tree_widget.clear() # 清空旧的演员列表

        if not api_response:
            QMessageBox.warning(self, "搜索结果", "豆瓣名称搜索没有返回任何结果。")
            self._update_detail_display_qt(item, message="豆瓣名称搜索无结果。")
            self.main_app.api_type_label.setText("---")
            return

        if api_response.get("error"):
            error_msg = f"豆瓣名称搜索失败: {api_response.get('message', api_response.get('error'))}"
            QMessageBox.warning(self, "搜索错误", error_msg)
            self._update_detail_display_qt(item, message=error_msg)
            self.main_app.api_type_label.setText("---")
            return

        if api_response.get("search_candidates"):
            candidates = api_response.get("search_candidates")
            if not candidates:
                QMessageBox.information(self, "搜索结果", "豆瓣名称搜索未找到匹配的条目。")
                self._update_detail_display_qt(item, message="豆瓣名称搜索未找到匹配条目。")
                self.main_app.api_type_label.setText("---")
                return

            # 构建选择列表
            choices = [f"{c.get('title')} ({c.get('year', '未知年份')}) - ID: {c.get('id')}" for c in candidates]
            if not choices: # 以防万一 candidates 结构不对
                QMessageBox.warning(self, "错误", "无法解析豆瓣返回的候选列表。")
                self.main_app.api_type_label.setText("---")
                return

            item_text, ok = QInputDialog.getItem(self, "选择匹配的豆瓣条目", 
                                                f"为“{title_to_search}”找到多个结果：", choices, 0, False)
            
            if ok and item_text:
                selected_index = choices.index(item_text)
                selected_candidate = candidates[selected_index]
                selected_douban_id = selected_candidate.get("id")
                selected_media_type = selected_candidate.get("type", media_type_to_search) # 优先用候选的类型

                self.main_app.log_to_progress_text_qt(f"用户选择了豆瓣条目: ID={selected_douban_id}, Title='{selected_candidate.get('title')}'", "INFO")
                self.main_app.api_type_label.setText("获取选中条目演员...")

                # 使用选中的豆瓣ID再次调用 get_acting (这次用 douban_id_override)
                final_cast_response = self.main_app._douban_api_instance.get_acting(
                    name=selected_candidate.get('title'), # name 和 year 仅供日志或参考
                    mtype=selected_media_type,
                    year=selected_candidate.get('year'),
                    douban_id_override=selected_douban_id 
                )
                self._process_and_display_cast_response(final_cast_response, item,来源="豆瓣名称搜索 (用户选择)")
            else:
                self.main_app.log_to_progress_text_qt("用户取消了豆瓣条目选择。", "INFO")
                self._update_detail_display_qt(item, message="用户取消选择。")
                self.main_app.api_type_label.setText("---")
        
        elif api_response.get("cast") is not None: # 直接返回了演员表 (可能是精确匹配或唯一候选)
            self.main_app.log_to_progress_text_qt(f"豆瓣名称搜索找到匹配并直接获取了演员表。", "INFO")
            self._process_and_display_cast_response(api_response, item, 来源="豆瓣名称搜索 (自动匹配)")
        else:
            QMessageBox.warning(self, "未知结果", "豆瓣名称搜索返回了未知的结果结构。")
            self._update_detail_display_qt(item, message="豆瓣名称搜索返回未知结果。")
            self.main_app.api_type_label.setText("---")
    
    @pyqtSlot()
    def translate_selected_roles_manually(self):
        if not self.current_api_cast_data:
            QMessageBox.information(self, "无数据", "当前没有加载演员数据可供翻译。")
            return

        selected_tree_items = self.cast_tree_widget.selectedItems()
        if not selected_tree_items:
            QMessageBox.information(self, "提示", "请先在演员列表中选择要翻译角色的演员。")
            return

        if self.pending_manual_translations > 0:
            QMessageBox.information(self, "提示", f"已有 {self.pending_manual_translations} 个翻译任务正在进行中，请稍后再试。")
            return

        self.main_app.log_to_progress_text_qt("DEBUG: Entering translate_selected_roles_manually", "DEBUG")

        self.translation_progress_dialog = QProgressDialog("正在准备翻译任务...", None, 0, 0, self)
        self.translation_progress_dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.translation_progress_dialog.setMinimumDuration(0)
        self.translation_progress_dialog.setValue(0)
        self.translation_progress_dialog.setAutoClose(False)
        self.translation_progress_dialog.setAutoReset(False)
        self.translation_progress_dialog.setCancelButton(None) # 明确禁用取消按钮
        self.main_app.log_to_progress_text_qt("DEBUG: ProgressDialog created for selected roles.", "DEBUG")

        self.btn_translate_selected_roles.setEnabled(False)
        self.btn_translate_all_roles.setEnabled(False)
        self.btn_add_actor.setEnabled(False)
        self.btn_delete_actor.setEnabled(False)
        self.btn_move_actor_up.setEnabled(False)
        self.btn_move_actor_down.setEnabled(False)
        self.btn_save_changes.setEnabled(False)

        self.translation_progress_dialog.show()
        QApplication.processEvents()
        self.main_app.log_to_progress_text_qt(f"DEBUG: ProgressDialog (selected) isVisible: {self.translation_progress_dialog.isVisible()}", "DEBUG")

        count_started = 0
        actors_to_translate_info = []

        if hasattr(self, 'translation_progress_dialog') and self.translation_progress_dialog.isVisible():
            self.translation_progress_dialog.setLabelText(f"正在分析选中的角色...")
            QApplication.processEvents()
        else:
            self.main_app.log_to_progress_text_qt("DEBUG: ProgressDialog (selected) not visible when updating label (1).", "WARN")

        for tree_item in selected_tree_items:
            actor_idx = tree_item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(actor_idx, int) and 0 <= actor_idx < len(self.current_api_cast_data):
                actor_data = self.current_api_cast_data[actor_idx]
                # 翻译角色名
                char_name = actor_data.get("character", "")
                if should_translate_text(char_name):
                    text_stripped_char = char_name.strip()
                    actors_to_translate_info.append((text_stripped_char, char_name, actor_idx, "character"))
                # 翻译演员名
                actor_name_val = actor_data.get("name", "")
                if should_translate_text(actor_name_val):
                    text_stripped_actor = actor_name_val.strip()
                    actors_to_translate_info.append((text_stripped_actor, actor_name_val, actor_idx, "name"))
            else:
                self.main_app.log_to_progress_text_qt(f"警告：选中的演员条目 '{tree_item.text(0)}' 没有有效的索引数据。", "WARN")

        if not actors_to_translate_info:
            QMessageBox.information(self, "提示", "选中的字段无需翻译或已是中文。")
            if hasattr(self, 'translation_progress_dialog') and self.translation_progress_dialog.isVisible():
                self.translation_progress_dialog.close()
            self._update_actor_action_buttons_state()
            self.btn_save_changes.setEnabled(True)
            self.main_app.log_to_progress_text_qt("DEBUG: translate_selected_roles_manually - no tasks, exiting.", "DEBUG")
            return

        if hasattr(self, 'translation_progress_dialog') and self.translation_progress_dialog.isVisible():
            self.translation_progress_dialog.setLabelText(f"正在启动 {len(actors_to_translate_info)} 个翻译任务...")
            QApplication.processEvents()
        else:
            self.main_app.log_to_progress_text_qt("DEBUG: ProgressDialog (selected) not visible when updating label (2).", "WARN")

        for text_to_translate, original_text, actor_idx, field_type in actors_to_translate_info:
            if self._start_manual_translation(text_to_translate, original_text, actor_idx, field_type):
                count_started += 1

        if count_started > 0:
            self.main_app.log_to_progress_text_qt(f"已为选中的 {count_started} 个字段启动翻译任务。", "INFO")
            if hasattr(self, 'translation_progress_dialog') and self.translation_progress_dialog.isVisible():
                self.translation_progress_dialog.setLabelText(f"翻译进行中... ({self.pending_manual_translations} 个任务待完成)")
                QApplication.processEvents()
            else:
                self.main_app.log_to_progress_text_qt("DEBUG: ProgressDialog (selected) not visible when updating label (3).", "WARN")
        else:
            if hasattr(self, 'translation_progress_dialog') and self.translation_progress_dialog.isVisible():
                self.translation_progress_dialog.close()
            self._update_actor_action_buttons_state()
            self.btn_save_changes.setEnabled(True)
        self.main_app.log_to_progress_text_qt("DEBUG: Exiting translate_selected_roles_manually", "DEBUG")
    @pyqtSlot()
    def translate_all_roles_manually(self):
        if not self.current_api_cast_data:
            QMessageBox.information(self, "无数据", "当前没有加载演员数据可供翻译。")
            return
        if self.pending_manual_translations > 0:
            QMessageBox.information(self, "提示", f"已有 {self.pending_manual_translations} 个翻译任务正在进行中，请稍后再试。")
            return

        self.main_app.log_to_progress_text_qt("DEBUG: Entering translate_all_roles_manually", "DEBUG")

        self.translation_progress_dialog = QProgressDialog("正在准备翻译任务...", None, 0, 0, self)
        self.translation_progress_dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.translation_progress_dialog.setMinimumDuration(0)
        self.translation_progress_dialog.setValue(0)
        self.translation_progress_dialog.setAutoClose(False)
        self.translation_progress_dialog.setAutoReset(False)
        self.translation_progress_dialog.setCancelButton(None) # 明确禁用取消按钮
        self.main_app.log_to_progress_text_qt("DEBUG: ProgressDialog created for all roles.", "DEBUG")

        self.btn_translate_selected_roles.setEnabled(False)
        self.btn_translate_all_roles.setEnabled(False)
        self.btn_add_actor.setEnabled(False)
        self.btn_delete_actor.setEnabled(False)
        self.btn_move_actor_up.setEnabled(False)
        self.btn_move_actor_down.setEnabled(False)
        self.btn_save_changes.setEnabled(False)

        self.translation_progress_dialog.show()
        QApplication.processEvents()
        self.main_app.log_to_progress_text_qt(f"DEBUG: ProgressDialog (all) isVisible: {self.translation_progress_dialog.isVisible()}", "DEBUG")

        count_started = 0
        actors_to_translate_info = []

        if hasattr(self, 'translation_progress_dialog') and self.translation_progress_dialog.isVisible():
            self.translation_progress_dialog.setLabelText(f"正在分析需要翻译的字段...")
            QApplication.processEvents()
        else:
            self.main_app.log_to_progress_text_qt("DEBUG: ProgressDialog (all) not visible when updating label (1).", "WARN")

        for actor_idx, actor_data in enumerate(self.current_api_cast_data):
            char_name = actor_data.get("character", "")
            if should_translate_text(char_name):
                text_stripped_char = char_name.strip()
                actors_to_translate_info.append((text_stripped_char, char_name, actor_idx, "character"))
            
            actor_name_val = actor_data.get("name", "")
            if should_translate_text(actor_name_val):
                text_stripped_actor_name = actor_name_val.strip()
                actors_to_translate_info.append((text_stripped_actor_name, actor_name_val, actor_idx, "name"))

        if not actors_to_translate_info:
            QMessageBox.information(self, "提示", "当前列表所有字段无需翻译或已是中文。")
            if hasattr(self, 'translation_progress_dialog') and self.translation_progress_dialog.isVisible():
                self.translation_progress_dialog.close()
            self._update_actor_action_buttons_state()
            self.btn_save_changes.setEnabled(True)
            self.main_app.log_to_progress_text_qt("DEBUG: translate_all_roles_manually - no tasks, exiting.", "DEBUG")
            return

        if hasattr(self, 'translation_progress_dialog') and self.translation_progress_dialog.isVisible():
            self.translation_progress_dialog.setLabelText(f"正在启动 {len(actors_to_translate_info)} 个翻译任务...")
            QApplication.processEvents()
        else:
            self.main_app.log_to_progress_text_qt("DEBUG: ProgressDialog (all) not visible when updating label (2).", "WARN")

        for text_to_translate, original_text, actor_idx, field_type in actors_to_translate_info:
            if self._start_manual_translation(text_to_translate, original_text, actor_idx, field_type):
                count_started += 1
        
        if count_started > 0:
            self.main_app.log_to_progress_text_qt(f"已为当前列表中的 {count_started} 个字段启动翻译任务。", "INFO")
            if hasattr(self, 'translation_progress_dialog') and self.translation_progress_dialog.isVisible():
                self.translation_progress_dialog.setLabelText(f"翻译进行中... ({self.pending_manual_translations} 个任务待完成)")
                QApplication.processEvents()
            else:
                self.main_app.log_to_progress_text_qt("DEBUG: ProgressDialog (all) not visible when updating label (3).", "WARN")
        else:
            if hasattr(self, 'translation_progress_dialog') and self.translation_progress_dialog.isVisible():
                self.translation_progress_dialog.close()
            self._update_actor_action_buttons_state()
            self.btn_save_changes.setEnabled(True)
        self.main_app.log_to_progress_text_qt("DEBUG: Exiting translate_all_roles_manually", "DEBUG")
    def _process_and_display_cast_response(self, api_response_with_cast, item_data_for_ui,来源="豆瓣"):
        """
        处理从豆瓣API(get_acting)返回的演员表，并更新UI。
        新增逻辑：对比豆瓣演员与当前已有演员，对新增的豆瓣演员尝试TMDB名称搜索。
        修改：为TMDB演员搜索过程使用单个总进度对话框，并增加详细调试日志。
        """
        self.main_app.log_to_progress_text_qt(f"DEBUG_PDR: _process_and_display_cast_response called. Source: {来源}", "DEBUG")

        if not api_response_with_cast or api_response_with_cast.get("cast") is None:
            error_msg = f"{来源} 未能返回有效的演员信息。"
            if api_response_with_cast and "error" in api_response_with_cast:
                error_msg += f" API错误: {api_response_with_cast.get('message', api_response_with_cast['error'])}"
            QMessageBox.warning(self, "API结果", error_msg)
            self.main_app.log_to_progress_text_qt(error_msg, "WARN")
            self._update_detail_display_qt(item_data_for_ui, message=error_msg)
            self.main_app.api_type_label.setText("---")
            return

        douban_api_cast_list = api_response_with_cast.get("cast", [])
        self.main_app.log_to_progress_text_qt(f"DEBUG_PDR: Received douban_api_cast_list with {len(douban_api_cast_list)} items.", "DEBUG")
        if douban_api_cast_list:
             self.main_app.log_to_progress_text_qt(f"  DEBUG_PDR: First douban actor (if any): {str(douban_api_cast_list[0])[:100]}", "DEBUG")

        current_existing_cast = []
        if self.current_selected_item_data and \
           self.current_selected_item_data.get("tmdb_id") == item_data_for_ui.get("tmdb_id") and \
           self.current_api_cast_data:
            current_existing_cast = self.current_api_cast_data
            self.main_app.log_to_progress_text_qt(f"  DEBUG_PDR: Using current_api_cast_data ({len(current_existing_cast)} items) as existing cast.", "DEBUG")
        else:
            current_existing_cast = self._get_cast_from_local_json(item_data_for_ui) or []
            self.main_app.log_to_progress_text_qt(f"  DEBUG_PDR: Loaded {len(current_existing_cast)} actors from local JSON as existing cast.", "DEBUG")

        final_processed_cast_list = []
        processed_douban_actors_tracker = set()

        num_douban_actors = len(douban_api_cast_list)
        overall_tmdb_search_progress = None
        
        self.main_app.log_to_progress_text_qt(f"DEBUG_PDR: num_douban_actors = {num_douban_actors}", "DEBUG")

        if num_douban_actors > 0 and self.main_app.cfg_tmdb_api_key:
            self.main_app.log_to_progress_text_qt(f"DEBUG_PDR: Creating QProgressDialog for TMDB matching.", "DEBUG")
            overall_tmdb_search_progress = QProgressDialog(
                f"准备为豆瓣演员匹配TMDB信息...",
                "取消匹配", 0, num_douban_actors, self
            )
            overall_tmdb_search_progress.setWindowModality(Qt.WindowModality.ApplicationModal)
            overall_tmdb_search_progress.setMinimumDuration(100) # 确保能弹出来
            overall_tmdb_search_progress.setValue(0)
            overall_tmdb_search_progress.show() # 明确调用 show
            QApplication.processEvents() # 确保对话框立即显示
            self.main_app.log_to_progress_text_qt(f"DEBUG_PDR: QProgressDialog isVisible: {overall_tmdb_search_progress.isVisible() if overall_tmdb_search_progress else 'Not Created'}", "DEBUG")
        elif num_douban_actors == 0:
             self.main_app.log_to_progress_text_qt(f"DEBUG_PDR: No douban actors to process for TMDB matching.", "INFO")
        elif not self.main_app.cfg_tmdb_api_key:
             self.main_app.log_to_progress_text_qt(f"DEBUG_PDR: TMDB API Key not configured, skipping TMDB matching for douban actors.", "INFO")

        processed_actor_count_for_progress = 0
        self.main_app.log_to_progress_text_qt(f"DEBUG_PDR: Entering for loop to process {num_douban_actors} douban actors (if any).", "DEBUG")

        for douban_actor_info in douban_api_cast_list:
            self.main_app.log_to_progress_text_qt(f"  DEBUG_PDR: Loop iteration for douban_actor: {douban_actor_info.get('name', 'N/A')}", "DEBUG")

            if overall_tmdb_search_progress and overall_tmdb_search_progress.wasCanceled():
                self.main_app.log_to_progress_text_qt("TMDB演员匹配被用户取消。", "WARN")
                break
            
            processed_actor_count_for_progress += 1
            if overall_tmdb_search_progress and overall_tmdb_search_progress.isVisible():
                overall_tmdb_search_progress.setValue(processed_actor_count_for_progress)
                overall_tmdb_search_progress.setLabelText(
                    f"匹配TMDB: {douban_actor_info.get('name', '未知')} ({processed_actor_count_for_progress}/{num_douban_actors})"
                )
                QApplication.processEvents() # 确保标签更新
                self.main_app.log_to_progress_text_qt(f"    DEBUG_PDR: ProgressDialog updated for {douban_actor_info.get('name', 'N/A')}", "DEBUG")
            
            if not isinstance(douban_actor_info, dict): continue

            douban_actor_name = douban_actor_info.get("name")
            douban_actor_char = clean_character_name_dialog(douban_actor_info.get("character"))
            douban_actor_latin_name = douban_actor_info.get("original_name")
            douban_person_id_from_api = douban_actor_info.get("id")
            
            matched_existing_actor = None
            # ... (匹配 existing_actor 的逻辑，与之前版本一致) ...
            if isinstance(douban_person_id_from_api, int) and douban_person_id_from_api > 0:
                for existing_actor in current_existing_cast:
                    if str(existing_actor.get("id")) == str(douban_person_id_from_api):
                        matched_existing_actor = existing_actor; break
            if not matched_existing_actor and douban_actor_name:
                for existing_actor in current_existing_cast:
                    if existing_actor.get("name", "").strip() == douban_actor_name.strip() or \
                       (douban_actor_latin_name and existing_actor.get("original_name", "").strip() == douban_actor_latin_name.strip()):
                        matched_existing_actor = existing_actor; break
            
            actor_entry_for_final_list = {}
            tmdb_search_was_performed = False

            if matched_existing_actor:
                actor_entry_for_final_list = matched_existing_actor.copy()
                actor_entry_for_final_list["character"] = douban_actor_char
                if douban_actor_name: actor_entry_for_final_list["name"] = douban_actor_name
                if douban_actor_latin_name: actor_entry_for_final_list["original_name"] = douban_actor_latin_name
                if douban_actor_info.get("profile_path"): actor_entry_for_final_list["profile_path"] = douban_actor_info.get("profile_path")
                actor_entry_for_final_list["_tmdb_status"] = "matched_douban"
                self.main_app.log_to_progress_text_qt(f"    DEBUG_PDR: Actor '{douban_actor_name}' matched existing. Status: matched_douban", "DEBUG")
            else:
                self.main_app.log_to_progress_text_qt(f"    DEBUG_PDR: Actor '{douban_actor_name}' not in existing, trying TMDB search.", "DEBUG")
                tmdb_person_search_results = []
                if self.main_app.cfg_tmdb_api_key:
                    tmdb_search_was_performed = True
                    self.main_app.log_to_progress_text_qt(f"      DEBUG_PDR: Before calling search_tmdb_person_by_name for '{douban_actor_name}'", "DEBUG")
                    QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
                    tmdb_person_search_results = search_tmdb_person_by_name(
                        douban_actor_name, self.main_app.cfg_tmdb_api_key,
                        actor_original_name=douban_actor_latin_name
                    )
                    QApplication.restoreOverrideCursor()
                    self.main_app.log_to_progress_text_qt(f"      DEBUG_PDR: After calling search_tmdb_person_by_name for '{douban_actor_name}', found {len(tmdb_person_search_results)} results.", "DEBUG")

                if len(tmdb_person_search_results) == 1:
                    tmdb_person = tmdb_person_search_results[0]
                    actor_entry_for_final_list = format_tmdb_person_to_cast_entry(
                        tmdb_person, character_name=douban_actor_char, order=len(final_processed_cast_list)
                    )
                    if actor_entry_for_final_list: actor_entry_for_final_list["_tmdb_status"] = "tmdb_auto_matched"
                    else: actor_entry_for_final_list = self._create_actor_entry_from_douban_info(douban_actor_info, douban_actor_char, len(final_processed_cast_list), "tmdb_format_failed")
                elif len(tmdb_person_search_results) > 1:
                    actor_entry_for_final_list = self._create_actor_entry_from_douban_info(douban_actor_info, douban_actor_char, len(final_processed_cast_list), "tmdb_multiple_matches")
                    actor_entry_for_final_list["_tmdb_candidates"] = tmdb_person_search_results
                else: # No TMDB results or API key issue
                   actor_entry_for_final_list = self._create_actor_entry_from_douban_info(douban_actor_info, douban_actor_char, len(final_processed_cast_list), "tmdb_not_found") # Default to not_found if search was attempted
                   if not self.main_app.cfg_tmdb_api_key: # If no API key, it's not really "not_found" but "not_searched"
                        actor_entry_for_final_list["_tmdb_status"] = "tmdb_search_skipped_no_key"


            if actor_entry_for_final_list: # Ensure actor_entry is not None (e.g. if TMDB search returned nothing and we chose to discard)
                douban_actor_key = (douban_actor_name, douban_actor_char, douban_actor_info.get("id")) 
                if douban_actor_key not in processed_douban_actors_tracker:
                    final_processed_cast_list.append(actor_entry_for_final_list)
                    processed_douban_actors_tracker.add(douban_actor_key)
            
            if tmdb_search_was_performed and self.main_app.cfg_tmdb_api_key:
                time.sleep(0.15) # Brief delay after TMDB API call

        self.main_app.log_to_progress_text_qt(f"DEBUG_PDR: Exited for loop for douban actors.", "DEBUG")

        if overall_tmdb_search_progress and overall_tmdb_search_progress.isVisible():
            overall_tmdb_search_progress.setValue(num_douban_actors)
            overall_tmdb_search_progress.close()
            self.main_app.log_to_progress_text_qt(f"DEBUG_PDR: Closed QProgressDialog.", "DEBUG")
            
        existing_tmdb_ids_in_final = {str(actor.get("id")) for actor in final_processed_cast_list if actor.get("id")}
        if current_existing_cast:
            for existing_actor in current_existing_cast:
                if str(existing_actor.get("id")) not in existing_tmdb_ids_in_final:
                    existing_actor_copy = existing_actor.copy()
                    existing_actor_copy["order"] = len(final_processed_cast_list)
                    final_processed_cast_list.append(existing_actor_copy)
        
        final_processed_cast_list.sort(key=lambda x: x.get("order", 999))
        for i, actor_data in enumerate(final_processed_cast_list):
            actor_data['order'] = i

        self.current_api_cast_data = final_processed_cast_list
        self.main_app.log_to_progress_text_qt(f"DEBUG_PDR: Final processed cast list has {len(self.current_api_cast_data)} actors. Updating UI.", "DEBUG")
        self._update_detail_display_qt(item_data_for_ui, cast_list=self.current_api_cast_data)
        self.main_app.api_type_label.setText(f"{来源} (已处理)")
        self._update_actor_action_buttons_state()
        self.main_app.log_to_progress_text_qt(f"DEBUG_PDR: _process_and_display_cast_response finished.", "DEBUG")

    def _create_actor_entry_from_douban_info(self, douban_actor_info, character_name, order, tmdb_status):
        """辅助函数：根据豆瓣演员信息创建一个基础的演员条目字典"""
        return {
            "id": None, # TMDB ID 未知
            "name": douban_actor_info.get("name"),
            "original_name": douban_actor_info.get("original_name", douban_actor_info.get("name")),
            "character": character_name,
            "profile_path": douban_actor_info.get("profile_path"),
            "adult": False, "gender": 0, "known_for_department": "Acting",
            "popularity": 0.0, "cast_id": None,
            "credit_id": f"douban_placeholder_{int(time.time())}_{order}",
            "order": order,
            "_tmdb_status": tmdb_status, # 例如 "tmdb_not_found", "tmdb_multiple_matches"
            "_douban_person_id": douban_actor_info.get("id") # 保存豆瓣原始ID
        }

    def _search_web(self, engine_url_template):
        if not self.current_selected_item_data:
            QMessageBox.information(self, "提示", "请先从左侧列表选择一个媒体项。")
            return
        item = self.current_selected_item_data
        title_for_search = item.get(
            "display_title_for_list", os.path.basename(item['path']))
        if title_for_search == item.get("tmdb_id"):
            original_mtype = self.main_app.mtype
            title, year, _ = self.main_app.extract_info_from_media_directory(
                item['path'])
            self.main_app.mtype = original_mtype
            if title:
                title_for_search = title
            if year:
                title_for_search += f" {year}"
        query = f"{title_for_search} 演员表"
        search_url = engine_url_template + \
            urllib.parse.quote(query.encode('utf-8'))
        self.main_app.log_to_progress_text_qt(f"正在浏览器中搜索: {query}", "INFO")
        webbrowser.open_new_tab(search_url)

    def _show_tmdb_candidate_selection_dialog(self, actor_data_list_index: int, candidates: List[Dict]):
        if not candidates:
            self.main_app.log_to_progress_text_qt("DEBUG: _show_tmdb_candidate_selection_dialog called with empty candidates.", "DEBUG")
            return

        actor_to_confirm = self.current_api_cast_data[actor_data_list_index]
        self.main_app.log_to_progress_text_qt(f"DEBUG: Showing candidate selection for actor: {actor_to_confirm.get('name')}", "DEBUG")
        
        dialog = ActorCandidateSelectionDialog(candidates, self)
        
        # --- 关键：调用 exec() 并检查返回值 ---
        self.main_app.log_to_progress_text_qt("DEBUG: About to call dialog.exec()", "DEBUG")
        if dialog.exec(): # dialog.exec() 会显示对话框并阻塞，直到用户关闭它
            self.main_app.log_to_progress_text_qt("DEBUG: dialog.exec() returned True (Accepted)", "DEBUG")
            selected_option = dialog.get_selected_id()
            self.main_app.log_to_progress_text_qt(f"DEBUG: Candidate dialog accepted. Selected option: {selected_option}", "DEBUG")

            if selected_option == "manual_input":
                new_person_id_str, ok_input = QInputDialog.getText(self, "手动输入ID", "请输入正确的TMDB Person ID:")
                if ok_input and new_person_id_str and new_person_id_str.strip().isdigit():
                    self._confirm_and_update_actor_with_tmdb_id(actor_data_list_index, int(new_person_id_str.strip()))
                elif ok_input: # 用户点了OK但输入无效
                    QMessageBox.warning(self, "输入无效", "TMDB Person ID必须是数字。")
            elif isinstance(selected_option, int): # 用户选择了一个TMDB ID
                self._confirm_and_update_actor_with_tmdb_id(actor_data_list_index, selected_option)
            elif selected_option is None: # 用户选择了 "保持现状" 或通过其他方式接受了对话框但没有有效选择
                self.main_app.log_to_progress_text_qt(f"用户为演员 '{actor_to_confirm.get('name')}' 选择保持现状或未做有效选择。", "INFO")
            # else: 如果 selected_option 是其他意外的值，这里可以加日志
            
        else: # 用户点击了Cancel按钮或关闭了对话框 (dialog.exec() 返回 0 或 QDialog.Rejected)
            self.main_app.log_to_progress_text_qt("DEBUG: dialog.exec() returned False (Rejected/Cancelled)", "DEBUG")
            self.main_app.log_to_progress_text_qt(f"用户取消为演员 '{actor_to_confirm.get('name')}' 选择TMDB匹配项。", "INFO")

    def _confirm_and_update_actor_with_tmdb_id(self, actor_data_list_index: int, tmdb_person_id: int):
        if not self.main_app.cfg_tmdb_api_key:
            QMessageBox.warning(self, "API Key缺失", "TMDB API Key未配置。")
            return

        original_actor_data = self.current_api_cast_data[actor_data_list_index]
        self.main_app.log_to_progress_text_qt(f"为演员 '{original_actor_data.get('name')}' 使用TMDB ID {tmdb_person_id} 获取详细信息...", "INFO")

        progress_dialog = QProgressDialog(f"正在获取TMDB演员 {tmdb_person_id} 详细信息...", None, 0, 0, self)
        progress_dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
        progress_dialog.setMinimumDuration(0)
        progress_dialog.setValue(0)
        QApplication.processEvents()

        full_person_details = None
        try:
            full_person_details = get_tmdb_person_details(tmdb_person_id, self.main_app.cfg_tmdb_api_key)
        finally:
            if progress_dialog.isVisible():
                progress_dialog.close()
        
        if full_person_details:
            # 保留原始的角色名和顺序（或基于当前列表的顺序）
            character_name = original_actor_data.get("character", "") 
            current_order = original_actor_data.get("order", actor_data_list_index)

            updated_actor_entry = format_tmdb_person_to_cast_entry(
                full_person_details,
                character_name=character_name,
                order=current_order
            )
            if updated_actor_entry:
                updated_actor_entry["_tmdb_status"] = "tmdb_manual_selected" # 新的状态
                # --- <<< 关键日志：打印替换前后的数据 >>> ---
                self.main_app.log_to_progress_text_qt(f"DEBUG_CONFIRM: Replacing actor at index {actor_data_list_index}.", "DEBUG")
                if 0 <= actor_data_list_index < len(self.current_api_cast_data):
                    self.main_app.log_to_progress_text_qt(f"  DEBUG_CONFIRM: OLD data: {self.current_api_cast_data[actor_data_list_index]}", "DEBUG")
                else:
                    self.main_app.log_to_progress_text_qt(f"  DEBUG_CONFIRM: OLD data index {actor_data_list_index} out of bounds for current_api_cast_data (len {len(self.current_api_cast_data) if self.current_api_cast_data else 0}).", "WARN")
                
                self.current_api_cast_data[actor_data_list_index] = updated_actor_entry # 替换
                
                self.main_app.log_to_progress_text_qt(f"  DEBUG_CONFIRM: NEW data at index {actor_data_list_index}: {self.current_api_cast_data[actor_data_list_index]}", "DEBUG")
                self.main_app.log_to_progress_text_qt(f"演员 '{original_actor_data.get('name')}' 已更新为TMDB演员 '{updated_actor_entry.get('name')}' (ID: {tmdb_person_id})。", "SUCCESS")
                # --- <<< 日志结束 >>> ---
                # 替换掉 self.current_api_cast_data 中的旧条目
                self.current_api_cast_data[actor_data_list_index] = updated_actor_entry
                self.main_app.log_to_progress_text_qt(f"演员 '{original_actor_data.get('name')}' 已更新为TMDB演员 '{updated_actor_entry.get('name')}' (ID: {tmdb_person_id})。", "SUCCESS")
                self._update_detail_display_qt(self.current_selected_item_data, cast_list=self.current_api_cast_data)
                self._update_actor_action_buttons_state()
            else:
                QMessageBox.warning(self, "错误", f"从TMDB获取的演员ID {tmdb_person_id} 数据格式化失败。")
        else:
            QMessageBox.warning(self, "获取失败", f"未能从TMDB获取ID为 {tmdb_person_id} 的演员详细信息。")

    @pyqtSlot()
    def search_google_for_selected(self): self._search_web(
        "https://www.google.com/search?q=")

    @pyqtSlot()
    def search_baidu_for_selected(self): self._search_web(
        "https://www.baidu.com/s?wd=")

    def _rewrite_failure_log_file(self):
        if not self.failure_log_path:
            self.main_app.log_to_progress_text_qt("错误：失败日志路径未设置。", "ERROR")
            return False
        try:
            with open(self.failure_log_path, 'w', encoding='utf-8', newline='') as f:
                writer = csv.writer(f)
                f.write(
                    f"# 处理失败的媒体列表 (更新于 {time.strftime('%Y%m%d_%H%M%S')})\n# 原始文件: {os.path.basename(self.failure_log_path)}\n# 格式: 媒体类型,TMDB_ID,\"目录路径\",\"失败原因\"\n" + "="*50 + "\n")
                for item in self.parsed_failed_items:
                    writer.writerow([item.get(k, 'N/A')
                                    for k in ['type', 'tmdb_id', 'path', 'reason']])
            self.main_app.log_to_progress_text_qt(
                f"失败日志 '{os.path.basename(self.failure_log_path)}' 已更新。", "INFO")
            return True
        except Exception as e:
            self.main_app.log_to_progress_text_qt(
                f"重写失败日志 '{self.failure_log_path}' 失败: {e}", "ERROR")
            QMessageBox.critical(self, "错误", f"重写失败日志文件时发生错误:\n{e}")
            return False

    def _remove_item_from_current_view_and_data(self, list_widget_row_to_remove):
        if not (0 <= list_widget_row_to_remove < self.failed_list_widget.count()):
            self.main_app.log_to_progress_text_qt(
                f"试图移除无效列表索引: {list_widget_row_to_remove}", "ERROR")
            return False
        self.failed_list_widget.takeItem(list_widget_row_to_remove)
        if 0 <= list_widget_row_to_remove < len(self.parsed_failed_items):
            removed_item = self.parsed_failed_items.pop(
                list_widget_row_to_remove)
            self.main_app.log_to_progress_text_qt(
                f"已从视图移除项: {removed_item.get('display_title_for_list', removed_item.get('tmdb_id'))}", "DEBUG")
        else:
            self.main_app.log_to_progress_text_qt(
                f"警告: 列表框索引 {list_widget_row_to_remove} 与 parsed_failed_items 不同步。", "WARN")
        if self.failed_list_widget.count() > 0:
            self.failed_list_widget.setCurrentRow(
                min(list_widget_row_to_remove, self.failed_list_widget.count() - 1))
        else:
            self.current_selected_item_data = None
            self.current_api_cast_data = None
            self._update_detail_display_qt(None, "所有失败项已处理或移除。")
        return True

# failure_window_qt.py

    # failure_window_qt.py
    # class FailureProcessingWindowQt:
        # ...
    # failure_window_qt.py
    # class FailureProcessingWindowQt:
        # ... (其他方法) ...

    @pyqtSlot()
    def save_changes_and_remove(self):
        items_to_process_info = []
        selected_list_widget_items = self.failed_list_widget.selectedItems()

        if selected_list_widget_items:
            for list_item_widget in selected_list_widget_items:
                original_row_index = self.failed_list_widget.row(list_item_widget)
                if 0 <= original_row_index < len(self.parsed_failed_items):
                    item_data_from_parsed_list = self.parsed_failed_items[original_row_index]
                    items_to_process_info.append(
                        (item_data_from_parsed_list, list_item_widget, original_row_index)
                    )
                else:
                    self.main_app.log_to_progress_text_qt(f"警告：选中的列表项 '{list_item_widget.text()}' 无法在 parsed_failed_items 中找到对应数据。", "WARN")
        elif self.current_selected_item_data and \
             self.current_selected_item_data.get("reason", "").startswith("手动按名称加载进行编辑"):
            items_to_process_info.append(
                (self.current_selected_item_data, None, None)
            )
            self.main_app.log_to_progress_text_qt(f"准备处理按名称加载的项: {self.current_selected_item_data.get('display_title_for_list')}", "INFO")
        elif self.failed_list_widget.currentItem():
             current_list_widget_item = self.failed_list_widget.currentItem()
             current_row_idx = self.failed_list_widget.row(current_list_widget_item)
             if 0 <= current_row_idx < len(self.parsed_failed_items):
                 if self.current_selected_item_data and self.parsed_failed_items[current_row_idx]['tmdb_id'] == self.current_selected_item_data.get('tmdb_id'):
                     items_to_process_info.append(
                         (self.current_selected_item_data, current_list_widget_item, current_row_idx)
                     )
                 else:
                    self.main_app.log_to_progress_text_qt(f"警告：右侧编辑数据与列表选中项不一致，将以列表选中项为准进行处理。", "WARN")
                    self.on_failed_item_select_qt(current_list_widget_item, None)
                    if self.current_selected_item_data:
                         items_to_process_info.append(
                            (self.current_selected_item_data, current_list_widget_item, current_row_idx)
                         )
                    else:
                        QMessageBox.information(self, "提示", "无法确定要处理的项，请重新选择。")
                        return
             else:
                QMessageBox.information(self, "提示", "请先选择一个或多个媒体项进行保存。")
                return
        else:
            QMessageBox.information(self, "提示", "请先选择一个或多个媒体项进行保存。")
            return

        if not items_to_process_info:
            QMessageBox.warning(self, "无处理项", "未能确定任何要处理的媒体项。")
            return

        num_items = len(items_to_process_info)
        confirm_message = f"您准备处理 {num_items} 个媒体项。\n\n"
        first_item_data_to_check, _, _ = items_to_process_info[0]
        is_first_item_current_editing = (self.current_selected_item_data and
                                         first_item_data_to_check.get("path") == self.current_selected_item_data.get("path") and
                                         first_item_data_to_check.get("tmdb_id") == self.current_selected_item_data.get("tmdb_id"))
        if is_first_item_current_editing and self.current_api_cast_data:
            item_display_name_single = first_item_data_to_check.get("display_title_for_list", first_item_data_to_check.get("tmdb_id", "未知项"))
            still_invalid_single = [
                f"演员 '{a.get('name','?')}': 角色 '{a.get('character','')}'"
                for a in self.current_api_cast_data
                if not is_role_name_valid(a.get("character",""), a.get("name",""))
            ]
            if still_invalid_single:
                msg_invalid = f"当前编辑的项 '{item_display_name_single}' 中以下角色名似乎仍无效:\n" + "\n".join(still_invalid_single[:3]) + \
                              (f"\n...等共 {len(still_invalid_single)} 个。" if len(still_invalid_single) > 3 else "")
                confirm_message += f"警告：{msg_invalid}\n\n"
        confirm_message += "确定要将这些项目应用修改（如适用）、保存到项目文件，并从失败日志移除（如适用）吗？"
        if QMessageBox.question(self, f"确认处理 {num_items} 项", confirm_message,
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) == QMessageBox.StandardButton.No:
            self.main_app.log_to_progress_text_qt("用户取消了批量保存修改操作。", "DEBUG")
            return

        processed_successfully_count = 0
        processed_with_issues_count = 0
        indices_in_parsed_list_to_remove = []

        for item_data, list_widget_item_ref, original_parsed_list_idx in items_to_process_info:
            item_display_name = item_data.get("display_title_for_list", item_data.get("tmdb_id", "未知项"))
            self.main_app.log_to_progress_text_qt(f"--- 开始处理项: {item_display_name} ---", "INFO")
            
            cast_data_for_this_item = None
            is_current_editing_item_loop = (self.current_selected_item_data and
                                            item_data.get("path") == self.current_selected_item_data.get("path") and
                                            item_data.get("tmdb_id") == self.current_selected_item_data.get("tmdb_id"))
            if is_current_editing_item_loop and self.current_api_cast_data is not None:
                cast_data_for_this_item = self.current_api_cast_data
                self.main_app.log_to_progress_text_qt(f"  DEBUG_SAVE: 使用当前编辑区的演员数据 ({len(cast_data_for_this_item) if cast_data_for_this_item else 0}人) 进行保存。", "DEBUG")
                if cast_data_for_this_item:
                    self.main_app.log_to_progress_text_qt(f"    DEBUG_SAVE: First actor in cast_data_for_this_item: Name='{cast_data_for_this_item[0].get('name')}', ID='{cast_data_for_this_item[0].get('id')}', Char='{cast_data_for_this_item[0].get('character')}'", "DEBUG")
            else:
                cast_data_for_this_item = self._get_cast_from_local_json(item_data)
                if cast_data_for_this_item is not None:
                    self.main_app.log_to_progress_text_qt(f"  DEBUG_SAVE: 从本地JSON为项 '{item_display_name}' 加载了 {len(cast_data_for_this_item)} 位演员。", "DEBUG")
                else:
                    self.main_app.log_to_progress_text_qt(f"  DEBUG_SAVE: 未能从本地JSON为项 '{item_display_name}' 加载演员数据。", "DEBUG")
            
            media_type = item_data.get('type')
            tmdb_id = item_data.get('tmdb_id')
            if not all([media_type, tmdb_id]):
                self.main_app.log_to_progress_text_qt(f"保存修改失败：项 '{item_display_name}' 媒体类型或TMDB ID缺失。", "ERROR")
                processed_with_issues_count += 1; continue

            target_save_base_dir_final = None
            type_subdir_name_for_save = "tmdb-movies2" if media_type == "movie" else "tmdb-tv"
            if self.main_app.cfg_override_cache_path and os.path.isdir(self.main_app.cfg_override_cache_path):
                target_save_base_dir_final = os.path.join(self.main_app.cfg_override_cache_path, type_subdir_name_for_save, tmdb_id)
            elif self.main_app.cfg_main_cache_path and os.path.isdir(self.main_app.cfg_main_cache_path):
                target_save_base_dir_final = os.path.join(self.main_app.cfg_main_cache_path, type_subdir_name_for_save, tmdb_id)
            else:
                self.main_app.log_to_progress_text_qt(f"错误：无法确定 '{item_display_name}' 的保存目录。", "ERROR")
                processed_with_issues_count += 1; continue
            target_save_base_dir_final = os.path.normpath(target_save_base_dir_final)
            try: os.makedirs(target_save_base_dir_final, exist_ok=True)
            except Exception as e_mkdir_save:
                self.main_app.log_to_progress_text_qt(f"创建保存目录 '{target_save_base_dir_final}' 失败: {e_mkdir_save}。", "ERROR")
                processed_with_issues_count += 1; continue
            
            json_files_to_consider_paths = []
            path_ref_for_structure = item_data.get("path")
            if not path_ref_for_structure or not os.path.isdir(path_ref_for_structure):
                if self.main_app.cfg_main_cache_path and os.path.isdir(self.main_app.cfg_main_cache_path):
                    path_ref_for_structure = os.path.join(self.main_app.cfg_main_cache_path, type_subdir_name_for_save, tmdb_id)
                else:
                    main_json_filename_default = "all.json" if media_type == "movie" else "series.json"
                    json_files_to_consider_paths = [os.path.join(target_save_base_dir_final, main_json_filename_default)]
            if not json_files_to_consider_paths and path_ref_for_structure and os.path.isdir(path_ref_for_structure):
                json_files_to_consider_paths = self.main_app._get_json_files_for_media_item(
                    path_ref_for_structure, media_type, force_get_all_relevant_types=True
                )
            elif not json_files_to_consider_paths :
                 main_json_filename_default = "all.json" if media_type == "movie" else "series.json"
                 json_files_to_consider_paths = [os.path.join(target_save_base_dir_final, main_json_filename_default)]

            item_json_update_all_ok = True
            json_content_actually_changed_for_this_media_item = False # 追踪当前媒体项是否有任何JSON文件内容改变

            if json_files_to_consider_paths and cast_data_for_this_item is not None:
                for f_path_ref in json_files_to_consider_paths:
                    json_filename = os.path.basename(f_path_ref)
                    target_json_save_path = os.path.join(target_save_base_dir_final, json_filename)
                    source_json_to_read = target_json_save_path
                    if not os.path.exists(source_json_to_read): source_json_to_read = f_path_ref
                    if not os.path.exists(source_json_to_read):
                        self.main_app.log_to_progress_text_qt(f"警告：源JSON文件 '{source_json_to_read}' (模板: '{f_path_ref}') 不存在，无法更新 '{json_filename}'。", "WARN")
                        if json_filename not in ["all.json", "series.json"]: continue
                        item_json_update_all_ok = False; break
                    try:
                        with open(source_json_to_read, 'r', encoding='utf-8') as f_read: data_to_update = json.load(f_read)
                        
                        original_json_str_for_compare = json.dumps(data_to_update.get("casts", {}).get("cast", data_to_update.get("credits", {}).get("cast", data_to_update.get("cast", []))), sort_keys=True)
                        
                        updated_content, names_upd, chars_upd = self.main_app._update_characters_in_this_json(data_to_update, cast_data_for_this_item)
                        self.main_app.log_to_progress_text_qt(f"    DEBUG_SAVE: _update_characters_in_this_json for '{json_filename}' reported: names_updated={names_upd}, chars_updated={chars_upd}", "DEBUG")
                        
                        updated_json_cast_str_for_compare = json.dumps(updated_content.get("casts", {}).get("cast", updated_content.get("credits", {}).get("cast", updated_content.get("cast", []))), sort_keys=True)

                        if original_json_str_for_compare != updated_json_cast_str_for_compare:
                            json_content_actually_changed_for_this_media_item = True # 只要有一个JSON文件改变了，就标记
                            self.main_app.log_to_progress_text_qt(f"    DEBUG_SAVE: Cast in JSON content for '{json_filename}' HAS changed.", "DEBUG")
                        else:
                            self.main_app.log_to_progress_text_qt(f"    DEBUG_SAVE: Cast in JSON content for '{json_filename}' has NOT changed after _update_characters_in_this_json.", "DEBUG")
                        
                        needs_write = json_content_actually_changed_for_this_media_item or (os.path.normpath(target_json_save_path).lower() != os.path.normpath(f_path_ref).lower())

                        if needs_write:
                            with open(target_json_save_path, 'w', encoding='utf-8') as f_write: json.dump(updated_content, f_write, indent=4, ensure_ascii=False)
                            self.main_app.log_to_progress_text_qt(f"  文件 '{json_filename}' 已保存到 '{target_json_save_path}'.", "INFO")
                            try: os.utime(target_json_save_path, None)
                            except Exception as e_utime: self.main_app.log_to_progress_text_qt(f"  更新文件 '{json_filename}' 时间戳失败: {e_utime}", "WARN")
                    except Exception as e_json_save_loop:
                        self.main_app.log_to_progress_text_qt(f"  处理/保存文件 '{json_filename}' 到 '{target_json_save_path}' 失败: {e_json_save_loop}", "ERROR")
                        item_json_update_all_ok = False; break
            elif not cast_data_for_this_item and json_files_to_consider_paths:
                 self.main_app.log_to_progress_text_qt(f"项 '{item_display_name}' 无有效演员数据，不执行JSON更新。", "DEBUG")
            
            if item_json_update_all_ok and json_content_actually_changed_for_this_media_item:
                self.main_app.save_to_processed_log(media_type, tmdb_id)
                if self.main_app.cfg_enable_emby_item_refresh:
                    if self.main_app.cfg_emby_server_url and self.main_app.cfg_emby_api_key:
                        title_for_emby_search = item_display_name
                        main_json_to_read_title_path = os.path.join(target_save_base_dir_final, "all.json" if media_type == "movie" else "series.json")
                        if os.path.exists(main_json_to_read_title_path):
                            try:
                                with open(main_json_to_read_title_path, 'r', encoding='utf-8') as f_title: json_title_data = json.load(f_title)
                                title_for_emby_search = json_title_data.get("title", json_title_data.get("name", item_display_name))
                            except Exception: pass
                        emby_item_id = emby_handler.get_emby_item_id_from_tmdb(
                            tmdb_id, title_for_emby_search, media_type,
                            self.main_app.cfg_emby_server_url, self.main_app.cfg_emby_api_key
                        )
                        if emby_item_id:
                            is_recursive_refresh = True if media_type == "tv" else False
                            refresh_success = emby_handler.refresh_emby_item_metadata(
                                emby_item_id, self.main_app.cfg_emby_server_url, self.main_app.cfg_emby_api_key,
                                recursive=is_recursive_refresh, metadata_refresh_mode="FullRefresh",
                                image_refresh_mode="Default", replace_all_metadata_param=True, replace_all_images_param=False
                            )
                            if refresh_success: self.main_app.log_to_progress_text_qt(f"手动处理：Emby刷新请求已为 '{item_display_name}' 发送。", "SUCCESS")
                            else: self.main_app.log_to_progress_text_qt(f"手动处理：为 '{item_display_name}' 发送Emby刷新请求失败。", "WARN")
                        else:
                            self.main_app.log_to_progress_text_qt(f"手动处理：未能为 '{item_display_name}' (TMDb ID: {tmdb_id}) 找到对应的Emby Item ID，无法自动刷新。", "WARN")
                    else:
                        self.main_app.log_to_progress_text_qt("手动处理：Emby服务器未配置，跳过自动刷新。", "INFO")
                else:
                    self.main_app.log_to_progress_text_qt(f"手动处理：根据配置，未对 '{item_display_name}' 发送Emby刷新通知。", "INFO")
                
                if original_parsed_list_idx is not None: 
                    if original_parsed_list_idx not in indices_in_parsed_list_to_remove:
                        indices_in_parsed_list_to_remove.append(original_parsed_list_idx)
                elif item_data.get("reason", "").startswith("手动按名称加载进行编辑"):
                    for i, failed_item_in_list in enumerate(self.parsed_failed_items):
                        if failed_item_in_list.get("tmdb_id") == tmdb_id and failed_item_in_list.get("type") == media_type:
                            if i not in indices_in_parsed_list_to_remove: indices_in_parsed_list_to_remove.append(i); break
                processed_successfully_count += 1
            elif item_json_update_all_ok and not json_content_actually_changed_for_this_media_item:
                self.main_app.log_to_progress_text_qt(f"项 '{item_display_name}' 的JSON内容未发生实际变化，不计为成功处理（不从列表移除）。", "INFO")
            else: # item_json_update_all_ok is False
                processed_with_issues_count += 1
        
        if indices_in_parsed_list_to_remove:
            indices_in_parsed_list_to_remove.sort(reverse=True)
            for idx_to_remove_from_data in indices_in_parsed_list_to_remove:
                if 0 <= idx_to_remove_from_data < len(self.parsed_failed_items):
                    self.parsed_failed_items.pop(idx_to_remove_from_data)
            if not self._rewrite_failure_log_file():
                QMessageBox.warning(self, "警告", "项目修改已保存，但更新失败日志文件出错。")

        self.failed_list_widget.clear()
        for item_d in self.parsed_failed_items:
            list_display_entry = f"[{item_d['type'].upper()}] {item_d.get('display_title_for_list', item_d['tmdb_id'])}"
            if item_d.get('reason'): list_display_entry += f" - {item_d['reason'][:25]}..." if len(item_d['reason']) > 25 else f" - {item_d['reason']}"
            self.failed_list_widget.addItem(list_display_entry)

        if self.current_selected_item_data and \
           self.current_selected_item_data.get("reason", "").startswith("手动按名称加载进行编辑") and \
           (processed_successfully_count > 0 or processed_with_issues_count > 0) and \
           any(info[0].get('tmdb_id') == self.current_selected_item_data.get('tmdb_id') for info in items_to_process_info if info[0].get('tmdb_id') == self.current_selected_item_data.get('tmdb_id')):
            self.current_selected_item_data = None
            self.current_api_cast_data = None
            self._update_detail_display_qt(None, "按名加载的项已处理。请选择或加载新项。")
        elif self.failed_list_widget.count() > 0:
            self.failed_list_widget.setCurrentRow(0)
        else:
            self.current_selected_item_data = None
            self.current_api_cast_data = None
            self._update_detail_display_qt(None, "所有失败项已处理或移除。")
        
        self._update_actor_action_buttons_state()
        summary_msg = f"处理完成: {processed_successfully_count} 个项目成功应用修改并更新了JSON文件。"
        if processed_with_issues_count > 0: summary_msg += f"\n{processed_with_issues_count} 个项目处理时遇到问题。"
        QMessageBox.information(self, "处理结果", summary_msg)
        self.main_app.log_to_progress_text_qt(summary_msg, "INFO")
    @pyqtSlot()
    def remove_from_list_only(self):
        current_list_item = self.failed_list_widget.currentItem()
        if not current_list_item:
            QMessageBox.information(self, "提示", "请先从左侧列表选择一个媒体项。")
            return
        selected_index = self.failed_list_widget.row(current_list_item)
        item_display_name = current_list_item.text()
        if QMessageBox.question(self, "确认忽略", f"确定要从当前列表移除项目吗？\n\n{item_display_name}\n\n此操作不修改原始失败日志，重载后项目会再出现。", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            self._remove_item_from_current_view_and_data(selected_index)
            self.main_app.log_to_progress_text_qt(
                f"项目 '{item_display_name}' 已从当前列表临时移除。", "INFO")
        else:
            self.main_app.log_to_progress_text_qt("用户取消了临时移除操作。", "DEBUG")

    def closeEvent(self, event):
        if hasattr(self.main_app, 'failure_window_qt_instance') and self.main_app.failure_window_qt_instance == self:
            self.main_app.failure_window_qt_instance = None
        super().closeEvent(event)
    @pyqtSlot(QTreeWidgetItem, QTreeWidgetItem) # 槽函数的参数根据信号来
    def _update_actor_action_buttons_state_on_selection(self, current, previous):
        self._update_actor_action_buttons_state()
    @pyqtSlot()
    def delete_selected_actor(self):
        if not self.current_api_cast_data:
            QMessageBox.information(self, "提示", "当前没有可操作的演员数据。")
            return

        selected_item = self.cast_tree_widget.currentItem()
        if not selected_item:
            QMessageBox.information(self, "提示", "请先在演员列表中选择要删除的演员。")
            return

        # 获取选中演员在 QTreeWidget 中的顶层索引
        row_index = self.cast_tree_widget.indexOfTopLevelItem(selected_item)

        if 0 <= row_index < len(self.current_api_cast_data):
            actor_to_delete_name = self.current_api_cast_data[row_index].get("name", "未知演员")
            reply = QMessageBox.question(self, "确认删除",
                                        f"确定要删除演员 '{actor_to_delete_name}' 吗？\n此操作仅影响当前编辑，保存后生效。",
                                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                        QMessageBox.StandardButton.No)

            if reply == QMessageBox.StandardButton.Yes:
                # 从数据源中删除
                del self.current_api_cast_data[row_index]
                self.main_app.log_to_progress_text_qt(f"演员 '{actor_to_delete_name}' 已从当前编辑列表中移除。", "INFO")

                # 从UI中删除 (直接重新加载整个演员列表到UI是最简单可靠的方式)
                # 或者也可以用 self.cast_tree_widget.takeTopLevelItem(row_index)
                # 但为了确保数据和UI完全同步，尤其在有order的情况下，重新加载更好
                self._update_detail_display_qt(self.current_selected_item_data, cast_list=self.current_api_cast_data)
                
                # 更新按钮状态，因为演员数量或选中状态可能改变
                self._update_actor_action_buttons_state()
        else:
            self.main_app.log_to_progress_text_qt(f"删除演员错误：选择的行索引 {row_index} 无效。", "ERROR")
            QMessageBox.critical(self, "错误", "无法删除演员，选择的行索引无效。")
    def _start_manual_translation(self, text: str, original_text: str, actor_idx: int, field_type: str, specific_engine: Optional[str] = None) -> bool:
        print(f"调试演员名翻译[手动-启动]: 准备翻译 '{text}', 字段类型: '{field_type}', 演员索引: {actor_idx}") # <--- 新增
        self.main_app.log_to_progress_text_qt(f"DEBUG: _start_manual_translation called with: text='{text}', actor_idx={actor_idx}, field_type='{field_type}'", "DEBUG")
        """启动一个手动翻译线程任务，如果文本需要翻译。"""
        if not text or not text.strip() or contains_chinese(text): # 如果为空、或已包含中文，则不翻译
            # self.main_app.log_to_progress_text_qt(f"文本 '{text}' 无需翻译。", "DEBUG")
            return False 
        
        text_stripped = text.strip()
        # 简单的短名跳过逻辑 (可以从 app_window_qt 的 TranslationThread 借鉴或做得更完善)
        if len(text_stripped) == 1 and 'A' <= text_stripped <= 'Z':
            # self.main_app.log_to_progress_text_qt(f"文本 '{text_stripped}' 为单大写字母，跳过翻译。", "DEBUG")
            return False
        if len(text_stripped) == 2 and text_stripped.isupper() and text_stripped.isalpha():
            # self.main_app.log_to_progress_text_qt(f"文本 '{text_stripped}' 为双大写字母，跳过翻译。", "DEBUG")
            return False

        # TODO (可选): 在这里加入从全局翻译缓存 (self.main_app._douban_api_instance._translation_cache) 读取的逻辑
        # 如果缓存命中，直接调用 _handle_manual_translation_result 并返回 True

        thread = ManualTranslationThread(text_stripped, original_text, actor_idx, field_type, self)
        thread.translation_done.connect(self._handle_manual_translation_result)
        thread.finished.connect(lambda t=thread: self._on_manual_translation_thread_finished(t)) # 使用lambda传递线程实例
        self.manual_translation_threads.append(thread)
        self.pending_manual_translations += 1
        thread.start()
        self.main_app.log_to_progress_text_qt(f"启动手动翻译: '{text_stripped}' (演员索引: {actor_idx}, 字段: {field_type})", "DEBUG")
        return True

    @pyqtSlot(str, object, int, str)
    def _handle_manual_translation_result(self, original_text: str, translated_text_obj: Optional[str], actor_idx: int, field_type: str):
        handle_start_time = time.time()
        result_debug_id = f"Result_{actor_idx}_{field_type}_{id(self)}" # 使用id(self)确保唯一性，如果一个槽函数被多个信号连接
        log_prefix = f"DEBUG_TIME: [{result_debug_id}]"

        self.main_app.log_to_progress_text_qt(f"{log_prefix} _handle_manual_translation_result START for original '{original_text}'", "DEBUG")
        
        translated_text: Optional[str] = None
        if isinstance(translated_text_obj, str):
            translated_text = translated_text_obj
        elif translated_text_obj is not None:
            try:
                translated_text = str(translated_text_obj)
                self.main_app.log_to_progress_text_qt(f"警告：手动翻译结果非字符串，已尝试转换: {type(translated_text_obj)} -> '{translated_text}'", "WARN")
            except Exception as e_conv:
                self.main_app.log_to_progress_text_qt(f"错误：手动翻译结果无法转换为字符串: {type(translated_text_obj)}, Error: {e_conv}", "ERROR")
        
        self.main_app.log_to_progress_text_qt(f"  {log_prefix} Converted translated_text: '{translated_text}'", "DEBUG")

        self.pending_manual_translations = max(0, self.pending_manual_translations - 1)
        self.main_app.log_to_progress_text_qt(f"  {log_prefix} Pending manual translations: {self.pending_manual_translations}", "DEBUG")

        if hasattr(self, 'translation_progress_dialog') and self.translation_progress_dialog.isVisible():
            if self.pending_manual_translations > 0:
                self.translation_progress_dialog.setLabelText(f"翻译进行中... ({self.pending_manual_translations} 个任务待完成)")
            else:
                self.translation_progress_dialog.setLabelText("翻译完成，正在更新界面...")
            QApplication.processEvents()
        
        if not (self.current_api_cast_data and 0 <= actor_idx < len(self.current_api_cast_data)):
            self.main_app.log_to_progress_text_qt(f"  {log_prefix} Invalid actor_idx {actor_idx} or no cast data. Current pending: {self.pending_manual_translations}", "WARN")
            if self.pending_manual_translations == 0:
                self.main_app.log_to_progress_text_qt(f"  {log_prefix} All pending, but actor_idx invalid. Closing dialog.", "DEBUG")
                if hasattr(self, 'translation_progress_dialog') and self.translation_progress_dialog.isVisible():
                    self.translation_progress_dialog.close()
                self._update_actor_action_buttons_state()
                self.btn_save_changes.setEnabled(True)
            return

        actor_data = self.current_api_cast_data[actor_idx]
        actor_name_for_log = actor_data.get("name", f"索引{actor_idx}")

        if translated_text and translated_text.strip():
            self.main_app.log_to_progress_text_qt(f"手动翻译成功: '{original_text}' -> '{translated_text}' (演员: {actor_name_for_log}, 字段: {field_type})", "SUCCESS")
            if field_type == "character":
                actor_data["character"] = translated_text.strip()
                self.main_app.log_to_progress_text_qt(f"    {log_prefix} DEBUG: Updated character for actor_idx {actor_idx}. New data: {self.current_api_cast_data[actor_idx]}", "DEBUG")
            elif field_type == "name":
                actor_data["name"] = translated_text.strip()
                self.main_app.log_to_progress_text_qt(f"    {log_prefix} DEBUG: Updated name for actor_idx {actor_idx}. New data: {self.current_api_cast_data[actor_idx]}", "DEBUG")
        else:
            self.main_app.log_to_progress_text_qt(f"手动翻译失败或返回空: '{original_text}' (演员: {actor_name_for_log}, 字段: {field_type})", "WARN")
        
        if self.pending_manual_translations == 0:
            self.main_app.log_to_progress_text_qt(f"{log_prefix} All pending tasks finished. BEFORE _update_detail_display_qt.", "DEBUG")
            update_ui_start_time = time.time()
            self._update_detail_display_qt(self.current_selected_item_data, cast_list=self.current_api_cast_data)
            update_ui_end_time = time.time()
            self.main_app.log_to_progress_text_qt(f"{log_prefix} AFTER _update_detail_display_qt. UI update took {update_ui_end_time - update_ui_start_time:.3f}s.", "DEBUG")
            
            if hasattr(self, 'translation_progress_dialog') and self.translation_progress_dialog.isVisible():
                self.main_app.log_to_progress_text_qt(f"{log_prefix} Closing ProgressDialog (all tasks finished).", "DEBUG")
                self.translation_progress_dialog.close()
            self._update_actor_action_buttons_state()
            self.btn_save_changes.setEnabled(True)
        
        handle_end_time = time.time()
        self.main_app.log_to_progress_text_qt(f"{log_prefix} _handle_manual_translation_result END. Took {handle_end_time - handle_start_time:.3f}s.", "DEBUG")
    @pyqtSlot(QThread) # 接收线程实例作为参数
    def _on_manual_translation_thread_finished(self, thread_instance: QThread):
        if thread_instance in self.manual_translation_threads:
            self.manual_translation_threads.remove(thread_instance)
        
        # 如果所有线程对象都已从列表中移除，并且挂起计数仍大于0（异常情况）
        # 或者即使计数为0，也检查一下是否需要刷新（如果_handle_manual_translation_result没有在最后一次调用时刷新）
        if not self.manual_translation_threads and self.pending_manual_translations == 0:
            self.main_app.log_to_progress_text_qt("所有翻译线程均已结束且任务计数为0，确保UI已刷新。", "DEBUG")
            # 再次调用以确保，即使 _handle_manual_translation_result 的最后一次调用时条件没满足
            self._update_detail_display_qt(self.current_selected_item_data, cast_list=self.current_api_cast_data)
            self._update_actor_action_buttons_state()
        elif not self.manual_translation_threads and self.pending_manual_translations > 0:
            # ... (之前的警告和重置逻辑) ...
            self._update_detail_display_qt(self.current_selected_item_data, cast_list=self.current_api_cast_data) # 也刷新
            self._update_actor_action_buttons_state()
    @pyqtSlot()
    def add_actor_manually(self):
        if not self.current_selected_item_data: # 确保有一个媒体项被选中，演员是属于媒体项的
            QMessageBox.information(self, "提示", "请先选择一个媒体项以添加演员。")
            return

        if not self.main_app.cfg_tmdb_api_key:
            QMessageBox.warning(self, "TMDB API Key缺失", "未配置TMDB API Key，无法获取演员信息。\n请在主程序设置中配置。")
            self.main_app.log_to_progress_text_qt("新增演员失败：TMDB API Key未配置。", "WARN")
            return

        # 1. 弹出输入对话框获取 TMDB Person ID
        person_id_str, ok = QInputDialog.getText(self, "新增演员", "请输入演员的TMDB Person ID:")

        if ok and person_id_str and person_id_str.strip():
            try:
                person_id = int(person_id_str.strip())
            except ValueError:
                QMessageBox.warning(self, "输入错误", "输入的TMDB Person ID无效，请输入纯数字。")
                return

            self.main_app.log_to_progress_text_qt(f"尝试通过TMDB API获取演员ID {person_id} 的信息...", "INFO")
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor) # 设置等待光标

            # 2. 调用TMDB API获取演员详情
            # (确保 get_tmdb_person_details 已从 utils 导入)
            tmdb_person_data = get_tmdb_person_details(person_id, self.main_app.cfg_tmdb_api_key)
            
            QApplication.restoreOverrideCursor() # 恢复光标

            if tmdb_person_data:
                # 3. 格式化演员数据
                # (确保 format_tmdb_person_to_cast_entry 已从 utils 导入)
                # 新增演员的角色名可以默认为空或"未知"，让用户后续编辑
                # order 可以设置为当前列表的末尾
                new_order = len(self.current_api_cast_data) if self.current_api_cast_data else 0
                new_actor_entry = format_tmdb_person_to_cast_entry(
                    tmdb_person_data,
                    character_name="", # 初始角色名为空，让用户编辑
                    order=new_order
                )

                if new_actor_entry:
                    # 4. 添加到数据列表
                    if self.current_api_cast_data is None: # 如果之前没有演员数据，初始化列表
                        self.current_api_cast_data = []
                    
                    # 检查是否已存在该演员 (通过ID)
                    if any(str(actor.get("id")) == str(new_actor_entry.get("id")) for actor in self.current_api_cast_data):
                        QMessageBox.information(self, "提示", f"演员 '{new_actor_entry.get('name')}' (ID: {new_actor_entry.get('id')}) 已存在于列表中。")
                        return

                    self.current_api_cast_data.append(new_actor_entry)
                    self.main_app.log_to_progress_text_qt(f"成功添加演员 '{new_actor_entry.get('name')}' 到当前编辑列表。", "SUCCESS")

                    # 5. 刷新UI
                    self._update_detail_display_qt(self.current_selected_item_data, cast_list=self.current_api_cast_data)
                    
                    # 选中新添加的演员并使其可见 (可选)
                    last_item_index = self.cast_tree_widget.topLevelItemCount() - 1
                    if last_item_index >= 0:
                        newly_added_tree_item = self.cast_tree_widget.topLevelItem(last_item_index)
                        self.cast_tree_widget.setCurrentItem(newly_added_tree_item)
                        self.cast_tree_widget.scrollToItem(newly_added_tree_item)
                        # 可以考虑直接让用户编辑新添加演员的角色名
                        # self.cast_tree_widget.editItem(newly_added_tree_item, 1) # 1 是角色名列

                    # 6. 更新按钮状态
                    self._update_actor_action_buttons_state()
                else:
                    QMessageBox.warning(self, "错误", "从TMDB获取的演员数据格式化失败。")
                    self.main_app.log_to_progress_text_qt(f"格式化演员ID {person_id} 的TMDB数据失败。", "ERROR")
            else:
                QMessageBox.warning(self, "获取失败", f"未能从TMDB获取到ID为 {person_id} 的演员信息。\n请检查ID是否正确或网络连接。")
                self.main_app.log_to_progress_text_qt(f"从TMDB获取演员ID {person_id} 的信息失败。", "WARN")
        elif ok: # 用户点击了OK，但输入为空
            QMessageBox.information(self, "提示", "请输入有效的TMDB Person ID。")

    # failure_window_qt.py
# ... (其他方法) ...

    @pyqtSlot()
    def move_selected_actor_up(self):
        if not self.current_api_cast_data or len(self.current_api_cast_data) < 2: # 少于2个演员无法移动
            return

        selected_tree_item = self.cast_tree_widget.currentItem()
        if not selected_tree_item:
            QMessageBox.information(self, "提示", "请先选择要上移的演员。")
            return

        current_index = self.cast_tree_widget.indexOfTopLevelItem(selected_tree_item)

        if current_index > 0: # 确保不是第一个元素
            # 1. 在数据列表中移动
            actor_to_move = self.current_api_cast_data.pop(current_index)
            self.current_api_cast_data.insert(current_index - 1, actor_to_move)
            
            # 2. 更新所有演员的 'order' 字段 (可选但推荐，以保持数据一致性)
            for i, actor_data in enumerate(self.current_api_cast_data):
                actor_data['order'] = i
            
            self.main_app.log_to_progress_text_qt(f"演员 '{actor_to_move.get('name')}' 已上移。", "DEBUG")

            # 3. 更新UI (重新加载列表)
            # 保存当前选中的演员的ID，以便移动后重新选中
            selected_actor_id = actor_to_move.get("id")
            self._update_detail_display_qt(self.current_selected_item_data, cast_list=self.current_api_cast_data)
            
            # 重新选中移动后的项
            if selected_actor_id is not None:
                for i in range(self.cast_tree_widget.topLevelItemCount()):
                    item = self.cast_tree_widget.topLevelItem(i)
                    # 假设 self.current_api_cast_data 中的第 i 个元素对应UI中的第 i 个顶层项
                    # 并且其 'id' 存储在某个地方或可以直接从 self.current_api_cast_data[i] 获取
                    if self.current_api_cast_data[i].get("id") == selected_actor_id:
                        self.cast_tree_widget.setCurrentItem(item)
                        self.cast_tree_widget.scrollToItem(item)
                        break
            
            # 4. 更新按钮状态
            self._update_actor_action_buttons_state()
        else:
            self.main_app.log_to_progress_text_qt("无法上移：已是第一个演员或未选中。", "DEBUG")


    @pyqtSlot()
    def load_media_by_name_for_editing(self):
        if not self.main_app.cfg_tmdb_api_key:
            QMessageBox.warning(self, "TMDB API Key缺失", "未配置TMDB API Key，无法搜索影视。\n请在主程序设置中配置。")
            return

        media_name_query, ok = QInputDialog.getText(self, "按名称加载", "请输入要搜索的影视名称:")
        if not (ok and media_name_query and media_name_query.strip()):
            if ok : QMessageBox.information(self, "提示", "影视名称不能为空。")
            return
        media_name_query = media_name_query.strip()

        media_types_options = ["电影 (Movie)", "电视剧 (TV)", "任意类型"]
        selected_type_display, ok = QInputDialog.getItem(self, "选择媒体类型", "请选择要搜索的媒体类型:", media_types_options, 0, False)
        if not ok: return
        
        search_media_type_param = None
        if selected_type_display == "电影 (Movie)": search_media_type_param = "movie"
        elif selected_type_display == "电视剧 (TV)": search_media_type_param = "tv"

        self.main_app.log_to_progress_text_qt(f"[{time.strftime('%H:%M:%S')}] 开始通过TMDB API按名称搜索影视: '{media_name_query}', 类型: {search_media_type_param or '任意'}", "INFO")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        tmdb_search_results = search_tmdb_media_by_name(
            media_name_query, self.main_app.cfg_tmdb_api_key, media_type=search_media_type_param
        )
        QApplication.restoreOverrideCursor()
        self.main_app.log_to_progress_text_qt(f"[{time.strftime('%H:%M:%S')}] TMDB API 搜索完成.", "DEBUG")

        self.main_app.log_to_progress_text_qt(f"DEBUG: TMDB API search_tmdb_media_by_name returned {len(tmdb_search_results)} results.", "DEBUG")
        if tmdb_search_results:
            for i, res_debug in enumerate(tmdb_search_results[:5]):
                self.main_app.log_to_progress_text_qt(f"  DEBUG Result {i}: {res_debug}", "DEBUG")

        if not tmdb_search_results:
            QMessageBox.information(self, "无结果", f"未能通过名称 '{media_name_query}' 找到任何匹配的影视。")
            self.main_app.log_to_progress_text_qt(f"TMDB名称搜索 '{media_name_query}' 无结果。", "WARN")
            return

        selected_tmdb_media_info = None
        if len(tmdb_search_results) == 1:
            selected_tmdb_media_info = tmdb_search_results[0]
            self.main_app.log_to_progress_text_qt(f"DEBUG: TMDB search returned 1 result, auto-selecting.", "DEBUG")
        else:
            self.main_app.log_to_progress_text_qt(f"DEBUG: TMDB search returned {len(tmdb_search_results)} results, preparing choices for QInputDialog.", "DEBUG")
            choices = [
                f"{res.get('title', res.get('name', '未知标题'))} ({res.get('year', '未知年份')}) - {res.get('media_type', '').upper()} - ID: {res.get('id')}"
                for res in tmdb_search_results
            ] #确保TV剧集也能正确显示标题
            self.main_app.log_to_progress_text_qt(f"  DEBUG: Generated choices for QInputDialog: {choices}", "DEBUG")
            if not choices:
                QMessageBox.warning(self, "错误", "无法为TMDB搜索结果构建选择列表。")
                self.main_app.log_to_progress_text_qt("ERROR: Failed to build choices from TMDB results.", "ERROR")
                return
            item_text, ok = QInputDialog.getItem(self, "选择匹配的影视", f"为“{media_name_query}”找到多个结果：", choices, 0, False)
            self.main_app.log_to_progress_text_qt(f"  DEBUG: QInputDialog.getItem returned: ok={ok}, item_text='{item_text}'", "DEBUG")
            if ok and item_text:
                selected_index = choices.index(item_text)
                selected_tmdb_media_info = tmdb_search_results[selected_index]
            else:
                self.main_app.log_to_progress_text_qt("用户取消了影视选择或QInputDialog未返回有效项。", "INFO")
                return

        if not selected_tmdb_media_info: # 双重保险
            self.main_app.log_to_progress_text_qt("ERROR: selected_tmdb_media_info is None after selection logic.", "ERROR")
            return

        tmdb_id = str(selected_tmdb_media_info.get("id"))
        media_type = selected_tmdb_media_info.get("media_type")
        display_title = selected_tmdb_media_info.get("title", selected_tmdb_media_info.get("name", "未知标题"))

        self.main_app.log_to_progress_text_qt(f"[{time.strftime('%H:%M:%S')}] 用户选择/自动选择加载影视: '{display_title}' (TMDB ID: {tmdb_id}, 类型: {media_type})", "INFO")

        path_to_load_from = None
        source_description = ""
        type_subdir_name = "tmdb-movies2" if media_type == "movie" else "tmdb-tv"
        main_json_filename = "all.json" if media_type == "movie" else "series.json"
        found_in_failure_list = False
        item_data_from_failure_list = None

        self.main_app.log_to_progress_text_qt(f"[{time.strftime('%H:%M:%S')}] 开始检查失败列表...", "DEBUG")
        for idx, failed_item_data_loop in enumerate(self.parsed_failed_items):
            if failed_item_data_loop.get("tmdb_id") == tmdb_id and failed_item_data_loop.get("type") == media_type:
                path_to_load_from = os.path.normpath(failed_item_data_loop.get("path"))
                source_description = f"失败列表项 (源路径参考: {path_to_load_from})"
                item_data_from_failure_list = failed_item_data_loop
                self.failed_list_widget.clearSelection()
                list_widget_item = self.failed_list_widget.item(idx)
                if list_widget_item: self.failed_list_widget.setCurrentItem(list_widget_item)
                found_in_failure_list = True
                self.main_app.log_to_progress_text_qt(f"按名加载：影视 '{display_title}' 在当前失败列表中找到。", "INFO")
                break
        self.main_app.log_to_progress_text_qt(f"[{time.strftime('%H:%M:%S')}] 检查失败列表结束. Found: {found_in_failure_list}", "DEBUG")

        if not found_in_failure_list:
            self.main_app.log_to_progress_text_qt(f"[{time.strftime('%H:%M:%S')}] 未在失败列表找到，检查覆盖缓存: {self.main_app.cfg_override_cache_path}", "DEBUG")
            if self.main_app.cfg_override_cache_path: # 先检查路径是否配置
                is_override_dir = os.path.isdir(self.main_app.cfg_override_cache_path) # 再检查是否是目录
                self.main_app.log_to_progress_text_qt(f"  [{time.strftime('%H:%M:%S')}] os.path.isdir(override_cache_path) returned: {is_override_dir}", "DEBUG")
                if is_override_dir:
                    override_media_item_base_dir = os.path.join(self.main_app.cfg_override_cache_path, type_subdir_name, tmdb_id)
                    override_media_item_base_dir = os.path.normpath(override_media_item_base_dir)
                    override_json_path = os.path.join(override_media_item_base_dir, main_json_filename)
                    self.main_app.log_to_progress_text_qt(f"  [{time.strftime('%H:%M:%S')}] 尝试检查覆盖缓存路径: {override_json_path}", "DEBUG")
                    exists_in_override = os.path.exists(override_json_path)
                    self.main_app.log_to_progress_text_qt(f"    [{time.strftime('%H:%M:%S')}] os.path.exists(override_json_path) returned: {exists_in_override}", "DEBUG")
                    if exists_in_override:
                        path_to_load_from = override_media_item_base_dir
                        source_description = f"覆盖缓存 (路径: {path_to_load_from})"
                        self.main_app.log_to_progress_text_qt(f"按名加载：影视 '{display_title}' 在覆盖缓存中找到。", "INFO")
                    else:
                        self.main_app.log_to_progress_text_qt(f"按名加载：影视 '{display_title}' 未在覆盖缓存中找到主JSON。", "DEBUG")
                else:
                     self.main_app.log_to_progress_text_qt("按名加载：覆盖缓存路径配置了，但它不是一个有效目录。", "DEBUG")
            else:
                self.main_app.log_to_progress_text_qt("按名加载：覆盖缓存未配置，跳过检查覆盖缓存。", "DEBUG")
            self.main_app.log_to_progress_text_qt(f"[{time.strftime('%H:%M:%S')}] 检查覆盖缓存结束.", "DEBUG")


        if not path_to_load_from:
            self.main_app.log_to_progress_text_qt(f"[{time.strftime('%H:%M:%S')}] 未在覆盖缓存找到，检查主缓存: {self.main_app.cfg_main_cache_path}", "DEBUG")
            if self.main_app.cfg_main_cache_path: # 先检查路径是否配置
                is_main_dir = os.path.isdir(self.main_app.cfg_main_cache_path) # 再检查是否是目录
                self.main_app.log_to_progress_text_qt(f"  [{time.strftime('%H:%M:%S')}] os.path.isdir(main_cache_path) returned: {is_main_dir}", "DEBUG")
                if is_main_dir:
                    main_cache_media_item_base_dir = os.path.join(self.main_app.cfg_main_cache_path, type_subdir_name, tmdb_id)
                    main_cache_media_item_base_dir = os.path.normpath(main_cache_media_item_base_dir)
                    main_cache_json_path = os.path.join(main_cache_media_item_base_dir, main_json_filename)
                    self.main_app.log_to_progress_text_qt(f"  [{time.strftime('%H:%M:%S')}] 尝试检查主缓存路径: {main_cache_json_path}", "DEBUG")
                    exists_in_main = os.path.exists(main_cache_json_path)
                    self.main_app.log_to_progress_text_qt(f"    [{time.strftime('%H:%M:%S')}] os.path.exists(main_cache_json_path) returned: {exists_in_main}", "DEBUG")
                    if exists_in_main:
                        path_to_load_from = main_cache_media_item_base_dir
                        source_description = f"主缓存 (路径: {path_to_load_from})"
                        self.main_app.log_to_progress_text_qt(f"按名加载：影视 '{display_title}' 在主缓存中找到。", "INFO")
                    else:
                        self.main_app.log_to_progress_text_qt(f"按名加载：影视 '{display_title}' 未在主缓存中找到主JSON。", "WARN")
                else:
                    self.main_app.log_to_progress_text_qt("按名加载：主缓存路径配置了，但它不是一个有效目录。", "WARN")
            else:
                self.main_app.log_to_progress_text_qt("按名加载：主缓存未配置，跳过检查主缓存。", "WARN")
            self.main_app.log_to_progress_text_qt(f"[{time.strftime('%H:%M:%S')}] 检查主缓存结束.", "DEBUG")

        if not path_to_load_from:
            QMessageBox.warning(self, "文件未找到", f"未能为影视 '{display_title}' (ID: {tmdb_id}) 在任何已知位置找到对应的JSON文件。")
            self.main_app.log_to_progress_text_qt(f"[{time.strftime('%H:%M:%S')}] 按名加载：最终未能为 '{display_title}' 找到可加载的JSON文件。", "ERROR")
            return

        self.main_app.log_to_progress_text_qt(f"[{time.strftime('%H:%M:%S')}] 成功确定加载路径: {path_to_load_from} (来源: {source_description})", "INFO")
        
        temp_item_data = {}
        if item_data_from_failure_list:
            temp_item_data = item_data_from_failure_list.copy()
            temp_item_data["reason"] = f"从失败列表加载并编辑 (原原因: {item_data_from_failure_list.get('reason', 'N/A')})"
            temp_item_data["display_title_for_list"] = f"{display_title} ({selected_tmdb_media_info.get('year', 'N/A')}) [来自失败列表]"
        else:
            temp_item_data = {
                "path": path_to_load_from,
                "tmdb_id": tmdb_id, "type": media_type,
                "reason": f"手动按名称加载进行编辑 (源: {source_description})",
                "display_title_for_list": f"{display_title} ({selected_tmdb_media_info.get('year', 'N/A')}) [手动加载]"
            }
        
        if not found_in_failure_list: self.failed_list_widget.clearSelection()
            
        self.current_selected_item_data = temp_item_data
        self.main_app.log_to_progress_text_qt(f"DEBUG: temp_item_data for loading local JSON: {temp_item_data}", "DEBUG")

        self.current_api_cast_data = self._get_cast_from_local_json(temp_item_data)
        
        if self.current_api_cast_data is not None:
            self.main_app.log_to_progress_text_qt(f"DEBUG: _get_cast_from_local_json returned {len(self.current_api_cast_data)} cast members.", "DEBUG")
            if self.current_api_cast_data:
                self.main_app.log_to_progress_text_qt(f"  DEBUG: First actor from local JSON: Name='{self.current_api_cast_data[0].get('name')}', Char='{self.current_api_cast_data[0].get('character')}'", "DEBUG")
        else:
            self.main_app.log_to_progress_text_qt("DEBUG: _get_cast_from_local_json returned None.", "DEBUG")

        if self.current_api_cast_data is None:
            self.main_app.log_to_progress_text_qt(f"警告：从 '{path_to_load_from}' 加载演员信息失败或无演员。", "WARN")
            self._update_detail_display_qt(temp_item_data, message=f"从 {source_description} 加载演员失败或无演员。")
        else:
            self.main_app.log_to_progress_text_qt(f"成功从 '{source_description}' 加载 {len(self.current_api_cast_data)} 位演员。", "INFO")
            self._update_detail_display_qt(temp_item_data, cast_list=self.current_api_cast_data)
        
        self._update_actor_action_buttons_state()
        self.main_app.log_to_progress_text_qt(f"[{time.strftime('%H:%M:%S')}] load_media_by_name_for_editing 完成.", "DEBUG")
    @pyqtSlot()
    def move_selected_actor_down(self):
        if not self.current_api_cast_data or len(self.current_api_cast_data) < 2: # 少于2个演员无法移动
            return

        selected_tree_item = self.cast_tree_widget.currentItem()
        if not selected_tree_item:
            QMessageBox.information(self, "提示", "请先选择要下移的演员。")
            return

        current_index = self.cast_tree_widget.indexOfTopLevelItem(selected_tree_item)
        
        # 确保不是最后一个元素
        if 0 <= current_index < len(self.current_api_cast_data) - 1:
            # 1. 在数据列表中移动
            actor_to_move = self.current_api_cast_data.pop(current_index)
            self.current_api_cast_data.insert(current_index + 1, actor_to_move)

            # 2. 更新所有演员的 'order' 字段
            for i, actor_data in enumerate(self.current_api_cast_data):
                actor_data['order'] = i

            self.main_app.log_to_progress_text_qt(f"演员 '{actor_to_move.get('name')}' 已下移。", "DEBUG")

            # 3. 更新UI
            selected_actor_id = actor_to_move.get("id")
            self._update_detail_display_qt(self.current_selected_item_data, cast_list=self.current_api_cast_data)
            
            # 重新选中移动后的项
            if selected_actor_id is not None:
                for i in range(self.cast_tree_widget.topLevelItemCount()):
                    item = self.cast_tree_widget.topLevelItem(i)
                    if self.current_api_cast_data[i].get("id") == selected_actor_id:
                        self.cast_tree_widget.setCurrentItem(item)
                        self.cast_tree_widget.scrollToItem(item)
                        break
            
            # 4. 更新按钮状态
            self._update_actor_action_buttons_state()
        else:
            self.main_app.log_to_progress_text_qt("无法下移：已是最后一个演员或未选中。", "DEBUG")
    @pyqtSlot(QTreeWidgetItem, int)
    def on_cast_tree_item_double_clicked(self, item: QTreeWidgetItem, column: int):
        self.main_app.log_to_progress_text_qt(f"Item double clicked: {item.text(0)}, Column: {column}", "DEBUG") # 确认双击事件触发

        if not self.current_api_cast_data:
            self.main_app.log_to_progress_text_qt("  No current_api_cast_data.", "DEBUG")
            return

        row_index = self.cast_tree_widget.indexOfTopLevelItem(item)
        self.main_app.log_to_progress_text_qt(f"  Row index: {row_index}", "DEBUG")

        if not (0 <= row_index < len(self.current_api_cast_data)):
            self.main_app.log_to_progress_text_qt(f"  Invalid row index.", "DEBUG")
            return

        actor_data_entry = self.current_api_cast_data[row_index]
        tmdb_status = actor_data_entry.get("_tmdb_status")
        has_candidates_field = "_tmdb_candidates" in actor_data_entry
        candidates_list = actor_data_entry.get("_tmdb_candidates", [])
        
        self.main_app.log_to_progress_text_qt(f"  Actor: {actor_data_entry.get('name')}, TMDB Status: {tmdb_status}, Has Candidates Field: {has_candidates_field}, Candidates Count: {len(candidates_list) if candidates_list else 0}", "DEBUG")
        
        if tmdb_status == "tmdb_multiple_matches" and has_candidates_field:
            self.main_app.log_to_progress_text_qt(f"    Condition met for showing dialog. Candidates: {candidates_list}", "DEBUG")
            if candidates_list: # 确保候选列表不为空
                self._show_tmdb_candidate_selection_dialog(row_index, candidates_list)
            else:
                QMessageBox.information(self, "无候选", "该演员标记为多匹配，但候选列表为空。")
                self.main_app.log_to_progress_text_qt(f"    Candidates list is empty for actor {actor_data_entry.get('name')}.", "WARN")
        else:
            self.main_app.log_to_progress_text_qt(f"    Condition NOT met for showing dialog.", "DEBUG")
