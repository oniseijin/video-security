from __future__ import annotations

from video_security.jp_plates import ken_for_plate, looks_japanese


def test_ken_for_plate_place_name() -> None:
    assert ken_for_plate("習志野500 れ12-08") == ("千葉県", "Chiba")


def test_ken_for_plate_tokyo() -> None:
    assert ken_for_plate("品川 500 あ 12-08") == ("東京都", "Tokyo")


def test_ken_for_plate_kana_place() -> None:
    assert ken_for_plate("なにわ れ 12-08") == ("大阪府", "Osaka")


def test_ken_for_plate_no_match() -> None:
    assert ken_for_plate("L12-08") is None
    assert ken_for_plate("") is None
    assert ken_for_plate("AB123") is None


def test_ken_for_plate_earliest_match_wins() -> None:
    assert ken_for_plate("横浜と品川") == ("神奈川県", "Kanagawa")


def test_looks_japanese() -> None:
    assert looks_japanese("L12-08") is False
    assert looks_japanese("習志野500") is True
    assert looks_japanese("よ12") is True
