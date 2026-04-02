"""
AI 정성적 분석 모듈 (Phase 3)

Mode A: 감성 분석 (SentimentAnalyzer)
  - Claude Haiku 4.5 기반 뉴스/공시 감성 점수 산출
  - Redis 캐시 TTL: 1시간

Mode B: 투자 의견 (OpinionGenerator)
  - Claude Sonnet 4 기반 거시경제 분석 + 투자 판단
  - Redis 캐시 TTL: 4시간
"""

from core.ai_analyzer.sentiment import SentimentAnalyzer, SentimentResult
from core.ai_analyzer.opinion import OpinionGenerator, InvestmentOpinion

__all__ = [
    "SentimentAnalyzer",
    "SentimentResult",
    "OpinionGenerator",
    "InvestmentOpinion",
]