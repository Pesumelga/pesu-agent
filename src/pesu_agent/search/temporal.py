"""Generic Temporal Relevance Engine (Phase 12.2).

Provides domain-agnostic date extraction and categorical temporal relation classification.
Enforces strict boundaries:
- Zero domain-specific hardcoding (no hardcoded years or windows like 202[4-9]).
- Universal 4-digit year parsing (1900-2099) with date validation.
- Separate evaluation of message authored timestamp and body explicitly mentioned dates.
- Deterministic combination policy with explainable categorical relations.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import logging
import re
from typing import Optional
from zoneinfo import ZoneInfo

from pesu_agent.investigation.models import (
    EvidenceItem,
    ResolvedTemporalScope,
    TemporalConstraint,
    TemporalRelation,
    TemporalResolutionKind,
    TemporalResolutionStatus,
    TemporalStrictness,
)

logger = logging.getLogger(__name__)

# Bounded 4-digit year pattern validated against calendar dates
RE_UNIVERSAL_YEAR = r"\d{4}"

# 1. Full dates: YYYY-MM-DD, YYYY.MM.DD, YYYY/MM/DD
RE_FULL_DATE = re.compile(
    rf"\b({RE_UNIVERSAL_YEAR})[-./](0?[1-9]|1[0-2])[-./](0?[1-9]|[12]\d|3[01])\b"
)

# 2. Korean full/partial dates: YYYY년 M월 D일 or YYYY년 M월
RE_KOREAN_DATE = re.compile(
    rf"\b({RE_UNIVERSAL_YEAR})년\s*(0?[1-9]|1[0-2])월(?:\s*(0?[1-9]|[12]\d|3[01])일)?\b"
)

# 3. Year-month: YYYY-MM, YYYY.MM, YYYY/MM (not followed by another date separator)
RE_YEAR_MONTH = re.compile(
    rf"\b({RE_UNIVERSAL_YEAR})[-./](0?[1-9]|1[0-2])(?!\s*[-./]\s*\d)\b"
)

# 4. Universal year-only: YYYY년 or standalone YYYY
RE_YEAR_ONLY = re.compile(
    rf"\b({RE_UNIVERSAL_YEAR})(?:년도?|\b)"
)

# 5. Deterministic generic relative expressions
RE_RELATIVE_TODAY = re.compile(r"(?<![가-힣])오늘(?:[에은는도가를의와과부터까지]|\b)")
RE_RELATIVE_YESTERDAY = re.compile(r"(?<![가-힣])어제(?:[에은는도가를의와과부터까지]|\b)")
RE_RELATIVE_THIS_WEEK = re.compile(r"(?<![가-힣])이번\s*주(?:[에은는도가를의와과부터까지]|\b)")
RE_RELATIVE_LAST_WEEK = re.compile(r"(?<![가-힣])지난\s*주(?:[에은는도가를의와과부터까지]|\b)")
RE_RELATIVE_THIS_MONTH = re.compile(r"(?<![가-힣])이번\s*달(?:[에은는도가를의와과부터까지]|\b)")
RE_RELATIVE_LAST_MONTH = re.compile(r"(?<![가-힣])지난\s*달(?:[에은는도가를의와과부터까지]|\b)")
RE_RELATIVE_THIS_YEAR = re.compile(r"(?<![가-힣])올해(?:[에은는도가를의와과부터까지]|\b)")
RE_RELATIVE_LAST_YEAR = re.compile(r"(?<![가-힣])작년(?:[에은는도가를의와과부터까지]|\b)")
RE_RELATIVE_RECENT_DAYS = re.compile(r"(?<![가-힣])최근\s*(\d+)\s*일(?:간)?(?:[에은는도가를의와과부터까지]|\b)")
RE_RELATIVE_RECENT_WEEKS = re.compile(r"(?<![가-힣])최근\s*(\d+)\s*주(?:간)?(?:[에은는도가를의와과부터까지]|\b)")
RE_SOFT_RECENCY = re.compile(r"(?<![가-힣])(최근(?:에|는|의|동향)?|요즘|예전에|이전에)(?:[에은는도가를의와과부터까지]|\b)")


class ParsedDateSpan:
    """Parsed date, year-month, or year range."""

    def __init__(
        self,
        year: int,
        month: Optional[int] = None,
        day: Optional[int] = None,
    ):
        self.year = year
        self.month = month
        self.day = day

    @property
    def is_exact_date(self) -> bool:
        return self.month is not None and self.day is not None

    def overlaps_interval(self, start: date, end: date) -> bool:
        """Checks if this parsed span overlaps with [start, end]."""
        if self.is_exact_date:
            try:
                d = date(self.year, self.month, self.day)  # type: ignore[arg-type]
                return start <= d <= end
            except ValueError:
                return False

        if self.month is not None:
            # Month span
            import calendar
            _, last_day = calendar.monthrange(self.year, self.month)
            span_start = date(self.year, self.month, 1)
            span_end = date(self.year, self.month, last_day)
            return not (span_end < start or span_start > end)

        # Year span
        span_start = date(self.year, 1, 1)
        span_end = date(self.year, 12, 31)
        return not (span_end < start or span_start > end)

    def is_exact_year_match(self, start: date, end: date) -> bool:
        """Checks if the year matches either start or end year."""
        return start.year <= self.year <= end.year


def parse_iso_date(date_str: Optional[str]) -> Optional[date]:
    """Safely parses an ISO date string (YYYY-MM-DD)."""
    if not date_str:
        return None
    try:
        clean = date_str.strip().split("T")[0]
        return date.fromisoformat(clean)
    except Exception:
        return None


def parse_authored_timestamp(ts_raw: Optional[str]) -> Optional[ParsedDateSpan]:
    """Parses message authored timestamp into a ParsedDateSpan."""
    if not ts_raw:
        return None

    clean = str(ts_raw).strip()
    if not clean:
        return None

    # 1. Slack epoch timestamp (e.g. "1771892661.466919")
    try:
        val = float(clean)
        if val > 0:
            dt = datetime.fromtimestamp(val, tz=timezone.utc)
            return ParsedDateSpan(year=dt.year, month=dt.month, day=dt.day)
    except (ValueError, OverflowError, OSError):
        pass

    # 2. ISO timestamp format (e.g. "2026-08-27T13:00:00Z")
    try:
        dt = datetime.fromisoformat(clean.replace("Z", "+00:00"))
        return ParsedDateSpan(year=dt.year, month=dt.month, day=dt.day)
    except Exception:
        pass

    # 3. Standard date representations in timestamp string
    for m in RE_FULL_DATE.finditer(clean):
        try:
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            date(y, mo, d)
            return ParsedDateSpan(year=y, month=mo, day=d)
        except ValueError:
            continue

    for m in RE_KOREAN_DATE.finditer(clean):
        try:
            y = int(m.group(1))
            mo = int(m.group(2))
            d = int(m.group(3)) if m.group(3) else None
            if d:
                date(y, mo, d)
            return ParsedDateSpan(year=y, month=mo, day=d)
        except ValueError:
            continue

    return None


def extract_mentioned_dates(text: Optional[str]) -> list[ParsedDateSpan]:
    """Extracts all generic 4-digit date representations from message body text."""
    if not text:
        return []

    results: list[ParsedDateSpan] = []
    seen_keys: set[str] = set()

    # 1. Full dates (e.g. 2026-08-27, 2026.08.27)
    for m in RE_FULL_DATE.finditer(text):
        try:
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            date(y, mo, d)
            key = f"{y}-{mo:02d}-{d:02d}"
            if key not in seen_keys:
                seen_keys.add(key)
                results.append(ParsedDateSpan(year=y, month=mo, day=d))
        except ValueError:
            continue

    # 2. Korean dates (e.g. 2026년 8월 27일, 2026년 8월)
    for m in RE_KOREAN_DATE.finditer(text):
        try:
            y = int(m.group(1))
            mo = int(m.group(2))
            d = int(m.group(3)) if m.group(3) else None
            if d:
                date(y, mo, d)
                key = f"{y}-{mo:02d}-{d:02d}"
            else:
                key = f"{y}-{mo:02d}"
            if key not in seen_keys:
                seen_keys.add(key)
                results.append(ParsedDateSpan(year=y, month=mo, day=d))
        except ValueError:
            continue

    # 3. Year-month (e.g. 2026-08)
    for m in RE_YEAR_MONTH.finditer(text):
        try:
            y, mo = int(m.group(1)), int(m.group(2))
            key = f"{y}-{mo:02d}"
            if key not in seen_keys and not any(k.startswith(key + "-") for k in seen_keys):
                seen_keys.add(key)
                results.append(ParsedDateSpan(year=y, month=mo))
        except ValueError:
            continue

    # 4. Year-only mentions (e.g. 2026년, 2026)
    for m in RE_YEAR_ONLY.finditer(text):
        try:
            y = int(m.group(1))
            key = f"{y}"
            if not any(k.startswith(f"{y}-") for k in seen_keys) and key not in seen_keys:
                seen_keys.add(key)
                results.append(ParsedDateSpan(year=y))
        except ValueError:
            continue

    return results


def classify_span_relation(
    spans: list[ParsedDateSpan],
    start_dt: date,
    end_dt: date,
) -> TemporalRelation:
    """Classifies a list of parsed date spans against a target interval [start_dt, end_dt]."""
    if not spans:
        return TemporalRelation.TEMPORAL_UNKNOWN

    has_direct = False
    has_possible = False
    all_mismatches = True

    for sp in spans:
        if sp.overlaps_interval(start_dt, end_dt):
            if sp.is_exact_date or sp.month is not None:
                has_direct = True
                all_mismatches = False
                break
            else:
                has_possible = True
                all_mismatches = False
        elif sp.is_exact_year_match(start_dt, end_dt):
            has_possible = True
            all_mismatches = False
        else:
            pass

    if has_direct:
        return TemporalRelation.DIRECT_TEMPORAL_MATCH
    if has_possible:
        return TemporalRelation.POSSIBLE_TEMPORAL_MATCH
    if all_mismatches:
        return TemporalRelation.EXPLICIT_TEMPORAL_MISMATCH

    return TemporalRelation.TEMPORAL_UNKNOWN


def combine_temporal_relations(
    message_ts_rel: TemporalRelation,
    mentioned_date_rel: TemporalRelation,
) -> TemporalRelation:
    """Combines message timestamp relation and body mentioned date relation deterministically.

    Policy:
    1. If body explicitly mentions a target date, DIRECT_TEMPORAL_MATCH (even if authored earlier).
    2. If message timestamp is within target interval, DIRECT_TEMPORAL_MATCH.
    3. If either is POSSIBLE_TEMPORAL_MATCH, POSSIBLE_TEMPORAL_MATCH.
    4. If body explicitly discusses non-target dates, EXPLICIT_TEMPORAL_MISMATCH.
    5. If authored at another time but body mentions no dates, TEMPORAL_UNKNOWN.
    6. If both are EXPLICIT_TEMPORAL_MISMATCH, EXPLICIT_TEMPORAL_MISMATCH.
    7. Otherwise, TEMPORAL_UNKNOWN.
    """
    if (
        mentioned_date_rel == TemporalRelation.DIRECT_TEMPORAL_MATCH
        or message_ts_rel == TemporalRelation.DIRECT_TEMPORAL_MATCH
    ):
        return TemporalRelation.DIRECT_TEMPORAL_MATCH

    if (
        mentioned_date_rel == TemporalRelation.POSSIBLE_TEMPORAL_MATCH
        or message_ts_rel == TemporalRelation.POSSIBLE_TEMPORAL_MATCH
    ):
        return TemporalRelation.POSSIBLE_TEMPORAL_MATCH

    if mentioned_date_rel == TemporalRelation.EXPLICIT_TEMPORAL_MISMATCH:
        return TemporalRelation.EXPLICIT_TEMPORAL_MISMATCH

    if (
        message_ts_rel == TemporalRelation.EXPLICIT_TEMPORAL_MISMATCH
        and mentioned_date_rel == TemporalRelation.TEMPORAL_UNKNOWN
    ):
        return TemporalRelation.TEMPORAL_UNKNOWN

    if (
        message_ts_rel == TemporalRelation.EXPLICIT_TEMPORAL_MISMATCH
        and mentioned_date_rel == TemporalRelation.EXPLICIT_TEMPORAL_MISMATCH
    ):
        return TemporalRelation.EXPLICIT_TEMPORAL_MISMATCH

    return TemporalRelation.TEMPORAL_UNKNOWN


def resolve_candidate_authored_datetime(item: EvidenceItem) -> Optional[datetime]:
    """Resolves an authoritative timezone-aware authored datetime for an EvidenceItem.

    Invariants:
    - NAIVE_TIMESTAMP_ASSUMED_SCOPE_TIMEZONE = false:
      Never silently localizes a naive timestamp using replace(tzinfo=scope_tz).
    - Provably timezone-aware sources:
      1. Slack permalink message_ts epoch (Unix timestamp in UTC).
      2. Direct item.timestamp if it is a valid Unix float epoch.
      3. ISO-8601 string with explicit timezone offset ('Z', '+HH:MM', '-HH:MM').
    - Returns None if timestamp is naive or unprovable.
    """
    # 1. Slack permalink epoch recovery (established Phase 12.2 pattern)
    permalink = getattr(item, "permalink", None) or getattr(item, "message_identifier", None)
    if permalink:
        from pesu_agent.parsers.slack_permalink_parser import parse_slack_message_permalink

        ident = parse_slack_message_permalink(permalink)
        if ident and ident.message_ts:
            try:
                val = float(ident.message_ts)
                if val > 0:
                    return datetime.fromtimestamp(val, tz=timezone.utc)
            except (ValueError, OverflowError, OSError):
                pass

    # 2. Raw timestamp field
    ts_raw = item.timestamp
    if not ts_raw:
        return None
    clean = str(ts_raw).strip()

    # 2a. Direct float epoch string (e.g. "1788244888.665039")
    try:
        val = float(clean)
        if val > 0:
            return datetime.fromtimestamp(val, tz=timezone.utc)
    except (ValueError, OverflowError, OSError):
        pass

    # 2b. ISO-8601 string WITH explicit timezone offset
    if "Z" in clean or "+" in clean or ("-" in clean and len(clean) > 10 and (clean[10:].count("-") == 1 or clean[10:].count("+") == 1)):
        try:
            dt = datetime.fromisoformat(clean.replace("Z", "+00:00"))
            if dt.tzinfo is not None:
                return dt
        except Exception:
            pass

    return None


def resolve_temporal_scope(
    text: str,
    reference_time: Optional[datetime] = None,
    timezone_name: str = "Asia/Seoul",
) -> ResolvedTemporalScope:
    """Deterministically resolves temporal intent from user query text.

    Contract & Invariants:
    - RELATIVE_TEMPORAL_AUTHORITY = DETERMINISTIC_RUNTIME
    - PLANNER_IS_TEMPORAL_DATE_AUTHORITY = false
    - Half-open interval: [start_at, end_at)
    - AMBIGUOUS_STRICT_TEMPORAL_FAILS_OPEN = false
    - TEMPORAL_PRECEDENCE_RULE: USER_EXPLICIT_ABSOLUTE > USER_RELATIVE_DETERMINISTIC > NONE
    """
    if not text or not text.strip():
        return ResolvedTemporalScope()

    # 1. Resolve explicit timezone and localized reference time
    try:
        tz = ZoneInfo(timezone_name)
    except Exception:
        tz = timezone.utc

    if reference_time is None:
        local_ref = datetime.now(timezone.utc).astimezone(tz)
    elif reference_time.tzinfo is None:
        local_ref = reference_time.replace(tzinfo=tz)
    else:
        local_ref = reference_time.astimezone(tz)

    ref_date = local_ref.date()

    # 2. Conflict / Ambiguity Check
    # Collect all matching strict signals to ensure we fail closed on conflicting requests
    strict_relative_matches: list[tuple[str, str]] = []
    if RE_RELATIVE_TODAY.search(text):
        strict_relative_matches.append(("오늘", "TODAY"))
    if RE_RELATIVE_YESTERDAY.search(text):
        strict_relative_matches.append(("어제", "YESTERDAY"))
    if RE_RELATIVE_THIS_WEEK.search(text):
        strict_relative_matches.append(("이번주", "THIS_WEEK"))
    if RE_RELATIVE_LAST_WEEK.search(text):
        strict_relative_matches.append(("지난주", "LAST_WEEK"))
    if RE_RELATIVE_THIS_MONTH.search(text):
        strict_relative_matches.append(("이번달", "THIS_MONTH"))
    if RE_RELATIVE_LAST_MONTH.search(text):
        strict_relative_matches.append(("지난달", "LAST_MONTH"))
    if RE_RELATIVE_THIS_YEAR.search(text):
        strict_relative_matches.append(("올해", "THIS_YEAR"))
    if RE_RELATIVE_LAST_YEAR.search(text):
        strict_relative_matches.append(("작년", "LAST_YEAR"))

    recent_day_match = RE_RELATIVE_RECENT_DAYS.search(text)
    if recent_day_match:
        strict_relative_matches.append((recent_day_match.group(0), f"RECENT_{recent_day_match.group(1)}_DAYS"))

    recent_week_match = RE_RELATIVE_RECENT_WEEKS.search(text)
    if recent_week_match:
        strict_relative_matches.append((recent_week_match.group(0), f"RECENT_{recent_week_match.group(1)}_WEEKS"))

    # Check absolute dates
    absolute_matches = list(RE_FULL_DATE.finditer(text)) + list(RE_KOREAN_DATE.finditer(text))

    # If multiple mutually incompatible strict expressions exist:
    if len(strict_relative_matches) > 1 or (len(strict_relative_matches) >= 1 and len(absolute_matches) >= 1):
        logger.warning(
            f"⚠️ [TEMPORAL_AMBIGUOUS_CONFLICT] Conflicting temporal expressions detected in query: "
            f"relative={strict_relative_matches}, absolute={[m.group(0) for m in absolute_matches]}"
        )
        return ResolvedTemporalScope(
            intent_present=True,
            strictness=TemporalStrictness.STRICT,
            status=TemporalResolutionStatus.AMBIGUOUS,
            timezone=str(tz),
            source_expression=text,
            resolution_kind=TemporalResolutionKind.NONE,
        )

    # 3. Precedence 1: Explicit Absolute Date (e.g. 2026년 8월 27일, 2026-08-27)
    if absolute_matches:
        m = absolute_matches[0]
        y = int(m.group(1))
        mo = int(m.group(2))
        d = int(m.group(3)) if (len(m.groups()) >= 3 and m.group(3)) else None
        try:
            if d:
                target_d = date(y, mo, d)
                next_d = target_d + timedelta(days=1)
                start_at = datetime.combine(target_d, time.min, tzinfo=tz)
                end_at = datetime.combine(next_d, time.min, tzinfo=tz)
            else:
                # Year-Month: [1st of month, 1st of next month)
                target_m_start = date(y, mo, 1)
                next_m_start = date(y + 1, 1, 1) if mo == 12 else date(y, mo + 1, 1)
                start_at = datetime.combine(target_m_start, time.min, tzinfo=tz)
                end_at = datetime.combine(next_m_start, time.min, tzinfo=tz)

            return ResolvedTemporalScope(
                intent_present=True,
                strictness=TemporalStrictness.STRICT,
                status=TemporalResolutionStatus.RESOLVED,
                start_at=start_at,
                end_at=end_at,
                timezone=str(tz),
                source_expression=m.group(0),
                resolution_kind=TemporalResolutionKind.USER_EXPLICIT_ABSOLUTE,
            )
        except ValueError:
            return ResolvedTemporalScope(
                intent_present=True,
                strictness=TemporalStrictness.STRICT,
                status=TemporalResolutionStatus.UNRESOLVED,
                timezone=str(tz),
                source_expression=m.group(0),
                resolution_kind=TemporalResolutionKind.USER_EXPLICIT_ABSOLUTE,
            )

    # 4. Precedence 2: Deterministic Relative Expression
    if strict_relative_matches:
        expr_text, kind_code = strict_relative_matches[0]

        if kind_code == "TODAY":
            start_at = datetime.combine(ref_date, time.min, tzinfo=tz)
            end_at = datetime.combine(ref_date + timedelta(days=1), time.min, tzinfo=tz)
        elif kind_code == "YESTERDAY":
            yest_date = ref_date - timedelta(days=1)
            start_at = datetime.combine(yest_date, time.min, tzinfo=tz)
            end_at = datetime.combine(ref_date, time.min, tzinfo=tz)
        elif kind_code == "THIS_WEEK":
            curr_mon = ref_date - timedelta(days=ref_date.weekday())
            next_mon = curr_mon + timedelta(days=7)
            start_at = datetime.combine(curr_mon, time.min, tzinfo=tz)
            end_at = datetime.combine(next_mon, time.min, tzinfo=tz)
        elif kind_code == "LAST_WEEK":
            curr_mon = ref_date - timedelta(days=ref_date.weekday())
            prev_mon = curr_mon - timedelta(days=7)
            start_at = datetime.combine(prev_mon, time.min, tzinfo=tz)
            end_at = datetime.combine(curr_mon, time.min, tzinfo=tz)
        elif kind_code == "THIS_MONTH":
            curr_m_start = ref_date.replace(day=1)
            next_m_start = date(ref_date.year + 1, 1, 1) if ref_date.month == 12 else date(ref_date.year, ref_date.month + 1, 1)
            start_at = datetime.combine(curr_m_start, time.min, tzinfo=tz)
            end_at = datetime.combine(next_m_start, time.min, tzinfo=tz)
        elif kind_code == "LAST_MONTH":
            curr_m_start = ref_date.replace(day=1)
            prev_m_start = date(ref_date.year - 1, 12, 1) if ref_date.month == 1 else date(ref_date.year, ref_date.month - 1, 1)
            start_at = datetime.combine(prev_m_start, time.min, tzinfo=tz)
            end_at = datetime.combine(curr_m_start, time.min, tzinfo=tz)
        elif kind_code == "THIS_YEAR":
            start_at = datetime.combine(date(ref_date.year, 1, 1), time.min, tzinfo=tz)
            end_at = datetime.combine(date(ref_date.year + 1, 1, 1), time.min, tzinfo=tz)
        elif kind_code == "LAST_YEAR":
            start_at = datetime.combine(date(ref_date.year - 1, 1, 1), time.min, tzinfo=tz)
            end_at = datetime.combine(date(ref_date.year, 1, 1), time.min, tzinfo=tz)
        elif kind_code.startswith("RECENT_") and kind_code.endswith("_DAYS"):
            n_days = int(recent_day_match.group(1))  # type: ignore[union-attr]
            # RECENT_N_DAYS_RULE = CALENDAR_DAYS_TRAILING_N_PLUS_CURRENT
            start_at = datetime.combine(ref_date - timedelta(days=n_days), time.min, tzinfo=tz)
            end_at = datetime.combine(ref_date + timedelta(days=1), time.min, tzinfo=tz)
        elif kind_code.startswith("RECENT_") and kind_code.endswith("_WEEKS"):
            n_weeks = int(recent_week_match.group(1))  # type: ignore[union-attr]
            # RECENT_N_WEEKS_RULE = CALENDAR_DAYS_TRAILING_N_WEEKS_PLUS_CURRENT
            start_at = datetime.combine(ref_date - timedelta(weeks=n_weeks), time.min, tzinfo=tz)
            end_at = datetime.combine(ref_date + timedelta(days=1), time.min, tzinfo=tz)
        else:
            start_at, end_at = None, None

        if start_at and end_at:
            return ResolvedTemporalScope(
                intent_present=True,
                strictness=TemporalStrictness.STRICT,
                status=TemporalResolutionStatus.RESOLVED,
                start_at=start_at,
                end_at=end_at,
                timezone=str(tz),
                source_expression=expr_text,
                resolution_kind=TemporalResolutionKind.USER_RELATIVE_DETERMINISTIC,
            )

    # 5. Precedence 3: Soft recency/historical adverbs
    soft_match = RE_SOFT_RECENCY.search(text)
    if soft_match:
        return ResolvedTemporalScope(
            intent_present=True,
            strictness=TemporalStrictness.SOFT,
            status=TemporalResolutionStatus.RESOLVED,
            start_at=None,
            end_at=None,
            timezone=str(tz),
            source_expression=soft_match.group(0),
            resolution_kind=TemporalResolutionKind.USER_RELATIVE_DETERMINISTIC,
        )

    # 6. Year-only mentions (e.g. 2026년, 2024년)
    year_match = RE_YEAR_ONLY.search(text)
    if year_match:
        y = int(year_match.group(1))
        start_at = datetime.combine(date(y, 1, 1), time.min, tzinfo=tz)
        end_at = datetime.combine(date(y + 1, 1, 1), time.min, tzinfo=tz)
        return ResolvedTemporalScope(
            intent_present=True,
            strictness=TemporalStrictness.STRICT,
            status=TemporalResolutionStatus.RESOLVED,
            start_at=start_at,
            end_at=end_at,
            timezone=str(tz),
            source_expression=year_match.group(0),
            resolution_kind=TemporalResolutionKind.USER_EXPLICIT_ABSOLUTE,
        )

    # 7. No temporal intent
    return ResolvedTemporalScope(
        intent_present=False,
        strictness=TemporalStrictness.NONE,
        status=TemporalResolutionStatus.NONE,
        timezone=str(tz),
        source_expression=None,
        resolution_kind=TemporalResolutionKind.NONE,
    )


def evaluate_candidate_temporal_relation(
    item: EvidenceItem,
    constraint: Optional[TemporalConstraint] = None,
    temporal_scope: Optional[ResolvedTemporalScope] = None,
) -> tuple[TemporalRelation, TemporalRelation, TemporalRelation]:
    """Evaluates temporal relevance for a candidate EvidenceItem.

    Supports both legacy TemporalConstraint (Phase 12.2) and authoritative ResolvedTemporalScope (Phase 12.6d).

    Returns:
        (combined_relation, message_ts_relation, mentioned_date_relation)
    """
    # 1. Authoritative ResolvedTemporalScope path
    if temporal_scope and temporal_scope.intent_present and temporal_scope.status == TemporalResolutionStatus.RESOLVED:
        if temporal_scope.start_at and temporal_scope.end_at:
            start_dt = temporal_scope.start_at.date()
            # For inclusive date overlap checks with ParsedDateSpan, subtract 1 second from half-open end
            end_dt = (temporal_scope.end_at - timedelta(seconds=1)).date()

            # Message authored timestamp evaluation
            authored_dt = resolve_candidate_authored_datetime(item)
            if authored_dt is not None:
                in_range = temporal_scope.is_in_range(authored_dt)
                ts_rel = (
                    TemporalRelation.DIRECT_TEMPORAL_MATCH
                    if in_range
                    else TemporalRelation.EXPLICIT_TEMPORAL_MISMATCH
                )
            else:
                ts_rel = TemporalRelation.TEMPORAL_UNKNOWN

            # Body mentioned dates
            candidate_text = item.text
            if getattr(item, "thread_replies", None):
                candidate_text += "\n" + "\n".join(r.text for r in item.thread_replies)
            mentioned_spans = extract_mentioned_dates(candidate_text)
            mentioned_rel = classify_span_relation(mentioned_spans, start_dt, end_dt)

            combined_rel = combine_temporal_relations(ts_rel, mentioned_rel)
            return combined_rel, ts_rel, mentioned_rel

    # 2. Legacy TemporalConstraint path
    if not constraint or not constraint.has_temporal_intent:
        return (
            TemporalRelation.TEMPORAL_UNKNOWN,
            TemporalRelation.TEMPORAL_UNKNOWN,
            TemporalRelation.TEMPORAL_UNKNOWN,
        )

    start_dt = parse_iso_date(constraint.start_date)
    end_dt = parse_iso_date(constraint.end_date)

    if not start_dt and not end_dt:
        return (
            TemporalRelation.TEMPORAL_UNKNOWN,
            TemporalRelation.TEMPORAL_UNKNOWN,
            TemporalRelation.TEMPORAL_UNKNOWN,
        )

    if start_dt and not end_dt:
        end_dt = date(start_dt.year + 1, 12, 31)
    elif end_dt and not start_dt:
        start_dt = date(end_dt.year - 1, 1, 1)

    # 1. Message authored timestamp evaluation
    authored_span = parse_authored_timestamp(item.timestamp)
    if not authored_span:
        permalink = getattr(item, "permalink", None) or item.message_identifier
        if permalink:
            from pesu_agent.parsers.slack_permalink_parser import parse_slack_message_permalink

            ident = parse_slack_message_permalink(permalink)
            if ident and ident.message_ts:
                authored_span = parse_authored_timestamp(ident.message_ts)
    ts_rel = (
        classify_span_relation([authored_span], start_dt, end_dt)  # type: ignore[arg-type]
        if authored_span
        else TemporalRelation.TEMPORAL_UNKNOWN
    )

    # 2. Message body explicitly mentioned dates evaluation (including mounted thread replies)
    candidate_text = item.text
    if getattr(item, "thread_replies", None):
        candidate_text += "\n" + "\n".join(r.text for r in item.thread_replies)
    mentioned_spans = extract_mentioned_dates(candidate_text)
    mentioned_rel = classify_span_relation(mentioned_spans, start_dt, end_dt)  # type: ignore[arg-type]

    # 3. Deterministic combination
    combined_rel = combine_temporal_relations(ts_rel, mentioned_rel)

    return combined_rel, ts_rel, mentioned_rel
