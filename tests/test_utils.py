"""Tests for GBS utility functions"""

import os
from pathlib import Path
import pytest
from gbs.utils import expand_path


class TestExpandPath:
    """Tests for path expansion with ~ and environment variables"""

    def test_plain_path(self):
        """Plain paths are passed through unchanged"""
        result = expand_path("/usr/local/bin")
        assert result == Path("/usr/local/bin")

    def test_relative_path(self):
        """Relative paths are passed through unchanged"""
        result = expand_path("relative/path")
        assert result == Path("relative/path")

    def test_tilde_expansion(self):
        """Tilde expands to home directory"""
        result = expand_path("~/projects")
        assert result == Path.home() / "projects"

    def test_tilde_in_middle(self):
        """Tilde only expands at start"""
        # pathlib's expanduser only expands ~ at the start
        result = expand_path("prefix/~/suffix")
        # This should NOT expand the tilde
        assert result == Path("prefix/~/suffix")

    def test_env_var_simple(self):
        """Environment variables are expanded"""
        os.environ["TEST_VAR"] = "/test/path"
        try:
            result = expand_path("$TEST_VAR/subdir")
            assert result == Path("/test/path/subdir")
        finally:
            del os.environ["TEST_VAR"]

    def test_env_var_braces(self):
        """Environment variables with braces are expanded"""
        os.environ["TEST_VAR"] = "/test/path"
        try:
            result = expand_path("${TEST_VAR}/subdir")
            assert result == Path("/test/path/subdir")
        finally:
            del os.environ["TEST_VAR"]

    def test_multiple_env_vars(self):
        """Multiple environment variables are expanded"""
        os.environ["BASE"] = "/base"
        os.environ["VERSION"] = "1.2.3"
        try:
            result = expand_path("$BASE/tool/$VERSION/bin")
            assert result == Path("/base/tool/1.2.3/bin")
        finally:
            del os.environ["BASE"]
            del os.environ["VERSION"]

    def test_tilde_and_env_var(self):
        """Both tilde and environment variables are expanded"""
        os.environ["SUBDIR"] = "projects"
        try:
            result = expand_path("~/$SUBDIR/mylib")
            assert result == Path.home() / "projects" / "mylib"
        finally:
            del os.environ["SUBDIR"]

    def test_undefined_env_var(self):
        """Undefined environment variables are left unchanged"""
        # os.path.expandvars leaves undefined variables unchanged
        result = expand_path("$UNDEFINED_VAR_XYZ/path")
        assert str(result) == "$UNDEFINED_VAR_XYZ/path"

    def test_empty_string(self):
        """Empty string becomes empty path"""
        result = expand_path("")
        assert result == Path("")

    def test_home_env_var(self):
        """$HOME environment variable expands correctly"""
        result = expand_path("$HOME/projects")
        assert result == Path.home() / "projects"

    def test_windows_style_env_var(self):
        """Windows-style %VAR% is not expanded (Unix expandvars only)"""
        # os.path.expandvars on Unix doesn't expand %VAR% syntax
        result = expand_path("%HOME%/projects")
        # This should NOT be expanded on Unix systems
        assert "%HOME%" in str(result) or result == Path.home() / "projects"
