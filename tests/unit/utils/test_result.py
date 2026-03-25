"""Tests for result module."""

import pytest

from dexalot_sdk.utils.result import Result


class TestResult:
    """Test Result class."""

    def test_ok_creates_success_result(self):
        """Test Result.ok creates a successful result."""
        result = Result.ok("data")
        assert result.success is True
        assert result.data == "data"
        assert result.error is None

    def test_fail_creates_error_result(self):
        """Test Result.fail creates an error result."""
        result = Result.fail("error message")
        assert result.success is False
        assert result.data is None
        assert result.error == "error message"

    def test_bool_success(self):
        """Test __bool__ method returns True for success."""
        result = Result.ok("data")
        assert bool(result) is True
        assert result.success is True

    def test_bool_failure(self):
        """Test __bool__ method returns False for failure."""
        result = Result.fail("error")
        assert bool(result) is False
        assert result.success is False

    def test_bool_in_conditionals(self):
        """Test Result can be used in boolean contexts."""
        success_result = Result.ok("data")
        fail_result = Result.fail("error")

        if success_result:
            assert True
        else:
            pytest.fail("Success result should be truthy")

        if fail_result:
            pytest.fail("Fail result should be falsy")
        else:
            assert True

    def test_generic_type(self):
        """Test Result with generic type."""
        result: Result[dict] = Result.ok({"key": "value"})
        assert result.success is True
        assert isinstance(result.data, dict)
        assert result.data["key"] == "value"

    def test_none_data(self):
        """Test Result with None data."""
        result = Result.ok(None)
        assert result.success is True
        assert result.data is None

    def test_empty_error(self):
        """Test Result with empty error message."""
        result = Result.fail("")
        assert result.success is False
        assert result.error == ""
