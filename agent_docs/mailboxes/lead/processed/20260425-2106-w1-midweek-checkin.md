---
from: 4
to: lead
subject: w1-midweek-checkin
created: 2026-04-25T12:06:12Z
priority: Ask
---

# [Ask] ADR-002 Stage 2 W1 mid-week 체크인 (kickoff 응답 기한 준수)

## 요약

- **§2 누적 호출**: **0회**. task a 는 anthropic-skills 호출을 동반하지 않는 일반 정적 검사기 개발이라 §2 카운트에 기여하지 않음.
- **F1~F7 발현**: 0건. **G1~G7 위반**: 0건.
- **task a (parity 검사기)**: ✅ 완료 (PR #37, `ab10f0b`, 2026-04-23). + 보너스 산출물 PR #39/#40 (만료일 검사기 OPS-023→OPS-026 재발급).
- **task b (AQTS SKILL 2종)**: ⏳ **본 mid-week 시점(2026-04-25 21:06 KST) 부터 착수**. W1 잔여 4일.
- **Stop 조건 4 위험 신호**: §2 < 2회 인 채 2026-04-29 도달 시 Pilot 교체 발동. 회피 경로는 task b 산출물 + Pilot 세션 자동 트리거 ≥ 2회 달성 단 하나.

## 맥락

### task a 완료 경위

`pilot/team4-skills-w1` 워크트리는 본 세션 시작 시점에 `f32b177` 이었으나, OPS-023 mailbox #32 의 B-1 경로(즉시 main merge)를 채택하여 `e62a328` 까지 ff-only 머지 완료 (W1 로그 §8 line 154). 이 머지 안에 PR #37 (task a 완료) + PR #39/#40 (만료일 검사기 보너스) 가 포함됨.

§C 필수 검증 3종 모두 PASS:
1. `.claude/settings.local.json.disabledMcpjsonServers=["*"]` — 외부 MCP 차단 유지
2. github MCP 비활성 (`mcp__github__*` 도구 현 세션 부재)
3. `scripts/team/wiring_smoke.sh` PASSED

### task a 의 §2 카운트 기여 없음 — 구조적 이유

task a 산출물(`scripts/check_vuln_ignore_parity.py` + 회귀 테스트 + workflow 스텝 + OPS-022) 는 모두 **AQTS 자체 정적 검사기 개발** 이며 anthropic-skills 호출 경로가 전무. ADR-002 §7.5.5 항목 2 의 "스킬 호출" 정의가 `anthropic-skills:` prefix 의 외부 SKILL.md 를 Claude Code 가 자동 트리거하는 사례에 한정되므로, task a 가 아무리 풍부해도 §2 카운트에는 0 으로 남음.

### task b 가 §2 ≥ 2 달성의 유일한 경로

§2 누적 ≥ 2회 도달 경로는 **task b 산출물(SKILL.md 2종) + Pilot 세션 자동 트리거 ≥ 1회씩** 만이 유일. 이를 W1 종료(2026-04-29) 까지 달성하지 못하면 ADR-002 §2.2 Stop 조건 4 (Pilot 교체) 발동.

본 mid-week 시점에 task b 착수. 진행 상황은 W1 로그 §3.2 체크포인트 + lead inbox 후속 메일에 누적 보고 예정.

## 요청 / 정보

### Pilot → 리드 요청 (Ask)

1. **CLAUDE.md §9 TODO `[x]` 전환 일정 확인**: §3.1 5번째 체크포인트(리드 전용 영역). W1 종료 리뷰(2026-04-29) 시 일괄 처리 가능 여부 확인 부탁드립니다. 가능하다면 본 mid-week 회신 시 yes/no 만이라도 회신 부탁.
2. **task b SKILL 자동 트리거 측정 방법론 합의**: ADR-002 §7.5.5 항목 2 의 "자동 트리거" 판정 기준이 (a) 시스템 reminder 의 `available skills` 목록에 등장, (b) Claude 세션이 user prompt 매칭으로 SKILL 을 invoke, (c) skill body 의 첫 line 까지 도달, (d) skill 산출물이 실제 file 로 떨어짐 — 어느 단계까지를 "1회"로 카운트할지 W1 마감 전 합의 필요. 본 메일 회신에 의견 남겨주시면 즉시 W1 로그 §2 표 헤더에 반영하겠습니다.
3. **task b SKILL 검증 도구**: `skills-ref validate` 가 AQTS 환경에 설치되어 있지 않다면 (확인 필요) 수동 검증 체크리스트(YAML frontmatter / 500줄 / G1~G7 명시 / PEP 723) 를 OPS-022 옆에 OPS-027 로 신설 권장. 본 작업도 task b 의 일부로 진행 가능하나 OPS 번호 발급은 OPS registry (PR #41) 경로 필요 — 발급 절차 위임 가능한지 회신 부탁.

### 리드 → Pilot 정보 공유 (FYI)

- W1 로그 §3.1 4 체크포인트 [x] 처리 완료 (PR #37 근거). 5 번째는 [ ] 유지하고 "리드 전용 영역. 본 W1 종료 리뷰(2026-04-29) 시 일괄 처리 요청" 명시.
- W1 로그 §3.1 보너스 항목으로 PR #39/#40 만료일 검사기 추가 인지함 (kickoff scope 외 이득).
- §4 (F1~F7) / §5 (G1~G7) 모두 0 으로 유지. task b 진행 중 발현 시 즉시 update + lead inbox 후속.

## 응답 기한

**2026-04-26 (일) 21:00 KST** — 위 3건(CLAUDE.md TODO 일정 / 트리거 카운트 기준 / OPS-027 발급 위임) 회신. 미응답 시 (b) 는 가장 보수적 해석(=skill 산출물이 file 로 떨어졌을 때만 1회) 으로 카운트하고, (c) 는 OPS 번호 미부착 임시 문서로 진행 후 W1 종료 시 일괄 정리하는 fallback 으로 진행하겠습니다.
