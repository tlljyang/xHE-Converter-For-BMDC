"""Unit tests for naming rules."""
from pathlib import Path

from xhe4bmdc.engine.naming import apply_rename, sanitize_name, unique_output_path


def test_sanitize_removes_illegal():
    assert "<>:not allowed?*" not in sanitize_name('<>:not"allowed?*|')
    assert sanitize_name("normal.mp4") == "normal.mp4"


def test_sanitize_reserved_names():
    assert sanitize_name("CON.mp4").startswith("_")
    assert sanitize_name("aux") == "_aux"


def test_sanitize_trailing_dots():
    assert sanitize_name("name. ") == "name"


def test_unique_output_appends_suffix(tmp_path: Path):
    first = unique_output_path(tmp_path, "a.mp4")
    first.write_bytes(b"x")
    second = unique_output_path(tmp_path, "a.mp4")
    assert second.name == "a_1.mp4"
    second.write_bytes(b"x")
    third = unique_output_path(tmp_path, "a.mp4")
    assert third.name == "a_2.mp4"


def test_unique_output_missing_dir(tmp_path: Path):
    out = unique_output_path(tmp_path / "sub" / "dir", "v.mp4")
    assert out.parent.is_dir()
    assert out.name == "v.mp4"


def test_apply_rename_defaults_to_original():
    assert apply_rename("clip.mp4", "") == "clip.mp4"
    assert apply_rename("clip.mp4", "clip.mp4") == "clip.mp4"


def test_apply_rename_keeps_extension():
    assert apply_rename("clip.mp4", "newname") == "newname.mp4"


def test_apply_rename_changes_extension():
    assert apply_rename("clip.mp4", "clip.mkv") == "clip.mkv"


def test_apply_rename_sanitizes():
    assert apply_rename("clip.mp4", "my clip?:1") == "my clip__1.mp4"
