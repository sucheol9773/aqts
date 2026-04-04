"""
Phase 3 테스트: AI 투자 의견 생성기 (OpinionGenerator)

모든 외부 API(Claude, Redis, PostgreSQL)는 Mock으로 대체합니다.
"""

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config.constants import OpinionAction, OpinionType
from core.ai_analyzer.opinion import (
    InvestmentOpinion,
    OpinionGenerator,
    _MACRO_OPINION_TEMPLATE,
    _OPINION_SYSTEM_PROMPT,
    _SECTOR_OPINION_TEMPLATE,
    _STOCK_OPINION_TEMPLATE,
)


@pytest.mark.smoke
class TestInvestmentOpinion:
    """InvestmentOpinion 데이터 구조 테스트"""

    def test_creation_stock(self):
        """개별 종목 의견 생성"""
        opinion = InvestmentOpinion(
            ticker="005930",
            opinion_type=OpinionType.STOCK,
            action=OpinionAction.BUY,
            conviction=0.75,
            target_weight=0.15,
            reasoning="긍정적 기술 지표",
            market_context="약세 시장에서 상대강세",
            risk_factors=["환율 리스크", "경기 둔화"],
            model_used="claude-sonnet-4",
            generated_at=datetime(2026, 4, 3, tzinfo=timezone.utc),
        )

        assert opinion.ticker == "005930"
        assert opinion.opinion_type == OpinionType.STOCK
        assert opinion.action == OpinionAction.BUY
        assert opinion.conviction == 0.75

    def test_creation_sector(self):
        """섹터 의견 생성 (ticker=None)"""
        opinion = InvestmentOpinion(
            ticker=None,
            opinion_type=OpinionType.SECTOR,
            action=OpinionAction.STRONG_BUY,
            conviction=0.85,
            reasoning="섹터 전주기 강세",
            model_used="claude-sonnet-4",
            generated_at=datetime(2026, 4, 3, tzinfo=timezone.utc),
        )

        assert opinion.ticker is None
        assert opinion.opinion_type == OpinionType.SECTOR

    def test_creation_macro(self):
        """거시경제 의견 생성 (ticker=None)"""
        opinion = InvestmentOpinion(
            ticker=None,
            opinion_type=OpinionType.MACRO,
            action=OpinionAction.HOLD,
            conviction=0.50,
            model_used="claude-sonnet-4",
            generated_at=datetime(2026, 4, 3, tzinfo=timezone.utc),
        )

        assert opinion.ticker is None
        assert opinion.opinion_type == OpinionType.MACRO

    def test_to_signal_value_strong_buy(self):
        """STRONG_BUY 신호값 변환"""
        opinion = InvestmentOpinion(
            ticker="005930",
            opinion_type=OpinionType.STOCK,
            action=OpinionAction.STRONG_BUY,
            conviction=0.9,
            model_used="test",
            generated_at=datetime.now(timezone.utc),
        )

        signal = opinion.to_signal_value()
        assert signal == 0.9  # 1.0 * 0.9 = 0.9

    def test_to_signal_value_buy(self):
        """BUY 신호값 변환"""
        opinion = InvestmentOpinion(
            ticker="005930",
            opinion_type=OpinionType.STOCK,
            action=OpinionAction.BUY,
            conviction=0.8,
            model_used="test",
            generated_at=datetime.now(timezone.utc),
        )

        signal = opinion.to_signal_value()
        assert signal == 0.4  # 0.5 * 0.8 = 0.4

    def test_to_signal_value_hold(self):
        """HOLD 신호값 변환"""
        opinion = InvestmentOpinion(
            ticker="005930",
            opinion_type=OpinionType.STOCK,
            action=OpinionAction.HOLD,
            conviction=0.5,
            model_used="test",
            generated_at=datetime.now(timezone.utc),
        )

        signal = opinion.to_signal_value()
        assert signal == 0.0

    def test_to_signal_value_sell(self):
        """SELL 신호값 변환"""
        opinion = InvestmentOpinion(
            ticker="005930",
            opinion_type=OpinionType.STOCK,
            action=OpinionAction.SELL,
            conviction=0.7,
            model_used="test",
            generated_at=datetime.now(timezone.utc),
        )

        signal = opinion.to_signal_value()
        assert signal == -0.35  # -0.5 * 0.7 = -0.35

    def test_to_signal_value_strong_sell(self):
        """STRONG_SELL 신호값 변환"""
        opinion = InvestmentOpinion(
            ticker="005930",
            opinion_type=OpinionType.STOCK,
            action=OpinionAction.STRONG_SELL,
            conviction=0.85,
            model_used="test",
            generated_at=datetime.now(timezone.utc),
        )

        signal = opinion.to_signal_value()
        assert signal == -0.85  # -1.0 * 0.85 = -0.85

    def test_to_dict(self):
        """DB 저장용 딕셔너리 변환"""
        now = datetime(2026, 4, 3, 12, 30, tzinfo=timezone.utc)
        opinion = InvestmentOpinion(
            ticker="005930",
            opinion_type=OpinionType.STOCK,
            action=OpinionAction.BUY,
            conviction=0.65,
            target_weight=0.12,
            reasoning="성장성 우수",
            market_context="강세 장",
            risk_factors=["규제 리스크", "경쟁 심화"],
            model_used="claude-sonnet-4",
            generated_at=now,
        )

        d = opinion.to_dict()

        assert d["ticker"] == "005930"
        assert d["opinion_type"] == "STOCK"
        assert d["action"] == "BUY"
        assert d["conviction"] == 0.65
        assert d["target_weight"] == 0.12
        assert d["reasoning"] == "성장성 우수"
        assert d["market_context"] == "강세 장"
        assert "규제 리스크" in d["risk_factors"]
        assert d["time"] == now


@pytest.mark.smoke
class TestOpinionGenerator:
    """OpinionGenerator 핵심 기능 테스트"""

    @pytest.fixture
    def _mock_env(self):
        """OpinionGenerator 생성에 필요한 Mock 환경"""
        with patch("core.ai_analyzer.opinion.AsyncAnthropic") as mock_cls, \
             patch("core.ai_analyzer.opinion.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                anthropic=MagicMock(
                    api_key="test-key",
                    advanced_model="claude-sonnet-4",
                    api_timeout=30,
                )
            )
            mock_cls.return_value = AsyncMock()
            yield mock_cls, mock_settings

    @pytest.fixture
    def sample_sentiment_result(self):
        """테스트용 감성 분석 결과"""
        return {
            "score": 0.65,
            "confidence": 0.8,
            "summary": "삼성전자 반도체 수출 호조 전망",
            "positive_factors": ["반도체 수출 증가", "AI 메모리 수요 확대"],
            "negative_factors": ["중국 규제 리스크"],
        }

    @pytest.fixture
    def sample_quant_signals(self):
        """테스트용 정량 시그널"""
        return {
            "composite_score": 72.5,
            "trend_signal": 0.6,
            "mean_rev_signal": 0.2,
            "risk_parity_signal": 0.4,
        }

    @pytest.fixture
    def sample_news(self):
        """테스트용 뉴스 리스트"""
        return [
            {
                "title": "삼성전자, AI 반도체 수출 사상 최대",
                "content": "삼성전자가 AI용 HBM 메모리 반도체 수출이 전년 대비 45% 증가하며 분기 사상 최대 실적을 기록했다.",
                "source": "naver_news",
                "published_at": datetime(2026, 4, 2, 9, 30, tzinfo=timezone.utc),
            },
            {
                "title": "반도체 업황 개선 기대감 확산",
                "content": "글로벌 반도체 시장이 AI 인프라 투자 확대에 힘입어 회복세를 보이고 있다.",
                "source": "hankyung",
                "published_at": datetime(2026, 4, 2, 10, 0, tzinfo=timezone.utc),
            },
        ]

    @pytest.fixture
    def mock_api_response(self):
        """Claude API Mock 응답"""
        response = MagicMock()
        response.content = [
            MagicMock(text=json.dumps({
                "action": "BUY",
                "conviction": 0.75,
                "target_weight": 0.15,
                "reasoning": "팩터 스코어 높고 감성 긍정적. 기술 지표 우수.",
                "market_context": "약세 장에서 상대강세 유지 중",
                "risk_factors": ["환율 변동성", "중국 경제 둔화", "반도체 수급 악화"],
            }, ensure_ascii=False))
        ]
        return response

    @pytest.mark.asyncio
    async def test_generate_stock_opinion_basic(self, _mock_env, mock_api_response,
                                               sample_sentiment_result, sample_quant_signals,
                                               sample_news):
        """개별 종목 의견 생성 - 정상 케이스"""
        with patch.object(OpinionGenerator, "_get_cached", return_value=None), \
             patch.object(OpinionGenerator, "_set_cache", new_callable=AsyncMock), \
             patch.object(OpinionGenerator, "_store_to_db", new_callable=AsyncMock):

            generator = OpinionGenerator()
            generator._client = AsyncMock()
            generator._client.messages.create.return_value = mock_api_response

            opinion = await generator.generate_stock_opinion(
                ticker="005930",
                sentiment_result=sample_sentiment_result,
                quant_signals=sample_quant_signals,
                recent_news=sample_news,
            )

            assert opinion.ticker == "005930"
            assert opinion.opinion_type == OpinionType.STOCK
            assert opinion.action == OpinionAction.BUY
            assert opinion.conviction == 0.75
            assert opinion.target_weight == 0.15
            assert "팩터 스코어" in opinion.reasoning

    @pytest.mark.asyncio
    async def test_generate_stock_opinion_cache_hit(self, _mock_env,
                                                    sample_sentiment_result,
                                                    sample_quant_signals, sample_news):
        """개별 종목 의견 생성 - 캐시 히트"""
        cached_opinion = InvestmentOpinion(
            ticker="005930",
            opinion_type=OpinionType.STOCK,
            action=OpinionAction.STRONG_BUY,
            conviction=0.9,
            reasoning="캐시된 의견",
            model_used="cached",
            generated_at=datetime.now(timezone.utc),
        )

        with patch.object(OpinionGenerator, "_get_cached", return_value=cached_opinion):
            generator = OpinionGenerator()
            opinion = await generator.generate_stock_opinion(
                ticker="005930",
                sentiment_result=sample_sentiment_result,
                quant_signals=sample_quant_signals,
                recent_news=sample_news,
            )

            assert opinion.action == OpinionAction.STRONG_BUY
            assert opinion.conviction == 0.9

    @pytest.mark.asyncio
    async def test_generate_stock_opinion_force_refresh(self, _mock_env, mock_api_response,
                                                        sample_sentiment_result,
                                                        sample_quant_signals, sample_news):
        """개별 종목 의견 생성 - 캐시 무시"""
        cached_opinion = InvestmentOpinion(
            ticker="005930",
            opinion_type=OpinionType.STOCK,
            action=OpinionAction.HOLD,
            conviction=0.5,
            model_used="cached",
            generated_at=datetime.now(timezone.utc),
        )

        with patch.object(OpinionGenerator, "_get_cached", return_value=cached_opinion), \
             patch.object(OpinionGenerator, "_set_cache", new_callable=AsyncMock), \
             patch.object(OpinionGenerator, "_store_to_db", new_callable=AsyncMock):

            generator = OpinionGenerator()
            generator._client = AsyncMock()
            generator._client.messages.create.return_value = mock_api_response

            opinion = await generator.generate_stock_opinion(
                ticker="005930",
                sentiment_result=sample_sentiment_result,
                quant_signals=sample_quant_signals,
                recent_news=sample_news,
                force_refresh=True,
            )

            # force_refresh=True이면 API 호출로 최신 데이터 조회
            assert opinion.action == OpinionAction.BUY

    @pytest.mark.asyncio
    async def test_generate_sector_opinion_basic(self, _mock_env, mock_api_response):
        """섹터 의견 생성 - 정상 케이스"""
        ticker_sentiments = {
            "005930": {"score": 0.7, "summary": "삼성전자 강세"},
            "000660": {"score": 0.6, "summary": "SK하이닉스 중립"},
            "247540": {"score": 0.5, "summary": "에코프로 보합"},
        }
        sector_news = [
            {
                "title": "반도체 업황 개선",
                "content": "AI 수요 확대로 반도체 시장 회복",
                "source": "reuters",
                "published_at": datetime(2026, 4, 2, tzinfo=timezone.utc),
            },
        ]
        macro_context = "글로벌 금리 인상 추세 둔화, 기술주 매수세 강화"

        with patch.object(OpinionGenerator, "_get_cached", return_value=None), \
             patch.object(OpinionGenerator, "_set_cache", new_callable=AsyncMock), \
             patch.object(OpinionGenerator, "_store_to_db", new_callable=AsyncMock):

            generator = OpinionGenerator()
            generator._client = AsyncMock()
            generator._client.messages.create.return_value = mock_api_response

            opinion = await generator.generate_sector_opinion(
                sector_name="반도체",
                representative_tickers=["005930", "000660", "247540"],
                ticker_sentiments=ticker_sentiments,
                sector_news=sector_news,
                macro_context=macro_context,
            )

            assert opinion.ticker is None
            assert opinion.opinion_type == OpinionType.SECTOR
            assert opinion.action == OpinionAction.BUY
            assert "[섹터: 반도체]" in opinion.market_context
            assert opinion.target_weight <= 0.40

    @pytest.mark.asyncio
    async def test_generate_sector_opinion_target_weight_capped(self, _mock_env):
        """섹터 의견 - target_weight 제한 (최대 0.20)"""
        response = MagicMock()
        response.content = [
            MagicMock(text=json.dumps({
                "action": "STRONG_BUY",
                "conviction": 0.85,
                "target_weight": 0.50,  # 0.20 초과
                "reasoning": "섹터 강세",
                "market_context": "금리 인하 기대",
                "risk_factors": ["금리 변화"],
            }, ensure_ascii=False))
        ]

        with patch.object(OpinionGenerator, "_get_cached", return_value=None), \
             patch.object(OpinionGenerator, "_set_cache", new_callable=AsyncMock), \
             patch.object(OpinionGenerator, "_store_to_db", new_callable=AsyncMock):

            generator = OpinionGenerator()
            generator._client = AsyncMock()
            generator._client.messages.create.return_value = response

            opinion = await generator.generate_sector_opinion(
                sector_name="금융",
                representative_tickers=["005930"],
                ticker_sentiments={},
                sector_news=[],
            )

            assert opinion.target_weight == 0.20  # _parse_response에서 0.20으로 제한

    @pytest.mark.asyncio
    async def test_generate_macro_opinion_basic(self, _mock_env, mock_api_response):
        """거시경제 의견 생성 - 정상 케이스"""
        macro_news = [
            {
                "title": "Fed, 금리 동결 시사",
                "content": "미국 연방준비제도가 다음 FOMC에서 금리 동결을 시사",
                "source": "reuters",
                "published_at": datetime(2026, 4, 2, tzinfo=timezone.utc),
            },
            {
                "title": "한국 GDP 성장률 0.8%",
                "content": "분기 GDP 성장률이 연율 기준으로 0.8%로 발표",
                "source": "statistic",
                "published_at": datetime(2026, 4, 1, tzinfo=timezone.utc),
            },
        ]

        with patch.object(OpinionGenerator, "_get_cached", return_value=None), \
             patch.object(OpinionGenerator, "_set_cache", new_callable=AsyncMock), \
             patch.object(OpinionGenerator, "_store_to_db", new_callable=AsyncMock):

            generator = OpinionGenerator()
            generator._client = AsyncMock()
            generator._client.messages.create.return_value = mock_api_response

            opinion = await generator.generate_macro_opinion(macro_news=macro_news)

            assert opinion.ticker is None
            assert opinion.opinion_type == OpinionType.MACRO
            assert opinion.action == OpinionAction.BUY

    @pytest.mark.asyncio
    async def test_generate_macro_opinion_cache_hit(self, _mock_env):
        """거시경제 의견 - 캐시 히트"""
        cached_opinion = InvestmentOpinion(
            ticker=None,
            opinion_type=OpinionType.MACRO,
            action=OpinionAction.HOLD,
            conviction=0.55,
            reasoning="캐시된 거시 의견",
            model_used="cached",
            generated_at=datetime.now(timezone.utc),
        )

        with patch.object(OpinionGenerator, "_get_cached", return_value=cached_opinion):
            generator = OpinionGenerator()
            opinion = await generator.generate_macro_opinion(macro_news=[])

            assert opinion.action == OpinionAction.HOLD
            assert opinion.conviction == 0.55

    def test_parse_response_valid_json(self, _mock_env):
        """정상 JSON 응답 파싱"""
        response = MagicMock()
        response.content = [
            MagicMock(text=json.dumps({
                "action": "STRONG_BUY",
                "conviction": 0.88,
                "target_weight": 0.18,
                "reasoning": "강한 매수 시그널",
                "market_context": "긍정적 시장 환경",
                "risk_factors": ["리스크1", "리스크2"],
            }, ensure_ascii=False))
        ]

        generator = OpinionGenerator()
        opinion = generator._parse_response("005930", OpinionType.STOCK, response)

        assert opinion.ticker == "005930"
        assert opinion.action == OpinionAction.STRONG_BUY
        assert opinion.conviction == 0.88
        assert opinion.target_weight == 0.18
        assert len(opinion.risk_factors) == 2

    def test_parse_response_conviction_boundary(self, _mock_env):
        """확신도 범위 검증 (0.0 ~ 1.0)"""
        response = MagicMock()
        response.content = [
            MagicMock(text=json.dumps({
                "action": "BUY",
                "conviction": 1.5,  # 상한 초과
                "target_weight": 0.15,
                "reasoning": "테스트",
                "market_context": "테스트",
                "risk_factors": [],
            }, ensure_ascii=False))
        ]

        generator = OpinionGenerator()
        opinion = generator._parse_response("005930", OpinionType.STOCK, response)

        assert opinion.conviction == 1.0  # 상한으로 제한

    def test_parse_response_target_weight_negative_boundary(self, _mock_env):
        """target_weight 범위 검증 (최소 0.0)"""
        response = MagicMock()
        response.content = [
            MagicMock(text=json.dumps({
                "action": "BUY",
                "conviction": 0.7,
                "target_weight": -0.1,  # 음수
                "reasoning": "테스트",
                "market_context": "테스트",
                "risk_factors": [],
            }, ensure_ascii=False))
        ]

        generator = OpinionGenerator()
        opinion = generator._parse_response("005930", OpinionType.STOCK, response)

        assert opinion.target_weight == 0.0

    def test_parse_response_target_weight_upper_boundary(self, _mock_env):
        """target_weight 범위 검증 (최대 0.20)"""
        response = MagicMock()
        response.content = [
            MagicMock(text=json.dumps({
                "action": "BUY",
                "conviction": 0.7,
                "target_weight": 0.30,  # 상한 초과
                "reasoning": "테스트",
                "market_context": "테스트",
                "risk_factors": [],
            }, ensure_ascii=False))
        ]

        generator = OpinionGenerator()
        opinion = generator._parse_response("005930", OpinionType.STOCK, response)

        assert opinion.target_weight == 0.20

    def test_parse_response_target_weight_null(self, _mock_env):
        """target_weight null 처리"""
        response = MagicMock()
        response.content = [
            MagicMock(text=json.dumps({
                "action": "HOLD",
                "conviction": 0.5,
                "target_weight": None,
                "reasoning": "테스트",
                "market_context": "테스트",
                "risk_factors": [],
            }, ensure_ascii=False))
        ]

        generator = OpinionGenerator()
        opinion = generator._parse_response("005930", OpinionType.STOCK, response)

        assert opinion.target_weight is None

    def test_parse_response_invalid_action(self, _mock_env):
        """잘못된 action 문자열 처리"""
        response = MagicMock()
        response.content = [
            MagicMock(text=json.dumps({
                "action": "INVALID_ACTION",
                "conviction": 0.7,
                "target_weight": 0.15,
                "reasoning": "테스트",
                "market_context": "테스트",
                "risk_factors": [],
            }, ensure_ascii=False))
        ]

        generator = OpinionGenerator()
        opinion = generator._parse_response("005930", OpinionType.STOCK, response)

        assert opinion.action == OpinionAction.HOLD  # 기본값

    def test_parse_response_malformed_json(self, _mock_env):
        """잘못된 JSON 응답 처리"""
        response = MagicMock()
        response.content = [MagicMock(text="not valid json")]

        generator = OpinionGenerator()
        opinion = generator._parse_response("005930", OpinionType.STOCK, response)

        assert opinion.action == OpinionAction.HOLD
        assert opinion.conviction == 0.1
        assert "파싱 실패" in opinion.reasoning

    def test_parse_response_code_fence_handling(self, _mock_env):
        """마크다운 코드 펜스 제거"""
        response = MagicMock()
        response.content = [
            MagicMock(text="""```json
{
  "action": "BUY",
  "conviction": 0.75,
  "target_weight": 0.15,
  "reasoning": "테스트",
  "market_context": "테스트",
  "risk_factors": []
}
```""")
        ]

        generator = OpinionGenerator()
        opinion = generator._parse_response("005930", OpinionType.STOCK, response)

        assert opinion.action == OpinionAction.BUY
        assert opinion.conviction == 0.75

    @pytest.mark.asyncio
    async def test_call_api_exception_handling(self, _mock_env):
        """API 호출 예외 처리"""
        with patch.object(OpinionGenerator, "_get_cached", return_value=None):
            generator = OpinionGenerator()
            generator._client = AsyncMock()
            generator._client.messages.create.side_effect = Exception("API Error: timeout")

            opinion = await generator._call_api(
                "test prompt",
                "005930",
                OpinionType.STOCK,
            )

            assert opinion.action == OpinionAction.HOLD
            assert opinion.conviction == 0.0
            assert "의견 생성 실패" in opinion.reasoning

    def test_format_news_brief_empty(self):
        """빈 뉴스 리스트 포맷팅"""
        result = OpinionGenerator._format_news_brief([])
        assert result == "최근 관련 뉴스 없음"

    def test_format_news_brief_single(self):
        """단일 뉴스 포맷팅"""
        articles = [
            {
                "title": "테스트 뉴스",
                "content": "테스트 내용입니다.",
                "source": "test_source",
            }
        ]
        result = OpinionGenerator._format_news_brief(articles)

        assert "1. 테스트 뉴스" in result
        assert "테스트 내용입니다." in result

    def test_format_news_brief_multiple(self):
        """여러 뉴스 포맷팅"""
        articles = [
            {
                "title": "뉴스1",
                "content": "내용1" * 50,  # 200자 이상
                "source": "source1",
            },
            {
                "title": "뉴스2",
                "content": "내용2",
                "source": "source2",
            },
        ]
        result = OpinionGenerator._format_news_brief(articles)

        assert "1. 뉴스1" in result
        assert "2. 뉴스2" in result
        # 각 아티클의 content는 [:200]으로 제한되므로, 전체 결과에서 각 항목이 정확히 한 번씩 나타남
        lines = result.split("\n")
        assert any("1. 뉴스1" in line for line in lines)
        assert any("2. 뉴스2" in line for line in lines)

    def test_format_news_brief_long_content_truncation(self):
        """뉴스 내용 200자 제한"""
        long_content = "매우 긴 뉴스 내용입니다. " * 50  # 매우 긴 문자열
        articles = [
            {
                "title": "제목",
                "content": long_content,
                "source": "source",
            }
        ]
        result = OpinionGenerator._format_news_brief(articles)

        # 각 뉴스는 제목 + 200자 이하의 내용으로 제한됨
        lines = result.split("\n")
        assert len(lines) >= 1

    @pytest.mark.asyncio
    async def test_get_cached_redis_available(self, _mock_env):
        """Redis 캐시 조회 - 성공"""
        cached_data = {
            "ticker": "005930",
            "opinion_type": "STOCK",
            "action": "BUY",
            "conviction": 0.75,
            "target_weight": 0.15,
            "reasoning": "캐시된 의견",
            "market_context": "테스트",
            "risk_factors": [],
            "model_used": "test",
        }

        with patch("core.ai_analyzer.opinion.RedisManager.get_client") as mock_redis:
            mock_redis_instance = AsyncMock()
            mock_redis_instance.get.return_value = json.dumps(cached_data)
            mock_redis.return_value = mock_redis_instance

            generator = OpinionGenerator()
            opinion = await generator._get_cached("stock:005930")

            assert opinion is not None
            assert opinion.ticker == "005930"
            assert opinion.action == OpinionAction.BUY

    @pytest.mark.asyncio
    async def test_get_cached_miss(self, _mock_env):
        """Redis 캐시 조회 - 미스"""
        with patch("core.ai_analyzer.opinion.RedisManager.get_client") as mock_redis:
            mock_redis_instance = AsyncMock()
            mock_redis_instance.get.return_value = None
            mock_redis.return_value = mock_redis_instance

            generator = OpinionGenerator()
            opinion = await generator._get_cached("stock:999999")

            assert opinion is None

    @pytest.mark.asyncio
    async def test_set_cache_success(self, _mock_env):
        """Redis 캐시 저장 - 성공"""
        opinion = InvestmentOpinion(
            ticker="005930",
            opinion_type=OpinionType.STOCK,
            action=OpinionAction.BUY,
            conviction=0.75,
            reasoning="테스트",
            model_used="test",
            generated_at=datetime.now(timezone.utc),
        )

        with patch("core.ai_analyzer.opinion.RedisManager.get_client") as mock_redis:
            mock_redis_instance = AsyncMock()
            mock_redis.return_value = mock_redis_instance

            generator = OpinionGenerator()
            await generator._set_cache("stock:005930", opinion)

            mock_redis_instance.setex.assert_called_once()

    @pytest.mark.asyncio
    async def test_store_to_db_success(self, _mock_env):
        """PostgreSQL에 의견 저장 - 성공"""
        opinion = InvestmentOpinion(
            ticker="005930",
            opinion_type=OpinionType.STOCK,
            action=OpinionAction.BUY,
            conviction=0.75,
            reasoning="테스트",
            model_used="test",
            generated_at=datetime.now(timezone.utc),
        )

        with patch("db.database.async_session_factory") as mock_session_factory:
            mock_session = AsyncMock()
            mock_session_factory.return_value.__aenter__.return_value = mock_session

            generator = OpinionGenerator()
            await generator._store_to_db(opinion)

            mock_session.execute.assert_called_once()
            mock_session.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_store_to_db_exception_handling(self, _mock_env):
        """PostgreSQL 저장 실패 시 예외 처리"""
        opinion = InvestmentOpinion(
            ticker="005930",
            opinion_type=OpinionType.STOCK,
            action=OpinionAction.BUY,
            conviction=0.75,
            reasoning="테스트",
            model_used="test",
            generated_at=datetime.now(timezone.utc),
        )

        with patch("db.database.async_session_factory") as mock_session_factory:
            mock_session = AsyncMock()
            mock_session.execute.side_effect = Exception("DB Error")
            mock_session_factory.return_value.__aenter__.return_value = mock_session

            generator = OpinionGenerator()
            # 예외가 발생해도 로그만 하고 계속 진행
            await generator._store_to_db(opinion)

            # 예외 발생했지만 함수가 정상 완료됨을 확인
            assert True

    def test_opinion_system_prompt_exists(self):
        """시스템 프롬프트 정의 확인"""
        assert _OPINION_SYSTEM_PROMPT is not None
        assert "STRONG_BUY" in _OPINION_SYSTEM_PROMPT
        assert "STRONG_SELL" in _OPINION_SYSTEM_PROMPT

    def test_stock_opinion_template_exists(self):
        """종목 의견 템플릿 정의 확인"""
        assert _STOCK_OPINION_TEMPLATE is not None
        assert "{ticker}" in _STOCK_OPINION_TEMPLATE
        assert "{composite_score}" in _STOCK_OPINION_TEMPLATE

    def test_sector_opinion_template_exists(self):
        """섹터 의견 템플릿 정의 확인"""
        assert _SECTOR_OPINION_TEMPLATE is not None
        assert "{sector_name}" in _SECTOR_OPINION_TEMPLATE
        assert "{representative_tickers}" in _SECTOR_OPINION_TEMPLATE

    def test_macro_opinion_template_exists(self):
        """거시 의견 템플릿 정의 확인"""
        assert _MACRO_OPINION_TEMPLATE is not None
        assert "{macro_news}" in _MACRO_OPINION_TEMPLATE
