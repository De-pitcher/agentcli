import pytest

import agentcli.files as files_mod
from agentcli.files import FileReadError, expand_file_references, read_file_for_context


def test_read_file_for_context(tmp_path):
    f = tmp_path / "hello.py"
    f.write_text("print('hi')")
    block = read_file_for_context(f)
    assert "### File:" in block
    assert "```py" in block
    assert "print('hi')" in block


def test_missing_file_raises():
    with pytest.raises(FileReadError):
        read_file_for_context("/does/not/exist.py")


def test_directory_raises(tmp_path):
    with pytest.raises(FileReadError):
        read_file_for_context(tmp_path)


def test_oversized_file_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(files_mod, "MAX_FILE_BYTES", 10)
    f = tmp_path / "big.txt"
    f.write_text("x" * 100)
    with pytest.raises(FileReadError):
        read_file_for_context(f)


def test_expand_file_references(tmp_path):
    f = tmp_path / "note.md"
    f.write_text("some notes")
    expanded = expand_file_references(f"summarize this @{f}")
    assert "summarize this" in expanded
    assert "some notes" in expanded


def test_expand_no_references_passthrough():
    text = "just a normal message"
    assert expand_file_references(text) == text


def test_expand_missing_reference_raises():
    with pytest.raises(FileReadError):
        expand_file_references("look at @/nope/nothing.py")


def test_expand_with_punctuation(tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("content")
    expanded = expand_file_references(f"look at @{f}, please.")
    assert "content" in expanded
