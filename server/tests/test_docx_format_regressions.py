"""Neutral DOCX regressions for run styles, numbering and nested list exports."""

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.text import WD_ALIGN_PARAGRAPH
import pytest

from app.docx_export import edit_original_docx
from app.docx_parser import parse_docx, _list_info


def test_new_heading_uses_native_custom_heading_format(tmp_path):
    source, output = tmp_path / 'custom.docx', tmp_path / 'edited.docx'
    doc = Document()
    custom = doc.styles.add_style('自定义章节标题', WD_STYLE_TYPE.PARAGRAPH)
    custom.base_style = doc.styles['Heading 2']
    heading = doc.add_paragraph('现有章节', style=custom)
    heading.paragraph_format.line_spacing = 1.5
    heading.paragraph_format.space_before = Pt(8)
    heading.runs[0].font.name = '宋体'
    heading.runs[0].font.size = Pt(12)
    heading.runs[0].bold = True
    doc.add_paragraph('原有正文')
    doc.save(source)
    target = parse_docx(str(source), outline_headings=True).markdown + '\n\n## 新增章节'
    edit_original_docx(target, source, output, outline_headings=True)
    added = Document(output).paragraphs[-1]
    assert added.style.name == custom.name
    assert added.paragraph_format.line_spacing == 1.5
    assert added.paragraph_format.space_before == Pt(8)
    assert added.runs[0].font.name == '宋体' and added.runs[0].font.size == Pt(12)
    assert added.runs[0].bold is True


def test_new_body_does_not_borrow_caption_format(tmp_path):
    source, output = tmp_path / 'caption.docx', tmp_path / 'edited.docx'
    doc = Document()
    doc.add_heading('章节', 1)
    body = doc.add_paragraph('原有正文')
    body.paragraph_format.first_line_indent = Pt(24)
    body.paragraph_format.line_spacing = 1.5
    body.runs[0].font.size = Pt(12)
    caption = doc.add_paragraph('表1 验收表')
    caption.alignment = WD_ALIGN_PARAGRAPH.CENTER
    caption.runs[0].font.size = Pt(14)
    doc.add_heading('下一章节', 1)
    doc.save(source)
    target = parse_docx(str(source)).markdown.replace('# 下一章节', '新增正文\n\n# 下一章节')
    edit_original_docx(target, source, output)
    added = next(p for p in Document(output).paragraphs if p.text == '新增正文')
    assert added.paragraph_format.first_line_indent == Pt(24)
    assert added.paragraph_format.line_spacing == 1.5
    assert added.runs[0].font.size == Pt(12)
    assert added.alignment != WD_ALIGN_PARAGRAPH.CENTER


def test_new_body_does_not_borrow_custom_outline_heading_format(tmp_path):
    source, output = tmp_path / 'outline.docx', tmp_path / 'edited.docx'
    doc = Document()
    body = doc.add_paragraph('原有正文')
    body.runs[0].font.size = Pt(12)
    body.paragraph_format.first_line_indent = Pt(24)
    custom = doc.styles.add_style('模板自定义标题', WD_STYLE_TYPE.PARAGRAPH)
    custom.base_style = doc.styles['Normal']
    level = OxmlElement('w:outlineLvl')
    level.set(qn('w:val'), '1')
    custom.element.get_or_add_pPr().append(level)
    heading = doc.add_paragraph('空白章节', style=custom)
    heading.runs[0].font.size = Pt(16)
    doc.save(source)
    target = parse_docx(str(source), outline_headings=True).markdown + '\n\n新增正文'
    edit_original_docx(target, source, output, outline_headings=True)
    added = Document(output).paragraphs[-1]
    assert added.style.name == 'Normal'
    assert added.runs[0].font.size == Pt(12)
    assert added.paragraph_format.first_line_indent == Pt(24)


def test_new_table_inherits_matching_table_cell_format(tmp_path):
    source, output = tmp_path / 'table.docx', tmp_path / 'edited.docx'
    doc = Document()
    doc.add_heading('章节', 1)
    doc.add_paragraph('正文')
    table = doc.add_table(rows=2, cols=2, style='Table Grid')
    table.columns[0].width, table.columns[1].width = Pt(90), Pt(210)
    for row in table.rows:
        for cell in row.cells:
            cell.text = '原有单元格'
            cell.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
            cell.paragraphs[0].paragraph_format.line_spacing = 1.5
            cell.paragraphs[0].runs[0].font.name = '宋体'
            cell.paragraphs[0].runs[0].font.size = Pt(9)
    doc.save(source)
    target = parse_docx(str(source)).markdown + '\n\n| 新项目 | 新内容 |\n| --- | --- |\n| 甲 | 乙 |'
    edit_original_docx(target, source, output)
    added = Document(output).tables[-1]
    assert [column.width for column in added.columns] == [Pt(90), Pt(210)]
    for row in added.rows:
        for cell in row.cells:
            paragraph = cell.paragraphs[0]
            assert paragraph.alignment == WD_ALIGN_PARAGRAPH.CENTER
            assert paragraph.paragraph_format.line_spacing == 1.5
            assert paragraph.runs[0].font.name == '宋体' and paragraph.runs[0].font.size == Pt(9)


def test_new_table_borrows_merged_table_format_without_copying_merges_or_text(tmp_path):
    source, output = tmp_path / 'merged.docx', tmp_path / 'edited.docx'
    doc = Document()
    table = doc.add_table(rows=2, cols=2, style='Table Grid')
    table.columns[0].width, table.columns[1].width = Pt(90), Pt(210)
    table.cell(0, 0).merge(table.cell(0, 1)).text = '保留合并表头'
    original = table._tbl.xml
    doc.save(source)
    target = parse_docx(str(source)).markdown + '\n\n| 新项目 | 新内容 |\n| --- | --- |\n| 甲 | 乙 |'
    edit_original_docx(target, source, output)
    result = Document(output)
    assert result.tables[0]._tbl.xml == original
    added = result.tables[-1]
    assert added.style.name == 'Table Grid'
    assert [column.width for column in added.columns] == [Pt(90), Pt(210)]
    assert not added._tbl.xpath('.//w:gridSpan|.//w:vMerge')
    assert [[cell.text for cell in row.cells] for row in added.rows] == [['新项目','新内容'],['甲','乙']]


def test_contents_refresh_updates_indexes_and_pagination_without_other_fields(tmp_path):
    from types import SimpleNamespace
    from app.documents import _refresh_word_contents, _has_word_contents
    calls = []
    index = SimpleNamespace(Update=lambda: calls.append('entries'), UpdatePageNumbers=lambda: calls.append('pages'))
    opened = SimpleNamespace(TablesOfContents=[index], TablesOfFigures=[index], Repaginate=lambda: calls.append('paginate'))
    _refresh_word_contents(opened)
    assert calls == ['entries','entries','paginate','pages','pages']
    doc = Document()
    doc.add_paragraph('普通正文')
    source = tmp_path / 'contents.docx'
    doc.save(source)
    assert not _has_word_contents(source)
    field = OxmlElement('w:fldSimple')
    field.set(qn('w:instr'), ' TOC \\o "1-3" ')
    doc.paragraphs[0]._p.append(field)
    doc.save(source)
    assert _has_word_contents(source)


def test_edited_export_keeps_bold_table_header_with_data_and_repeats_on_new_pages(tmp_path):
    source, output = tmp_path / 'headers.docx', tmp_path / 'edited.docx'
    doc = Document()
    doc.add_paragraph('原有正文')
    table = doc.add_table(rows=2, cols=2)
    for cell in table.rows[0].cells:
        cell.text = '表头'
        cell.paragraphs[0].runs[0].bold = True
    doc.save(source)
    edit_original_docx(parse_docx(str(source)).markdown.replace('原有正文','修改正文'), source, output)
    header = Document(output).tables[0].rows[0]
    assert header._tr.xpath('./w:trPr/w:tblHeader')
    assert all(cell.paragraphs[0].paragraph_format.keep_with_next is True for cell in header.cells)


def test_added_emphasis_preserves_unmodified_run_properties_and_plain_body(tmp_path):
    source, output = tmp_path / "source.docx", tmp_path / "edited.docx"
    doc = Document()
    doc.add_heading("格式检查", 1)
    paragraph = doc.add_paragraph()
    first = paragraph.add_run("原有重点")
    first.bold, first.font.size, first.font.color.rgb = True, Pt(14), RGBColor(160, 0, 0)
    last = paragraph.add_run("原有普通")
    last.bold, last.font.size, last.font.color.rgb = False, Pt(12), RGBColor(0, 0, 0)
    last_xml = last._r.xml
    doc.sections[0].header.paragraphs[0].text = "保留页眉"
    doc.sections[0].footer.paragraphs[0].text = "保留页脚"
    doc.save(source)
    target = parse_docx(str(source)).markdown.replace("原有重点原有普通", "**更新重点**原有普通")
    edit_original_docx(target + "\n\n普通新增段落", source, output)
    result = Document(output)
    ordinary = next(run for run in result.paragraphs[1].runs if run.text == "原有普通")
    assert ordinary._r.xml == last_xml
    assert (ordinary.bold, ordinary.font.size, ordinary.font.color.rgb) == (False, Pt(12), RGBColor(0, 0, 0))
    added = result.paragraphs[-1]
    assert added.text == "普通新增段落" and all(run.bold is not True for run in added.runs)
    assert all(run.font.size == Pt(12) and run.font.color.rgb == RGBColor(0, 0, 0) for run in added.runs)
    assert result.sections[0].header.paragraphs[0].text == "保留页眉"
    assert result.sections[0].footer.paragraphs[0].text == "保留页脚"


def test_plain_paragraph_does_not_inherit_direct_numbering(tmp_path):
    source, output = tmp_path / "source.docx", tmp_path / "edited.docx"
    doc = Document()
    doc.add_heading("流程检查", 1)
    paragraph = doc.add_paragraph("步骤正文", style="Normal")
    properties = OxmlElement("w:numPr")
    for tag, value in (("ilvl", "0"), ("numId", "5")):
        element = OxmlElement("w:" + tag)
        element.set(qn("w:val"), value)
        properties.append(element)
    paragraph._p.get_or_add_pPr().append(properties)
    original_xml = paragraph._p.xml
    doc.save(source)
    edit_original_docx(parse_docx(str(source)).markdown + "\n\n新增说明段落", source, output)
    result = Document(output)
    assert result.paragraphs[1]._p.xml == original_xml
    assert result.paragraphs[1]._p.xpath("./w:pPr/w:numPr")
    assert result.paragraphs[2].text == "新增说明段落"
    assert not result.paragraphs[2]._p.xpath("./w:pPr/w:numPr")


def test_legacy_plain_markdown_keeps_normal_direct_numbering_during_text_edits(tmp_path):
    source, output = tmp_path / "source.docx", tmp_path / "edited.docx"
    doc = Document()
    doc.add_heading("旧稿兼容", 1)
    paragraph = doc.add_paragraph("原步骤文字", style="Normal")
    numbering = paragraph._p.get_or_add_pPr().get_or_add_numPr()
    numbering.get_or_add_numId().val = 5
    numbering.get_or_add_ilvl().val = 0
    original_properties = paragraph._p.pPr.xml
    ordinary = doc.add_paragraph("保留普通正文")
    ordinary_xml = ordinary._p.xml
    doc.save(source)
    legacy = "# 旧稿兼容\n\n原步骤文字\n\n保留普通正文"
    assert _list_info(paragraph) is None
    edit_original_docx(legacy, source, output)
    assert output.read_bytes() == source.read_bytes()
    edit_original_docx(legacy.replace("原步骤文字", "修改后的步骤文字"), source, output)
    result = Document(output)
    assert result.paragraphs[1].text == "修改后的步骤文字"
    assert result.paragraphs[1]._p.pPr.xml == original_properties
    assert result.paragraphs[2]._p.xml == ordinary_xml


@pytest.mark.parametrize("styles", [
    ["List Bullet", "List Bullet 2", "List Bullet 3"],
    ["List Number", "List Bullet 2", "List Number 3"],
])
def test_legacy_flat_list_markdown_does_not_remove_original_nesting(tmp_path, styles):
    source, output = tmp_path / "source.docx", tmp_path / "edited.docx"
    doc = Document()
    doc.add_heading("旧稿层级", 1)
    for text, style in zip(("主项", "子项", "孙项"), styles):
        doc.add_paragraph(text, style=style)
    doc.add_paragraph("保留普通正文")
    original_properties = [paragraph._p.pPr.xml for paragraph in doc.paragraphs[1:4]]
    original_parents = [doc.paragraphs[index]._p.xml for index in (1, 3)]
    doc.save(source)
    # This is the exact old parser representation, not a database migration.
    legacy = parse_docx(str(source), flat_lists=True).markdown
    assert legacy != parse_docx(str(source)).markdown and "\n    " not in legacy
    edit_original_docx(legacy, source, output)
    assert output.read_bytes() == source.read_bytes()
    edit_original_docx(legacy.replace("子项", "子项更新"), source, output)
    result = Document(output)
    assert [paragraph.text for paragraph in result.paragraphs[1:4]] == ["主项", "子项更新", "孙项"]
    assert [_list_info(paragraph)[1] for paragraph in result.paragraphs[1:4]] == [0, 1, 2]
    assert [paragraph._p.pPr.xml for paragraph in result.paragraphs[1:4]] == original_properties
    assert [result.paragraphs[index]._p.xml for index in (1, 3)] == original_parents


@pytest.mark.parametrize("listing,kinds", [
    ("- 主项\n    - 子项\n        - **孙项**", ["ul", "ul", "ul"]),
    ("1. 主项\n    - 子项\n        1. **孙项**", ["ol", "ul", "ol"]),
])
def test_nested_lists_export_as_independent_native_levels_and_can_be_edited_again(tmp_path, listing, kinds):
    source, output, again = tmp_path / "source.docx", tmp_path / "edited.docx", tmp_path / "again.docx"
    doc = Document()
    doc.add_heading("层级检查", 1)
    doc.add_paragraph("原有正文")
    doc.save(source)
    target = "# 层级检查\n\n" + listing + "\n\n独立段落"
    edit_original_docx(target, source, output)
    result = Document(output)
    items = result.paragraphs[1:4]
    assert [paragraph.text for paragraph in items] == ["主项", "子项", "孙项"]
    assert [_list_info(paragraph) for paragraph in items] == list(zip(kinds, [0, 1, 2]))
    assert next(run for run in items[-1].runs if run.text == "孙项").bold is True
    numbers = result.part.numbering_part.element
    for paragraph in items:
        number_id = paragraph._p.xpath("./w:pPr/w:numPr/w:numId/@w:val")[0]
        abstract_id = numbers.xpath(f"./w:num[@w:numId='{number_id}']/w:abstractNumId/@w:val")[0]
        assert len(numbers.xpath(f"./w:abstractNum[@w:abstractNumId='{abstract_id}']/w:lvl")) == 9
    assert not result.paragraphs[-1]._p.xpath("./w:pPr/w:numPr")
    roundtrip = parse_docx(str(output)).markdown
    assert "\n    - 子项\n        " in roundtrip
    unchanged = tmp_path / "unchanged.docx"
    edit_original_docx(roundtrip, output, unchanged)
    assert unchanged.read_bytes() == output.read_bytes()
    parent_xml, grandchild_xml = items[0]._p.xml, items[-1]._p.xml
    edit_original_docx(roundtrip.replace("子项", "子项更新"), output, again)
    edited = Document(again)
    assert [paragraph.text for paragraph in edited.paragraphs[1:4]] == ["主项", "子项更新", "孙项"]
    assert edited.paragraphs[1]._p.xml == parent_xml and edited.paragraphs[3]._p.xml == grandchild_xml
    assert [_list_info(paragraph)[1] for paragraph in edited.paragraphs[1:4]] == [0, 1, 2]


def test_adding_child_to_existing_list_preserves_parent_xml(tmp_path):
    source, output = tmp_path / "source.docx", tmp_path / "edited.docx"
    doc = Document()
    doc.add_heading("层级检查", 1)
    parent = doc.add_paragraph("主项", style="List Bullet")
    parent_xml = parent._p.xml
    doc.save(source)
    edit_original_docx(parse_docx(str(source)).markdown + "\n    - 新子项\n", source, output)
    result = Document(output)
    assert result.paragraphs[1]._p.xml == parent_xml
    assert [paragraph.text for paragraph in result.paragraphs[1:]] == ["主项", "新子项"]
    assert [_list_info(paragraph) for paragraph in result.paragraphs[1:]] == [("ul", 0), ("ul", 1)]


def test_list_nesting_beyond_word_limit_is_rejected_before_writing(tmp_path):
    source, output = tmp_path / "source.docx", tmp_path / "edited.docx"
    doc = Document()
    doc.add_heading("层级检查", 1)
    doc.save(source)
    target = "# 层级检查\n\n" + "\n".join("    " * depth + "- 项目" for depth in range(10))
    with pytest.raises(ValueError, match="九级"):
        edit_original_docx(target, source, output)
    assert not output.exists()
