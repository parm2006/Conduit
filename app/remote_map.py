"""Pure map rendering shared with the monitor editor's squircle geometry."""
import math
from PIL import Image, ImageChops, ImageColor, ImageDraw

SQUIRCLE_EXPONENT = 3.888
MAP_OPACITY = 179  # 70% of 255 (+10% opacity); includes border and grid.


def squircle_points(left, top, right, bottom, samples=96):
    cx, cy = (left + right) / 2, (top + bottom) / 2
    rx, ry = (right - left) / 2, (bottom - top) / 2
    points = []
    for index in range(samples):
        angle = 2 * math.pi * index / samples
        cosine, sine = math.cos(angle), math.sin(angle)
        points.extend((cx + rx * math.copysign(abs(cosine)**(2 / SQUIRCLE_EXPONENT), cosine),
                       cy + ry * math.copysign(abs(sine)**(2 / SQUIRCLE_EXPONENT), sine)))
    return points


def map_bounds(primary):
    left, top, right, _bottom = primary
    side = max(1, round((right - left) * 0.1))
    return left, top, side, side


def cursor_near_map(cursor, bounds):
    x, y, width, height = bounds
    cx, cy = x + width / 2, y + height / 2
    return (abs(cursor[0] - cx) <= width * math.sqrt(3) / 2
            and abs(cursor[1] - cy) <= height * math.sqrt(3) / 2)


def render_map(cells, active, side):
    """Cells: (machine_id, display_id, grid_x, grid_y, color), no labels."""
    scale = 3
    size = max(1, int(side)) * scale
    result = Image.new('RGBA', (size, size))
    if not cells:
        return result.resize((side, side))
    min_x, min_y = min(c[2] for c in cells), min(c[3] for c in cells)
    max_x, max_y = max(c[2] for c in cells) + 1, max(c[3] for c in cells) + 1
    tile = size / (max(max_x - min_x, max_y - min_y) + 2 / 3)
    origin_x = (size - (max_x - min_x) * tile) / 2 - min_x * tile
    origin_y = (size - (max_y - min_y) * tile) / 2 - min_y * tile
    bounds = [(origin_x + c[2] * tile, origin_y + c[3] * tile,
               origin_x + (c[2] + 1) * tile, origin_y + (c[3] + 1) * tile) for c in cells]
    mask = Image.new('L', result.size)
    # Union of each occupied square plus its own 1/3-tile fade, preserving L shapes.
    for box in bounds:
        local = Image.new('L', result.size)
        draw = ImageDraw.Draw(local)
        for step in range(24, -1, -1):
            offset = tile / 3 * step / 24
            draw.rectangle((box[0] - offset, box[1] - offset,
                            box[2] + offset, box[3] + offset),
                           fill=round(255 * (1 - step / 24)))
        mask = ImageChops.lighter(mask, local)
    lines = Image.new('L', result.size)
    draw = ImageDraw.Draw(lines)
    for column in range(min_x, max_x + 1):
        x = round(origin_x + column * tile)
        draw.line((x, 0, x, size), fill=255, width=scale)
    for row in range(min_y, max_y + 1):
        y = round(origin_y + row * tile)
        draw.line((0, y, size, y), fill=255, width=scale)
    result.paste((255, 255, 255, 255), (0, 0, size, size), ImageChops.multiply(lines, mask))
    draw = ImageDraw.Draw(result)
    order = sorted(range(len(cells)), key=lambda i: cells[i][:2] == active)
    for index in order:
        cell, box = cells[index], bounds[index]
        selected = cell[:2] == active
        color = ImageColor.getrgb(cell[4])
        inset = -tile * .075 if selected else scale * .7
        points = squircle_points(box[0] + inset, box[1] + inset,
                                 box[2] - inset, box[3] - inset)
        draw.polygon(points, fill=(*color, 255))
        if selected:
            draw.line(points + points[:2], fill=(255, 255, 255, 255), width=3 * scale, joint='curve')
    result = result.resize((side, side), Image.Resampling.LANCZOS)
    result.putalpha(result.getchannel('A').point(lambda alpha: round(alpha * (MAP_OPACITY / 255.0))))
    return result
