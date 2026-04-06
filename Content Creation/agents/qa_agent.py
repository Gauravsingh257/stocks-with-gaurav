"""
Content Creation / agents / qa_agent.py

QAAgent -- validates carousel content before publishing.

12 checks across 3 categories:

  STRUCTURAL (layout/format):
    1. Slide count (4-12)
    2. Text lengths (headline <=60, body <=300)
    3. Brand compliance (disclaimer, handle)
    4. Caption + hashtag limits
    5. Image files exist
    6. No duplicate headlines
    7. Cover slide present

  FINANCIAL ACCURACY (no fake news / hallucination):
    8. Data consistency -- numbers in slides must match MarketData
    9. No fabricated percentages outside plausible range
   10. Stock pick validation -- symbols traceable to source data

  CONTENT QUALITY (clear language):
   11. No vague filler phrases ("experts say", "sources confirm")
   12. Readability -- short sentences, no jargon walls
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from agents.base import BaseContentAgent
from models.contracts import (
    CarouselContent,
    DesignOutput,
    MarketData,
    QAIssue,
    QAResult,
    SlideType,
    StockPicks,
)

log = logging.getLogger("content_creation.agents.qa")

_MAX_CAPTION_LENGTH = 2200
_MIN_SLIDES = 4
_MAX_SLIDES = 12
_MIN_HASHTAGS = 5
_MAX_HASHTAGS = 15
_MAX_HEADLINE_LENGTH = 60
_MAX_BODY_LENGTH = 300
_MAX_CHANGE_PCT = 25.0  # anything beyond +-25% is suspicious for a single day

# Phrases that indicate unsourced / vague claims
_FILLER_PHRASES = [
    "experts say", "sources confirm", "insiders reveal", "rumor has it",
    "guaranteed", "100% returns", "sure shot", "never fail",
    "secret strategy", "whales are buying", "you won't believe",
]


class QAAgent(BaseContentAgent):
    name = "QAAgent"
    description = "Validates carousel for accuracy, clarity, and compliance"

    def run(
        self,
        *,
        carousel: CarouselContent,
        design: DesignOutput,
        market_data: MarketData | None = None,
        picks: StockPicks | None = None,
    ) -> QAResult:
        issues: list[QAIssue] = []
        checks: list[str] = []
        score = 100.0

        # ══════════════════════════════════════════════════════════════════
        #  STRUCTURAL CHECKS
        # ══════════════════════════════════════════════════════════════════

        # ── 1. Slide count ────────────────────────────────────────────────
        checks.append("slide_count")
        if carousel.total_slides < _MIN_SLIDES:
            issues.append(QAIssue(
                severity="error",
                issue=f"Too few slides: {carousel.total_slides} (min {_MIN_SLIDES})",
                suggestion="Add more content slides",
            ))
            score -= 20
        elif carousel.total_slides > _MAX_SLIDES:
            issues.append(QAIssue(
                severity="warning",
                issue=f"Too many slides: {carousel.total_slides} (max {_MAX_SLIDES})",
                suggestion="Remove less important slides",
            ))
            score -= 10

        # ── 2. Text lengths ───────────────────────────────────────────────
        checks.append("text_length")
        for slide in carousel.slides:
            if len(slide.headline) > _MAX_HEADLINE_LENGTH:
                issues.append(QAIssue(
                    slide_number=slide.slide_number,
                    severity="warning",
                    issue=f"Headline too long: {len(slide.headline)} chars",
                    suggestion=f"Shorten to under {_MAX_HEADLINE_LENGTH} chars",
                ))
                score -= 5
            if slide.body and len(slide.body) > _MAX_BODY_LENGTH:
                issues.append(QAIssue(
                    slide_number=slide.slide_number,
                    severity="info",
                    issue=f"Body text long: {len(slide.body)} chars",
                    suggestion="Consider shortening for readability",
                ))
                score -= 2

        # ── 3. Brand compliance ───────────────────────────────────────────
        checks.append("brand_compliance")
        has_disclaimer = any(
            slide.slide_type in (SlideType.CTA, SlideType.DISCLAIMER, SlideType.INSIGHT_CTA)
            or (slide.footer and "SEBI" in slide.footer.upper())
            for slide in carousel.slides
        )
        if not has_disclaimer:
            issues.append(QAIssue(
                severity="error",
                issue="Missing disclaimer/CTA slide",
                suggestion="Add a CTA slide with SEBI disclaimer",
            ))
            score -= 15

        if "@StocksWithGaurav" not in carousel.caption:
            issues.append(QAIssue(
                severity="warning",
                issue="Brand handle missing from caption",
                suggestion="Add @StocksWithGaurav to caption",
            ))
            score -= 5

        # ── 4. Caption + hashtag limits ───────────────────────────────────
        checks.append("caption_length")
        full_caption = carousel.caption + " " + " ".join(carousel.hashtags)
        if len(full_caption) > _MAX_CAPTION_LENGTH:
            issues.append(QAIssue(
                severity="warning",
                issue=f"Caption too long: {len(full_caption)} chars (max {_MAX_CAPTION_LENGTH})",
                suggestion="Shorten caption or reduce hashtags",
            ))
            score -= 10

        checks.append("hashtag_count")
        n_hashtags = len(carousel.hashtags)
        if n_hashtags < _MIN_HASHTAGS:
            issues.append(QAIssue(
                severity="info",
                issue=f"Low hashtag count: {n_hashtags}",
                suggestion=f"Add more (optimal {_MIN_HASHTAGS}-{_MAX_HASHTAGS})",
            ))
            score -= 3
        elif n_hashtags > _MAX_HASHTAGS:
            issues.append(QAIssue(
                severity="info",
                issue=f"Too many hashtags: {n_hashtags}",
                suggestion=f"Trim to {_MAX_HASHTAGS}",
            ))
            score -= 3

        # ── 5. Image files exist ──────────────────────────────────────────
        checks.append("image_files")
        for path_str in design.slide_paths:
            p = Path(path_str)
            if not p.exists():
                issues.append(QAIssue(
                    severity="error",
                    issue=f"Missing image: {p.name}",
                    suggestion="Re-run DesignAgent",
                ))
                score -= 15

        # ── 6. Duplicate headlines ────────────────────────────────────────
        checks.append("no_duplicates")
        headlines = [s.headline for s in carousel.slides]
        if len(headlines) != len(set(headlines)):
            issues.append(QAIssue(
                severity="warning",
                issue="Duplicate slide headlines detected",
                suggestion="Ensure each slide has a unique headline",
            ))
            score -= 5

        # ── 7. Cover slide present ────────────────────────────────────────
        checks.append("cover_slide")
        has_cover = any(s.slide_type == SlideType.COVER for s in carousel.slides)
        if not has_cover:
            issues.append(QAIssue(
                severity="error",
                issue="Missing cover slide",
                suggestion="Add a cover slide as slide 1",
            ))
            score -= 10

        # ══════════════════════════════════════════════════════════════════
        #  FINANCIAL ACCURACY CHECKS
        # ══════════════════════════════════════════════════════════════════

        # ── 8. Data consistency (slide numbers vs MarketData) ─────────────
        checks.append("data_consistency")
        if market_data:
            score = self._check_data_consistency(
                carousel, market_data, issues, score
            )

        # ── 9. Plausible percentages ──────────────────────────────────────
        checks.append("plausible_percentages")
        score = self._check_plausible_numbers(carousel, issues, score)

        # ── 10. Stock pick traceability ───────────────────────────────────
        checks.append("stock_traceability")
        if picks:
            score = self._check_stock_traceability(
                carousel, picks, issues, score
            )

        # ══════════════════════════════════════════════════════════════════
        #  CONTENT QUALITY CHECKS
        # ══════════════════════════════════════════════════════════════════

        # ── 11. No filler / unsubstantiated claims ────────────────────────
        checks.append("no_filler_phrases")
        score = self._check_filler_phrases(carousel, issues, score)

        # ── 12. Readability ───────────────────────────────────────────────
        checks.append("readability")
        score = self._check_readability(carousel, issues, score)

        # ══════════════════════════════════════════════════════════════════
        #  VERDICT
        # ══════════════════════════════════════════════════════════════════
        has_errors = any(i.severity == "error" for i in issues)
        passed = not has_errors and score >= 60

        result = QAResult(
            passed=passed,
            score=max(0.0, round(score, 1)),
            issues=issues,
            checks_performed=checks,
        )

        log.info(
            "QA result: passed=%s score=%.1f issues=%d checks=%d",
            passed, score, len(issues), len(checks),
        )
        for iss in issues:
            log.info("  QA issue: [%s] %s — %s", iss.severity, iss.issue, iss.suggestion)
        return result

    # ──────────────────────────────────────────────────────────────────────
    #  Financial accuracy helpers
    # ──────────────────────────────────────────────────────────────────────

    def _check_data_consistency(
        self,
        carousel: CarouselContent,
        market_data: MarketData,
        issues: list[QAIssue],
        score: float,
    ) -> float:
        """Verify numbers shown in slides trace back to MarketData."""
        # Build lookup of known values from market_data
        known_values: dict[str, float] = {}
        for idx in market_data.indices:
            known_values[idx.name.upper()] = idx.change_pct
        for sec in market_data.sectors:
            known_values[sec.name.upper()] = sec.change_pct

        # Scan slide text for percentage claims and cross-check
        pct_pattern = re.compile(r"([+-]?\d+\.?\d*)\s*%")
        mismatches = 0
        for slide in carousel.slides:
            all_text = f"{slide.headline} {slide.body}"
            # Also check sections
            for sec in slide.sections:
                val_text = sec.value
                all_text += f" {sec.label} {val_text}"
                # If section label matches a known index/sector, verify
                # Use word-boundary-style matching to avoid "NIFTY" matching "BANKNIFTY"
                label_upper = sec.label.upper().strip()
                for known_name, known_pct in known_values.items():
                    # Require full-word match: label must equal known name,
                    # or contain it as a whole token (not as a substring of a longer word)
                    label_words = set(re.split(r"[\s\-_/]+", label_upper))
                    known_words = set(re.split(r"[\s\-_/]+", known_name))
                    if not (label_words == known_words or label_upper == known_name):
                        continue
                    # Extract percentage from value
                    m = pct_pattern.search(val_text)
                    if m:
                        slide_pct = float(m.group(1))
                        # Compare absolute values — sign context varies in text
                        if abs(abs(slide_pct) - abs(known_pct)) > 0.5:
                            issues.append(QAIssue(
                                slide_number=slide.slide_number,
                                severity="error",
                                issue=(
                                    f"Data mismatch: {sec.label} shows "
                                    f"{slide_pct}% but source is {known_pct:.2f}%"
                                ),
                                suggestion="Fix to match actual market data",
                            ))
                            mismatches += 1
                            score -= 10

        if mismatches == 0 and known_values:
            log.info("Data consistency: all slide numbers match source")
        return score

    def _check_plausible_numbers(
        self,
        carousel: CarouselContent,
        issues: list[QAIssue],
        score: float,
    ) -> float:
        """Flag suspiciously large single-day percentage moves."""
        pct_pattern = re.compile(r"([+-]?\d+\.?\d*)\s*%")
        for slide in carousel.slides:
            full_text = f"{slide.headline} {slide.body}"
            for sec in slide.sections:
                full_text += f" {sec.value}"
            for match in pct_pattern.finditer(full_text):
                val = abs(float(match.group(1)))
                if val > _MAX_CHANGE_PCT:
                    issues.append(QAIssue(
                        slide_number=slide.slide_number,
                        severity="warning",
                        issue=f"Unusually large move: {match.group(0)} (>{_MAX_CHANGE_PCT}%)",
                        suggestion="Verify this is not a hallucinated number",
                    ))
                    score -= 5
        return score

    def _check_stock_traceability(
        self,
        carousel: CarouselContent,
        picks: StockPicks,
        issues: list[QAIssue],
        score: float,
    ) -> float:
        """Ensure stock cards in slides match the actual StockPicks output."""
        pick_symbols = {p.symbol.upper() for p in picks.picks}
        stock_slides = [
            s for s in carousel.slides if s.slide_type == SlideType.STOCK_CARD
        ]
        for slide in stock_slides:
            # Try to find the symbol in headline or sections
            slide_text = f"{slide.headline} {slide.subheadline}".upper()
            for sec in slide.sections:
                slide_text += f" {sec.label} {sec.value}".upper()
            found = any(sym in slide_text for sym in pick_symbols)
            if not found:
                issues.append(QAIssue(
                    slide_number=slide.slide_number,
                    severity="error",
                    issue="Stock card does not match any picked symbol",
                    suggestion="Only show stocks from StockPickerAgent output",
                ))
                score -= 10
        return score

    def _check_filler_phrases(
        self,
        carousel: CarouselContent,
        issues: list[QAIssue],
        score: float,
    ) -> float:
        """Detect vague / unsubstantiated filler language."""
        all_text = carousel.caption.lower()
        for slide in carousel.slides:
            all_text += f" {slide.headline} {slide.body}".lower()
            for sec in slide.sections:
                all_text += f" {sec.value}".lower()

        found_fillers = [p for p in _FILLER_PHRASES if p in all_text]
        for filler in found_fillers:
            issues.append(QAIssue(
                severity="warning",
                issue=f"Filler/unsubstantiated phrase: '{filler}'",
                suggestion="Replace with data-backed statement",
            ))
            score -= 3
        return score

    def _check_readability(
        self,
        carousel: CarouselContent,
        issues: list[QAIssue],
        score: float,
    ) -> float:
        """Check for overly long sentences or jargon walls."""
        for slide in carousel.slides:
            # Headlines should be crisp
            word_count = len(slide.headline.split())
            if word_count > 10:
                issues.append(QAIssue(
                    slide_number=slide.slide_number,
                    severity="info",
                    issue=f"Headline has {word_count} words (target <=6)",
                    suggestion="Make it punchier",
                ))
                score -= 2

            # Body sentences should be short
            if slide.body:
                sentences = re.split(r"[.!?]+", slide.body)
                for sent in sentences:
                    words = sent.split()
                    if len(words) > 25:
                        issues.append(QAIssue(
                            slide_number=slide.slide_number,
                            severity="info",
                            issue=f"Long sentence ({len(words)} words)",
                            suggestion="Break into shorter sentences",
                        ))
                        score -= 1
                        break  # one flag per slide is enough
        return score
