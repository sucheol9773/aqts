# KIS WebSocket 전송 보안 정책

> 최종 갱신: 2026-04-16

## 1. 현황

한국투자증권(KIS) OpenAPI의 WebSocket 엔드포인트는 2026-04-11 기준으로 `ws://` (비암호화) 프로토콜만 공식 지원한다.

| 구분 | URL | 프로토콜 |
|------|-----|----------|
| 실전 | `ws://ops.koreainvestment.com:21000` | ws (평문) |
| 모의 | `ws://ops.koreainvestment.com:31000` | ws (평문) |

참고: KIS REST API는 `https://` (TLS)를 사용하므로 REST 구간은 안전하다.

## 2. 위험 분석

WebSocket `ws://` 프로토콜은 평문 전송이므로 다음 위험이 존재한다:

- 실시간 시세/체결 데이터 도청 가능
- WebSocket 접속키가 네트워크 경로상에서 노출될 수 있음
- 중간자 공격(MITM)에 의한 데이터 변조 가능성

다만, 현재 전송 데이터는 시세 수신 위주이며 주문 실행은 REST(HTTPS)를 통해 이루어지므로, 직접적인 금전 피해 위험은 제한적이다.

## 3. 부팅 가드 정책

AQTS는 **운영 환경(`ENVIRONMENT=production`) + 실전 모드(`KIS_TRADING_MODE=LIVE`)**에서 `ws://` WebSocket URL 사용 시 부팅을 차단한다.

### 3.1 정상 경로 (A안: wss 지원 시)

KIS가 `wss://` 엔드포인트를 제공하면 즉시 전환한다:

```bash
# .env
KIS_LIVE_WEBSOCKET_URL=wss://ops.koreainvestment.com:21000
```

추가 설정 불필요. 부팅 가드가 `wss://`를 감지하면 통과한다.

### 3.2 예외 경로 (B안: ws://만 지원 시)

KIS가 `ws://`만 지원하는 경우, 아래 3개 환경변수를 **모두** 설정해야 부팅이 허용된다:

```bash
# .env — 3개 모두 필수
KIS_WS_INSECURE_ALLOW=true
KIS_WS_EXCEPTION_TICKET=CHG-2026-0042    # 변경 승인번호
KIS_WS_EXCEPTION_EXPIRES_AT=2026-06-30   # 예외 만료일 (YYYY-MM-DD, 당일 23:59:59 UTC까지 유효)
```

| 조건 | 미충족 시 |
|------|-----------|
| `KIS_WS_INSECURE_ALLOW=true` | 즉시 부팅 차단 |
| `KIS_WS_INSECURE_ALLOW`에 비표준 값 (예: `yes`) | ValueError |
| `KIS_WS_EXCEPTION_TICKET` 비어있음 | 부팅 차단 |
| `KIS_WS_EXCEPTION_EXPIRES_AT` 비어있음 | 부팅 차단 |
| `KIS_WS_EXCEPTION_EXPIRES_AT` 형식 오류 | 부팅 차단 |
| 만료일 경과 | 부팅 차단 |
| WebSocket URL 스킴이 ws/wss가 아님 | 즉시 부팅 차단 |

만료일이 경과하면 자동으로 부팅이 차단되므로, 예외의 영구화가 방지된다.

**만료일 경계 정책**: `YYYY-MM-DD`는 해당 날짜의 **23:59:59 UTC**까지 유효하다. 예를 들어 `2026-06-30`은 `2026-06-30T23:59:59Z`까지 허용되고, `2026-07-01T00:00:00Z`부터 차단된다.

**URL scheme allowlist**: WebSocket URL은 `ws://` 또는 `wss://`만 허용한다. `http://`, `ftp://` 등 다른 스킴은 환경과 무관하게 즉시 차단된다.

### 3.3 예외 갱신 절차

1. 변경 승인(티켓) 발급
2. `.env`에서 `KIS_WS_EXCEPTION_TICKET`과 `KIS_WS_EXCEPTION_EXPIRES_AT` 갱신
3. 서비스 재시작 후 로그에서 `[보안 예외]` 메시지 확인
4. 갱신 주기: 최대 90일 권장

## 4. 네트워크 계층 보안 (장기 대응)

ws://를 장기간 사용해야 하는 경우, 다음 네트워크 계층 암호화를 검토한다:

1. **VPN/IPSec**: GCP ↔ KIS 간 VPN 터널 구성 (KIS 측 협조 필요)
2. **이그레스 프록시**: Envoy/stunnel로 앱↔프록시 mTLS 구간 보호
3. **방화벽 ACL**: KIS 목적지 IP+포트만 허용

단, 프록시↔KIS 구간은 KIS가 TLS 종단을 제공하지 않는 한 평문이 유지된다.

## 5. 모니터링

WebSocket 연결 상태에 대해 다음을 관측한다:

- reconnect 빈도 급증 알람
- 비정상 close code 감지
- RTT(Round-Trip Time) 급등 알람

## 6. 체크리스트

- [ ] KIS 개발자센터에서 wss:// 공식 지원 여부 정기 확인 (분기 1회)
- [ ] 미지원 시 예외 만료일 갱신
- [ ] 네트워크 보안 설계 승인 (VPN/전용회선 검토)
- [ ] 변경 후 모의장/실전장 연결 smoke test
- [ ] 장애 시 fail-closed 동작 확인

## 7. ws:// 운영 전환 전 사전 점검 체크리스트 (LIVE 진입 게이트)

> 대상: `KIS_TRADING_MODE` 를 `DEMO` → `LIVE` 로 전환하면서 동시에 `ws://` 예외 3개 키를 설정해야 하는 운영자.
>
> 원칙: 부팅 가드 통과 (`KIS_WS_INSECURE_ALLOW=true` + ticket + expires_at) 는 **필요조건이지 충분조건이 아니다**. "KIS 가 ws 만 지원한다" 는 사실만으로 예외를 발행하면 안 되고, 아래 보완 통제가 갖춰져서 **잔여 리스크가 수용 가능한 수준으로 내려왔는가** 를 운영책임자가 판단한 뒤에만 예외 티켓을 발행한다.
>
> 본 체크리스트는 4개 계층으로 구성되며, 각 항목은 **재현 가능한 관측 증거**(로그/지표/테스트 결과) 로 증명돼야 한다. "그럴 것이다" 로 체크하지 않는다. RBAC Wiring Rule / 공급망 Wiring Rule 의 "정의 ≠ 적용" 원칙이 동일하게 적용된다.

### 7.1 위협 모델 재확인

| 공격 벡터 | 경로 | 실현 시 피해 |
|-----------|------|--------------|
| 패킷 스니핑 (passive) | 경로상 공격자 (ISP / Wi-Fi / IDC 측면이동) | 체결 통보 메시지에서 계좌번호·종목·수량·체결가 노출 |
| approval_key 탈취 | WebSocket handshake 중간자 | 공격자가 동일 세션으로 시세/통보 구독 가능 (만료 시까지) |
| DNS 스푸핑 (active MITM) | 네트워크 제어권 보유 공격자 | 위조 KIS 서버 연결 → 가짜 체결 통지 주입 → 알고 포지션 상태 왜곡 |
| 메시지 변조 (active MITM) | 경로상 능동 공격자 | 체결가·수량 조작 → 내부 P&L 오계산, 리스크 한도 우회 |

**주문 위조는 불가**: 주문 송신은 REST API (`https://openapi.koreainvestment.com:9443`, TLS) 로만 이루어지므로, WebSocket 트래픽을 완전히 탈취해도 공격자가 거래를 직접 일으킬 수는 없다. 최대 피해는 "포지션 상태 왜곡으로 AQTS 가 스스로 오주문을 내는 것" 이다. 따라서 보완 통제의 핵심은 **WebSocket 으로 받은 상태를 REST 로 재검증해 불일치를 감지하는 것** 이다.

### 7.2 네트워크 레이어 점검 (L3/L4)

- [ ] KIS 엔드포인트와 운영 호스트 사이의 공용 인터넷 통과 구간이 최소화돼 있다 (동일 리전/AZ, 가능하면 동일 IDC).
- [ ] 아웃바운드 방화벽(egress ACL) 이 KIS 공식 IP/포트로만 허용된다. `0.0.0.0/0` 아웃바운드는 금지.
- [ ] KIS 가 제공하는 고정 IP 화이트리스팅/전용망 연계 사용 여부를 계약상 확인했다. (미사용 시 후속 검토 일정 존재)
- [ ] 호스트 OS 에서 ARP spoofing 탐지 (예: `arpwatch`, IDC 레벨 port security) 가 운영되고 있다.
- [ ] DNS 는 내부 resolver (DNSSEC validating) 또는 정적 `/etc/hosts` 고정 IP 로 운영된다. 공용 DNS (8.8.8.8 등) 만 사용하는 구성은 금지.

### 7.3 애플리케이션 레이어 점검 (L7, 코드/설정)

- [ ] **체결 통보 ↔ REST 재조회 교차검증 활성화 확인**: WebSocket 으로 수신한 체결 통보를 "신뢰하지 않고" 분당 1회 이상 `/uapi/domestic-stock/v1/trading/inquire-daily-ccld` 로 재조회해 불일치 시 즉시 kill-switch 발동하는 reconcile 경로가 **실측 로그로 작동 확인**돼야 한다. 테스트 통과가 아니라 **실제 운영 환경에서 reconcile 로그가 남는지** 까지 확인.
- [ ] **approval_key 재발급 주기 단축**: KIS 가 허용하는 최단 TTL 로 설정. 환경변수 `KIS_TOKEN_REFRESH_INTERVAL` 의 실제 운영값이 DEMO/개발 기본값(3600s) 대비 더 짧거나 동일한지 확인.
- [ ] **시세 cross-check**: WebSocket 시세와 REST 일봉/분봉 API 의 최신 체결가 괴리가 임계값 (예: 1%) 초과 시 risk-off 로 전이하는 경로가 있다. (`core.risk` 영역에 테스트 커버리지 확인)
- [ ] **approval_key 가 로그/예외 스택/알림 메시지에 노출되지 않는다**: loguru/logging 포맷터에서 민감정보 마스킹 규칙이 적용돼 있음. 로그 sample 에서 `approval_key=` 값이 `***` 로 가려지는지 실측.
- [ ] **WebSocket 재접속 rate-limit**: 비정상 재접속 폭주 (예: 5초 내 10회 초과) 시 circuit breaker 로 재접속 중단 후 알림. `reconnect 빈도 급증 알람` (§5) 의 실제 작동 확인.

### 7.4 거버넌스 레이어 점검

- [ ] `KIS_WS_EXCEPTION_TICKET` 에 기재할 변경번호가 **실제 변경 관리 시스템**(사내 티켓/Jira/GitHub Issue 등) 에 존재하고, 사유·승인자·검토자가 기록돼 있다.
- [ ] `KIS_WS_EXCEPTION_EXPIRES_AT` 이 **90일 이내** 로 설정돼 있다. 무제한 / 수년 뒤 만료일 설정은 금지.
- [ ] 만료 14일 전 자동 알림이 운영 캘린더/티켓 시스템에 등록돼 있다. 만료 당일 부팅 차단을 운영자가 "사고" 로 만나는 일이 없어야 한다.
- [ ] ws:// 사용 구간이 **감사 로그로 보존**된다. Prometheus 지표 `aqts_kis_ws_insecure_exception_active` (gauge) 가 1 로 표시되는 구간이 별도 대시보드에서 시계열로 조회 가능하다.
- [ ] KIS 개발자센터의 wss:// 지원 공지 모니터링 담당자가 지정돼 있다 (분기 1회 수동 확인 + 공지 구독).

### 7.5 테스트/검증 증거

예외 티켓 발행 전에 다음 증거를 수집해 티켓 본문에 첨부한다:

- [ ] `backend/tests/test_websocket_security.py` 전수 PASS 스크린샷 (부팅 가드 테스트)
- [ ] DEMO 환경에서 WebSocket MITM 시뮬레이션 (로컬 proxy / traffic 변조 도구) 시 reconcile 경로가 불일치 감지 후 risk-off 로 전이하는 로그
- [ ] 최근 3거래일 reconcile 로그 요약 (0건 mismatch 여야 함 — 현재 구간에서 불일치가 발생하는데 LIVE 전환은 불가)
- [ ] KIS 개발자센터 공지 페이지 스냅샷 (티켓 발행일 기준 wss:// 미지원 확인)

### 7.6 운영책임자 최종 승인

모든 체크 박스가 채워지고 7.5 증거가 첨부된 뒤, 운영책임자가 다음 질문에 **서면** (티켓 본문) 으로 답한 뒤 서명한다:

1. "오늘 네트워크 경로상 능동 MITM 이 발생했다고 가정할 때, 최대 몇 분 안에 reconcile 이 불일치를 감지하고 거래를 멈출 수 있는가?" (정량값 기재)
2. "만료일 경과 시 부팅 차단이 발생한 경우, 서비스 복구까지의 RTO 는 몇 분인가? 복구 런북이 문서화돼 있는가?" (문서 경로 기재)
3. "KIS 가 wss:// 를 지원하는 즉시 전환할 책임자는 누구이고, 전환 작업의 예상 소요는?"

### 7.7 회고 — 보완 통제는 "만들었다" 로는 부족하다

이 체크리스트는 **"통제가 존재한다"** 와 **"통제가 실제로 작동한다"** 를 분리해 검증한다. 알림 파이프라인 Wiring Rule / RBAC Wiring Rule / 공급망 Wiring Rule 의 "정의 ≠ 적용" 원칙이 보안 보완 통제 도메인에 그대로 적용되는 확장이다. 예: reconcile 경로가 코드에 존재하고 단위 테스트가 PASS 한다고 해서 **운영 환경에서 실제로 reconcile 이 매 분 호출되고 있다** 는 보장은 없다 — lifespan wiring 결손, task cancel 경로, 예외 swallow 등으로 조용히 멈춰 있을 수 있다. 따라서 7.3 의 체크는 전부 "실측 로그/지표" 로 증명해야 한다.
