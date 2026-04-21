---
paths:
  - "backend/config/**/*.py"
  - "backend/config/**/*.yaml"
  - "backend/config/**/*.yml"
  - ".env.example"
  - ".env"
---

# 설정 / 환경변수 영역 가드

**소유**: **혼합 영역** — 리드 전용 파일과 팀메이트 1 파일이 공존합니다. 상세: `agent_docs/governance.md §2.1, §2.5`.
**SSOT**: `docs/conventions/boolean-config.md` (bool 환경변수), `agent_docs/development-policies.md §4,§11` (설정 일관성 / Wiring Rule).

## 파일별 소유권 (반드시 확인 후 수정)

| 경로 | 소유 | 비고 |
|---|---|---|
| `backend/config/settings.py` | **리드 전용** | Pydantic Settings 단일 진입점. 새 환경변수 추가는 리드 승인 |
| `.env.example` | **리드 전용** | 키 추가/삭제는 리드 승인. **실값 커밋 금지** |
| `backend/config/ensemble_config.yaml` | 팀메이트 1 | 전략 가중치 / 하이퍼옵트 파라미터 |
| `backend/config/ensemble_config_loader.py` | 팀메이트 1 | yaml → config 객체 변환 |
| `backend/config/operational_thresholds.yaml` | 팀메이트 2 | 임계값 (risk-off, cooldown 등) |

경계 밖 파일을 수정할 필요가 있으면 메일박스로 담당자에게 위임합니다.

## 절대 규칙

- **하드코딩 절대 금지** (CLAUDE.md §1). `.env` 실값 / API 키 / 계좌번호 / 개인정보는 어떤 문서/프롬프트/커밋에도 포함하지 않고, `.env.example` 의 **키 이름만** 인용합니다.
- **bool 환경변수 표준** (development-policies.md §11): 소문자 `"true"`/`"false"` 만 허용. `1/0/yes/no/on/off` 는 Phase 1 경고 → Phase 2 `ValueError`. 환경변수 → bool 변환은 **반드시** `core.utils.env.env_bool()` 단일 진입점 사용. `os.environ.get(...) == "true"` 같은 ad-hoc 파싱 금지.
- **설정값 일관성** (development-policies.md §4): `operational_thresholds.yaml` ↔ 코드 내 `DEFAULT_THRESHOLDS` (또는 유사 기본값 딕셔너리) 는 항상 동일. yaml 이 코드 기본값을 override 하는 구조에서는 yaml 수정을 빠뜨리면 코드 변경이 무효화됩니다.

## Wiring Rule — 설정 → 실제 동작 전달 경로

`STRATEGY_RISK_PRESETS` (또는 유사 설정 딕셔너리) 에 새 키를 추가할 때:

1. 프리셋 dict 에 키 추가
2. Config 객체 생성부에서 해당 파라미터 전달 확인 (예: `BacktestConfig(dd_cushion_start=...)`)
3. 엔진에서 해당 값을 실제로 읽는지 확인
4. **통합 테스트 또는 런타임 로그**로 활성화 확인 (유닛테스트만으로는 wiring 검증 불가 — 엔진은 독립 동작)
5. 커밋 전: 새로 추가한 설정값이 실행 시 로그에 출력되는지, 실제 동작에 영향을 미치는지 확인

"정의했다 ≠ 적용했다" — 이는 RBAC / 메트릭 / 알림 파이프라인에도 공통되는 원칙입니다.

## 새 bool 환경변수 추가 절차

1. `.env.example` 에 키 + 소문자 bool 예시값 추가 (리드 승인 필요)
2. `backend/config/settings.py` 에 Pydantic 필드 추가 (리드 승인 필요)
3. 사용처에서 `env_bool()` 경유로 읽기
4. `backend/scripts/check_bool_literals.py::BOOL_ENV_KEYS` 화이트리스트에 등록
5. `docs/conventions/boolean-config.md` 에 사용 예 추가
6. 커밋 전 `python scripts/check_bool_literals.py` 0 errors 확인

## 커밋 전 체크

```bash
python scripts/check_bool_literals.py                # bool 규약 준수
python scripts/check_doc_sync.py --verbose           # .env.example 변경 시 필수
cd backend && python -m ruff check . --config pyproject.toml
cd backend && python -m black --check . --config pyproject.toml
# settings.py 또는 yaml 변경 시 관련 단위 테스트
cd backend && python -m pytest tests/test_settings*.py tests/test_config*.py -q
```

## 회귀 사례 — silent miss 경계

- **operational_thresholds.yaml ↔ DEFAULT_THRESHOLDS 불일치**: yaml 수정을 빠뜨리면 테스트는 통과하지만 운영 환경에서 코드 변경이 무효화됩니다. 설정값 변경 시 `grep` 으로 모든 참조 위치를 확인하세요.
- **신규 프리셋 키 미전달**: dict 에 키를 추가했지만 `BacktestConfig(...)` 생성부에 파라미터를 추가하지 않으면, 유닛테스트는 통과하지만 엔진은 기본값으로 동작합니다.
