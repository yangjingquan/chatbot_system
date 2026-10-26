import pytest
from fastapi import HTTPException
from app.knowledge import parse_file,split_content

def test_scan_pdf_does_not_claim_success():
    from pypdf import PdfWriter
    import io
    buffer=io.BytesIO()
    writer=PdfWriter()
    writer.add_blank_page(width=500,height=500)
    writer.write(buffer)
    with pytest.raises(HTTPException) as error:
        parse_file('scan.pdf',buffer.getvalue())
    assert error.value.status_code==422
    assert 'OCR' in error.value.detail

def test_invalid_encoding_is_reported():
    with pytest.raises(HTTPException) as error:
        parse_file('bad.txt',b'\xff\xfe\xfd')
    assert error.value.status_code==422

def test_chunks_have_boundaries_and_no_lost_tail():
    chunks=split_content('## 第一节\n'+ '学习资料'*500+'结束标记')
    assert all(len(c)<=650 for c in chunks)
    assert chunks[-1].endswith('结束标记')
