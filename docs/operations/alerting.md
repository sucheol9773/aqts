# Alerting 운영 가이드

## 1. 구성 개요

```
Prometheus (rule eval) ──fire──▶ Alertmanager (route/group/inhibit) ──▶ Telegram
```

- **Prometheus**: `monitoring/prometheus/rules/*.yml` 의 alert rule 을 평가하고
  발화 시 Alertmanager 로 전달한다.
- **Alertmanager**: `monitoring/alertmanager/alertmanager.yml.tmpl` 을 기반으로
  심각도별 라우팅, 그룹핑, 억제(inhibition) 를 수행한 뒤 Telegram receiver 로
  전송한다.
- **수신 채널**: Telegram bot (`TELEGRAM_BOT_TOKEN`) → 채널/채팅 ID
  (`TELEGRAM_CHAT_ID`).

## 2. 템플릿 렌더링 모델

Alertmanager 바이너리는 환경변수를 **네이티브로 치환하지 않는다**. Prometheus
도 동일하다. 따라서 `${VAR}` 표기를 `alertmanager.yml` 에 그대로 적어두면
config loader 가 리터럴 문자열로 인식하고, 특히 `chat_id` 처럼 int64 스키마
필드에서는 즉시 unmarshal 에러를 내고 컨테이너가 부팅되지 못한다.

본 프로젝트는 다음 흐름으로 이 문제를 회피한다:

1. 사람이 편집하는 원본은 `monitoring/alertmanager/alertmanager.yml.tmpl`
   (템플릿).
2. `docker-compose.yml` 의 `alertmanager` 서비스 entrypoint 가 컨테이너 부팅
   시점에 sed 로 `${TELEGRAM_BOT_TOKEN}`, `${TELEGRAM_CHAT_ID}` 를 치환하여
   `/tmp/alertmanager.yml` 로 렌더링한다.
3. `alertmanager` 바이너리는 렌더링 결과 파일을 `--config.file` 로 읽는다.
4. 환경변수가 비어 있으면 `: "${VAR:?...}"` 가드가 즉시 실패시켜 잘못된
   설정이 부팅되지 않도록 한다.

이 흐름은 다음 불변식을 만족한다:

- 템플릿 파일은 read-only 마운트 (`:ro`) 라 컨테이너가 원본을 변경할 수 없다.
- 렌더링 결과는 `/tmp` 에 한정되어 컨테이너 재시작마다 항상 새로 만들어진다.
- 환경변수가 누락되면 부팅 자체가 차단된다 (silent fallback 없음).

## 3. 환경변수

| 변수 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `TELEGRAM_BOT_TOKEN` | string | yes | Telegram Bot API 토큰 (`123456:ABC...` 형식) |
| `TELEGRAM_CHAT_ID`   | int64  | yes | 알림을 받을 채널/채팅의 ID |
| `ALERTMANAGER_PORT`  | int    | no  | 호스트 노출 포트, 기본 9093 |

`.env.example` 에 더미 값이 등록되어 있으니 실제 값은 운영 환경의 `.env`
에서 채운다.

## 4. CI 게이트

`.github/workflows/ci.yml` 의 lint 잡에 두 단계가 추가되어 있다:

1. **템플릿 렌더링 + 스키마 자체 검증**: 더미 env 로 sed 렌더링한 뒤 PyYAML
   으로 파싱하고 `chat_id` 가 `int`, `bot_token` 이 비어 있지 않은 `str` 인지
   확인. 회귀 사례 (2026-04, alertmanager 가 한 번도 부팅하지 못한 채 머지된
   `888db64` 도입 PR) 를 잡기 위함.
2. **amtool check-config**: `prom/alertmanager:v0.27.0` 이미지의 `amtool` 로
   alertmanager 스키마(라우트 트리, receivers, inhibit_rules 전반) 를 검증.

두 단계 중 하나라도 실패하면 머지가 차단된다.

## 5. 운영 절차

### 5.1 라우트/receiver 추가

1. `monitoring/alertmanager/alertmanager.yml.tmpl` 을 편집한다.
2. 새 환경변수가 필요하면:
   - `docker-compose.yml` 의 `alertmanager.environment` 에 등록
   - `docker-compose.yml` 의 entrypoint sed 명령에 치환 라인 추가
   - `.env.example` 에 더미 값 추가
   - 본 문서의 §3 표 갱신
3. 로컬에서 §6 의 수동 검증을 한 번 통과시킨다.
4. CI 의 amtool 게이트를 통과하는지 PR 단계에서 확인한다.
5. 머지 후 운영 서버에서 `docker compose up -d alertmanager` 로 재기동.

### 5.2 헬스 체크

`docker-compose.yml` 의 `prometheus`/`alertmanager` 두 서비스 모두에 Docker
healthcheck 가 등록되어 있다 (`wget -qO- http://localhost:<port>/-/ready`,
15s 간격, 5회 재시도, start_period 20~30s). 따라서 `docker compose ps` 의
STATUS 컬럼이 `(healthy)` 인지를 1차 지표로 사용한다. `(unhealthy)` 로 표시
되면 config 로드 실패 또는 storage/cluster 문제를 의미한다.

또한 `alertmanager` 는 `prometheus` 의 healthy 상태를 `depends_on` condition
으로 요구한다 — prometheus 가 부팅 실패한 상태에서 alertmanager 만 살아
있는 비정상 조합이 compose 레벨에서 차단된다.

```bash
# 컨테이너 상태 (healthy/unhealthy 확인)
# 주의: 운영 서버 .env 에 IMAGE_NAMESPACE 가 설정되어 있어야 한다.
# compose 는 parse 단계에서 모든 서비스의 interpolation 을 한번에 검사하므로
# backend 의 ${IMAGE_NAMESPACE:?...} 가드가 prometheus/alertmanager 만 타겟한
# 명령까지 차단한다. 값 설정 방법: docs/operations/docker-setup-guide.md §3.2.
docker compose ps prometheus alertmanager

# compose parse 우회 (IMAGE_NAMESPACE 미설정 상황 fallback)
docker inspect --format '{{.State.Health.Status}}' aqts-prometheus aqts-alertmanager

# 상세 healthcheck 결과 (최근 실패 로그 포함)
docker inspect --format '{{json .State.Health}}' aqts-alertmanager | jq
docker inspect --format '{{json .State.Health}}' aqts-prometheus | jq

# Alertmanager 자체 ready (수동 재확인)
curl -fsS http://localhost:9093/-/ready

# 현재 활성 알림
curl -fsS http://localhost:9093/api/v2/alerts | jq '.[] | {alertname: .labels.alertname, severity: .labels.severity, state: .status.state}'

# Prometheus 가 인식하는 alertmanager 인스턴스 (up=1 이어야 함)
curl -fsS http://localhost:9090/api/v1/targets?state=active | jq '.data.activeTargets[] | select(.labels.job == "alertmanager") | {health: .health, lastError: .lastError}'
```

### 5.3 토큰 회전

`TELEGRAM_BOT_TOKEN` 이 회전되면:

1. `.env` 의 값을 갱신.
2. `docker compose up -d alertmanager` (재기동, entrypoint 가 새로 렌더링).
3. `curl -fsS http://localhost:9093/-/ready` 로 부팅 성공 확인.
4. 테스트 알람 발화 (예: `prometheus` 잠시 down 시킨 뒤 BackendDown 발화)
   로 실제 텔레그램 수신 확인.

## 6. 수동 검증 (로컬)

```bash
export TELEGRAM_BOT_TOKEN="123456:DUMMY_TOKEN"
export TELEGRAM_CHAT_ID="987654321"

sed -e "s|\${TELEGRAM_BOT_TOKEN}|${TELEGRAM_BOT_TOKEN}|g" \
    -e "s|\${TELEGRAM_CHAT_ID}|${TELEGRAM_CHAT_ID}|g" \
    monitoring/alertmanager/alertmanager.yml.tmpl > /tmp/alertmanager.rendered.yml

python3 -c "
import yaml
d = yaml.safe_load(open('/tmp/alertmanager.rendered.yml'))
for r in d['receivers']:
    tg = r['telegram_configs'][0]
    assert isinstance(tg['chat_id'], int), r['name']
    assert isinstance(tg['bot_token'], str) and tg['bot_token'], r['name']
print('OK')
"

docker run --rm \
  -v /tmp/alertmanager.rendered.yml:/etc/alertmanager/alertmanager.yml:ro \
  --entrypoint /bin/amtool \
  prom/alertmanager:v0.27.0 \
  check-config /etc/alertmanager/alertmanager.yml
```

세 단계가 모두 통과해야 한다.

## 7. 회고: 2026-04 alertmanager 부팅 실패 (`888db64`)

### 7.1 증상

`docker compose ps alertmanager` 가 항상 `Restarting` 상태로 머물고 로그에는
다음이 1분마다 반복됐다:

```
Loading configuration file failed
yaml: unmarshal errors:
  line 75: cannot unmarshal !!str `${TELEG...` into int64
  line 93: cannot unmarshal !!str `${TELEG...` into int64
  line 105: cannot unmarshal !!str `${TELEG...` into int64
```

### 7.2 직접 원인

`monitoring/alertmanager/alertmanager.yml` 의 `chat_id` 필드에
`${TELEGRAM_CHAT_ID}` 가 리터럴로 들어 있었고, alertmanager 컨테이너에는
환경변수도 entrypoint 도 주입되지 않아 치환될 경로가 존재하지 않았다.
alertmanager 바이너리는 환경변수를 네이티브 치환하지 않으므로 문자열을
int64 로 unmarshal 하다 실패한 것이다.

### 7.3 근본 원인 (정의 ≠ 적용)

도입 PR `888db64` 는 다음을 정의했지만 어느 것도 wiring 되지 않았다:

- `alertmanager.yml` 안에 `${VAR}` 플레이스홀더를 둠 (정의)
- `.env` 에 `TELEGRAM_*` 변수 등록 (정의)
- 그러나 컨테이너가 그 변수를 받을 통로도, 받은 뒤 치환할 entrypoint 도
  없었음 (적용 누락)
- 부팅 검증 (헬스체크/스모크) 도 없었음 (관찰 누락)

**RBAC Wiring Rule** 과 동일한 사고 패턴이다 — "정의했다 ≠ 적용했다". 이번
재발 방지로 §4 의 CI 게이트(렌더링 + amtool) 를 도입했다.

### 7.4 재발 방지 체크리스트

신규 monitoring config 또는 신규 환경변수 도입 시:

- [ ] config 파일이 환경변수를 사용한다면, 렌더링 경로(entrypoint/init/외부
      도구) 가 명시적으로 존재하는가?
- [ ] 컨테이너 `environment:` 블록에 변수가 등록되어 있는가?
- [ ] `.env.example` 에 더미 값이 등록되어 있는가?
- [ ] CI 에서 더미 env 로 렌더링 + 스키마 검증이 가능한가?
- [ ] 첫 배포 후 헬스체크 (`/-/ready` 또는 동등) 가 200 을 반환하는지 직접
      확인했는가?
- [ ] `docker-compose.yml` 의 해당 서비스에 Docker `healthcheck:` 가 등록
      되어 있는가? 등록되어 있지 않다면 회귀 발생 시 관측 공백이 생긴다.
