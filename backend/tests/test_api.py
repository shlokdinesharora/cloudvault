import json

from conftest import load  # noqa: E402
api = load('cv_api', 'api')


def test_sanitize_filename():
    assert api.sanitize_filename("../../etc/passwd") == "passwd"
    assert api.sanitize_filename("C:\\temp\\my report.pdf") == "my report.pdf"
    assert api.sanitize_filename("we!rd$name.txt") == "we_rd_name.txt"
    assert api.sanitize_filename("...") == ""
    assert api.sanitize_filename(None) == ""


def test_password_hashing():
    stored = api.hash_password("s3cret")
    assert api.verify_password("s3cret", stored)
    assert not api.verify_password("wrong", stored)
    assert not api.verify_password("x", "garbage")
    assert api.hash_password("s3cret") != stored  # random salt


def test_clamp():
    assert api.clamp("5", 1, 10, 3) == 5
    assert api.clamp(99, 1, 10, 3) == 10
    assert api.clamp("abc", 1, 10, 3) == 3


def test_public_share_hides_hash():
    out = api.public_share({"token": "t", "file_id": "f", "password_hash": "x$y", "expires_at": 1})
    assert out["password_protected"] is True and "password_hash" not in out


def test_unknown_route_and_error_format():
    res = api.handler({"routeKey": "GET /nope"}, None)
    assert res["statusCode"] == 404
    assert json.loads(res["body"])["error"] == "no_route"
