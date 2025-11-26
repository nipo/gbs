"""Filter expression parsing and evaluation

Supports filter expressions for conditional source selection:
- Comparison: var = "value", var != "value", count > 10
- Regex matching: var matches "pattern"
- Default: default (always matches)

Syntax:
- Bare words are variable names
- String literals must be quoted: "string"
- Integer literals: 123, -45
- Operators: =, !=, <, >, <=, >=, matches
"""

import re
from dataclasses import dataclass
from typing import Any
from enum import Enum


class TokenType(Enum):
    """Token types for filter expressions"""
    VARIABLE = "VARIABLE"
    STRING = "STRING"
    INTEGER = "INTEGER"
    OPERATOR = "OPERATOR"
    DEFAULT = "DEFAULT"
    EOF = "EOF"


class Operator(Enum):
    """Comparison operators"""
    EQ = "="
    NE = "!="
    LT = "<"
    GT = ">"
    LE = "<="
    GE = ">="
    MATCHES = "matches"


@dataclass
class Token:
    """A lexical token"""
    type: TokenType
    value: Any
    position: int


class FilterLexer:
    """Tokenizer for filter expressions"""

    def __init__(self, expression: str):
        self.expression = expression
        self.position = 0

    def current_char(self) -> str | None:
        """Get current character"""
        if self.position >= len(self.expression):
            return None
        return self.expression[self.position]

    def peek_char(self, offset: int = 1) -> str | None:
        """Peek ahead at character"""
        pos = self.position + offset
        if pos >= len(self.expression):
            return None
        return self.expression[pos]

    def advance(self):
        """Move to next character"""
        self.position += 1

    def skip_whitespace(self):
        """Skip whitespace characters"""
        while self.current_char() and self.current_char().isspace():
            self.advance()

    def read_string(self) -> str:
        """Read a quoted string literal"""
        quote_char = self.current_char()
        assert quote_char in ('"', "'")
        self.advance()  # skip opening quote

        value = ""
        while self.current_char() and self.current_char() != quote_char:
            if self.current_char() == "\\":
                self.advance()
                # Simple escape handling
                if self.current_char() in ('"', "'", "\\"):
                    value += self.current_char()
                    self.advance()
                else:
                    raise ValueError(f"Invalid escape sequence at position {self.position}")
            else:
                value += self.current_char()
                self.advance()

        if self.current_char() != quote_char:
            raise ValueError(f"Unterminated string at position {self.position}")

        self.advance()  # skip closing quote
        return value

    def read_integer(self) -> int:
        """Read an integer literal"""
        start_pos = self.position
        if self.current_char() == "-":
            self.advance()

        if not (self.current_char() and self.current_char().isdigit()):
            raise ValueError(f"Invalid integer at position {start_pos}")

        while self.current_char() and self.current_char().isdigit():
            self.advance()

        return int(self.expression[start_pos:self.position])

    def read_word(self) -> str:
        """Read a bare word (variable name, keyword, or operator)"""
        start_pos = self.position
        while self.current_char() and (self.current_char().isalnum() or self.current_char() == "_"):
            self.advance()
        return self.expression[start_pos:self.position]

    def read_operator(self) -> str:
        """Read an operator"""
        start_pos = self.position

        # Try two-character operators first
        if self.current_char() in "!<>":
            self.advance()
            if self.current_char() == "=":
                self.advance()
                return self.expression[start_pos:self.position]
            return self.expression[start_pos:self.position]

        # Single character operators
        if self.current_char() == "=":
            self.advance()
            return "="

        raise ValueError(f"Invalid operator at position {start_pos}")

    def tokenize(self) -> list[Token]:
        """Tokenize the expression"""
        tokens = []

        while self.current_char():
            self.skip_whitespace()
            if not self.current_char():
                break

            start_pos = self.position
            char = self.current_char()

            # String literal
            if char in ('"', "'"):
                value = self.read_string()
                tokens.append(Token(TokenType.STRING, value, start_pos))

            # Integer literal
            elif char.isdigit() or (char == "-" and self.peek_char() and self.peek_char().isdigit()):
                value = self.read_integer()
                tokens.append(Token(TokenType.INTEGER, value, start_pos))

            # Operator or word
            elif char in "=!<>":
                op = self.read_operator()
                tokens.append(Token(TokenType.OPERATOR, op, start_pos))

            # Word (variable, keyword, or matches operator)
            elif char.isalpha() or char == "_":
                word = self.read_word()

                if word == "default":
                    tokens.append(Token(TokenType.DEFAULT, None, start_pos))
                elif word == "matches":
                    tokens.append(Token(TokenType.OPERATOR, "matches", start_pos))
                else:
                    tokens.append(Token(TokenType.VARIABLE, word, start_pos))

            else:
                raise ValueError(f"Unexpected character '{char}' at position {start_pos}")

        tokens.append(Token(TokenType.EOF, None, self.position))
        return tokens


@dataclass
class FilterExpression:
    """Parsed filter expression"""
    variable: str
    operator: Operator
    value: str | int

    def evaluate(self, context: dict[str, str | int]) -> bool:
        """Evaluate the expression against a context

        Args:
            context: Variable name -> value mapping

        Returns:
            True if the expression matches, False otherwise
        """
        if self.variable not in context:
            return False

        var_value = context[self.variable]

        if self.operator == Operator.EQ:
            return var_value == self.value

        elif self.operator == Operator.NE:
            return var_value != self.value

        elif self.operator in (Operator.LT, Operator.GT, Operator.LE, Operator.GE):
            # Comparison operators require both sides to be integers
            if not isinstance(var_value, int) or not isinstance(self.value, int):
                raise TypeError(
                    f"Comparison operator {self.operator.value} requires integer operands, "
                    f"got {type(var_value).__name__} and {type(self.value).__name__}"
                )

            if self.operator == Operator.LT:
                return var_value < self.value
            elif self.operator == Operator.GT:
                return var_value > self.value
            elif self.operator == Operator.LE:
                return var_value <= self.value
            elif self.operator == Operator.GE:
                return var_value >= self.value

        elif self.operator == Operator.MATCHES:
            # Regex matching requires string operands
            if not isinstance(self.value, str):
                raise TypeError("matches operator requires string pattern")

            var_str = str(var_value)
            try:
                return re.match(self.value, var_str) is not None
            except re.error as e:
                raise ValueError(f"Invalid regex pattern '{self.value}': {e}")

        return False


class FilterParser:
    """Parser for filter expressions"""

    def __init__(self, expression: str):
        self.expression = expression.strip()
        self.tokens: list[Token] = []
        self.position = 0

    def current_token(self) -> Token:
        """Get current token"""
        if self.position >= len(self.tokens):
            return self.tokens[-1]  # EOF token
        return self.tokens[self.position]

    def advance(self):
        """Move to next token"""
        if self.position < len(self.tokens) - 1:
            self.position += 1

    def parse(self) -> FilterExpression | None:
        """Parse the expression

        Returns:
            FilterExpression if valid, None if it's a default condition

        Raises:
            ValueError: If expression is invalid
        """
        # Handle empty or default
        if not self.expression or self.expression == "default":
            return None

        # Tokenize
        lexer = FilterLexer(self.expression)
        self.tokens = lexer.tokenize()
        self.position = 0

        # Handle default keyword
        if self.current_token().type == TokenType.DEFAULT:
            return None

        # Parse: VARIABLE OPERATOR VALUE
        if self.current_token().type != TokenType.VARIABLE:
            raise ValueError(
                f"Expected variable name at position {self.current_token().position}, "
                f"got {self.current_token().type.value}"
            )

        variable = self.current_token().value
        self.advance()

        if self.current_token().type != TokenType.OPERATOR:
            raise ValueError(
                f"Expected operator at position {self.current_token().position}, "
                f"got {self.current_token().type.value}"
            )

        op_str = self.current_token().value
        try:
            operator = Operator(op_str)
        except ValueError:
            raise ValueError(f"Invalid operator '{op_str}'")

        self.advance()

        if self.current_token().type not in (TokenType.STRING, TokenType.INTEGER):
            raise ValueError(
                f"Expected value at position {self.current_token().position}, "
                f"got {self.current_token().type.value}"
            )

        value = self.current_token().value
        self.advance()

        # Should be EOF now
        if self.current_token().type != TokenType.EOF:
            raise ValueError(
                f"Unexpected token at position {self.current_token().position}: "
                f"{self.current_token().type.value}"
            )

        return FilterExpression(variable, operator, value)


def parse_filter(expression: str) -> FilterExpression | None:
    """Parse a filter expression

    Args:
        expression: Filter expression string

    Returns:
        FilterExpression if valid, None if it's a default condition

    Raises:
        ValueError: If expression is invalid
    """
    parser = FilterParser(expression)
    return parser.parse()


def evaluate_filter(expression: str, context: dict[str, str | int]) -> bool:
    """Evaluate a filter expression

    Args:
        expression: Filter expression string
        context: Variable name -> value mapping

    Returns:
        True if expression matches (or is default), False otherwise
    """
    parsed = parse_filter(expression)
    if parsed is None:
        # Default always matches
        return True

    return parsed.evaluate(context)
