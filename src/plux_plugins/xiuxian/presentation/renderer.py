"""Layered, modular image rendering for the cultivation game."""

from __future__ import annotations

from functools import lru_cache
import math
from pathlib import Path
from typing import Mapping, Sequence
import unicodedata
from uuid import uuid4

from PIL import Image, ImageDraw, ImageFont, ImageOps

from ..domain.catalog import CATALOG, RARITY_NAMES, REALM_NAMES
from ..domain.config import DEFAULT_GAME_CONFIG, REALMS
from ..domain.cooldowns import self_cultivation_interval
from ..domain.shop import PROP_TEMPLATES


CANVAS_SIZE = (1080, 1440)
SAFE_X = 64
CONTENT_WIDTH = 952

ASSETS_DIR = Path(__file__).resolve().parents[1] / "resources" / "visual"
V2_DIR = ASSETS_DIR / "v2"
PROFILE_TEMPLATE = ASSETS_DIR / "v3" / "bg_profile.png"
TEMPLATES_DIR = V2_DIR / "templates"
COMP_DIR = V2_DIR / "components"
LEGACY_TEMPLATES_DIR = ASSETS_DIR / "templates"

DEFAULT_OUTPUT_DIR = None

FONT_XINGKAI = "C:/Windows/Fonts/STXINGKA.TTF"
FONT_KAITI = "C:/Windows/Fonts/simkai.ttf"
FONT_BOLD = "C:/Windows/Fonts/msyhbd.ttc"
FONT_REGULAR = "C:/Windows/Fonts/msyh.ttc"

TITLE_FONTS = (FONT_XINGKAI, FONT_KAITI, FONT_BOLD)
BODY_FONTS = (FONT_REGULAR, FONT_BOLD, FONT_KAITI)
BOLD_FONTS = (FONT_BOLD, FONT_REGULAR, FONT_KAITI)

GOLD = (235, 204, 126, 255)
PALE_GOLD = (249, 231, 178, 255)
JADE = (118, 215, 194, 255)
MUTED = (165, 190, 184, 255)
WHITE = (244, 248, 238, 255)


@lru_cache(maxsize=96)
def _font(paths: tuple[str, ...], size: int) -> ImageFont.ImageFont:
    """Load a preferred Windows font once, with a portable Pillow fallback."""
    for path in paths:
        try:
            return ImageFont.truetype(path, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def _safe_text(value: object) -> str:
    """Return printable text and replace emoji the Chinese fonts cannot cover."""
    result: list[str] = []
    for char in str(value):
        codepoint = ord(char)
        category = unicodedata.category(char)
        if codepoint in (0xFE0E, 0xFE0F) or 0x1F000 <= codepoint <= 0x1FAFF:
            if not result or result[-1] != "◇":
                result.append("◇")
        elif category in {"Cc", "Cs"} and char not in "\n\t":
            continue
        else:
            result.append(char)
    return "".join(result)


def _measure(text: str, font: ImageFont.ImageFont) -> float:
    try:
        return float(font.getlength(text))
    except AttributeError:
        left, _, right, _ = font.getbbox(text)
        return float(right - left)


def _ellipsize(text: str, font: ImageFont.ImageFont, max_width: int) -> str:
    text = _safe_text(text).strip()
    if _measure(text, font) <= max_width:
        return text
    suffix = "…"
    if _measure(suffix, font) > max_width:
        return ""
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if _measure(text[:middle].rstrip() + suffix, font) <= max_width:
            low = middle
        else:
            high = middle - 1
    return text[:low].rstrip() + suffix


def _fit_text(
    text: object,
    max_width: int,
    *,
    preferred_size: int,
    minimum_size: int,
    fonts: tuple[str, ...] = BODY_FONTS,
) -> tuple[str, ImageFont.ImageFont, int]:
    """Shrink text to the available width, then ellipsize at the minimum size."""
    safe = _safe_text(text).replace("\n", " ").strip()
    for size in range(preferred_size, minimum_size - 1, -1):
        font = _font(fonts, size)
        if _measure(safe, font) <= max_width:
            return safe, font, size
    font = _font(fonts, minimum_size)
    return _ellipsize(safe, font, max_width), font, minimum_size


def _wrap_text(
    text: object,
    font: ImageFont.ImageFont,
    max_width: int,
    *,
    max_lines: int | None = None,
) -> list[str]:
    """Pixel-wrap mixed Chinese/Latin text while preserving explicit line breaks."""
    source = _safe_text(text).replace("\r", "")
    lines: list[str] = []
    for paragraph in source.split("\n"):
        paragraph = paragraph.strip()
        if not paragraph:
            if lines and lines[-1]:
                lines.append("")
            continue
        current = ""
        for char in paragraph:
            candidate = current + char
            if current and _measure(candidate, font) > max_width:
                lines.append(current.rstrip())
                current = char.lstrip()
            else:
                current = candidate
        if current or not lines:
            lines.append(current.rstrip())
    if not lines:
        lines = [""]
    if max_lines is not None and len(lines) > max_lines:
        kept = lines[:max_lines]
        kept[-1] = _ellipsize(kept[-1] + "".join(lines[max_lines:]), font, max_width)
        return kept
    return lines


def _draw_fitted(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: object,
    max_width: int,
    *,
    preferred_size: int,
    minimum_size: int,
    fill: tuple[int, int, int, int],
    fonts: tuple[str, ...] = BODY_FONTS,
    anchor: str = "la",
) -> tuple[str, int]:
    fitted, font, size = _fit_text(
        text, max_width, preferred_size=preferred_size, minimum_size=minimum_size, fonts=fonts
    )
    draw.text(xy, fitted, font=font, fill=fill, anchor=anchor)
    return fitted, size


def _save_card(card: Image.Image, kind: str, output_dir=None) -> bytes:
    # The task publishes these bytes through platform assets outside its transaction.
    from io import BytesIO
    stream = BytesIO()
    card.save(stream, "PNG")
    return stream.getvalue()


def _background(name: str) -> Image.Image:
    requested = TEMPLATES_DIR / name
    legacy = LEGACY_TEMPLATES_DIR / name
    path = requested if requested.is_file() else legacy
    if name == "bg_profile.png" and PROFILE_TEMPLATE.is_file():
        path = PROFILE_TEMPLATE
    if not path.is_file():
        raise FileNotFoundError(f"Missing background template: {requested}")
    with Image.open(path) as source:
        rgba = source.convert("RGBA")
        return ImageOps.fit(rgba, CANVAS_SIZE, method=Image.Resampling.LANCZOS)


@lru_cache(maxsize=32)
def _cached_component(path_text: str, width: int, height: int) -> Image.Image:
    with Image.open(path_text) as source:
        component = source.convert("RGBA")
        if component.size != (width, height):
            component = component.resize((width, height), Image.Resampling.LANCZOS)
        return component.copy()


def _fallback_component(name: str, size: tuple[int, int]) -> Image.Image:
    width, height = size
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    if name.startswith("slot_"):
        color = {
            "slot_treasure": (211, 158, 83, 235),
            "slot_ancient": (168, 133, 205, 225),
            "slot_spirit": (93, 185, 184, 220),
            "slot_artifact": (117, 148, 145, 210),
            "slot_empty": (74, 105, 99, 150),
        }.get(name, (100, 150, 140, 200))
        draw.rounded_rectangle((1, 1, width - 2, height - 2), radius=18, fill=(9, 29, 30, 210), outline=color, width=2)
        draw.line((22, 45, width - 22, 45), fill=(*color[:3], 95), width=1)
        return image
    if name.startswith("rank_"):
        border = {
            "rank_1": (235, 197, 105, 235),
            "rank_2": (197, 215, 218, 225),
            "rank_3": (202, 151, 105, 225),
        }.get(name, (91, 159, 145, 205))
        draw.rounded_rectangle((1, 1, width - 2, height - 2), radius=14, fill=(8, 31, 31, 204), outline=border, width=2)
        return image
    border = (103, 179, 158, 205)
    fill = (7, 29, 29, 210)
    if name == "duel_winner":
        border, fill = (230, 190, 102, 230), (30, 43, 32, 218)
    elif name == "duel_loser":
        border, fill = (129, 164, 178, 220), (23, 33, 39, 218)
    draw.rounded_rectangle((1, 1, width - 2, height - 2), radius=22, fill=fill, outline=border, width=2)
    draw.rounded_rectangle((10, 10, width - 11, height - 11), radius=17, outline=(*border[:3], 70), width=1)
    return image


def _component(name: str, size: tuple[int, int]) -> Image.Image:
    path = COMP_DIR / f"{name}.png"
    if path.is_file():
        return _cached_component(str(path), *size).copy()
    return _fallback_component(name, size)


def _module(name: str, size: tuple[int, int]) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = _component(name, size)
    return image, ImageDraw.Draw(image)


def _rule(rules: Mapping[str, object] | object | None, key: str) -> object:
    if isinstance(rules, Mapping) and key in rules:
        return rules[key]
    if rules is not None and hasattr(rules, key):
        return getattr(rules, key)
    return getattr(DEFAULT_GAME_CONFIG, key)


def _positive_int(value: object, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return parsed if parsed > 0 else default


def _breakthrough_values(rules: Mapping[str, object] | object | None) -> tuple[tuple[int, ...], tuple[int, ...]]:
    cultivation = _rule(rules, "breakthrough_cultivation")
    stones = _rule(rules, "breakthrough_stones")
    defaults_c = DEFAULT_GAME_CONFIG.breakthrough_cultivation
    defaults_s = DEFAULT_GAME_CONFIG.breakthrough_stones
    try:
        values_c = tuple(_positive_int(value, defaults_c[index]) for index, value in enumerate(cultivation))
    except (TypeError, IndexError):
        values_c = defaults_c
    try:
        values_s = tuple(_positive_int(value, defaults_s[index]) for index, value in enumerate(stones))
    except (TypeError, IndexError):
        values_s = defaults_s
    if len(values_c) < 3:
        values_c = defaults_c
    if len(values_s) < 3:
        values_s = defaults_s
    return values_c, values_s


def _cultivation_target(realm: object, rules: Mapping[str, object] | object | None) -> tuple[int | None, int | None]:
    try:
        index = REALMS.index(str(realm))
    except ValueError:
        index = 0
    cultivation, stones = _breakthrough_values(rules)
    if index >= len(cultivation):
        return None, None
    return cultivation[index], stones[index]


def _header(
    card: Image.Image,
    title: str,
    subtitle: object,
    badge: str = "",
    *,
    subtitle_fonts: tuple[str, ...] = BODY_FONTS,
) -> None:
    layer = Image.new("RGBA", (1080, 340), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    draw.text((92, 55), "大爱仙途", font=_font(BOLD_FONTS, 25), fill=(205, 180, 112, 235))
    draw.text((88, 103), title, font=_font(TITLE_FONTS, 72), fill=(12, 28, 28, 155), anchor="la", stroke_width=4)
    draw.text((88, 97), title, font=_font(TITLE_FONTS, 72), fill=PALE_GOLD, anchor="la",
              stroke_width=1, stroke_fill=(99, 72, 33, 190))
    subtitle_width = 620 if badge else 590
    preferred_size = 45 if subtitle_fonts == TITLE_FONTS else 34
    minimum_size = 29 if subtitle_fonts == TITLE_FONTS else 25
    fitted, subtitle_font, _ = _fit_text(
        subtitle, subtitle_width, preferred_size=preferred_size, minimum_size=minimum_size, fonts=subtitle_fonts
    )
    subtitle_y = 227
    draw.text((90, subtitle_y), fitted, font=subtitle_font, fill=WHITE, anchor="la",
              stroke_width=1, stroke_fill=(8, 31, 29, 170))
    if badge:
        badge_text, badge_font, _ = _fit_text(badge, 190, preferred_size=30, minimum_size=25, fonts=BOLD_FONTS)
        bbox = draw.textbbox((0, 0), badge_text, font=badge_font)
        badge_width = max(130, bbox[2] - bbox[0] + 52)
        proposed_left = 90 + math.ceil(_measure(fitted, subtitle_font)) + 24
        same_line = proposed_left + badge_width <= 700
        left = proposed_left if same_line else 90
        center_y = 246 if same_line else 300
        draw.rounded_rectangle((left, center_y - 27, left + badge_width, center_y + 27), radius=27,
                               fill=(9, 48, 45, 225), outline=(139, 210, 185, 230), width=2)
        draw.text((left + badge_width // 2, center_y), badge_text, font=badge_font, fill=JADE, anchor="mm")
    card.alpha_composite(layer)


# Profile v3 uses its own palette; ranking and duel cards keep the v2 artwork.
PROFILE_GOLD = (211, 179, 120, 255)
PROFILE_WHITE = (246, 238, 221, 255)
PROFILE_MUTED = (180, 172, 157, 255)
PROFILE_RARITIES = {
    "artifact": (168, 185, 177, 255),
    "spirit": (124, 201, 192, 255),
    "ancient": (190, 162, 220, 255),
    "treasure": (239, 197, 117, 255),
}


def _profile_panel(size: tuple[int, int]) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    panel = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(panel)
    width, height = size
    points = [(12, 0), (width - 13, 0), (width - 1, 12), (width - 1, height - 13),
              (width - 13, height - 1), (12, height - 1), (0, height - 13), (0, 12)]
    draw.polygon(points, fill=(18, 19, 19, 236), outline=(129, 110, 75, 185), width=1)
    draw.line((20, 0, 108, 0), fill=PROFILE_GOLD, width=2)
    draw.line((width - 108, height - 1, width - 20, height - 1), fill=PROFILE_GOLD, width=2)
    return panel, draw


def _artifact_icon(draw: ImageDraw.ImageDraw, center: tuple[int, int], name: str,
                   color: tuple[int, int, int, int]) -> None:
    """Small code-drawn item silhouettes, independent of the painted background."""
    x, y = center
    draw.ellipse((x - 32, y - 32, x + 32, y + 32), outline=(*color[:3], 70), width=1)
    if "剑" in name or "针" in name:
        draw.polygon([(x + 18, y - 24), (x + 10, y - 4), (x - 10, y + 16),
                      (x - 16, y + 10), (x + 4, y - 10)], outline=color, width=2)
        draw.line((x - 21, y + 4, x - 4, y + 21), fill=color, width=3)
        draw.line((x - 13, y + 13, x - 22, y + 22), fill=color, width=4)
    elif "葫芦" in name or "瓶" in name or "钵" in name:
        draw.ellipse((x - 10, y - 24, x + 10, y - 4), outline=color, width=3)
        draw.ellipse((x - 18, y - 6, x + 18, y + 24), outline=color, width=3)
        draw.line((x - 7, y - 26, x + 7, y - 26), fill=color, width=3)
        draw.line((x - 15, y - 4, x + 16, y - 4), fill=color, width=2)
    elif "图" in name or "符" in name or "碑" in name:
        draw.rectangle((x - 16, y - 23, x + 16, y + 23), outline=color, width=2)
        draw.line((x - 23, y - 25, x + 23, y - 25), fill=color, width=4)
        draw.line((x - 23, y + 25, x + 23, y + 25), fill=color, width=4)
        draw.line([(x - 10, y + 12), (x, y - 8), (x + 5, y + 2), (x + 10, y - 3)], fill=color, width=2)
    elif "旗" in name or "幡" in name:
        draw.line((x - 17, y - 25, x - 17, y + 27), fill=color, width=3)
        draw.polygon([(x - 16, y - 24), (x + 22, y - 18), (x + 15, y - 2),
                      (x + 22, y + 12), (x - 16, y + 5)], outline=color, width=2)
    elif "鼎" in name or "印" in name or "甲" in name:
        draw.polygon([(x - 23, y - 17), (x + 23, y - 17), (x + 17, y + 12),
                      (x, y + 23), (x - 17, y + 12)], outline=color, width=3)
        draw.line((x - 12, y - 7, x + 12, y - 7), fill=color, width=2)
        draw.line((x, y - 7, x, y + 12), fill=color, width=2)
    elif "铃" in name or "鼓" in name:
        draw.arc((x - 16, y - 23, x + 16, y + 16), 180, 360, fill=color, width=3)
        draw.line((x - 16, y - 5, x - 22, y + 17, x + 22, y + 17, x + 16, y - 5), fill=color, width=3)
        draw.ellipse((x - 4, y + 20, x + 4, y + 26), fill=color)
    else:
        draw.ellipse((x - 21, y - 21, x + 21, y + 21), outline=color, width=3)
        draw.polygon([(x, y - 14), (x + 9, y), (x, y + 14), (x - 9, y)], outline=color, width=2)
        draw.line((x - 28, y, x - 24, y), fill=color, width=2)
        draw.line((x + 24, y, x + 28, y), fill=color, width=2)


def _profile_inventory(inventory: Sequence[Mapping[str, object]], limit: int) -> Image.Image:
    panel, draw = _profile_panel((CONTENT_WIDTH, 494))
    draw.text((28, 22), "随身法宝", font=_font(BOLD_FONTS, 32), fill=PROFILE_GOLD)
    note = f"{len(inventory)} / {limit} 件"
    if len(inventory) > limit:
        note += f" · 超限 {len(inventory) - limit}"
    draw.text((924, 29), note, font=_font(BODY_FONTS, 26), fill=PROFILE_MUTED, anchor="ra")
    # Two columns preserve full names; both six-slot and eight-slot bags are shown.
    slots = min(8, max(6, limit, len(inventory)))
    rows = math.ceil(slots / 2)
    gap = 12
    height = (392 - (rows - 1) * gap) // rows
    width = 442
    for index in range(slots):
        x = 28 + (index % 2) * (width + gap)
        y = 80 + (index // 2) * (height + gap)
        if index >= len(inventory):
            draw.rounded_rectangle((x, y, x + width, y + height), radius=8,
                                   fill=(26, 27, 26, 110), outline=(92, 83, 64, 110), width=1)
            draw.text((x + width // 2, y + height // 2), "虚位待宝",
                      font=_font(BODY_FONTS, 26), fill=(117, 112, 102, 255), anchor="mm")
            continue
        item = inventory[index]
        template = CATALOG.get(str(item.get("template_id", "")))
        name = template.name if template else str(item.get("name") or item.get("item_id") or "未名法宝")
        rarity = str(item.get("rarity") or (template.rarity if template else "artifact"))
        rarity = rarity if rarity in RARITY_NAMES else "artifact"
        color = PROFILE_RARITIES[rarity]
        draw.rounded_rectangle((x, y, x + width, y + height), radius=8,
                               fill=(*color[:3], 13), outline=(*color[:3], 110), width=1)
        draw.line((x, y + 14, x, y + height - 14), fill=color, width=3)
        _artifact_icon(draw, (x + 47, y + height // 2), name, color)
        _draw_fitted(draw, (x + 94, y + height // 2 - 13), name, 255,
                     preferred_size=34, minimum_size=28, fill=PROFILE_WHITE, fonts=BOLD_FONTS, anchor="lm")
        ability = template.ability_name if template and template.ability_name else "法宝攻势"
        if item.get("template_id") == "qiankun_ding":
            ability = "扩容 · 不加攻击"
        _draw_fitted(draw, (x + 94, y + height // 2 + 23), ability, 246,
                     preferred_size=25, minimum_size=23, fill=color, anchor="lm")
        draw.text((x + width - 14, y + height // 2 - 12), RARITY_NAMES[rarity],
                  font=_font(BODY_FONTS, 23), fill=color, anchor="rm")
    return panel


def render_profile_card(
    player: Mapping[str, object],
    inventory: Sequence[Mapping[str, object]],
    daily_info: Mapping[str, object],
    *,
    rules: Mapping[str, object] | object | None = None,
    output_dir: Path | None = None,
) -> Path:
    """Render the black-and-gold profile from live state and shared game rules."""
    from ..domain.artifact_effects import breakthrough_costs, exploration_cost

    card = _background("bg_profile.png")
    draw = ImageDraw.Draw(card)
    realm = str(player.get("realm", "qi"))
    realm_name = REALM_NAMES.get(realm, realm or "未知")
    cultivation = max(0, int(player.get("cultivation", 0) or 0))
    stones = max(0, int(player.get("spirit_stones", 0) or 0))
    target, stone_cost = _cultivation_target(realm, rules)
    if target is not None and stone_cost is not None:
        target, stone_cost = breakthrough_costs(target, stone_cost, inventory)
    debuffs = daily_info.get("debuffs") or []
    devil_tier = max(0, int(player.get("devil_contract_tier", 0) or 0))

    draw.text((86, 59), "大 爱 仙 途", font=_font(BOLD_FONTS, 27), fill=PROFILE_GOLD)
    _draw_fitted(draw, (82, 162), player.get("dao_name") or "无名修士", 610,
                 preferred_size=78, minimum_size=40, fill=PROFILE_WHITE, fonts=TITLE_FONTS, anchor="lm")
    realm_index = REALMS.index(realm) if realm in REALMS else 0
    stage = f"第 {realm_index + 1} 境"
    props = daily_info.get("props") or []
    subtitle = stage + f" · 道具 {len(props)} / 3"
    if devil_tier:
        subtitle += f" · 魔契 {devil_tier} 层"
    if debuffs:
        subtitle += " · 受咒"
    _draw_fitted(draw, (89, 246), subtitle, 610, preferred_size=26, minimum_size=24,
                 fill=PROFILE_MUTED, anchor="lm")
    draw.ellipse((758, 88, 932, 262), fill=(19, 20, 18, 222), outline=PROFILE_GOLD, width=2)
    draw.ellipse((766, 96, 924, 254), outline=(146, 121, 77, 190), width=1)
    draw.text((845, 128), "境 界", font=_font(BODY_FONTS, 21), fill=PROFILE_GOLD, anchor="mm")
    _draw_fitted(draw, (845, 184), realm_name, 142, preferred_size=62, minimum_size=32,
                 fill=PROFILE_WHITE, fonts=TITLE_FONTS, anchor="mm")

    stats, sd = _profile_panel((CONTENT_WIDTH, 218))
    sd.text((28, 22), "修为", font=_font(BODY_FONTS, 27), fill=PROFILE_MUTED)
    _draw_fitted(sd, (28, 82), f"{cultivation:,}", 285, preferred_size=53, minimum_size=32,
                 fill=PROFILE_WHITE, fonts=BOLD_FONTS, anchor="lm")
    target_label = f"/ {target:,}" if target is not None else "已达最高境界"
    _draw_fitted(sd, (534, 87), target_label, 208, preferred_size=28, minimum_size=23,
                 fill=PROFILE_MUTED, anchor="rm")
    progress = min(1.0, cultivation / target) if target else 1.0
    sd.rounded_rectangle((28, 127, 534, 136), radius=4, fill=(68, 61, 46, 255))
    if progress > 0:
        sd.rounded_rectangle((28, 127, 28 + max(8, round(506 * progress)), 136),
                             radius=4, fill=PROFILE_GOLD)
    sd.line((574, 26, 574, 145), fill=(140, 117, 74, 120), width=1)
    sd.text((608, 22), "灵石", font=_font(BODY_FONTS, 27), fill=PROFILE_MUTED)
    _draw_fitted(sd, (608, 82), f"{stones:,}", 314, preferred_size=46, minimum_size=26,
                 fill=PROFILE_GOLD, fonts=BOLD_FONTS, anchor="lm")
    if target is None:
        hint = "当前最高境界"
    else:
        next_realm = REALM_NAMES.get(REALMS[min(realm_index + 1, len(REALMS) - 1)], "")
        ready = cultivation >= target and stones >= stone_cost
        hint = (f"可突破至{next_realm} · #突破" if ready else f"突破至{next_realm}")
        hint += f"   /   修为 {target:,} · 灵石 {stone_cost:,}"
    _draw_fitted(sd, (28, 181), hint, 894, preferred_size=27, minimum_size=24,
                 fill=PROFILE_GOLD if target and cultivation >= target and stones >= stone_cost else PROFILE_MUTED,
                 anchor="lm")
    card.alpha_composite(stats, (SAFE_X, 300))

    inventory_limit = _positive_int(_rule(rules, "inventory_limit"), DEFAULT_GAME_CONFIG.inventory_limit)
    if any(item.get("template_id") == "qiankun_ding" for item in inventory):
        inventory_limit += 2
    card.alpha_composite(_profile_inventory(inventory, inventory_limit), (SAFE_X, 542))

    status, sd = _profile_panel((CONTENT_WIDTH, 157))
    remaining = max(0, int(daily_info.get("self_cultivate_remaining", 0) or 0))
    mining = max(0, int(daily_info.get("mine_remaining", 0) or 0))
    cooldown = self_cultivation_interval(
        _positive_int(_rule(rules, "self_cultivation_interval_seconds"),
                      DEFAULT_GAME_CONFIG.self_cultivation_interval_seconds), inventory, debuffs)
    cost = exploration_cost(_positive_int(_rule(rules, "exploration_cost"),
                                         DEFAULT_GAME_CONFIG.exploration_cost), inventory)
    cultivated, explored = bool(daily_info.get("cultivated")), bool(daily_info.get("explored"))
    entries = (
        ("每日修炼", "已领取" if cultivated else "可领取", "#修炼", cultivated),
        ("秘境探索", "已探索" if explored else "灵石不足" if stones < cost else "可探索",
         f"{cost} 灵石 · #秘境", explored or stones < cost),
        ("自主修炼", f"{math.ceil(remaining / 60)} 分钟后" if remaining else "可修炼",
         f"间隔 {math.ceil(cooldown / 60)} 分钟", remaining > 0),
        ("灵矿采矿", f"{math.ceil(mining / 60)} 分钟后" if mining else "可开采", "#采矿", mining > 0),
    )
    for index, (label, state, command, inactive) in enumerate(entries):
        cx = 119 + index * 238
        if index:
            sd.line((index * 238, 24, index * 238, 133), fill=(121, 103, 73, 120), width=1)
        sd.text((cx, 33), label, font=_font(BODY_FONTS, 25), fill=PROFILE_MUTED, anchor="mm")
        _draw_fitted(sd, (cx, 81), state, 214, preferred_size=32, minimum_size=25,
                     fill=PROFILE_MUTED if inactive else PROFILE_GOLD, fonts=BOLD_FONTS, anchor="mm")
        _draw_fitted(sd, (cx, 127), command, 214, preferred_size=23, minimum_size=22,
                     fill=PROFILE_MUTED, anchor="mm")
    card.alpha_composite(status, (SAFE_X, 1058))

    # Reserve three independent lines so debuffs never hide overflow or a live duel.
    notices = []
    if len(inventory) > inventory_limit:
        notices.append(f"储物袋超限 {len(inventory) - inventory_limit} 件 · 请先 #献宝 整理")
    if len(inventory) > 8:
        notices.append(f"面板展示前 8 件，另有 {len(inventory) - 8} 件 · #法宝 查看全部")
    if debuffs:
        names = [PROP_TEMPLATES[d["debuff_kind"]].name for d in debuffs
                 if d.get("debuff_kind") in PROP_TEMPLATES]
        notices.append("受咒：" + ("、".join(names) if names else "异常状态") + " · 可用清心净衣符解除")
    duel_status = _safe_text(daily_info.get("duel_status", "")).replace("\n", " ").strip()
    if duel_status:
        notices.append("当前斗法：" + duel_status)
    if not notices:
        notices = ["法宝随身生效 · 同名神通不叠加", "#法宝 查看详情   /   #法宝帮助 查看全录"]
    else:
        # There may be four warnings for an over-limit bag; combine the two bag lines.
        if len(notices) > 3:
            notices[0:2] = [notices[0] + f" · 另 {len(inventory) - 8} 件见 #法宝"]
    footer = Image.new("RGBA", (CONTENT_WIDTH, 118), (0, 0, 0, 0))
    fd = ImageDraw.Draw(footer)
    for index, line in enumerate(notices):
        _draw_fitted(fd, (24, 20 + index * 37), line, 904, preferred_size=25, minimum_size=23,
                     fill=PROFILE_GOLD if ("超限" in line or "受咒" in line) else PROFILE_MUTED, anchor="lm")
    card.alpha_composite(footer, (SAFE_X, 1240))
    draw = ImageDraw.Draw(card)
    draw.line((314, 1380, 444, 1380), fill=(155, 127, 77, 190), width=1)
    draw.line((636, 1380, 766, 1380), fill=(155, 127, 77, 190), width=1)
    draw.text((540, 1380), "广 告 招 租", font=_font(TITLE_FONTS, 25), fill=PROFILE_GOLD, anchor="mm")
    return _save_card(card, "profile", output_dir)


def render_ranking_card(
    ranking_rows: Sequence[Mapping[str, object]],
    *,
    total_count: int | None = 0,
    output_dir: Path | None = None,
) -> Path:
    """Render up to the true top ten ranking rows without inventing totals."""
    card = _background("bg_ranking.png")
    _header(card, "仙榜", "世界上没有最大的数亦没有真正的仙尊")
    labels = Image.new("RGBA", (CONTENT_WIDTH, 56), (0, 0, 0, 0))
    draw = ImageDraw.Draw(labels)
    label_font = _font(BOLD_FONTS, 27)
    draw.text((46, 27), "名次", font=label_font, fill=GOLD, anchor="mm")
    draw.text((120, 27), "仙名", font=label_font, fill=GOLD, anchor="lm")
    draw.text((560, 27), "境界", font=label_font, fill=GOLD, anchor="mm")
    draw.text((896, 27), "修为", font=label_font, fill=GOLD, anchor="rm")
    card.alpha_composite(labels, (SAFE_X, 298))

    visible_rows = ranking_rows[:10]
    if visible_rows:
        for index, row in enumerate(visible_rows):
            rank = index + 1
            y = 360 + index * 92
            key = str(rank) if rank <= 3 else "common"
            module, draw = _module(f"rank_{key}", (CONTENT_WIDTH, 84))
            rank_color = ((243, 204, 108, 255) if rank == 1 else (211, 226, 226, 255) if rank == 2
                          else (220, 166, 111, 255) if rank == 3 else JADE)
            draw.text((46, 43), str(rank), font=_font(BOLD_FONTS, 35), fill=rank_color, anchor="mm")
            _draw_fitted(draw, (120, 43), row.get("dao_name", "无名修士"), 330,
                         preferred_size=33, minimum_size=25, fill=WHITE, fonts=BOLD_FONTS, anchor="lm")
            realm = str(row.get("realm", "qi"))
            _draw_fitted(draw, (560, 43), REALM_NAMES.get(realm, realm), 170,
                         preferred_size=30, minimum_size=25, fill=(190, 224, 210, 255), anchor="mm")
            try:
                cultivation = f"{int(row.get('cultivation', 0) or 0):,}"
            except (TypeError, ValueError, OverflowError):
                cultivation = "0"
            _draw_fitted(draw, (896, 43), cultivation, 220, preferred_size=31, minimum_size=25,
                         fill=PALE_GOLD, fonts=BOLD_FONTS, anchor="rm")
            card.alpha_composite(module, (SAFE_X, y))
    else:
        empty, draw = _module("panel_jade", (CONTENT_WIDTH, 260))
        draw.text((476, 94), "榜上尚无修士", font=_font(TITLE_FONTS, 46), fill=PALE_GOLD, anchor="mm")
        draw.text((476, 163), "静候第一位问道之人", font=_font(BODY_FONTS, 29), fill=MUTED, anchor="mm")
        card.alpha_composite(empty, (SAFE_X, 470))

    footer = Image.new("RGBA", (CONTENT_WIDTH, 66), (0, 0, 0, 0))
    draw = ImageDraw.Draw(footer)
    note = _ranking_note(len(ranking_rows), total_count)
    draw.text((476, 30), note, font=_font(BODY_FONTS, 25), fill=MUTED, anchor="mm")
    card.alpha_composite(footer, (SAFE_X, 1298))
    return _save_card(card, "ranking", output_dir)


def _ranking_note(row_count: int, total_count: int | None) -> str:
    known_total = total_count if isinstance(total_count, int) and total_count > 0 else None
    if row_count > 10 or (row_count >= 10 and known_total is not None and known_total > 10):
        return "展示前 10 名" + (f" · 当前共 {known_total} 位修士" if known_total else "")
    if known_total:
        return f"当前共 {known_total} 位修士"
    if row_count:
        return f"当前展示 {row_count} 席"
    return "榜单暂空"


def _duel_meta(duel: Mapping[str, object]) -> str:
    public_id = _safe_text(duel.get("duel_id", "未编号")).replace("\n", " ").strip() or "未编号"
    public_id = _ellipsize(public_id, _font(BODY_FONTS, 25), 360)
    rounds = duel.get("total_rounds", duel.get("rounds"))
    try:
        round_count = int(rounds) if rounds is not None else None
    except (TypeError, ValueError, OverflowError):
        round_count = None
    if round_count is not None:
        suffix = f" · 共 {max(0, round_count)} 回合"
    else:
        try:
            current_round = int(duel["next_turn"])
        except (KeyError, TypeError, ValueError, OverflowError):
            current_round = None
        suffix = f" · 第 {max(1, current_round)} 轮结算" if current_round is not None else ""
    return f"战报 {public_id}{suffix}"


def _support_layout(text: object) -> tuple[int, list[list[str]]]:
    """Choose a readable one/two-column layout for a long support settlement."""
    safe = _safe_text(text).strip() or "本场没有围观支持"
    for size in range(30, 24, -1):
        font = _font(BODY_FONTS, size)
        lines = _wrap_text(safe, font, 872)
        line_height = size + 4
        capacity = ((298 - 74 - size) // line_height) + 1
        if len(lines) <= capacity:
            return size, [lines]
    font = _font(BODY_FONTS, 25)
    paragraphs = [part.strip() for part in safe.splitlines() if part.strip()] or [safe]
    wrapped = [line for part in paragraphs for line in _wrap_text(part, font, 420)]
    midpoint = math.ceil(len(wrapped) / 2)
    columns = [wrapped[:midpoint], wrapped[midpoint:]]
    capacity = ((298 - 72 - 25) // 29) + 1
    if any(len(column) > capacity for column in columns):
        raise ValueError("support settlement is too long for the duel card")
    return 25, columns


def _duel_outcome_tag(reason: object) -> str:
    return "应诀超时 · 判负" if str(reason) == "timeout" else "护体灵光已破 · 负"


def render_duel_card(
    duel: Mapping[str, object],
    winner_name: str,
    loser_name: str,
    loot_item: Mapping[str, object] | None,
    *,
    cultivation_loss: int = 20,
    support_text: str = "",
    reason: str = "lightning",
    output_dir: Path | None = None,
) -> Path:
    """Render a duel settlement card, including timeout and support details."""
    card = _background("bg_duel.png")
    _header(card, "斗法战报", _duel_meta(duel))

    for x, component_name, role, name, tag in (
        (64, "duel_winner", "胜方", winner_name, "斗法胜出"),
        (556, "duel_loser", "负方", loser_name, _duel_outcome_tag(reason)),
    ):
        module, draw = _module(component_name, (460, 356))
        role_color = GOLD if component_name == "duel_winner" else (169, 205, 211, 255)
        draw.text((32, 35), role, font=_font(BOLD_FONTS, 28), fill=role_color)
        _draw_fitted(draw, (230, 145), name, 390, preferred_size=42, minimum_size=28,
                     fill=WHITE, fonts=BOLD_FONTS, anchor="mm")
        draw.line((74, 210, 386, 210), fill=(*role_color[:3], 105), width=1)
        _draw_fitted(draw, (230, 267), tag, 390, preferred_size=31, minimum_size=25,
                     fill=role_color, fonts=BOLD_FONTS, anchor="mm")
        card.alpha_composite(module, (x, 360))

    versus = Image.new("RGBA", (96, 96), (0, 0, 0, 0))
    draw = ImageDraw.Draw(versus)
    draw.ellipse((3, 3, 92, 92), fill=(7, 28, 28, 238), outline=(222, 191, 107, 230), width=2)
    draw.text((48, 49), "VS", font=_font(BOLD_FONTS, 31), fill=PALE_GOLD, anchor="mm")
    card.alpha_composite(versus, (492, 490))

    loot, draw = _module("panel_loot", (CONTENT_WIDTH, 240))
    draw.text((32, 27), "战利品与结算", font=_font(BOLD_FONTS, 30), fill=GOLD)
    if loot_item:
        template = CATALOG.get(str(loot_item.get("template_id", "")))
        item_name = template.name if template else str(loot_item.get("name") or loot_item.get("item_id") or "未名法宝")
        rarity = str(loot_item.get("rarity") or (template.rarity if template else "artifact"))
        loot_line = f"{RARITY_NAMES.get(rarity, '法宝')}【{item_name}】转归 {winner_name}"
    else:
        loot_line = "本场没有法宝转移"
    loot_lines = _wrap_text(loot_line, _font(BOLD_FONTS, 32), 880, max_lines=2)
    for index, line in enumerate(loot_lines):
        draw.text((32, 82 + index * 42), line, font=_font(BOLD_FONTS, 32), fill=WHITE)
    try:
        loss = max(0, int(cultivation_loss))
    except (TypeError, ValueError, OverflowError):
        loss = 0
    loss_text = f"{loser_name} 实际扣除修为 {loss:,}" if loss else f"{loser_name} 本场未扣除修为"
    _draw_fitted(draw, (32, 182), loss_text, 880, preferred_size=29, minimum_size=25,
                 fill=(232, 177, 151, 255) if loss else MUTED)
    card.alpha_composite(loot, (SAFE_X, 744))

    support, draw = _module("panel_support", (CONTENT_WIDTH, 328))
    draw.text((32, 25), "围观支持结算", font=_font(BOLD_FONTS, 30), fill=GOLD)
    support_size, columns = _support_layout(support_text)
    support_font = _font(BODY_FONTS, support_size)
    line_height = support_size + 4
    if len(columns) == 1:
        for index, line in enumerate(columns[0]):
            draw.text((32, 74 + index * line_height), line, font=support_font, fill=(193, 226, 215, 255))
    else:
        for column_index, lines in enumerate(columns):
            x = 32 + column_index * 452
            if column_index:
                draw.line((x - 18, 72, x - 18, 298), fill=(105, 157, 143, 90), width=1)
            for index, line in enumerate(lines):
                draw.text((x, 72 + index * line_height), line, font=support_font, fill=(193, 226, 215, 255))
    card.alpha_composite(support, (SAFE_X, 1008))
    return _save_card(card, "duel", output_dir)
