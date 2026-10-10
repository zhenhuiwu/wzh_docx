"""Word 文档解析、转换和生成模块（增强版）"""

from __future__ import annotations

import logging

import base64
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

try:
    from docx import Document
    from docx.shared import Pt, RGBColor, Inches
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.text.paragraph import CT_P
    from docx.oxml.table import CT_Tbl
    from docx.table import _Cell, Table
    from docx.text.paragraph import Paragraph
    from docx.oxml.ns import qn
except ImportError:
    Document = None  # type: ignore


@dataclass
class ParsedDocument:
    """解析后的文档结构"""
    markdown: str
    outline: list[dict[str, Any]]
    format_template: dict[str, Any]
    images: list[dict[str, Any]]  # 新增：图片信息


def heading_level(paragraph: Paragraph, *, outline_headings: bool = True) -> int | None:
    """Word outline levels may live on a custom style's inherited base."""
    if not outline_headings:
        match = re.fullmatch(r'Heading\s*([1-6])', paragraph.style.name if paragraph.style else '')
        return int(match.group(1)) if match else None
    values = paragraph._p.xpath('./w:pPr/w:outlineLvl/@w:val')
    if values:
        return int(values[0]) + 1 if values[0].isdigit() and int(values[0]) < 6 else None
    style, seen = paragraph.style, set()
    while style is not None and style.style_id not in seen:
        seen.add(style.style_id)
        values = style.element.xpath('./w:pPr/w:outlineLvl/@w:val')
        if values:
            return int(values[0]) + 1 if values[0].isdigit() and int(values[0]) < 6 else None
        match = re.fullmatch(r'Heading\s*([1-6])', style.name or '')
        if match:
            return int(match.group(1))
        style = style.base_style
    return None


def _extract_paragraph_style(paragraph: Paragraph) -> dict[str, Any]:
    """提取段落样式"""
    style = {}

    if paragraph.style and paragraph.style.name:
        style['style_name'] = paragraph.style.name

    # 段落格式
    if paragraph.paragraph_format:
        pf = paragraph.paragraph_format
        if pf.alignment is not None:
            style['alignment'] = str(pf.alignment)
        if pf.left_indent:
            style['left_indent'] = pf.left_indent.pt
        if pf.first_line_indent:
            style['first_line_indent'] = pf.first_line_indent.pt
        if pf.line_spacing:
            style['line_spacing'] = pf.line_spacing
        if pf.space_before:
            style['space_before'] = pf.space_before.pt
        if pf.space_after:
            style['space_after'] = pf.space_after.pt

    # 字符格式（取第一个run的格式作为代表）
    if paragraph.runs:
        run = paragraph.runs[0]
        if run.font:
            font = run.font
            if font.name:
                style['font_name'] = font.name
            if font.size:
                style['font_size'] = font.size.pt
            if font.bold is not None:
                style['bold'] = font.bold
            if font.italic is not None:
                style['italic'] = font.italic
            if font.underline is not None:
                style['underline'] = bool(font.underline)
            if font.color and font.color.rgb:
                style['color'] = str(font.color.rgb)

    return style


def _build_format_template(doc: Document, *, outline_headings: bool = False) -> dict[str, Any]:
    """构建格式模板，记录各级标题和正文的样式"""
    template = {
        'headings': {},
        'body': {},
        'list': {},
        'table': {}
    }

    for paragraph in doc.paragraphs:
        style_name = paragraph.style.name if paragraph.style else 'Normal'

        # 标题样式
        if (level := heading_level(paragraph, outline_headings=outline_headings)) is not None:
            if str(level) not in template['headings']:
                template['headings'][str(level)] = _extract_paragraph_style(paragraph)

        # 正文样式
        elif style_name in ['Normal', 'Body Text'] and not template['body']:
            template['body'] = _extract_paragraph_style(paragraph)

    return template


def _list_info(paragraph: Paragraph) -> tuple[str, int] | None:
    """Map supported Word list styles and their direct nesting level."""
    style = paragraph.style.name if paragraph.style else ''
    if not style.startswith('List'):
        return None
    level = paragraph._p.xpath('./w:pPr/w:numPr/w:ilvl/@w:val')
    suffix = re.search(r'\s([2-9])$', style)
    depth = int(level[0]) if level and level[0].isdigit() else int(suffix.group(1)) - 1 if suffix else 0
    return ('ol' if 'Number' in style else 'ul', min(depth, 8))


def _paragraph_to_markdown(paragraph: Paragraph, *, flat_lists: bool = False, outline_headings: bool = False) -> str:
    """将段落转换为 Markdown"""
    text = _markdown_text(paragraph.text.strip())
    if not text:
        return ''

    style_name = paragraph.style.name if paragraph.style else 'Normal'

    # 标题转换
    if (level := heading_level(paragraph, outline_headings=outline_headings)) is not None:
        return f"{'#' * level} {text}\n"

    # 列表项
    listing = _list_info(paragraph)
    if listing:
        kind, depth = listing
        if flat_lists:
            depth = 0
        return f"{'    ' * depth}{'1. ' if kind == 'ol' else '- '}{text}\n"

    # 普通段落
    text = re.sub(r'^(\s*\d+)\.', r'\1\\.', text)
    text = re.sub(r'^([+-])(?=\s)', r'\\\1', text)
    if re.fullmatch(r'-{3,}', text):
        text = text.replace('-', '\\-')
    return f"{text}\n\n"


def _markdown_text(text: str) -> str:
    # Word text is literal; Markdown punctuation must not create new structure.
    return re.sub(r'([\\`*_{}\[\]<>|#])', r'\\\1', text)


def _table_to_markdown(table: Table) -> str:
    """将表格转换为 Markdown"""
    if not table.rows:
        return ''

    lines = []

    # 表头
    def cell_text(cell):
        return _markdown_text(cell.text.strip()).replace('\n', '<br>')

    header_cells = [cell_text(cell) for cell in table.rows[0].cells]
    lines.append('| ' + ' | '.join(header_cells) + ' |')
    lines.append('|' + '|'.join(['---' for _ in header_cells]) + '|')

    # 表格内容
    for row in table.rows[1:]:
        cells = [cell_text(cell) for cell in row.cells]
        lines.append('| ' + ' | '.join(cells) + ' |')

    return '\n'.join(lines) + '\n\n'


def _build_outline(doc: Document, *, outline_headings: bool = False) -> list[dict[str, Any]]:
    """构建文档大纲"""
    outline = []
    current_path = []  # 用于跟踪当前标题层级

    for i, paragraph in enumerate(doc.paragraphs):
        style_name = paragraph.style.name if paragraph.style else ''
        text = paragraph.text.strip()

        if (level := heading_level(paragraph, outline_headings=outline_headings)) is not None and text:

            # 调整当前路径
            while current_path and current_path[-1]['level'] >= level:
                current_path.pop()

            node = {
                'id': f'heading-{i}',
                'level': level,
                'text': text,
                'children': []
            }

            if current_path:
                current_path[-1]['children'].append(node)
            else:
                outline.append(node)

            current_path.append(node)

    return outline


def parse_docx(file_path: str, *, flat_lists: bool = False, outline_headings: bool = False) -> ParsedDocument:
    """解析 Word 文档，返回 Markdown 和格式模板"""
    if Document is None:
        raise RuntimeError("python-docx 未安装")

    doc = Document(file_path)

    # 构建格式模板
    format_template = _build_format_template(doc, outline_headings=outline_headings)

    # 构建大纲
    outline = _build_outline(doc, outline_headings=outline_headings)

    # 提取图片
    images = _extract_images(doc)
    image_map = {image['relationship_id']: image for image in images}

    # 转换为 Markdown
    markdown_parts = []
    list_root_kind = None

    for element in doc.element.body:
        if isinstance(element, CT_P):
            paragraph = Paragraph(element, doc)
            md = _paragraph_to_markdown(paragraph, flat_lists=flat_lists, outline_headings=outline_headings)
            listing = _list_info(paragraph) if not md.startswith('#') else None
            if listing and flat_lists:
                listing = (listing[0], 0)
            if md:
                if markdown_parts and not markdown_parts[-1].endswith('\n\n'):
                    same_list = bool(listing and list_root_kind and
                                     (listing[1] > 0 or listing[0] == list_root_kind))
                    if not same_list:
                        markdown_parts.append('\n')
                markdown_parts.append(md)
                list_root_kind = (listing[0] if listing[1] == 0 or not list_root_kind else list_root_kind) if listing else None

            # 检查段落中的图片
            for rel_id in element.xpath('.//w:drawing//a:blip/@r:embed'):
                img = image_map.get(rel_id)
                if img is not None:
                    markdown_parts.append(f"![{img['alt']}]({img['ref']})\n\n")
                    list_root_kind = None

        elif isinstance(element, CT_Tbl):
            table = Table(element, doc)
            md = _table_to_markdown(table)
            if md:
                if markdown_parts and not markdown_parts[-1].endswith('\n\n'):
                    markdown_parts.append('\n')
                markdown_parts.append(md)
                list_root_kind = None

    markdown = ''.join(markdown_parts).strip()

    return ParsedDocument(
        markdown=markdown,
        outline=outline,
        format_template=format_template,
        images=images
    )


def _extract_images(doc: Document) -> list[dict[str, Any]]:
    """提取文档中的图片"""
    images = []

    try:
        for rel_id, rel in doc.part.rels.items():
            if rel.reltype.endswith('/image') and not rel.is_external:
                image_part = rel.target_part
                image_bytes = image_part.blob

                # 生成图片引用
                image_ref = f"image_{len(images)}.{image_part.content_type.split('/')[-1]}"

                images.append({
                    'ref': image_ref,
                    'relationship_id': rel_id,
                    'alt': f'图片 {len(images) + 1}',
                    'data': base64.b64encode(image_bytes).decode('utf-8'),
                    'content_type': image_part.content_type
                })
    except Exception as e:
        print(f"图片提取失败: {e}")

    return images


def markdown_to_docx(
    markdown: str,
    format_template: dict[str, Any],
    output_path: str,
    images: list[dict[str, Any]] = None
) -> None:
    """将 Markdown 转换为 Word 文档，应用格式模板"""
    if Document is None:
        raise RuntimeError("python-docx 未安装")

    doc = Document()
    images = images or []
    image_map = {img['ref']: img for img in images}

    lines = markdown.split('\n')
    i = 0
    in_table = False
    table_lines = []

    while i < len(lines):
        line = lines[i]

        # 图片处理
        img_match = re.match(r'!\[(.*?)\]\((.*?)\)', line.strip())
        if img_match:
            alt_text = img_match.group(1)
            img_ref = img_match.group(2)

            if img_ref in image_map:
                img_data = image_map[img_ref]
                try:
                    # 解码图片数据
                    import io
                    image_bytes = base64.b64decode(img_data['data'])
                    image_stream = io.BytesIO(image_bytes)

                    # 添加图片到文档
                    doc.add_picture(image_stream, width=Inches(4))
                except Exception as e:
                    # 图片添加失败，添加文本说明
                    doc.add_paragraph(f"[图片: {alt_text}]")
            else:
                doc.add_paragraph(f"[图片: {alt_text}]")

            i += 1
            continue

        # 表格处理
        if line.strip().startswith('|'):
            if not in_table:
                in_table = True
                table_lines = []
            table_lines.append(line)
            i += 1
            continue
        elif in_table:
            # 表格结束，生成表格
            _add_markdown_table(doc, table_lines)
            table_lines = []
            in_table = False

        # 标题
        if line.startswith('#'):
            level = len(line) - len(line.lstrip('#'))
            text = line.lstrip('#').strip()
            _add_heading(doc, text, level, format_template)

        # 列表
        elif line.strip().startswith(('- ', '* ', '+ ')):
            text = line.strip()[2:]
            _add_list_item(doc, text, False, format_template)

        elif re.match(r'^\d+\.\s', line.strip()):
            text = re.sub(r'^\d+\.\s', '', line.strip())
            _add_list_item(doc, text, True, format_template)

        # 普通段落
        elif line.strip():
            _add_paragraph(doc, line.strip(), format_template)

        i += 1

    # 保存文档
    doc.save(output_path)


def _add_heading(
    doc: Document,
    text: str,
    level: int,
    format_template: dict[str, Any]
) -> None:
    """添加标题"""
    paragraph = doc.add_heading(text, level=level)

    # 应用格式模板中的样式
    if str(level) in format_template.get('headings', {}):
        style_info = format_template['headings'][str(level)]
        _apply_paragraph_style(paragraph, style_info)


def _add_paragraph(
    doc: Document,
    text: str,
    format_template: dict[str, Any]
) -> None:
    """添加普通段落"""
    paragraph = doc.add_paragraph(text)

    # 应用正文样式
    if format_template.get('body'):
        _apply_paragraph_style(paragraph, format_template['body'])


def _add_list_item(
    doc: Document,
    text: str,
    numbered: bool,
    format_template: dict[str, Any]
) -> None:
    """添加列表项"""
    style = 'List Number' if numbered else 'List Bullet'
    paragraph = doc.add_paragraph(text, style=style)


def _add_markdown_table(doc: Document, table_lines: list[str]) -> None:
    """添加表格"""
    if len(table_lines) < 2:
        return

    # 解析表格
    rows = []
    for line in table_lines:
        if '---' in line:  # 跳过分隔行
            continue
        cells = [cell.strip() for cell in line.strip('|').split('|')]
        rows.append(cells)

    if not rows:
        return

    # 创建表格
    table = doc.add_table(rows=len(rows), cols=len(rows[0]))
    table.style = 'Light Grid Accent 1'

    for i, row in enumerate(rows):
        for j, cell_text in enumerate(row):
            table.cell(i, j).text = cell_text


def _apply_paragraph_style(paragraph: Paragraph, style_info: dict[str, Any]) -> None:
    """应用段落样式"""
    try:
        pf = paragraph.paragraph_format

        # 对齐
        if 'alignment' in style_info:
            alignment_map = {
                'WD_ALIGN_PARAGRAPH.LEFT': WD_ALIGN_PARAGRAPH.LEFT,
                'WD_ALIGN_PARAGRAPH.CENTER': WD_ALIGN_PARAGRAPH.CENTER,
                'WD_ALIGN_PARAGRAPH.RIGHT': WD_ALIGN_PARAGRAPH.RIGHT,
                'WD_ALIGN_PARAGRAPH.JUSTIFY': WD_ALIGN_PARAGRAPH.JUSTIFY,
            }
            pf.alignment = alignment_map.get(style_info['alignment'], WD_ALIGN_PARAGRAPH.LEFT)

        # 缩进
        if 'left_indent' in style_info:
            pf.left_indent = Pt(style_info['left_indent'])
        if 'first_line_indent' in style_info:
            pf.first_line_indent = Pt(style_info['first_line_indent'])

        # 行距
        if 'line_spacing' in style_info:
            pf.line_spacing = style_info['line_spacing']

        # 段前段后间距
        if 'space_before' in style_info:
            pf.space_before = Pt(style_info['space_before'])
        if 'space_after' in style_info:
            pf.space_after = Pt(style_info['space_after'])

        # 字体样式
        if paragraph.runs:
            run = paragraph.runs[0]
            if 'font_name' in style_info:
                run.font.name = style_info['font_name']
            if 'font_size' in style_info:
                run.font.size = Pt(style_info['font_size'])
            if 'bold' in style_info:
                run.font.bold = style_info['bold']
            if 'italic' in style_info:
                run.font.italic = style_info['italic']
    except Exception:
        # 样式应用失败不影响内容生成
        logger.warning("applying run style failed; content kept without style", exc_info=True)
