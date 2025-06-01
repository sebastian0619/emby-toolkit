# config_dialog.py
import os
import configparser
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QFileDialog, QFrame, QSpinBox, QDialogButtonBox,
    QSizePolicy, 
    QListWidget,         # 用于显示引擎列表
    QListWidgetItem,     # <--- 添加这个，用于创建列表中的项
    QAbstractItemView,   # 用于设置拖拽模式
    QInputDialog,        # 用于弹出选择引擎的对话框
    QMessageBox,          # <--- 添加这个，用于弹出提示信息
    QCheckBox,
    QComboBox
)
from PyQt6.QtCore import Qt
from typing import Tuple
import constants
from utils import get_base_path_for_files
from PyQt6.QtWidgets import QListWidget, QAbstractItemView, QInputDialog

class ConfigDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("程序配置")
        self.setMinimumWidth(550)
        self.all_available_engines = constants.AVAILABLE_TRANSLATOR_ENGINES # 存储所有可选引擎

        self.config = configparser.ConfigParser()
        self.config_file_path = os.path.join(get_base_path_for_files(), constants.CONFIG_FILE)

        self._init_ui()
        self._load_config_to_ui()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)

        # --- 路径配置 ---
        paths_group = self._create_group_box("路径配置")
        paths_layout = QGridLayout(paths_group)
        self.main_cache_path_edit, main_browse_btn = self._add_path_row(paths_layout, 0, "主缓存目录:", "TMDB本地cache目录，或处理结果保存目录")
        self.override_cache_path_edit, override_browse_btn = self._add_path_row(paths_layout, 1, "覆盖缓存目录 (可选):", "为空则结果保存回主缓存目录")
        main_layout.addWidget(paths_group)

        # --- Emby 配置 ---
        emby_group = self._create_group_box("Emby 配置")
        emby_layout = QGridLayout(emby_group)
        self.emby_server_url_edit = self._add_config_row(emby_layout, 0, "Emby 服务器 URL:", "例如: http://localhost:8096")
        self.emby_api_key_edit = self._add_config_row(emby_layout, 1, "Emby API Key:")
        self.cb_enable_emby_item_refresh = QCheckBox("处理完成后自动通知Emby刷新该媒体项")
        self.cb_enable_emby_item_refresh.setToolTip(
            "如果勾选，在工具成功处理完一个媒体项（手动或批量）的本地元数据后，\n"
            "会自动向Emby发送一个针对该媒体项的“替换式”元数据刷新请求。\n"
            "如果不勾选，则只更新本地文件，Emby中的更新需等待其自身扫描或您手动刷新。"
        )
        emby_layout.addWidget(self.cb_enable_emby_item_refresh, 2, 0, 1, 2) # 第2行，跨两列
        main_layout.addWidget(emby_group)

        # --- TMDB API 配置 ---
        tmdb_group = self._create_group_box("TMDB API 配置")
        tmdb_layout = QGridLayout(tmdb_group)
        self.tmdb_api_key_edit = self._add_config_row(tmdb_layout, 0, "TMDB API Key (v3):", "请输入您的TMDB API v3 Key")
        main_layout.addWidget(tmdb_group)

        # --- 豆瓣 API 冷却配置 ---
        douban_group = self._create_group_box("豆瓣 API 冷却配置")
        douban_layout = QGridLayout(douban_group)
        self.douban_default_cooldown_spin = self._add_spinbox_row(douban_layout, 0, "默认冷却 (秒):", 0, 300)
        self.douban_max_cooldown_spin = self._add_spinbox_row(douban_layout, 1, "最大冷却 (秒):", 1, 600)
        self.douban_increment_cooldown_spin = self._add_spinbox_row(douban_layout, 2, "冷却增量 (秒):", 1, 300)
        main_layout.addWidget(douban_group)

        # +++ 国产影视数据源选择 (使用 QComboBox) +++
        domestic_source_group = self._create_group_box("国产影视数据源")
        domestic_source_layout = QVBoxLayout(domestic_source_group) # 改为QVBoxLayout更简单

        domestic_source_layout.addWidget(QLabel("国产影视豆瓣数据源策略:")) # 添加一个标签

        self.combo_domestic_source_mode = QComboBox()
        self.combo_domestic_source_mode.addItem("仅使用神医豆瓣数据", constants.DOMESTIC_SOURCE_MODE_LOCAL_ONLY)
        self.combo_domestic_source_mode.addItem("仅使用在线豆瓣API", constants.DOMESTIC_SOURCE_MODE_ONLINE_ONLY)
        self.combo_domestic_source_mode.addItem("神医豆瓣数据优先，豆瓣API备选", constants.DOMESTIC_SOURCE_MODE_LOCAL_THEN_ONLINE)
        self.combo_domestic_source_mode.setToolTip(
            "选择处理国产片时获取豆瓣演员角色信息的方式：\n"
            "- 仅本地: 只用神医插件已刮削的优化数据。\n"
            "- 仅在线: 直接联网从豆瓣API获取最新数据。\n"
            "- 本地优先: 先尝试本地，若无则尝试在线API。"
        )
        domestic_source_layout.addWidget(self.combo_domestic_source_mode)
        main_layout.addWidget(domestic_source_group)
        # +++ 国产影视数据源选择结束 +++

        # --- 在线翻译引擎配置 ---
        translation_engine_group = self._create_group_box("在线翻译引擎配置")
        translation_engine_layout = QVBoxLayout(translation_engine_group)

        translation_engine_layout.addWidget(QLabel("当前启用的翻译引擎 (按顶部优先顺序尝试，可拖动排序):"))
        self.selected_engines_list_widget = QListWidget()
        self.selected_engines_list_widget.setToolTip(
            "列表中的引擎会被依次尝试。\n"
            "拖动列表项可以改变尝试顺序。\n"
            "Google引擎可能需要代理，结果可能包含原文对照。"
        )
        self.selected_engines_list_widget.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.selected_engines_list_widget.setFixedHeight(150) # 给列表一个合适的高度
        translation_engine_layout.addWidget(self.selected_engines_list_widget)

        engine_buttons_layout = QHBoxLayout()
        self.btn_add_engine = QPushButton("添加引擎到列表...")
        self.btn_remove_engine = QPushButton("从列表移除选中引擎")
        engine_buttons_layout.addWidget(self.btn_add_engine)
        engine_buttons_layout.addWidget(self.btn_remove_engine)
        engine_buttons_layout.addStretch()
        translation_engine_layout.addLayout(engine_buttons_layout)
        
        main_layout.addWidget(translation_engine_group)
        # --- 翻译引擎配置结束 ---

        # --- 底部按钮 ---
        self.button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.button_box.accepted.connect(self._save_config_from_ui)
        self.button_box.rejected.connect(self.reject)
        main_layout.addWidget(self.button_box)


        # --- 连接新按钮的信号 ---
        self.btn_add_engine.clicked.connect(self._add_translation_engine_from_available)
        self.btn_remove_engine.clicked.connect(self._remove_selected_translation_engine)

    def _create_group_box(self, title: str) -> QFrame:
        """辅助函数：创建一个带标题的QFrame作为分组框"""
        group = QFrame()
        group.setFrameShape(QFrame.Shape.StyledPanel)
        # 如果需要在QFrame内直接设置标题，通常需要一个布局并在布局顶部添加QLabel
        # 这里我们简化，标题由外部管理或在布局中添加
        return group

    def _add_config_row(self, layout: QGridLayout, row: int, label_text: str, placeholder: str = "") -> QLineEdit:
        """辅助函数：向网格布局添加一个标签和行编辑器"""
        layout.addWidget(QLabel(label_text), row, 0)
        edit = QLineEdit()
        if placeholder:
            edit.setPlaceholderText(placeholder)
        layout.addWidget(edit, row, 1)
        return edit

    def _add_path_row(self, layout: QGridLayout, row: int, label_text: str, placeholder: str = "") -> Tuple[QLineEdit, QPushButton]:
        """辅助函数：向网格布局添加一个路径输入行（标签、行编辑器、浏览按钮）"""
        edit = self._add_config_row(layout, row, label_text, placeholder)
        browse_btn = QPushButton("浏览...")
        browse_btn.clicked.connect(lambda: self._browse_directory(edit))
        layout.addWidget(browse_btn, row, 2)
        return edit, browse_btn

    def _add_spinbox_row(self, layout: QGridLayout, row: int, label_text: str, min_val: int, max_val: int) -> QSpinBox:
        """辅助函数：向网格布局添加一个标签和数字选择框"""
        layout.addWidget(QLabel(label_text), row, 0)
        spin_box = QSpinBox()
        spin_box.setRange(min_val, max_val)
        layout.addWidget(spin_box, row, 1)
        return spin_box

    def _browse_directory(self, line_edit_widget: QLineEdit):
        initial_dir = line_edit_widget.text() or os.path.expanduser("~")
        directory = QFileDialog.getExistingDirectory(self, "选择目录", initial_dir)
        if directory:
            line_edit_widget.setText(directory)

    def _add_translation_engine_from_available(self):
        current_selected_texts = [self.selected_engines_list_widget.item(i).text() for i in range(self.selected_engines_list_widget.count())]
        
        # 找出尚未被选择的可用引擎
        available_to_add = [engine for engine in self.all_available_engines if engine not in current_selected_texts]
        
        if not available_to_add:
            QMessageBox.information(self, "提示", "所有可用翻译引擎都已在列表中。")
            return

        item, ok = QInputDialog.getItem(self, "添加翻译引擎", "请选择要添加到列表的引擎:", available_to_add, 0, False)
        if ok and item:
            self.selected_engines_list_widget.addItem(item)

    def _remove_selected_translation_engine(self):
        selected_items = self.selected_engines_list_widget.selectedItems()
        if not selected_items:
            QMessageBox.information(self, "提示", "请先在列表中选择要移除的引擎。")
            return
        for item in selected_items:
            self.selected_engines_list_widget.takeItem(self.selected_engines_list_widget.row(item))

    def _load_config_to_ui(self):
        if os.path.exists(self.config_file_path):
            self.config.read(self.config_file_path, encoding='utf-8')

        self.main_cache_path_edit.setText(self.config.get(
            constants.CONFIG_SECTION_PATHS,
            constants.CONFIG_OPTION_MAIN_CACHE_PATH,
            fallback=os.path.expanduser(constants.FALLBACK_DEFAULT_MAIN_CACHE_PATH)
        ))
        self.override_cache_path_edit.setText(self.config.get(
            constants.CONFIG_SECTION_PATHS,
            constants.CONFIG_OPTION_OVERRIDE_CACHE_PATH,
            fallback=""
        ))

        self.emby_server_url_edit.setText(self.config.get(
            constants.CONFIG_SECTION_EMBY,
            constants.CONFIG_OPTION_EMBY_SERVER_URL,
            fallback=""
        ))
        self.emby_api_key_edit.setText(self.config.get(
            constants.CONFIG_SECTION_EMBY,
            constants.CONFIG_OPTION_EMBY_API_KEY,
            fallback=""
        ))

        # 加载“处理完成后是否刷新单个Emby项目”的选项状态
        enable_item_refresh_str = self.config.get(
            constants.CONFIG_SECTION_EMBY, # 放在 [Emby] 段内
            constants.CONFIG_OPTION_ENABLE_EMBY_ITEM_REFRESH,
            fallback=str(constants.DEFAULT_ENABLE_EMBY_ITEM_REFRESH)
        )
        self.cb_enable_emby_item_refresh.setChecked(enable_item_refresh_str.lower() == "true")

        self.tmdb_api_key_edit.setText(self.config.get(
            constants.CONFIG_SECTION_TMDB,
            constants.CONFIG_OPTION_TMDB_API_KEY,
            fallback=constants.FALLBACK_TMDB_API_KEY
        ))

        self.douban_default_cooldown_spin.setValue(self.config.getint(
            constants.CONFIG_SECTION_API_DOUBAN,
            constants.CONFIG_OPTION_DOUBAN_DEFAULT_COOLDOWN,
            fallback=constants.DEFAULT_API_COOLDOWN_SECONDS_FALLBACK
        ))
        self.douban_max_cooldown_spin.setValue(self.config.getint(
            constants.CONFIG_SECTION_API_DOUBAN,
            constants.CONFIG_OPTION_DOUBAN_MAX_COOLDOWN,
            fallback=constants.MAX_API_COOLDOWN_SECONDS_FALLBACK
        ))
        self.douban_increment_cooldown_spin.setValue(self.config.getint(
            constants.CONFIG_SECTION_API_DOUBAN,
            constants.CONFIG_OPTION_DOUBAN_INCREMENT_COOLDOWN,
            fallback=constants.COOLDOWN_INCREMENT_SECONDS_FALLBACK
        ))
        
        self.selected_engines_list_widget.clear()
        user_engines_str = self.config.get(
            constants.CONFIG_SECTION_TRANSLATION,
            constants.CONFIG_OPTION_TRANSLATOR_ENGINES,
            fallback=",".join(constants.DEFAULT_TRANSLATOR_ENGINES_ORDER)
        )
        if user_engines_str.strip():
            user_engines_list = [engine.strip() for engine in user_engines_str.split(',') if engine.strip()]
            for engine_name in user_engines_list:
                if engine_name in self.all_available_engines:
                    item = QListWidgetItem(engine_name)
                    if engine_name == "google":
                        item.setToolTip("需要代理环境，结果可能包含原文对照。")
                    self.selected_engines_list_widget.addItem(item)
                else:
                    # 在 ConfigDialog 中，我们通常不直接调用主窗口的日志方法
                    # 可以考虑在主窗口重新加载配置后记录这个警告
                    print(f"[ConfigDialog WARN] 配置警告：忽略了未知的翻译引擎 '{engine_name}'。")
        
        # +++ 加载国产影视数据源选择状态 (使用新的 QComboBox 和常量) +++
        current_mode = self.config.get(
            constants.CONFIG_SECTION_DOMESTIC_SOURCE,
            constants.CONFIG_OPTION_DOMESTIC_SOURCE_MODE, # 使用新的常量
            fallback=constants.DEFAULT_DOMESTIC_SOURCE_MODE
        )
        index = self.combo_domestic_source_mode.findData(current_mode) # combo_domestic_source_mode 是新的UI组件
        if index != -1:
            self.combo_domestic_source_mode.setCurrentIndex(index)
        else:
            default_index = self.combo_domestic_source_mode.findData(constants.DEFAULT_DOMESTIC_SOURCE_MODE)
            if default_index != -1:
                self.combo_domestic_source_mode.setCurrentIndex(default_index)
            else:
                self.combo_domestic_source_mode.setCurrentIndex(0) # 默认选中第一个
        # +++ 加载结束 +++
    def _save_config_from_ui(self):
        print("DEBUG: _save_config_from_ui 方法被调用。") # <--- 入口日志
        # 确保配置段存在
        sections_to_ensure = [
            constants.CONFIG_SECTION_PATHS,
            constants.CONFIG_SECTION_EMBY,
            constants.CONFIG_SECTION_TMDB,
            constants.CONFIG_SECTION_API_DOUBAN,
            constants.CONFIG_SECTION_TRANSLATION,
            constants.CONFIG_SECTION_DOMESTIC_SOURCE
        ]
        for section in sections_to_ensure:
            if not self.config.has_section(section):
                self.config.add_section(section)
                print(f"DEBUG: 已创建配置段 [{section}]")

        # --- 保存主缓存目录 ---
        print(f"DEBUG: 准备保存路径配置...")
        main_cache_val = self.main_cache_path_edit.text()
        self.config.set(constants.CONFIG_SECTION_PATHS, constants.CONFIG_OPTION_MAIN_CACHE_PATH, main_cache_val)
        print(f"  - 主缓存目录值: '{main_cache_val}'")
        # ... (为其他每个 self.config.set(...) 调用都添加类似的打印) ...
        # +++ 保存覆盖缓存目录 +++
        override_cache_val = self.override_cache_path_edit.text() # 从 QLineEdit 获取文本值
        self.config.set(
            constants.CONFIG_SECTION_PATHS,                     # 段名："Paths"
            constants.CONFIG_OPTION_OVERRIDE_CACHE_PATH,        # 选项名："override_cache_path"
            override_cache_val                                  # 要保存的值
        )
        print(f"  - 覆盖缓存目录值: '{override_cache_val}'") # 确保有这条打印日志
        # +++ 检查结束 +++
        # --- 路径配置保存结束 ---

        print(f"DEBUG: 准备保存Emby配置...")
        emby_url_val = self.emby_server_url_edit.text()
        self.config.set(constants.CONFIG_SECTION_EMBY, constants.CONFIG_OPTION_EMBY_SERVER_URL, emby_url_val)
        print(f"  - Emby URL值: '{emby_url_val}'")
        emby_api_key_val = self.emby_api_key_edit.text() # 从对应的 QLineEdit 获取值
        self.config.set(constants.CONFIG_SECTION_EMBY, constants.CONFIG_OPTION_EMBY_API_KEY, emby_api_key_val)
        print(f"  - Emby API Key值: '{emby_api_key_val}'") # <--- 确保有这行打印

        # 保存“处理完成后是否刷新单个Emby项目”的选项状态
        enable_item_refresh_val = "true" if self.cb_enable_emby_item_refresh.isChecked() else "false"
        self.config.set(
            constants.CONFIG_SECTION_EMBY, # 放在 [Emby] 段内
            constants.CONFIG_OPTION_ENABLE_EMBY_ITEM_REFRESH,
            enable_item_refresh_val
        )
        print(f"  - 处理后自动刷新单个Emby项目: '{enable_item_refresh_val}'")

        print(f"DEBUG: 準備保存TMDB API Key...")
        tmdb_key_val = self.tmdb_api_key_edit.text()
        self.config.set(constants.CONFIG_SECTION_TMDB, constants.CONFIG_OPTION_TMDB_API_KEY, tmdb_key_val)
        print(f"  - TMDB API Key值: '{tmdb_key_val}'")

        print(f"DEBUG: 准备保存豆瓣API冷却配置...")
        # ... (为三个spinbox的值也添加打印) ...
        douban_default_cooldown_val = str(self.douban_default_cooldown_spin.value())
        self.config.set(constants.CONFIG_SECTION_API_DOUBAN, constants.CONFIG_OPTION_DOUBAN_DEFAULT_COOLDOWN, douban_default_cooldown_val)
        print(f"  - 豆瓣默认冷却: {douban_default_cooldown_val}")
        # ...

        print(f"DEBUG: 准备保存翻译引擎配置...")
        engines_to_save = []
        for i in range(self.selected_engines_list_widget.count()):
            engines_to_save.append(self.selected_engines_list_widget.item(i).text())
        engines_str_to_save = ",".join(engines_to_save)
        self.config.set(constants.CONFIG_SECTION_TRANSLATION, 
                        constants.CONFIG_OPTION_TRANSLATOR_ENGINES, 
                        engines_str_to_save)
        print(f"  - 翻译引擎顺序: '{engines_str_to_save}'")
        # --- 配置设置结束 ---

        # +++ 保存国产影视数据源选择状态 +++
        print(f"DEBUG: 准备保存国产影视数据源配置...")
        selected_mode_data = self.combo_domestic_source_mode.currentData() # 获取选中的data部分
        self.config.set(
            constants.CONFIG_SECTION_DOMESTIC_SOURCE,
            constants.CONFIG_OPTION_DOMESTIC_SOURCE_MODE,
            selected_mode_data # 保存的是 "local_only", "online_only" 等字符串
        )
        print(f"  - 国产影视豆瓣数据源策略: '{selected_mode_data}'")
        # +++ 保存结束 +++

        try:
            print(f"DEBUG: 准备将配置写入文件: '{self.config_file_path}'")
            with open(self.config_file_path, 'w', encoding='utf-8') as configfile:
                self.config.write(configfile)
            print(f"DEBUG: 配置已成功写入文件 '{self.config_file_path}'。")
            self.accept() # 如果写入成功，调用 accept()
            print("DEBUG: self.accept() 已调用。")
        except Exception as e:
            # 在实际应用中，这里应该使用 QMessageBox 来提示用户
            print(f"错误：保存配置到 '{self.config_file_path}' 失败: {e}") # <--- 打印错误
            import traceback
            print(traceback.format_exc()) # <--- 打印详细的 traceback
            QMessageBox.critical(self, "保存失败", f"保存配置文件时发生错误:\n{e}") # <--- 确保有UI提示
            # 注意：如果这里发生错误，self.accept() 就不会被调用，对话框不会正常关闭

if __name__ == '__main__':
    from PyQt6.QtWidgets import QApplication
    import sys

    # 临时的 DummyConstants，仅用于独立测试
    class DummyConstants:
        CONFIG_FILE = "test_config_dialog.ini"
        CONFIG_SECTION_PATHS = "Paths"
        CONFIG_OPTION_MAIN_CACHE_PATH = "main_cache_path"
        CONFIG_OPTION_OVERRIDE_CACHE_PATH = "override_cache_path"
        FALLBACK_DEFAULT_MAIN_CACHE_PATH = "~"
        CONFIG_SECTION_EMBY = "Emby"
        CONFIG_OPTION_EMBY_SERVER_URL = "server_url"
        CONFIG_OPTION_EMBY_API_KEY = "api_key"
        CONFIG_SECTION_TMDB = "TMDB" # 新增
        CONFIG_OPTION_TMDB_API_KEY = "api_key" # 新增
        FALLBACK_TMDB_API_KEY = "" # 新增
        CONFIG_SECTION_API_DOUBAN = "API_Douban"
        CONFIG_OPTION_DOUBAN_DEFAULT_COOLDOWN = "default_cooldown_seconds"
        CONFIG_OPTION_DOUBAN_MAX_COOLDOWN = "max_cooldown_seconds"
        CONFIG_OPTION_DOUBAN_INCREMENT_COOLDOWN = "cooldown_increment_seconds"
        DEFAULT_API_COOLDOWN_SECONDS_FALLBACK = 1
        MAX_API_COOLDOWN_SECONDS_FALLBACK = 60
        COOLDOWN_INCREMENT_SECONDS_FALLBACK = 10

    constants = DummyConstants() # 替换真实的 constants

    app = QApplication(sys.argv)
    dialog = ConfigDialog()
    if dialog.exec():
        print("配置已保存 (测试模式)。")
    else:
        print("配置对话框已取消 (测试模式)。")