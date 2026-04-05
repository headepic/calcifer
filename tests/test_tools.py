"""Tests for built-in tools."""

import tempfile
from pathlib import Path

import pytest

from calcifer.types.tools import ToolContext
from calcifer.tools.BashTool import BashTool
from calcifer.tools.FileEditTool import FileEditTool
from calcifer.tools.FileReadTool import FileReadTool
from calcifer.tools.FileWriteTool import FileWriteTool
from calcifer.tools.GlobTool import GlobTool
from calcifer.tools.GrepTool import GrepTool


@pytest.fixture
def ctx(tmp_path):
    return ToolContext(cwd=str(tmp_path))


# -- Bash --

@pytest.mark.asyncio
async def test_bash_echo(ctx):
    tool = BashTool()
    args = tool.validate_input({"command": "echo hello"})
    result = await tool.call(args, ctx)
    assert "hello" in result.content
    assert result.is_error is False


@pytest.mark.asyncio
async def test_bash_exit_code(ctx):
    tool = BashTool()
    args = tool.validate_input({"command": "exit 1"})
    result = await tool.call(args, ctx)
    assert "Exit code: 1" in result.content


@pytest.mark.asyncio
async def test_bash_timeout(ctx):
    tool = BashTool()
    args = tool.validate_input({"command": "sleep 10", "timeout": 1})
    result = await tool.call(args, ctx)
    assert result.is_error is True
    assert "timed out" in result.content


# -- FileRead --

@pytest.mark.asyncio
async def test_file_read(ctx, tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("line1\nline2\nline3\n")

    tool = FileReadTool()
    args = tool.validate_input({"file_path": str(f)})
    result = await tool.call(args, ctx)
    assert "line1" in result.content
    assert "line3" in result.content


@pytest.mark.asyncio
async def test_file_read_not_found(ctx):
    tool = FileReadTool()
    args = tool.validate_input({"file_path": "/nonexistent/file.txt"})
    result = await tool.call(args, ctx)
    assert result.is_error is True


@pytest.mark.asyncio
async def test_file_read_offset_limit(ctx, tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("\n".join(f"line{i}" for i in range(100)))

    tool = FileReadTool()
    args = tool.validate_input({"file_path": str(f), "offset": 10, "limit": 5})
    result = await tool.call(args, ctx)
    assert "line10" in result.content
    assert "line14" in result.content
    assert "line15" not in result.content


# -- FileWrite --

@pytest.mark.asyncio
async def test_file_write(ctx, tmp_path):
    f = tmp_path / "out.txt"
    tool = FileWriteTool()
    args = tool.validate_input({"file_path": str(f), "content": "hello world"})
    result = await tool.call(args, ctx)
    assert result.is_error is False
    assert f.read_text() == "hello world"


@pytest.mark.asyncio
async def test_file_write_creates_dirs(ctx, tmp_path):
    f = tmp_path / "sub" / "dir" / "out.txt"
    tool = FileWriteTool()
    args = tool.validate_input({"file_path": str(f), "content": "nested"})
    result = await tool.call(args, ctx)
    assert result.is_error is False
    assert f.read_text() == "nested"


# -- FileEdit --

@pytest.mark.asyncio
async def test_file_edit(ctx, tmp_path):
    f = tmp_path / "edit.txt"
    f.write_text("hello world")

    tool = FileEditTool()
    args = tool.validate_input({
        "file_path": str(f),
        "old_string": "world",
        "new_string": "python",
    })
    result = await tool.call(args, ctx)
    assert result.is_error is False
    assert f.read_text() == "hello python"


@pytest.mark.asyncio
async def test_file_edit_not_found(ctx, tmp_path):
    tool = FileEditTool()
    args = tool.validate_input({
        "file_path": str(tmp_path / "nope.txt"),
        "old_string": "a",
        "new_string": "b",
    })
    result = await tool.call(args, ctx)
    assert result.is_error is True


@pytest.mark.asyncio
async def test_file_edit_unique_check(ctx, tmp_path):
    f = tmp_path / "dup.txt"
    f.write_text("aaa aaa")

    tool = FileEditTool()
    args = tool.validate_input({
        "file_path": str(f),
        "old_string": "aaa",
        "new_string": "bbb",
    })
    result = await tool.call(args, ctx)
    assert result.is_error is True
    assert "2 times" in result.content


@pytest.mark.asyncio
async def test_file_edit_replace_all(ctx, tmp_path):
    f = tmp_path / "dup.txt"
    f.write_text("aaa aaa")

    tool = FileEditTool()
    args = tool.validate_input({
        "file_path": str(f),
        "old_string": "aaa",
        "new_string": "bbb",
        "replace_all": True,
    })
    result = await tool.call(args, ctx)
    assert result.is_error is False
    assert f.read_text() == "bbb bbb"


# -- Glob --

@pytest.mark.asyncio
async def test_glob(ctx, tmp_path):
    (tmp_path / "a.py").write_text("")
    (tmp_path / "b.py").write_text("")
    (tmp_path / "c.txt").write_text("")

    tool = GlobTool()
    args = tool.validate_input({"pattern": "*.py", "path": str(tmp_path)})
    result = await tool.call(args, ctx)
    assert "a.py" in result.content
    assert "b.py" in result.content
    assert "c.txt" not in result.content


# -- Grep --

@pytest.mark.asyncio
async def test_grep(ctx, tmp_path):
    (tmp_path / "file.txt").write_text("hello world\nfoo bar\nhello again")

    tool = GrepTool()
    args = tool.validate_input({"pattern": "hello", "path": str(tmp_path / "file.txt")})
    result = await tool.call(args, ctx)
    assert "hello" in result.content


@pytest.mark.asyncio
async def test_grep_no_match(ctx, tmp_path):
    (tmp_path / "file.txt").write_text("nothing here")

    tool = GrepTool()
    args = tool.validate_input({"pattern": "xyz", "path": str(tmp_path / "file.txt")})
    result = await tool.call(args, ctx)
    assert "No matches" in result.content
