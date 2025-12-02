"""Tests for filter expression parsing and evaluation"""

import pytest
from gbs.repository.filters import (
    FilterLexer,
    FilterParser,
    TokenType,
    Operator,
    parse_filter,
    evaluate_filter,
)


class TestLexer:
    """Tests for FilterLexer"""

    def test_tokenize_simple_equality(self):
        """Test tokenizing simple equality expression"""
        lexer = FilterLexer('vendor = "xilinx"')
        tokens = lexer.tokenize()

        assert len(tokens) == 4  # VARIABLE, OPERATOR, STRING, EOF
        assert tokens[0].type == TokenType.VARIABLE
        assert tokens[0].value == "vendor"
        assert tokens[1].type == TokenType.OPERATOR
        assert tokens[1].value == "="
        assert tokens[2].type == TokenType.STRING
        assert tokens[2].value == "xilinx"
        assert tokens[3].type == TokenType.EOF

    def test_tokenize_integer_comparison(self):
        """Test tokenizing integer comparison"""
        lexer = FilterLexer("count > 10")
        tokens = lexer.tokenize()

        assert tokens[0].type == TokenType.VARIABLE
        assert tokens[0].value == "count"
        assert tokens[1].type == TokenType.OPERATOR
        assert tokens[1].value == ">"
        assert tokens[2].type == TokenType.INTEGER
        assert tokens[2].value == 10

    def test_tokenize_negative_integer(self):
        """Test negative integers"""
        lexer = FilterLexer("temp < -10")
        tokens = lexer.tokenize()

        assert tokens[2].type == TokenType.INTEGER
        assert tokens[2].value == -10

    def test_tokenize_matches_operator(self):
        """Test matches operator"""
        lexer = FilterLexer('family matches "^7.*"')
        tokens = lexer.tokenize()

        assert tokens[1].type == TokenType.OPERATOR
        assert tokens[1].value == "matches"

    def test_tokenize_default(self):
        """Test default keyword"""
        lexer = FilterLexer("default")
        tokens = lexer.tokenize()

        assert tokens[0].type == TokenType.DEFAULT

    def test_tokenize_operators(self):
        """Test all operators"""
        operators = ["=", "!=", "<", ">", "<=", ">="]
        for op in operators:
            lexer = FilterLexer(f"var {op} 1")
            tokens = lexer.tokenize()
            assert tokens[1].type == TokenType.OPERATOR
            assert tokens[1].value == op

    def test_tokenize_single_quoted_string(self):
        """Test single-quoted strings"""
        lexer = FilterLexer("vendor = 'intel'")
        tokens = lexer.tokenize()

        assert tokens[2].type == TokenType.STRING
        assert tokens[2].value == "intel"

    def test_tokenize_escaped_string(self):
        """Test escaped characters in strings"""
        lexer = FilterLexer(r'name = "foo\"bar"')
        tokens = lexer.tokenize()

        assert tokens[2].type == TokenType.STRING
        assert tokens[2].value == 'foo"bar'

    def test_unterminated_string_error(self):
        """Test error on unterminated string"""
        lexer = FilterLexer('vendor = "xilinx')
        with pytest.raises(ValueError, match="Unterminated string"):
            lexer.tokenize()

    def test_invalid_character_error(self):
        """Test error on invalid character"""
        lexer = FilterLexer("vendor @ xilinx")
        with pytest.raises(ValueError, match="Unexpected character"):
            lexer.tokenize()


class TestParser:
    """Tests for FilterParser"""

    def test_parse_simple_equality(self):
        """Test parsing simple equality"""
        expr = parse_filter('vendor = "xilinx"')
        assert expr is not None
        assert expr.variable == "vendor"
        assert expr.operator == Operator.EQ
        assert expr.value == "xilinx"

    def test_parse_inequality(self):
        """Test parsing inequality"""
        expr = parse_filter('vendor != "intel"')
        assert expr is not None
        assert expr.variable == "vendor"
        assert expr.operator == Operator.NE
        assert expr.value == "intel"

    def test_parse_integer_comparison(self):
        """Test parsing integer comparisons"""
        cases = [
            ("count > 10", Operator.GT, 10),
            ("count < 5", Operator.LT, 5),
            ("count >= 10", Operator.GE, 10),
            ("count <= 5", Operator.LE, 5),
        ]

        for expression, expected_op, expected_val in cases:
            expr = parse_filter(expression)
            assert expr is not None
            assert expr.variable == "count"
            assert expr.operator == expected_op
            assert expr.value == expected_val

    def test_parse_matches(self):
        """Test parsing matches operator"""
        expr = parse_filter('family matches "^7.*"')
        assert expr is not None
        assert expr.variable == "family"
        assert expr.operator == Operator.MATCHES
        assert expr.value == "^7.*"

    def test_parse_default(self):
        """Test parsing default"""
        assert parse_filter("default") is None
        assert parse_filter("") is None
        assert parse_filter("  ") is None

    def test_parse_whitespace_handling(self):
        """Test that whitespace is handled correctly"""
        expr = parse_filter('  vendor   =   "xilinx"  ')
        assert expr is not None
        assert expr.variable == "vendor"
        assert expr.value == "xilinx"

    def test_parse_error_missing_operator(self):
        """Test error when operator is missing"""
        with pytest.raises(ValueError, match="Expected operator"):
            parse_filter('vendor "xilinx"')

    def test_parse_error_missing_value(self):
        """Test error when value is missing"""
        with pytest.raises(ValueError, match="Expected value"):
            parse_filter("vendor =")

    def test_parse_error_extra_tokens(self):
        """Test error on extra tokens"""
        with pytest.raises(ValueError, match="Unexpected token"):
            parse_filter('vendor = "xilinx" extra')


class TestEvaluation:
    """Tests for expression evaluation"""

    def test_evaluate_equality_match(self):
        """Test equality evaluation - match"""
        assert evaluate_filter('vendor = "xilinx"', {"vendor": "xilinx"})

    def test_evaluate_equality_no_match(self):
        """Test equality evaluation - no match"""
        assert not evaluate_filter('vendor = "xilinx"', {"vendor": "intel"})

    def test_evaluate_missing_variable(self):
        """Test evaluation with missing variable"""
        assert not evaluate_filter('vendor = "xilinx"', {})

    def test_evaluate_inequality(self):
        """Test inequality evaluation"""
        assert evaluate_filter('vendor != "intel"', {"vendor": "xilinx"})
        assert not evaluate_filter('vendor != "intel"', {"vendor": "intel"})

    def test_evaluate_integer_comparison(self):
        """Test integer comparison evaluation"""
        context = {"count": 15}

        assert evaluate_filter("count > 10", context)
        assert not evaluate_filter("count > 20", context)
        assert evaluate_filter("count < 20", context)
        assert not evaluate_filter("count < 10", context)
        assert evaluate_filter("count >= 15", context)
        assert evaluate_filter("count <= 15", context)

    def test_evaluate_integer_with_string_error(self):
        """Test error when comparing string with integer operator"""
        expr = parse_filter("count > 10")
        with pytest.raises(TypeError, match="requires integer operands"):
            expr.evaluate({"count": "not_a_number"})

    def test_evaluate_matches(self):
        """Test regex matching"""
        context = {"family": "7series"}

        assert evaluate_filter('family matches "^7.*"', context)
        assert evaluate_filter('family matches ".*series$"', context)
        assert not evaluate_filter('family matches "^ultra.*"', context)

    def test_evaluate_matches_with_integer(self):
        """Test matches works with integer values converted to string"""
        assert evaluate_filter('version matches "^2.*"', {"version": 2023})

    def test_evaluate_matches_invalid_regex(self):
        """Test error on invalid regex pattern"""
        expr = parse_filter('family matches "[invalid"')
        with pytest.raises(ValueError, match="Invalid regex pattern"):
            expr.evaluate({"family": "test"})

    def test_evaluate_default(self):
        """Test default always matches"""
        assert evaluate_filter("default", {})
        assert evaluate_filter("default", {"any": "context"})

    def test_evaluate_negative_numbers(self):
        """Test negative number comparisons"""
        context = {"temp": -5}

        assert evaluate_filter("temp > -10", context)
        assert evaluate_filter("temp < 0", context)
        assert evaluate_filter("temp = -5", context)

    def test_evaluate_string_equality_with_integer(self):
        """Test that string/integer type mismatches work for equality"""
        # String "10" should not equal integer 10
        assert not evaluate_filter('count = "10"', {"count": 10})
        assert not evaluate_filter("count = 10", {"count": "10"})


class TestIntegration:
    """Integration tests for complete filter scenarios"""

    def test_vendor_selection(self):
        """Test typical vendor selection scenario"""
        xilinx_ctx = {"vendor": "xilinx", "family": "7series"}
        intel_ctx = {"vendor": "intel", "family": "cyclone5"}

        # Vendor selection
        assert evaluate_filter('vendor = "xilinx"', xilinx_ctx)
        assert not evaluate_filter('vendor = "xilinx"', intel_ctx)

        # Family selection with matches
        assert evaluate_filter('family matches "^7.*"', xilinx_ctx)
        assert evaluate_filter('family matches "cyclone.*"', intel_ctx)

    def test_hierarchical_conditions(self):
        """Test conditions that might appear in hierarchical groups"""
        contexts = [
            {"vendor": "xilinx", "family": "7series", "speed": 2},
            {"vendor": "xilinx", "family": "ultrascale", "speed": 3},
            {"vendor": "intel", "family": "cyclone5", "speed": 1},
        ]

        # First level: vendor
        xilinx_filter = 'vendor = "xilinx"'
        assert evaluate_filter(xilinx_filter, contexts[0])
        assert evaluate_filter(xilinx_filter, contexts[1])
        assert not evaluate_filter(xilinx_filter, contexts[2])

        # Second level: family (assuming vendor=xilinx matched)
        series7_filter = 'family = "7series"'
        assert evaluate_filter(series7_filter, contexts[0])
        assert not evaluate_filter(series7_filter, contexts[1])

        # Speed selection
        assert evaluate_filter("speed >= 2", contexts[0])
        assert evaluate_filter("speed >= 2", contexts[1])
        assert not evaluate_filter("speed >= 2", contexts[2])
