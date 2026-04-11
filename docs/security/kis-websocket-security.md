# KIS WebSocket 전송 보안 정책

> 최종 갱신: 2026-04-11

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
KIS_WS_EXCEPTION_EXPIRES_AT=2026-06-30   # 예외 만료일 (YYYY-MM-DD)
```

| 조건 | 미충족 시 |
|------|-----------|
| `KIS_WS_INSECURE_ALLOW=true` | 즉시 부팅 차단 |
| `KIS_WS_EXCEPTION_TICKET` 비어있음 | 부팅 차단 |
| `KIS_WS_EXCEPTION_EXPIRES_AT` 비어있음 | 부팅 차단 |
| 만료일 경과 | 부팅 차단 |

만료일이 경과하면 자동으로 부팅이 차단되므로, 예외의 영구화가 방지된다.

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
