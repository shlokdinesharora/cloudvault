
from conftest import load  # noqa: E402
p = load('cv_processor', 'processor')


def test_detect_type_magic():
    assert p.detect_type(b"\x89PNG\r\n\x1a\n....", "a.png") == "png"
    assert p.detect_type(b"%PDF-1.7 ...", "a.pdf") == "pdf"
    assert p.detect_type(b"MZ\x90\x00", "setup.exe") == "exe"
    assert p.detect_type(b"hello world", "notes.txt") == "text"
    assert p.detect_type(b"\x00\xff\xfe\x80\x81", "blob.dat") == "binary"


def test_extension_mismatch_warning():
    assert p.check_extension("text", "holiday.png")
    assert p.check_extension("png", "holiday.png") is None
    assert p.check_extension("zip", "report.docx") is None


def test_analyze_stream_checksum_and_gzip(tmp_path):
    import gzip, hashlib
    data = b"abc" * 1000
    out = tmp_path / "x.gz"
    r = p.analyze_stream([data[:1500], data[1500:]], gz_path=str(out))
    assert r["sha256"] == hashlib.sha256(data).hexdigest()
    assert r["size"] == len(data)
    assert not r["infected"]
    assert gzip.open(out).read() == data


def test_eicar_detected_even_across_chunks():
    payload = b"junk" + p.EICAR + b"more"
    mid = len(payload) // 2
    assert p.analyze_stream([payload[:mid], payload[mid:]])["infected"]
    assert not p.analyze_stream([b"clean file"])["infected"]


def test_search_text_only_for_text():
    assert p.search_text(b"Hello   WORLD\nline", "text", "a.txt") == "hello world line"
    assert p.search_text(b"\x89PNG", "png", "a.png") == ""


def test_parse_key_and_records():
    assert p.parse_key("uploads/u1/f1/my file.txt") == ("u1", "f1", "my file.txt")
    assert p.parse_key("processed/u1/f1/x.gz") is None
    import json
    rec = {"body": json.dumps({"Records": [{"s3": {"object": {"key": "uploads/u/f/a+b.txt"}}}]})}
    assert p.keys_from_record(rec) == ["uploads/u/f/a b.txt"]
    assert p.keys_from_record({"body": json.dumps({"Event": "s3:TestEvent"})}) == []
