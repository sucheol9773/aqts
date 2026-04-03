"""
AI 정성적 분석 모듈 (Phase 3 + Stage 7)

Mode A: 감성 분석 (SentimentAnalyzer)
  - Claude Haiku 4.5 기반 뉴스/공시 감성 점수 산출
  - Redis 캐시 TTL: 1시간

Mode B: 투자 의견 (OpinionGenerator)
  - Claude Sonnet 4 기반 거시경제 분석 + 투자 판단
  - STOCK / SECTOR / MACRO 3가지 유형 지원
  - Redis 캐시 TTL: 4시간

프롬프트 버전 관리 (PromptManager)
  - MongoDB 기반 프롬프트 템플릿 CRUD + 버전 이력
  - A/B 테스트 메트릭 연결

Stage 7: LLM Production Promotion
  - DriftMonitor: KS-test 기반 드리프트 감지
  - CostAnalyzer: API 비용-편익 분석
  - ReproducibilityTest: 재현성 검증
  - PromotionChecklist: 프로덕션 승격 기준 평가
"""

from core.ai_analyzer.sentiment import SentimentAnalyzer, SentimentResult
from core.ai_analyzer.opinion import OpinionGenerator, InvestmentOpinion
from core.ai_analyzer.prompt_manager import PromptManager, PromptVersion
from core.ai_analyzer.drift_monitor import DriftMonitor
from core.ai_analyzer.cost_analyzer import CostAnalyzer
from core.ai_analyzer.reproducibility import ReproducibilityTest
from core.ai_analyzer.promotion_checklist import PromotionChecklist, PromotionDecision

__all__ = [
    "SentimentAnalyzer",
    "SentimentResult",
    "OpinionGenerator",
    "InvestmentOpinion",
    "PromptManager",
    "PromptVersion",
    "DriftMonitor",
    "CostAnalyzer",
    "ReproducibilityTest",
    "PromotionChecklist",
    "PromotionDecision",
]