# services/cover_generator/styles/badge_drawer.py

from PIL import Image, ImageDraw, ImageFont, ImageOps
import math

def _darken_color(color, factor=0.7):
    """一个独立的颜色加深辅助函数"""
    if not color or len(color) < 3:
        return (0, 0, 0) # 返回安全的默认值
    r, g, b = color[:3]
    return (int(r * factor), int(g * factor), int(b * factor))

def draw_badge(image, item_count, font_path, style='badge', size_ratio=0.12, base_color=None, badge_image_path=None):
    """
    【V10 - 图片徽章升级版】
    所有封面徽章的唯一绘制函数。
    新增 badge_image_path 参数，可使用PNG图片作为徽章背景。
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
        try:
            if not badge_image_path:
                raise FileNotFoundError("未提供徽章图片路径")

            # --- 1. 保持徽章图片原始比例 ---
            badge_texture = Image.open(badge_image_path).convert("RGBA")
            final_badge_height = int(canvas_height * size_ratio)
            original_width, original_height = badge_texture.size
            aspect_ratio = original_width / float(original_height)
            final_badge_width = int(final_badge_height * aspect_ratio)
            badge_bg_resized = badge_texture.resize((final_badge_width, final_badge_height), Image.Resampling.LANCZOS)

            # --- 2. 在徽章背景上绘制文字 ---
            draw_on_badge = ImageDraw.Draw(badge_bg_resized)
            count_text = str(item_count)
            
            # ==================== 最终微调旋钮 (正方形徽章优化版) ====================
            # 【旋钮1: 文字大小】相对于徽章高度的比例。对于正方形徽章，可以适当增大。
            font_size_ratio = 0.5
            # 【旋钮2: 文字垂直偏移】补偿字体预留空白的关键。对于标准字体，-0.05到-0.1之间通常能实现视觉居中。
            vertical_offset_ratio = -0.08
            # 【旋钮3: 文字颜色】使用高对比度的纯黑或深色。
            text_fill_color = (10, 10, 10, 230) # 近乎纯黑
            # 【旋钮4: 阴影/描边】使用柔和的亮色阴影，让深色文字更突出。
            shadow_fill_color = (255, 255, 255, 90)
            # =======================================================================

            font_size = int(final_badge_height * font_size_ratio)
            try:
                badge_font = ImageFont.truetype(font_path, size=font_size)
            except Exception:
                badge_font = ImageFont.load_default(size=font_size)

            badge_center_x = final_badge_width / 2
            badge_center_y = (final_badge_height / 2) + (final_badge_height * vertical_offset_ratio)
            
            shadow_offset = 2 # 阴影可以稍微偏移多一点，立体感更强
            draw_on_badge.text((badge_center_x, badge_center_y + shadow_offset), count_text, font=badge_font, fill=shadow_fill_color, anchor="mm")
            draw_on_badge.text((badge_center_x, badge_center_y), count_text, font=badge_font, fill=text_fill_color, anchor="mm")
            
            # --- 3. 将最终成品粘贴到主画布上 ---
            image.paste(badge_bg_resized, (margin, margin), badge_bg_resized)

        except (FileNotFoundError, IOError) as e:
            # --- 退回至纯色背景逻辑 (如果图片加载失败) ---
            if "未提供" not in str(e):
                 print(f"警告: 徽章图片加载失败 ({e})，将使用纯色背景。")

            # (这部分纯色背景的逻辑保持原样即可)
            badge_font_size = int(canvas_height * size_ratio)
            count_text = str(item_count)
            badge_font = ImageFont.truetype(font_path, size=badge_font_size)
            text_bbox = ImageDraw.Draw(Image.new('RGB',(1,1))).textbbox((0,0), count_text, font=badge_font)
            badge_width = int(text_bbox[2]-text_bbox[0] + badge_font_size * 0.8)
            badge_height = int(text_bbox[3]-text_bbox[1] + badge_font_size * 0.4)
            badge_pos = (margin, margin)
            badge_rect = (badge_pos[0], badge_pos[1], badge_pos[0] + badge_width, badge_pos[1] + badge_height)
            if base_color:
                badge_fill = _darken_color(base_color, 0.3) + (190,)
            else:
                badge_fill = (40, 40, 40, 180)
            badge_layer = Image.new('RGBA', image.size, (0, 0, 0, 0))
            badge_draw = ImageDraw.Draw(badge_layer)
            badge_draw.rounded_rectangle(badge_rect, radius=int(badge_height * 0.3), fill=badge_fill)
            image = Image.alpha_composite(image, badge_layer)
            draw = ImageDraw.Draw(image)
            badge_center_x, badge_center_y = badge_pos[0] + badge_width / 2, badge_pos[1] + badge_height / 2
            draw.text((badge_center_x + 2, badge_center_y + 2), count_text, font=badge_font, fill=(0, 0, 0, 100), anchor="mm")
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