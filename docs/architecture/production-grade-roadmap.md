# Production-Grade Trading System 보강 로드맵

> 본 문서는 현재 AQTS (Layer 1: 브로커 REST/WebSocket API 기반 자동매매 시스템) 가 실제 운영급 (production-grade) 으로 진화하기 위해 보강해야 할 항목을 정리한 단일 진실원천이다. `docs/security/security-integrity-roadmap.md` (§7 보안/정합성 축) 의 상위 컨텍스트 문서이며, 후자는 본 문서의 항목 중 우선순위가 높은 것을 P0/P1 단위로 분해해 실행한다.

## 0. 현재 위치 진단

AQTS 는 KIS Open API 를 통해 주문을 송신하고 응답을 받아 내부 상태에 반영하는 구조로, 헤지펀드 분류상 **Layer 1 — 리테일/소형 자기매매 계층** 에 해당한다. Interactive Brokers, Alpaca, 키움 Open API+ 와 같은 위치이며, 다음 4가지 전제 위에 동작한다.

첫째, 시세와 주문이 모두 단일 브로커 (KIS) 의 REST/WebSocket 채널을 통해 흐르며 거래소 직접 회선이 없다. 둘째, 주문 빈도는 초당 한 자리수 이하로 EGW00133 같은 호출 한도 내에서 운영 가능하다. 셋째, 자본 규모는 단일 계좌 단위이며 다중 펀드/계정 격리가 필요하지 않다. 넷째, 레이턴시 요구사항은 수백 ms~수 초 단위로, FIX 나 거래소 binary 프로토콜이 필요하지 않다.

이 전제가 깨지는 순간 (예: 운용 자산 100억원 돌파, 전략 수 10개 초과, 마이크로초 단위 의사결정 도입) 본 로드맵의 중·후반 항목이 수년 내 강제 사항이 된다. 따라서 본 문서는 단순한 위시리스트가 아니라, 자본·전략·규제 환경의 변화에 따라 어떤 순서로 무엇을 보강해야 하는지에 대한 실행 계획이다.

## 1. 이벤트 소싱 + Kafka 전환 (가장 시급)

현재 `core/portfolio_ledger.py` 의 `PortfolioLedger` 는 in-memory 싱글톤이며 OrderExecutor 가 체결 직후 직접 `record_fill` 을 호출한다. 이 구조의 구조적 한계는 세 가지다. 첫째, 프로세스가 종료되면 ledger 가 사라지므로 재시작 시 reconcile 의 내부 측 진실원천이 비어 있어 전 종목 mismatch 가 발생한다. 둘째, ledger 장애가 OrderExecutor 의 hot path 를 막을 수 있다. 셋째, 같은 체결 이벤트를 reconcile 외에 리스크/리포팅/감사 등 여러 소비자가 필요로 할 때마다 OrderExecutor 가 fan-out 책임을 떠안게 된다.

production-grade 패턴은 OrderExecutor 가 체결 확정 시 `OrderFilled` 이벤트를 Kafka 토픽 (`trading.orders.filled.v1`) 에 publish 하고, 별도 Ledger Service consumer 가 그 이벤트를 소비해 PostgreSQL 에 append-only 로 기록하며, 동일한 토픽을 Risk Service / Reporting Service / Audit Service 가 독립적으로 구독하는 것이다. 이 구조에서 ledger 는 단순히 이벤트 스트림의 한 materialized view 가 되며, 시점 복원 (point-in-time recovery) 과 재처리 (replay) 가 자연스럽게 가능해진다.

전환 시 보존해야 할 불변식은 두 가지다. (a) OrderExecutor 의 체결 확정 → 이벤트 publish 가 원자적이어야 한다 (transactional outbox 패턴 또는 Kafka transaction 사용). (b) Ledger Service 는 동일 이벤트를 두 번 소비해도 결과가 동일해야 한다 (idempotent consumer — 이벤트 ID 를 PK 로 upsert). 이 두 조건이 깨지면 reconcile 자체가 신뢰성을 잃는다.

`docs/security/security-integrity-roadmap.md` 에서는 본 항목을 "PortfolioLedger DB persistence" 로 분해할 예정이며, 첫 단계는 in-memory ledger 를 PostgreSQL append-only 테이블 + read-side cache 로 대체하는 것이다. Kafka 도입은 그 다음 단계 (다중 소비자 등장 시점) 로 미룬다.

## 2. Client Order ID + 강화된 Idempotency

현재 OrderExecutor 는 자체 `order_id` 를 생성해 KIS 로 송신하지만, KIS API 가 client-side ID 의 멱등성을 강제하지 않는다. 즉 동일 주문이 네트워크 재시도로 두 번 전송되면 KIS 는 두 건으로 인식할 수 있다. 우리 `core/scheduler_idempotency.py` 가 부분적으로 이 격차를 메우지만, 주문 송신 직전이 아니라 스케줄러 핸들러 진입 시점에서 작동하므로 OrderExecutor 내부의 재시도/타임아웃 경로는 보호하지 못한다.

production-grade 패턴은 모든 주문에 globally unique 한 `ClOrdID` (Client Order ID, FIX 표준 용어) 를 부여하고, 이를 OrderExecutor 가 송신 직전에 PostgreSQL 의 `idempotency_keys` 테이블에 insert (with `ON CONFLICT DO NOTHING`) 한 뒤 insert 가 성공한 경우에만 브로커로 송신하는 것이다. KIS 가 client ID 를 강제하지 않더라도 우리 측에서 "한 번만 송신" 을 보장할 수 있다. 동시에 audit 로그에 `ClOrdID` 를 함께 기록해 사후 추적 가능성을 높인다.

이 항목은 §7.3 의 후속 P1 으로 분리되며, 우선순위는 1번 (이벤트 소싱) 보다 낮지만 1번보다 구현 비용이 작아 단기 작업으로 먼저 끼워넣을 수도 있다.

## 3. 시세 출처 다중화 (Consolidated Tape)

현재 `core/order_executor/quote_provider_kis.py` 의 `KISQuoteProvider` 는 KIS Open API 단일 출처에서 last trade price 를 가져온다. 본 단일 출처 의존은 두 가지 위험을 만든다. 첫째, KIS 시세 피드가 stale 하거나 erroneous 한 값을 보내도 우리는 그것을 진실로 받아들인다 (5초 신선도 검증은 시각 기반이지 값의 합리성 기반이 아니다). 둘째, KIS 시세가 일시 중단되면 모든 거래가 fail-closed 로 막힌다.

production-grade 패턴은 동일 종목에 대해 2~3개 출처 (KIS + 거래소 직피드 + Bloomberg/Refinitiv) 에서 가격을 받아 cross-check 한 뒤 합성 (median 또는 weighted average) 하여 한 점을 만든다. Bloomberg B-PIPE, Refinitiv Elektron, KRX 의 KOSCOM MDS 가 한국에서 흔히 쓰이는 두 번째 출처들이며, 이들 중 어느 하나가 다른 출처와 1% 이상 벗어나면 그 출처를 staleness 로 간주하고 일시적으로 제외한다.

본 항목의 도입 임계는 명확하다. 단일 KIS 시세 피드의 분기당 incident 가 1건 이상 발생하기 시작하면, 또는 하루 중 몇 분이라도 KIS 가 stale 해지는 빈도가 관측되기 시작하면 그 시점이 두 번째 시세 출처를 도입해야 하는 시점이다. 그 전에는 비용 대비 효과가 낮다.

## 4. Pre-trade 리스크 게이트의 마이크로서비스 분리

미국의 SEC Rule 15c3-5 (Market Access Rule, 2010년) 은 브로커가 모든 주문에 대해 송신 직전에 주문 한도, 신용 한도, fat-finger price check, duplicate order check 를 강제하도록 의무화한다. 한국의 KRX 에도 유사한 risk-based access control 규정이 있다. 우리 `core/trading_guard.py` 와 `core/order_executor/price_guard.py` 가 이 역할을 담당하지만, 현재 구조에서는 이 가드가 OrderExecutor 의 라이브러리 함수로 in-process 호출된다는 한계가 있다.

production-grade 패턴은 risk gateway 를 별도 마이크로서비스 (보통 C++ 또는 Rust 로 작성, 대기시간 100µs 이하) 로 분리하고, 모든 주문 송신 경로가 OS 레벨 또는 network 레벨에서 반드시 그 게이트를 통과하도록 강제하는 것이다. 우회 경로를 코드/AST 검사가 아니라 네트워크 토폴로지로 차단한다는 점이 핵심이다. 우리 RBAC Wiring Rule (`get_current_user` 만으로는 부족하다) 의 사고방식을 네트워크 계층으로 확장한 것과 같다.

본 항목의 도입 임계는 (a) 다중 전략/다중 프로세스가 동시에 OrderExecutor 를 사용하기 시작하는 시점, (b) 컴플라이언스 감사가 "코드 검사가 아니라 네트워크 검사로 우회 가능성을 증명하라" 고 요구하는 시점이다. 단일 프로세스 단일 전략 단계에서는 in-process 가드도 충분하다.

## 5. Reconcile 주기 단축 (Scheduled → Event-driven)

현재 `core/reconciliation_runner.py` 의 `ReconciliationRunner` 는 `core/trading_scheduler.py` 의 MIDDAY_CHECK 와 POST_MARKET 두 시점에서만 작동한다. 즉 mismatch 가 발생해도 최악의 경우 몇 시간이 지나야 검출된다. 이는 우리 자본 규모에서는 수용 가능한 trade-off 이지만, production-grade 에서는 부족하다.

production-grade 패턴은 두 가지 주기를 병렬로 운영한다. 첫째는 **체결 이벤트 트리거 reconcile** — OrderExecutor 가 체결을 ledger 에 반영한 직후 그 종목 한 종에 대해서만 broker balance 를 조회해 즉시 비교한다. 둘째는 **주기적 전체 reconcile** — 5분 단위로 전 종목을 비교한다. 첫 번째는 단일 체결 정합성을, 두 번째는 누락된 이벤트 (Kafka 토픽 lag 등) 를 잡는다. 두 주기 사이의 모든 mismatch 가 5분 이내에 검출됨을 보장한다.

본 항목은 1번 (이벤트 소싱) 이 선행돼야 자연스럽게 구현된다. 이벤트 스트림이 있으면 Reconciler Service 가 동일 토픽을 구독하다가 `OrderFilled` 이벤트를 보면 그 종목만 trigger 하면 된다.

## 6. 관측성 + SRE 운영 체계

현재 우리는 Prometheus Counter/Gauge/Histogram 을 충실히 부착하고 있지만 (예: `aqts_quote_cache_hits_total`, `aqts_reconciliation_mismatches_total`), 이 지표들 위에 SLO (Service Level Objective) 와 alerting rule 이 명시적으로 정의되어 있지는 않다. Grafana 대시보드도 ad-hoc 으로 만들어져 있어 incident 시 어디를 봐야 할지 표준화돼 있지 않다.

production-grade 패턴은 다음 4가지 SLO 를 명시적으로 선언하고 자동 측정한다. (a) **Order success rate**: 99.9% 이상 (99.9th percentile 의 주문이 reject 없이 체결까지 도달). (b) **Reconciliation freshness**: 마지막 성공한 reconcile 시각으로부터 5분 이내. (c) **Quote freshness**: KIS Open API 응답 시각이 현재로부터 5초 이내. (d) **Kill switch latency**: kill switch 활성화 → 모든 후속 주문 차단까지 100ms 이내. 각 SLO 에 budget burn rate alert 를 부착해 budget 의 2% 를 1시간 안에 소진하면 PagerDuty 가 울리도록 한다.

이와 함께 incident response runbook 을 `docs/runbooks/` 에 표준화한다 — `kis-token-degraded.md`, `reconciliation-mismatch.md`, `kill-switch-activated.md`, `quote-stale-rejects.md` 같은 사례별 절차서. 운영자가 새벽 3시에 깨어났을 때 5분 안에 무엇을 확인하고 무엇을 누를지 명확해야 한다.

## 7. 컴플라이언스/감사 로그의 외부 불변성

현재 `core/audit_logger.py` (P0-4) 가 PostgreSQL 의 `audit_logs` 테이블에 fail-closed 로 기록한다. 같은 PostgreSQL 인스턴스를 운영하는 측이 (즉 우리가) 이 테이블을 임의로 수정/삭제할 수 있다는 점은 컴플라이언스 관점에서 약점이다. 운영자 본인의 악의 또는 실수로부터 감사 로그를 보호하지 못한다.

production-grade 패턴은 다음 3가지 중 하나 이상을 도입한다. (a) **Append-only 외부 저장소** — AWS S3 Object Lock with WORM (Write-Once-Read-Many) 모드, GCS Bucket Lock, 또는 Azure Blob Immutable Storage 로 PostgreSQL 의 audit row 를 비동기 복제하고 일정 보존 기간 (예: 7년) 동안 삭제 불가능하게 잠근다. (b) **암호학적 chain-hash** — 각 audit row 의 hash 가 직전 row 의 hash 를 포함하도록 chain 을 만들어 (Merkle log 와 동일 원리) 중간 수정이 불가능하게 만든다. (c) **외부 감사 SaaS** — Datadog Audit Trail, Splunk Enterprise Security 같은 외부 시스템에 동시 송신해 우리 측 인프라가 손상돼도 사본이 남도록 한다.

본 항목은 우리가 자기 자본만 운용하는 동안에는 우선순위가 낮지만, 외부 자금을 받기 시작하는 순간 (LP 가 등장하는 순간) 즉시 강제 사항이 된다. 펀드 감사인이 가장 먼저 묻는 질문 중 하나가 "운영자가 audit log 를 수정할 수 있느냐" 이기 때문이다.

## 8. Disaster Recovery + Multi-region

현재 우리 시스템은 단일 region (아마도 단일 데이터센터) 에 배포돼 있고 PostgreSQL 도 단일 인스턴스다. region 장애 또는 데이터센터 화재 발생 시 (드문 일이지만 0이 아니다) 전체 시스템이 정지하며 복구 시점 (RPO) 은 마지막 백업 시점이고 복구 시간 (RTO) 은 수동 재배포 시간이다.

production-grade 패턴은 다음 4가지 구성 요소를 갖는다. (a) **PostgreSQL streaming replication** — primary 와 동기적/반동기적 standby 를 다른 가용영역에 두어 RPO 0 을 목표한다. (b) **Hot standby region** — 전체 인프라를 다른 region 에 모방 배포하고 평시에는 traffic 0% 로 두었다가 region failover 시 DNS 전환으로 절체한다. (c) **백업 검증 자동화** — 매일 백업을 격리된 환경에 자동 복원하고 정합성 테스트를 돌린다. 백업이 존재한다는 것과 백업이 복원 가능한가는 다른 문제다. (d) **DR drill** — 분기 1회 의도적으로 region failover 를 수동 트리거하고 RTO/RPO 를 측정한다.

본 항목은 자본 규모와 운영 시간 (24/7 인지 시장 시간만인지) 에 따라 비용 정당화 임계가 달라진다. 한국 주식 시장만 거래하는 동안에는 시장이 닫혀 있는 시간에 복구할 수 있으므로 우선순위가 낮다. 미국 시장을 함께 거래하면 거의 24시간 가동이 필요하므로 우선순위가 즉시 올라간다.

## 9. Secret Management + Credential Rotation

현재 KIS API 키, JWT signing key, DB 비밀번호 등이 환경변수 또는 `.env` 파일로 주입되며, rotation 절차가 자동화돼 있지 않다. 운영자가 수동으로 키를 갱신하고 컨테이너를 재배포해야 한다. 이 구조의 문제는 두 가지다. 첫째, 키 유출 시 rotation 까지의 시간이 길다. 둘째, rotation 을 잊어버리는 경우가 있다.

production-grade 패턴은 HashiCorp Vault, AWS Secrets Manager, GCP Secret Manager 같은 전용 secret store 를 사용해 (a) 모든 secret 을 중앙 저장소에 두고, (b) 애플리케이션은 시작 시 또는 주기적으로 short-lived token 을 받아 secret 을 fetch 하고, (c) rotation 은 secret store 의 cron job 으로 자동화하고, (d) 모든 secret access 를 audit 로그에 기록한다. 컨테이너 이미지나 git 저장소에 secret 이 절대 포함되지 않는다는 불변식이 핵심이다.

본 항목은 외부 자금을 받기 전에 반드시 정리해야 한다 — 컴플라이언스 관점에서 "secret 이 어디 저장돼 있느냐" 는 첫 질문 중 하나이고, `.env` 파일이 답이면 곧바로 감점이다.

## 10. FIX 프로토콜 마이그레이션 (장기)

본 항목은 가장 마지막에 위치하며, 도입 임계가 명확하다. (a) 운용 자산이 수백억 원을 넘어 KIS Open API 의 호출 한도와 레이턴시가 병목이 되거나, (b) 여러 브로커를 동시에 사용하기 시작하거나 (smart order routing), (c) 외국 시장 (해외 주식, 선물, FX) 으로 확장해 KIS 한 곳으로 커버 불가능해지는 시점이다.

이 시점에 우리는 골드만삭스/모건스탠리/JP모건/한국투자증권 IB 사업부 같은 prime broker 와 FIX 세션 계약을 체결하고, 우리 OrderExecutor 의 KIS REST 호출 부분을 FIX `D=NewOrderSingle` 메시지 빌드 + TCP 세션 send 로 교체한다. ExecutionReport (`8`) 는 콜백 핸들러로 처리하고, OrderStatusRequest (`H`) 와 OrderMassStatusRequest (`AF`) 가 reconcile 의 새로운 진실원천이 된다. QuickFIX 가 가장 널리 쓰이는 오픈소스 엔진이며, 자체 구현하는 경우도 흔하다.

본 마이그레이션의 핵심 설계 원칙은 OrderExecutor 의 인터페이스 (`execute_order(request) -> OrderResult`) 를 변경하지 않는 것이다. 하부 구현이 KIS REST 든 FIX 든 동일한 contract 를 유지하면, TradingGuard / price_guard / portfolio_ledger / reconciliation 등 상위 계층은 전혀 수정 없이 그대로 작동한다. 우리가 지금 §7.3 에서 만들고 있는 추상화 (QuoteProvider Protocol, PositionProvider Protocol) 가 정확히 이 마이그레이션을 가능하게 만드는 토대다.

## 우선순위 매트릭스

본 로드맵의 항목들은 자본 규모, 외부 자금 유무, 시장 확장 단계라는 세 축으로 우선순위가 결정된다. 단일 자본·자기 자금·단일 시장 단계에서 즉시 착수해야 할 항목은 1번 (이벤트 소싱) 과 2번 (ClOrdID idempotency), 그리고 6번 (SLO + runbook) 이다. 이 셋은 비용 대비 효과가 크고, 도입을 미루면 후속 항목의 토대가 흔들린다.

외부 자금을 받기 시작하는 단계에서는 7번 (audit log 외부 불변성) 과 9번 (secret management) 이 강제 사항이 된다. 이 둘이 없으면 LP 또는 펀드 감사인이 자금을 집행하지 않는다.

다중 전략·다중 프로세스 단계에 진입하면 4번 (risk gateway 분리) 과 5번 (event-driven reconcile) 이 필요해진다. 단일 프로세스에서는 코드 검사로 충분하지만, 다중 프로세스에서는 네트워크 토폴로지 검사로 보강해야 우회 가능성을 차단할 수 있다.

10번 (FIX 마이그레이션) 은 마지막에 온다. 이 단계에 도달했다는 것은 시스템이 충분히 성숙하고 자본이 충분히 크고 KIS API 가 더 이상 충분하지 않다는 뜻이다. 그 시점까지 1~9번이 잘 정리돼 있으면 마이그레이션 자체는 OrderExecutor 한 클래스의 하부 구현 교체로 끝난다 — 그것이 §7.3 의 quote provider/position provider Protocol 추상화가 노리는 목표다.

## 본 로드맵과 §7.3 의 관계

`docs/security/security-integrity-roadmap.md` 의 §7 (보안/정합성 축) 은 본 로드맵의 항목 중 정합성·무결성 관련 항목을 P0/P1 단위로 분해해 실행하는 작전 계획이다. 본 로드맵은 그 상위의 전략 문서로, "우리가 어디로 가고 있는가" 를 정의하고 §7.3 은 "이번 분기에 무엇을 끝낼 것인가" 를 정의한다. 두 문서가 모순될 경우 본 로드맵을 먼저 갱신하고 §7.3 을 그에 맞춰 재정렬한다 — 즉 본 문서가 의사결정의 근거이며, §7.3 은 그 의사결정의 실행 단위다.

본 문서의 갱신은 다음 트리거에서 발생한다. (a) 자본 규모가 한 단위 (10배) 이상 변동, (b) 외부 자금 유치, (c) 시장 확장 (해외 주식/선물/FX 도입), (d) 신규 incident 후 회고에서 도출된 구조적 보강 항목. 이 4가지 외의 사유로는 본 문서를 수정하지 않는다 — 작전 계획 (§7.3) 은 자주 갱신되지만 전략 문서는 자주 갱신하지 않는 것이 단일 진실원천의 의미를 보존하는 길이다.
