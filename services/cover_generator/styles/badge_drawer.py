# services/cover_generator/styles/badge_drawer.py

from PIL import Image, ImageDraw, ImageFont, ImageOps
import math
import logging
logger = logging.getLogger(__name__)

def _darken_color(color, factor=0.7):
    """一个独立的颜色加深辅助函数"""
    if not color or len(color) < 3:
        return (0, 0, 0) # 返回安全的默认值
    r, g, b = color[:3]
    return (int(r * factor), int(g * factor), int(b * factor))

def draw_badge(image, item_count, font_path, style='badge', size_ratio=0.12, base_color=None, badge_image_path=None):
    """
    【V17 - 智能分流最终版】
    - 当数字为两位数及以下时，使用自定义图片徽章。
    - 当数字为三位数及以上时，自动切换到可读性更强的纯色背景徽章。
    - 修复了在预览模式下可能因字体路径问题导致的报错。
    """
    if not item_count:
        return image

    if image.mode != 'RGBA':
        image = image.convert('RGBA')
        
    canvas_width, canvas_height = image.size
    margin = int(canvas_height * 0.04)
    count_text = str(item_count)

    # =================================================
    # --- 风格一：徽章 (Badge Style) ---
    # =================================================
    if style == 'badge':
        # ★★★ 核心逻辑：智能分流 ★★★
        # 如果数字是两位数或更少，并且提供了图片路径，则使用图片徽章
        if len(count_text) <= 2 and badge_image_path:
            try:
                # --- 分支A: 使用自定义图片徽章 (适用于少数位) ---
                badge_texture = Image.open(badge_image_path).convert("RGBA")
                final_badge_height = int(canvas_height * size_ratio)
                aspect_ratio = badge_texture.width / float(badge_texture.height)
                final_badge_width = int(final_badge_height * aspect_ratio)
                badge_bg_resized = badge_texture.resize((final_badge_width, final_badge_height), Image.Resampling.LANCZOS)

                draw_on_badge = ImageDraw.Draw(badge_bg_resized)
                
                # 使用自适应字体大小逻辑
                max_text_width = final_badge_width * 0.8
                current_font_size = int(final_badge_height * 0.7)
                badge_font = None

                while current_font_size > 5:
                    try: # 修复预览报错的关键
                        font_to_test = ImageFont.truetype(font_path, size=current_font_size)
                    except (IOError, OSError):
                        font_to_test = ImageFont.load_default(size=current_font_size)

                    if hasattr(font_to_test, 'getbbox'):
                        text_box = font_to_test.getbbox(count_text)
                        text_width = text_box[2] - text_box[0]
                    else:
                        text_width, _ = font_to_test.getsize(count_text)

                    if text_width <= max_text_width:
                        badge_font = font_to_test
                        break
                    current_font_size -= 1
                
                if not badge_font:
                    badge_font = ImageFont.load_default(size=current_font_size)

                # 使用微调参数绘制文字
                vertical_offset_ratio = -0.08
                text_fill_color = (10, 10, 10, 230)
                shadow_fill_color = (255, 255, 255, 90)
                badge_center_x = final_badge_width / 2
                badge_center_y = (final_badge_height / 2) + (final_badge_height * vertical_offset_ratio)
                shadow_offset = 2
                draw_on_badge.text((badge_center_x, badge_center_y + shadow_offset), count_text, font=badge_font, fill=shadow_fill_color, anchor="mm")
                draw_on_badge.text((badge_center_x, badge_center_y), count_text, font=badge_font, fill=text_fill_color, anchor="mm")
                
                image.paste(badge_bg_resized, (margin, margin), badge_bg_resized)

            except (FileNotFoundError, IOError) as e:
                logger.warning(f"图片徽章加载失败 ({e})，将退回纯色背景模式。")
                # 如果图片加载失败，则故意不处理，让它进入下面的else分支
                pass
            else:
                # 如果图片徽章处理成功，直接返回，不再执行后续代码
                return image

        # --- 分支B: 使用纯色背景徽章 (适用于多位数或图片加载失败时) ---
        badge_font_size = int(canvas_height * size_ratio * 0.7) # 纯色背景的字体可以稍微大一点
        try:
            badge_font = ImageFont.truetype(font_path, size=badge_font_size)
        except (IOError, OSError):
            badge_font = ImageFont.load_default(size=badge_font_size)

        # 动态计算徽章尺寸
        temp_draw = ImageDraw.Draw(Image.new('RGB', (1,1)))
        if hasattr(temp_draw, 'textbbox'):
            text_bbox = temp_draw.textbbox((0,0), count_text, font=badge_font)
            text_width = text_bbox[2] - text_bbox[0]
            text_height = text_bbox[3] - text_bbox[1]
        else:
            text_width, text_height = temp_draw.textsize(count_text, font=badge_font)

        badge_padding_h = int(badge_font_size * 0.4)
        badge_padding_v = int(badge_font_size * 0.2)
        badge_width = int(text_width + badge_padding_h * 2)
        badge_height = int(text_height + badge_padding_v * 2)
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