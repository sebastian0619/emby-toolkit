# services/cover_generator/styles/badge_drawer.py

from PIL import Image, ImageDraw, ImageFont
import math

def _darken_color(color, factor=0.7):
    """一个独立的颜色加深辅助函数"""
    if not color or len(color) < 3:
        return (0, 0, 0) # 返回安全的默认值
    r, g, b = color[:3]
    return (int(r * factor), int(g * factor), int(b * factor))

def draw_badge(image, item_count, font_path, style='badge', size_ratio=0.12, base_color=None):
    """
    【V9 - 最终权威版】
    所有封面徽章的唯一绘制函数。
    """
    if not item_count:
        return image

    canvas_width, canvas_height = image.size
    if image.mode != 'RGBA':
        image = image.convert('RGBA')
        
    draw = ImageDraw.Draw(image)

    # --- 动态计算所有尺寸 ---
    badge_font_size = int(canvas_height * size_ratio)
    margin = int(canvas_height * 0.04)
    count_text = str(item_count)

    try:
        badge_font = ImageFont.truetype(font_path, size=badge_font_size)
    except Exception:
        badge_font = ImageFont.load_default(size=badge_font_size)

    # =================================================
    # --- 风格一：徽章 (Badge Style) ---
    # =================================================
    if style == 'badge':
        # ... (尺寸计算不变) ...
        temp_draw = ImageDraw.Draw(Image.new('RGB', (1,1)))
        text_bbox = temp_draw.textbbox((0,0), count_text, font=badge_font)
        text_width = text_bbox[2] - text_bbox[0]
        text_height = text_bbox[3] - text_bbox[1]
        badge_padding_h = int(badge_font_size * 0.4)
        badge_padding_v = int(badge_font_size * 0.2)
        badge_width = int(text_width + badge_padding_h * 2)
        badge_height = int(text_height + badge_padding_v * 2)
        badge_pos = (margin, margin)
        badge_rect = (badge_pos[0], badge_pos[1], badge_pos[0] + badge_width, badge_pos[1] + badge_height)
        
        # 【【【手术核心：动态生成徽章背景颜色】】】
        if base_color:
            # ==================== 可微调旋钮 (徽章颜色) ====================
            # `_darken_color` 的第二个参数 (0.0 - 1.0) 控制徽章背景的深浅。值越小，颜色越深。
            # 推荐范围: 0.4 - 0.6
            badge_fill = _darken_color(base_color, 0.3) + (190,)
            # =============================================================
        else:
            # 如果没有传入基础颜色，则使用安全的中性深灰色
            badge_fill = (40, 40, 40, 180)

        badge_layer = Image.new('RGBA', image.size, (0, 0, 0, 0))
        badge_draw = ImageDraw.Draw(badge_layer)
        badge_draw.rounded_rectangle(badge_rect, radius=int(badge_height * 0.3), fill=badge_fill)
        image = Image.alpha_composite(image, badge_layer)
        
        # ... (文字绘制逻辑不变) ...
        draw = ImageDraw.Draw(image)
        badge_center_x = badge_pos[0] + badge_width / 2
        badge_center_y = badge_pos[1] + badge_height / 2
        shadow_offset = 2
        draw.text((badge_center_x + shadow_offset, badge_center_y + shadow_offset), count_text, font=badge_font, fill=(0, 0, 0, 100), anchor="mm")
        draw.text((badge_center_x, badge_center_y), count_text, font=badge_font, fill=(255, 255, 255, 240), anchor="mm")

    # =================================================
    # --- 风格二：缎带 (Ribbon Style) ---
    # =================================================
    elif style == 'ribbon':
        # 1. 动态计算缎带尺寸
        ribbon_width = int(badge_font_size * 3.0)
        fold_size = int(ribbon_width * 0.3)

        # 2. 动态生成颜色
        if base_color:
            # ==================== 可微调旋钮 (缎带颜色) ====================
            # `_darken_color` 的第二个参数 (0.0 - 1.0) 控制缎带颜色深浅。值越小，颜色越深。
            # 推荐范围: 0.5 - 0.7
            ribbon_fill = _darken_color(base_color, 0.2) + (190,)
            # =============================================================
        else:
            ribbon_fill = (50, 50, 50, 180)

        # 3. 绘制带“透明缺口”的缎带
        ribbon_layer = Image.new('RGBA', image.size, (0, 0, 0, 0))
        ribbon_draw = ImageDraw.Draw(ribbon_layer)
        ribbon_draw.polygon([(0, 0), (ribbon_width, 0), (0, ribbon_width)], fill=ribbon_fill)
        ribbon_draw.polygon([(0, 0), (fold_size, 0), (0, fold_size)], fill=(0, 0, 0, 0))
        image = Image.alpha_composite(image, ribbon_layer)

        # 4. 计算文字位置并绘制
        text_center_x = int(ribbon_width * 0.35)
        text_center_y = int(ribbon_width * 0.35)
        draw = ImageDraw.Draw(image)
        shadow_offset = 2
        draw.text((text_center_x + shadow_offset, text_center_y + shadow_offset), count_text, font=badge_font, fill=(0, 0, 0, 100), anchor="mm")
        draw.text((text_center_x, text_center_y), count_text, font=badge_font, fill=(255, 255, 255, 240), anchor="mm")

    return image