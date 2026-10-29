import hashlib
from pathlib import Path
import zipfile

import pytest

from kakaocli_win.attachment_analysis import analyze_attachment


def test_utf8_text_has_local_identity_and_bounded_coverage(tmp_path):
    source = tmp_path / "대화.json"
    source.write_text('{"내용":"사진 설명"}', encoding="utf-8")
    result = analyze_attachment(source, max_chars=8)
    assert result["path"] == str(source.resolve())
    assert result["size_bytes"] == len(source.read_bytes())
    assert result["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert result["text"] == '{"내용":"사'
    assert result["text_chars"] == 8
    assert result["status"] == "partial"
    assert result["truncated"] is True


@pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-16", "cp949"])
def test_korean_text_encoding_is_preserved(tmp_path, encoding):
    source = tmp_path / "메모.csv"
    source.write_bytes("이름,날짜\n홍길동,2026-10-29".encode(encoding))
    result = analyze_attachment(source)
    assert result["supported"] is True
    assert result["status"] == "ok"
    assert result["text"] == "이름,날짜\n홍길동,2026-10-29"
    assert result["truncated"] is False


def test_unsupported_format_is_never_decoded_as_text(tmp_path):
    source = tmp_path / "program.exe"
    source.write_bytes(b"MZThis looks like text")
    result = analyze_attachment(source)
    assert result["supported"] is False
    assert result["status"] == "unsupported"
    assert result["text"] == ""


def test_binary_disguised_as_text_is_rejected(tmp_path):
    source = tmp_path / "bad.txt"
    source.write_bytes(b"hello\x00world")
    result = analyze_attachment(source)
    assert result["status"] == "error"
    assert result["text"] == ""


def test_utf16_binary_controls_are_rejected(tmp_path):
    source = tmp_path / "bad.txt"
    source.write_bytes("hello\0world".encode("utf-16"))
    assert analyze_attachment(source)["status"] == "error"


def test_missing_file_is_a_structured_error(tmp_path):
    result = analyze_attachment(tmp_path / "missing.pdf")
    assert result["error"]["code"] == "file_not_found"
    assert result["status"] == "error"


@pytest.mark.parametrize("limit", [0, -1, True, "20", 1000001])
def test_invalid_output_bounds_are_rejected(tmp_path, limit):
    with pytest.raises(ValueError):
        analyze_attachment(tmp_path / "missing.txt", max_chars=limit)


def test_image_is_verified_and_made_available_for_visual_review(tmp_path):
    Image = pytest.importorskip("PIL.Image")
    source = tmp_path / "photo.png"
    Image.new("RGB", (31, 17), "red").save(source)
    result = analyze_attachment(source)
    assert result["status"] == "ok"
    assert (result["width"], result["height"], result["format"]) == (31, 17, "PNG")
    assert result["visual_paths"] == [str(source.resolve())]
    assert result["needs_visual_review"] is True
    assert result["text"] == ""


def test_corrupt_image_does_not_claim_success(tmp_path):
    pytest.importorskip("PIL.Image")
    source = tmp_path / "fake.jpg"
    source.write_bytes(b"not an image")
    result = analyze_attachment(source)
    assert result["status"] == "error"
    assert result["visual_paths"] == []


def test_docx_preserves_runs_tables_and_headers_without_running_macros(tmp_path):
    source = tmp_path / "document.docx"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("word/document.xml", '<w:document xmlns:w="urn:word"><w:body>'
                         '<w:p><w:r><w:t>회의 </w:t></w:r><w:r><w:t>자료</w:t></w:r></w:p>'
                         '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>표 내용</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
                         '</w:body></w:document>')
        archive.writestr("word/header1.xml", '<w:hdr xmlns:w="urn:word"><w:p><w:r><w:t>머리말</w:t></w:r></w:p></w:hdr>')
        archive.writestr("word/vbaProject.bin", b"never execute this")
    result = analyze_attachment(source)
    assert result["status"] == "ok"
    assert "회의 자료\n표 내용" in result["text"]
    assert "머리말" in result["text"]
    assert "never execute" not in result["text"]


def test_hwpx_sections_use_numeric_order_and_nested_paragraphs_are_not_duplicated(tmp_path):
    source = tmp_path / "document.hwpx"
    with zipfile.ZipFile(source, "w") as archive:
        for index, value in [(10, "나중"), (2, "먼저")]:
            archive.writestr(f"Contents/section{index}.xml", '<hp:sec xmlns:hp="urn:hwp">'
                             f'<hp:p><hp:run><hp:t>{value}</hp:t><hp:tbl><hp:p><hp:run>'
                             '<hp:t>표 안</hp:t></hp:run></hp:p></hp:tbl></hp:run></hp:p></hp:sec>')
    result = analyze_attachment(source)
    assert result["status"] == "ok"
    assert result["text"].index("먼저") < result["text"].index("나중")
    assert result["text"].count("표 안") == 2
    assert result["mime_type"] == "application/hwp+zip"


def test_document_external_entities_are_rejected(tmp_path):
    source = tmp_path / "bad.docx"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("word/document.xml", '<!DOCTYPE x [<!ENTITY x SYSTEM "file:///secret">]><x>&x;</x>')
    result = analyze_attachment(source)
    assert result["status"] == "error"
    assert "DTD" in result["error"]["message"]


def test_utf16_xml_entities_are_also_rejected(tmp_path):
    source = tmp_path / "bad.docx"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("word/document.xml", '<!DOCTYPE x [<!ENTITY x "content">]><x>&x;</x>'.encode("utf-16"))
    assert analyze_attachment(source)["status"] == "error"


def test_partial_document_text_survives_a_corrupt_later_part(tmp_path):
    source = tmp_path / "partial.docx"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("word/document.xml", '<document><p><t>읽은 내용</t></p></document>')
        archive.writestr("word/header1.xml", "<broken>")
    result = analyze_attachment(source)
    assert result["status"] == "partial"
    assert "읽은 내용" in result["text"]
    assert result["error"]["code"] == "extraction_failed"


def _pdf_fixture(path, *, encrypted=False):
    pypdf = pytest.importorskip("pypdf")
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
    writer = pypdf.PdfWriter()
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    for index in range(4):
        page = writer.add_blank_page(width=300, height=200)
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 20 100 Td (Attachment page {index + 1}) Tj ET".encode())
        page[NameObject("/Contents")] = writer._add_object(stream)
    if encrypted:
        writer.encrypt("secret")
    writer.write(path)


def test_pdf_extracts_real_page_text(tmp_path):
    source = tmp_path / "document.pdf"
    _pdf_fixture(source)
    result = analyze_attachment(source)
    assert result["status"] == "ok"
    assert result["page_count"] == result["pages_extracted"] == 4
    assert "Attachment page 4" in result["text"]
    assert result["visual_paths"] == []


def test_encrypted_pdf_returns_explicit_error(tmp_path):
    source = tmp_path / "encrypted.pdf"
    _pdf_fixture(source, encrypted=True)
    result = analyze_attachment(source)
    assert result["status"] == "error"
    assert result["error"]["code"] == "encrypted_pdf"


def test_pdf_renders_only_first_three_bounded_previews_when_requested(tmp_path):
    pytest.importorskip("fitz")
    source = tmp_path / "document.pdf"
    _pdf_fixture(source)
    result = analyze_attachment(source, output_directory=tmp_path / "previews")
    assert len(result["visual_paths"]) == 3
    assert all(Path(path).is_file() for path in result["visual_paths"])
    assert all(Path(path).is_absolute() for path in result["visual_paths"])


def test_xlsx_keeps_korean_cells_and_stored_formula_without_evaluation(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    source = tmp_path / "table.xlsx"
    workbook = openpyxl.Workbook()
    workbook.active.title = "명단"
    workbook.active.append(["홍길동", 12])
    workbook.active.append(["합계", "=SUM(B1:B1)"])
    workbook.save(source)
    workbook.close()
    result = analyze_attachment(source)
    assert result["status"] == "ok"
    assert "홍길동\t12" in result["text"]
    assert "=SUM(B1:B1)" in result["text"]
    assert result["sheets"] == ["명단"]


def test_xlsx_reports_when_columns_are_omitted(tmp_path, monkeypatch):
    openpyxl = pytest.importorskip("openpyxl")
    import kakaocli_win.attachment_analysis as analysis
    monkeypatch.setattr(analysis, "_MAX_SHEET_COLUMNS", 2)
    source = tmp_path / "wide.xlsx"
    workbook = openpyxl.Workbook()
    workbook.active.append(["first", "second", "omitted"])
    workbook.active.append(["next", "row"])
    workbook.save(source)
    workbook.close()
    result = analyze_attachment(source)
    assert result["status"] == "partial"
    assert result["truncated"] is True
    assert "omitted" not in result["text"]
    assert "next\trow" in result["text"]
