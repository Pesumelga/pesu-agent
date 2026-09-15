"""Slack Search Query Compiler & Conservative Normalizer (Phase 9.9.5b.7).

Provides conservative dialect normalization, validation, and complexity telemetry
for Slack search queries before dispatching to Slack search execution backends.

Design & Architectural Invariants:
1. Conservative Normalization:
   - Whitespace trimming and internal whitespace collapsing.
   - Control / unprintable character sanitization.
   - Empty/whitespace-only and length limit enforcement.
2. Zero Semantic Invention:
   - Compiler does NOT invent synonyms or rewrite semantic intent.
   - Compiler does NOT make LLM calls.
   - Compiler does NOT access DOM or browser APIs.
   - Compiler does NOT employ speculative guessing (e.g. arbitrarily truncating OR chains).
3. Explicit Failure Semantics:
   - Invalid queries raise typed SlackQueryCompilationError.
   - On compilation error, zero search operations are initiated.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Iterator, Optional

from pydantic import BaseModel, Field


class SlackQueryCompilationError(ValueError):
    """Raised when a search query fails conservative Slack compilation/validation."""
    pass


class QueryComplexityMetrics(BaseModel):
    """Observable structural complexity telemetry for compiled queries."""

    token_count: int = Field(default=0, description="Total whitespace-delimited tokens")
    quoted_phrase_count: int = Field(default=0, description="Count of double-quoted phrases")
    boolean_operator_count: int = Field(default=0, description="Count of explicit boolean operators (OR, AND, NOT)")
    has_hyphenated_term: bool = Field(default=False, description="Whether query contains hyphenated terms")


class CompiledSlackQuery(BaseModel):
    """Typed result of compiling/normalizing a query for Slack search backend."""

    raw_query: str = Field(description="Original input query before normalization")
    compiled_query: str = Field(description="Normalized query ready for Slack search execution")
    length: int = Field(description="Character length of the compiled query")
    sha256_prefix: str = Field(description="First 16 hex characters of compiled query SHA-256 hash")
    raw_length: int = Field(default=0, description="Character length of the raw input query")
    raw_sha256_prefix: str = Field(default="", description="First 16 hex characters of raw query SHA-256 hash")
    normalization_changed_query: bool = Field(
        default=False, description="Whether normalization modified the query string"
    )
    outer_quotes_removed: bool = Field(
        default=False, description="Whether surrounding outer quotes were canonicalized/stripped"
    )
    complexity: QueryComplexityMetrics = Field(
        default_factory=QueryComplexityMetrics,
        description="Observable query complexity metrics",
    )

    def __iter__(self) -> Iterator[str]:
        """Supports sequence iteration for backend compatibility."""
        yield self.compiled_query

    def __len__(self) -> int:
        return 1

    def __getitem__(self, idx: int) -> str:
        if idx == 0 or idx == -1:
            return self.compiled_query
        raise IndexError("CompiledSlackQuery only contains 1 compiled query")


class SlackQueryCompiler:
    """Conservative compiler that validates and normalizes queries for Slack search."""

    MAX_QUERY_LENGTH = 500

    @classmethod
    def compile(cls, query: Any, max_queries: Optional[int] = None) -> CompiledSlackQuery:
        """Conservatively compiles and normalizes a candidate search query for Slack.

        Raises:
            SlackQueryCompilationError: If the query is invalid, empty, or exceeds length caps.
        """
        if hasattr(query, "query") and isinstance(query.query, str):
            raw_text = query.query
        else:
            raw_text = query

        if raw_text is None:
            raise SlackQueryCompilationError("Search query cannot be None.")

        if not isinstance(raw_text, str):
            raise SlackQueryCompilationError(
                f"Search query must be a string, got {type(raw_text).__name__}."
            )

        # 1. Control character sanitization: replace newlines, tabs, and unprintable chars with space
        cleaned_chars = []
        for ch in raw_text:
            code = ord(ch)
            if code < 32 and ch not in "\t\n\r":
                # Disallow low control characters (null, bell, backspace, etc.)
                raise SlackQueryCompilationError(
                    f"Search query contains invalid unprintable control character (ASCII {code})."
                )
            elif ch in "\t\n\r":
                cleaned_chars.append(" ")
            else:
                cleaned_chars.append(ch)

        cleaned_text = "".join(cleaned_chars)

        # 2. Whitespace normalization: collapse repeated whitespace and trim edges
        compiled_text = re.sub(r"\s+", " ", cleaned_text).strip()

        # 3. Outer Quote Canonicalization (Phase 9.9.5e.1)
        # Narrow rule: If the entire query is wrapped in exactly one matching quote pair
        # without internal delimiter quotes or compound boolean sub-expressions,
        # canonicalize by stripping the redundant outer quotes.
        # e.g., '"하위작업 추가"' -> '하위작업 추가'
        # Multi-quote/compound cases like '"foo" OR "bar"', 'foo -"bar"' remain strictly preserved.
        outer_quotes_removed = False
        if len(compiled_text) >= 2:
            first_ch = compiled_text[0]
            last_ch = compiled_text[-1]
            if (first_ch == '"' and last_ch == '"') or (first_ch == "'" and last_ch == "'"):
                inner = compiled_text[1:-1].strip()
                # Ensure no other quotes of the same delimiter exist inside
                if first_ch not in inner:
                    if not inner:
                        raise SlackQueryCompilationError("Search query cannot be empty or whitespace-only.")
                    compiled_text = inner
                    outer_quotes_removed = True

        # 4. Non-empty validation
        if not compiled_text:
            raise SlackQueryCompilationError("Search query cannot be empty or whitespace-only.")

        # 5. Length limits
        if len(compiled_text) > cls.MAX_QUERY_LENGTH:
            raise SlackQueryCompilationError(
                f"Search query exceeds maximum length limit ({cls.MAX_QUERY_LENGTH} chars, got {len(compiled_text)})."
            )

        # 6. Complexity Telemetry calculation (observability, non-destructive)
        quoted_phrases = re.findall(r'"[^"]*"', compiled_text)
        quoted_phrase_count = len(quoted_phrases)

        # Count boolean operators matching standalone words OR, AND, NOT (case-sensitive)
        boolean_ops = re.findall(r"\b(OR|AND|NOT)\b", compiled_text)
        boolean_operator_count = len(boolean_ops)

        # Check for hyphenated terms like sub-task
        has_hyphenated_term = bool(re.search(r"\b[\w]+-[\w]+\b", compiled_text))

        # Token count
        tokens = compiled_text.split()
        token_count = len(tokens)

        # SHA-256 fingerprints
        raw_sha256_prefix = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()[:16]
        sha256_prefix = hashlib.sha256(compiled_text.encode("utf-8")).hexdigest()[:16]
        normalization_changed = (raw_text != compiled_text)

        complexity = QueryComplexityMetrics(
            token_count=token_count,
            quoted_phrase_count=quoted_phrase_count,
            boolean_operator_count=boolean_operator_count,
            has_hyphenated_term=has_hyphenated_term,
        )

        return CompiledSlackQuery(
            raw_query=str(raw_text),
            compiled_query=compiled_text,
            length=len(compiled_text),
            sha256_prefix=sha256_prefix,
            raw_length=len(raw_text),
            raw_sha256_prefix=raw_sha256_prefix,
            normalization_changed_query=normalization_changed,
            outer_quotes_removed=outer_quotes_removed,
            complexity=complexity,
        )


def compile_slack_query(query: str) -> CompiledSlackQuery:
    """Convenience functional interface for compiling a Slack search query."""
    return SlackQueryCompiler.compile(query)
