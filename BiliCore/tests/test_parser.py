from __future__ import annotations

from urllib.parse import parse_qs, urlencode, urlparse

from bilicore import parser as bili_parser
from bilicore.parser import BilibiliParser, extract_bilibili_url, sign_wbi


def test_extracts_bilibili_video_and_short_links_and_trims_punctuation():
    assert extract_bilibili_url("看 BV https://www.bilibili.com/video/BV1xx411c7mD/?p=2。") == (
        "https://www.bilibili.com/video/BV1xx411c7mD/?p=2"
    )
    assert extract_bilibili_url("https://b23.tv/AbCd123!") == "https://b23.tv/AbCd123"
    assert extract_bilibili_url("ordinary text") is None


def test_sign_wbi_is_deterministic_and_contains_signature():
    signed = sign_wbi({"bvid": "BV1xx411c7mD", "foo": "a!b"}, "a" * 32, "b" * 32, timestamp=123)
    assert signed["wts"] == "123"
    assert len(signed["w_rid"]) == 32
    assert parse_qs(urlparse("https://x/?" + urlencode(signed)).query)["foo"] == ["a!b"]


def test_parser_returns_available_qualities_for_selected_page(monkeypatch):
    parser = BilibiliParser(cookies={"SESSDATA": "test"})
    parser._wbi_keys = ("a" * 32, "b" * 32)
    calls = []
    play_call_count = 0

    def fake_get_json(url, params=None):
        nonlocal play_call_count
        calls.append((url, params))
        if url == bili_parser.VIEW_URL:
            return {
                "bvid": "BV1xx411c7mD", "aid": 123, "title": "测试视频",
                "owner": {"name": "UP 主"}, "duration": 120, "pic": "https://img/cover.jpg",
                "pages": [
                    {"cid": 10, "page": 1, "part": "第一 P", "duration": 70},
                    {"cid": 20, "page": 2, "part": "第二 P", "duration": 50},
                ],
            }
        assert url == bili_parser.PLAYURL_URL
        play_call_count += 1
        streams = [
            {"id": 64, "width": 1280, "height": 720, "codecs": "avc1", "baseUrl": "https://cdn/720-avc", "backupUrl": ["https://backup/720"]},
            {"id": 80, "width": 1920, "height": 1080, "codecs": "hev1", "baseUrl": "https://cdn/1080-hevc"},
            {"id": 80, "width": 1920, "height": 1080, "codecs": "avc1", "baseUrl": "https://cdn/1080-avc"},
        ]
        if play_call_count == 2:
            streams.append({"id": 127, "width": 7680, "height": 4320, "codecs": "av01", "baseUrl": "https://cdn/8k-av1"})
        return {
            "support_formats": [
                {"quality": 80, "new_description": "1080P"},
                {"quality": 64, "new_description": "720P"},
                {"quality": 127, "new_description": "8K"},
            ],
            "dash": {
                "video": streams,
                "audio": [{"baseUrl": "https://cdn/audio", "backupUrl": ["https://backup/audio"]}],
            },
        }

    monkeypatch.setattr(parser, "_signed_get", fake_get_json)
    try:
        info = parser.parse("https://www.bilibili.com/video/BV1xx411c7mD/?p=2")
    finally:
        parser.close()

    assert info.page == 2
    assert info.cid == 20
    assert info.part == "第二 P"
    assert info.author == "UP 主"
    assert [(item.quality_id, item.name, item.height) for item in info.qualities] == [
        (127, "8K", 4320), (80, "1080P", 1080), (64, "720P", 720),
    ]
    assert info.qualities[1].codecs == ("hev1", "avc1")
    assert info.qualities[1].video_urls == ("https://cdn/1080-hevc", "https://cdn/1080-avc")
    assert info.qualities[1].audio_urls == ("https://cdn/audio", "https://backup/audio")
    assert [call[1]["qn"] for call in calls if call[0] == bili_parser.PLAYURL_URL] == [80, 127]
    assert all(call[1]["cid"] == 20 for call in calls if call[0] == bili_parser.PLAYURL_URL)


def test_http_flow_fetches_nav_keys_and_sends_signed_view_and_playurl(monkeypatch):
    parser = BilibiliParser(cookies={"SESSDATA": "private-test-cookie"})
    requests_seen = []
    img_key = "0123456789abcdef0123456789abcdef"
    sub_key = "fedcba9876543210fedcba9876543210"

    class Response:
        url = ""

        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self.payload

    def fake_get(url, params=None, **kwargs):
        requests_seen.append((url, params or {}, dict(parser.session.headers), dict(parser.session.cookies)))
        if url == bili_parser.NAV_URL:
            return Response({"code": 0, "data": {"wbi_img": {
                "img_url": f"https://i0.hdslb.com/bfs/wbi/{img_key}.png",
                "sub_url": f"https://i0.hdslb.com/bfs/wbi/{sub_key}.png",
            }}})
        if url == bili_parser.VIEW_URL:
            return Response({"code": 0, "data": {
                "bvid": "BV1xx411c7mD", "aid": 1, "title": "HTTP 测试",
                "pages": [{"cid": 9, "part": "P1", "duration": 60}],
            }})
        quality = 64 if str(params["qn"]) == "80" else 127
        height = 720 if quality == 64 else 4320
        return Response({"code": 0, "data": {
            "support_formats": [{"quality": quality, "new_description": f"{height}P"}],
            "dash": {"video": [{
                "id": quality, "width": height * 16 // 9, "height": height,
                "codecs": "avc1", "baseUrl": f"https://cdn/{quality}",
            }], "audio": [{"baseUrl": "https://cdn/audio"}]},
        }})

    monkeypatch.setattr(parser.session, "get", fake_get)
    try:
        info = parser.parse("https://www.bilibili.com/video/BV1xx411c7mD/")
    finally:
        parser.close()

    assert [request[0] for request in requests_seen] == [
        bili_parser.NAV_URL, bili_parser.VIEW_URL,
        bili_parser.PLAYURL_URL, bili_parser.PLAYURL_URL,
    ]
    assert requests_seen[0][3]["SESSDATA"] == "private-test-cookie"
    assert requests_seen[0][2]["Referer"] == bili_parser.REFERER
    for url, params, _headers, _cookies in requests_seen[1:]:
        assert params["wts"]
        assert len(params["w_rid"]) == 32
        assert "bvid" in params
        if url == bili_parser.PLAYURL_URL:
            assert params["fnval"] == "4048"
    assert [(item.quality_id, item.height) for item in info.qualities] == [(127, 4320), (64, 720)]
    assert info.qualities[0].audio_urls == ("https://cdn/audio",)


def test_parser_reports_unavailable_page(monkeypatch):
    parser = BilibiliParser()
    parser._wbi_keys = ("a" * 32, "b" * 32)
    monkeypatch.setattr(parser, "_signed_get", lambda *_: {"pages": [{"cid": 1}]})
    try:
        try:
            parser.parse("https://www.bilibili.com/video/BV1xx411c7mD/?p=2")
        except ValueError as exc:
            assert "只有 1 P" in str(exc)
        else:
            raise AssertionError("expected page validation failure")
    finally:
        parser.close()
