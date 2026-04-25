# lxml 5.2.2 → 6.1.0 업그레이드 — CVE-2026-41066 정상 해소 — 2026-04-25

> **문서 번호**: OPS-021
>
> **목적**: `backend/requirements.txt` 의 `lxml==5.2.2` → `lxml==6.1.0` 업그레이드를 통해 CVE-2026-41066 / GHSA-vfmq-68hx-4jfw (XXE, CVSS 7.5 High) 를 정상 해소하고, 임시 ignore 엔트리를 두 파일(`backend/.pip-audit-ignore`, `.grype.yaml`)에서 동시에 제거한다. CLAUDE.md §9 미해결 TODO "lxml 6.1.0 업그레이드 (CVE-2026-41066 후속)" 의 공식 산출물.

---

## 1. 배경

### 1.1 임시 ignore 도입 (2026-04-22)

`chore/pip-audit-ignore-lxml-xxe` (PR #25) 에서 lxml `GHSA-vfmq-68hx-4jfw` 를 `backend/.pip-audit-ignore` 에 만료일 2026-06-06 으로 등록하여 CI 블록을 우선 해소했다. 근거:

- 취약 sink 는 `lxml.etree.iterparse()` / `lxml.etree.ETCompatXMLParser()` 두 진입점에 한정 (lxml 5.0 이후 나머지 XML/HTML 파서는 `resolve_entities='internal'` safe default 적용).
- 앱 내 직접 호출 0건: `grep -rnE 'iterparse|ETCompatXMLParser|etree\.parse\(|etree\.fromstring' backend/{core,api,scripts,tests,alembic}` → empty.
- 유일한 lxml 간접 사용은 `backend/core/data_collector/news_collector.py:222,224` 의 `BeautifulSoup(raw, "lxml").get_text(strip=True)` 두 곳. BeautifulSoup 의 `"lxml"` parser 는 HTML 파서 경로로 본 CVE 영역 외.

이 ignore 는 만료일 이전 lxml 버전 업그레이드로 해소하는 것이 정규 경로였다 (`backend/.pip-audit-ignore:1-14` 의 헤더 규칙 — "수동 갱신 금지, 근본 해소 또는 갱신 PR 작성").

### 1.2 grype parity 결손 회귀 (2026-04-22)

PR #25 직후, main CI 의 `anchore/scan-action@v6` (grype) 스텝이 동일 GHSA 를 high severity 로 재판정하여 배포 블록. 원인: `.grype.yaml` 병기 엔트리 누락. `fix/grype-yaml-glibc-lxml-parity` 브랜치에서 `.grype.yaml` 병기 추가로 해소 (PR #26).

이 silent miss 는 OPS-022 (`check_vuln_ignore_parity.py`) 의 직접 동기가 되었고, 본 OPS-021 의 ignore 삭제 단계에서 두 파일을 **동일 커밋** 안에서 제거하도록 강제하는 정적 방어선이 이미 활성 상태다 (2026-04-23 머지).

### 1.3 lxml 6.1.0 의 보안 fix

lxml 6.0 changelog 에서 `iterparse()` 와 `ETCompatXMLParser()` 가 `resolve_entities='internal'` 을 default 로 채택. lxml 6.1.0 은 6.0 의 minor patch 시리즈로 본 fix 를 포함하고, 추가로 다음 버그 수정:

- 6.0.0: XXE default 강화, Python 3.8 sunset
- 6.1.0: 메모리 누수 수정, Windows 빌드 안정화

본 업그레이드는 6.1.0 을 선택 — 6.0.x 보다 9개월 추가 stabilization 기간 + 동일 보안 fix 포함. 6.x major 라인은 BeautifulSoup 4.12.x 와 호환성이 4.13 reroll 없이 유지된다 (BeautifulSoup `_html_parser_for_features` 어댑터 인터페이스 무변경).

---

## 2. Smoke test 검증

CLAUDE.md §9 line 139 의 검증 항목 (2) "beautifulsoup4 4.12.3 과 lxml 6.x 조합 smoke test, 특히 `news_collector._parse_entry` 의 RSS 본문 파싱이 깨지지 않는지 실측" 의 1:1 충족.

### 2.1 격리 venv 구성

```bash
pyenv exec python -m venv /tmp/lxml-smoke
/tmp/lxml-smoke/bin/pip install lxml==6.1.0 beautifulsoup4==4.12.3 feedparser==6.0.11 httpx==0.27.0
```

확인:

```text
lxml: 6.1.0
bs4: 4.12.3
feedparser: 6.0.11
httpx: 0.27.0
```

### 2.2 인라인 파서 smoke (`_parse_entry` 핵심 경로)

`backend/core/data_collector/news_collector.py:222,224` 와 동일한 `BeautifulSoup(raw, "lxml").get_text(strip=True)` 호출을 6 종 입력으로 실행:

| 케이스 | 입력 | 결과 |
|---|---|---|
| HTML 본문 | `<p>Apple <b>raises</b> guidance...</p>` | OK — 정상 텍스트 추출 |
| 한글 + 인라인 태그 | `<div><p>주식 시장 <em>강세</em></p>...</div>` | OK |
| HTML entity reference | `Tom &amp; Jerry &lt;news&gt; &copy; 2026` | OK — `Tom & Jerry <news> © 2026` |
| 빈 입력 | `""` | OK — 빈 문자열 |
| 미닫힘 태그 | `<p>unclosed` | OK — `unclosed` |
| 한글 + 특수문자 | `<article><h1>속보</h1>...$85 돌파...` | OK |

전부 PASS. lxml 6.1.0 + bs4 4.12.3 의 HTML 파서 경로는 5.x 와 동일 동작.

### 2.3 feedparser → BeautifulSoup 파이프라인

RSS 2.0 fixture (CDATA 본문 포함):

```xml
<item>
  <title>Apple Q3 Earnings</title>
  <link>https://example.com/news/1</link>
  <description><![CDATA[<p>Apple beats <b>expectations</b>.</p>]]></description>
  <pubDate>Wed, 23 Apr 2026 12:00:00 GMT</pubDate>
</item>
```

`feedparser.parse()` → `entry.summary` → `BeautifulSoup(summary_html, "lxml").get_text(strip=True)` 결과: `"Apple beatsexpectations."`. 본 경로에서 lxml 6.1.0 은 5.2.2 와 동일 출력.

### 2.4 실 RSS feed 1회 pull (BBC News)

`https://feeds.bbci.co.uk/news/rss.xml` 에 대해 `_parse_feed` 동일 절차 (httpx → feedparser → BeautifulSoup):

```text
[BBC] title="Katya Adler: Europe's Nato allies push b..." body='On Friday morning, souring relations...'
[BBC] title="Two children die in house fire in Wolver..." body='Two other children and a woman were already out...'
[BBC] title="Tensions flare in heated I'm A Celebrity..." body='Adam Thomas, Craig Charles, Mo Farah and Harry...'
[BBC] parsed=3
```

3 entries 정상 파싱. Reuters Markets feed 는 404 응답 (URL 구조 변경) — 본 작업과 무관, 미카운트.

### 2.5 backend/tests 의 mock 한계 메모

`backend/tests/test_coverage_collectors_v2.py:865` `test_parse_feed_success` 와 `:893` `test_parse_feed_missing_title` 는 `httpx.AsyncClient` 와 `feedparser.parse` 를 모두 mock 처리한다. `MagicMock(title=..., link=..., summary="Summary")` 형태의 entry 가 `_parse_entry` 의 `BeautifulSoup(raw, "lxml")` 까지 도달해도 `summary` 가 plain string `"Summary"` 라 lxml 파서가 실질적으로 깊이 진입하지 않는다. 따라서 본 unit test 는 lxml 회귀를 잡지 못한다 — §2.2~§2.4 의 smoke test 가 실효 검증이다.

후속 강화 후보 (별도 PR): `tests/test_news_collector_smoke.py` 에 실 HTML fixture 로 `_parse_entry` 의 lxml 경로를 직접 호출하는 회귀 테스트를 추가. 본 OPS-021 범위는 외 — 해소된 CVE 자체에 직접 연관되지 않는다.

---

## 3. 변경 절차

### 3.1 변경 파일 목록

| 파일 | 변경 |
|---|---|
| `backend/requirements.txt:71` | `lxml==5.2.2` → `lxml==6.1.0` |
| `backend/.pip-audit-ignore` | GHSA-vfmq-68hx-4jfw 블록 11줄 삭제 (헤더 4줄 주석 + ID 1줄 + 빈 줄) |
| `.grype.yaml` | GHSA-vfmq-68hx-4jfw 블록 8줄 삭제 (`# lxml 5.2.2 ...` 주석 7줄 + entry 1줄) |
| `CLAUDE.md §9` | 본 TODO `[ ]` → `[x]`, 본 OPS-021 링크 |
| `docs/operations/ops-numbering.md §2` | OPS-021 `예약` → `활성`, 발급일 2026-04-25, 분류 보안 업그레이드 |
| `docs/operations/lxml-6.1.0-upgrade-2026-04-25.md` | 신설 (본 문서) |

### 3.2 ignore 파일 동시 삭제 검증

OPS-022 `scripts/check_vuln_ignore_parity.py` 가 두 파일의 식별자 차집합을 자동 검사. 본 작업 후 출력:

```text
vuln-ignore parity OK (grype=25, pip-audit=3, shared=3)
```

이전 (2026-04-23 baseline): `grype=26, pip-audit=4, shared=4`. 본 작업으로 1건씩 감소(lxml). shared 도 4→3 으로 정합 유지.

OPS-026 `scripts/check_vuln_ignore_expiry.py`:

```text
vuln-ignore expiry OK (grype=25, pip-audit=3, reference=2026-04-25 UTC)
```

만료 2026-06-06 엔트리 전량 보존, 만료된 엔트리 0.

### 3.3 ignore 삭제만의 위험 회피 (단방향 회귀 방지)

ignore 삭제와 lxml 버전 bump 를 **동일 커밋** 안에 포함한다. 만약 분리하면:

- ignore 만 먼저 삭제 → CI 가 lxml 5.2.2 의 GHSA 를 다시 high 로 차단 → green 못함 → main 진입 불가
- lxml 만 먼저 bump → ignore 는 stale 하지만 GHSA 자체가 6.1.0 에서 사라지므로 silent OK → ignore 가 영구 잔존하는 silent miss

전자가 안전하므로 두 변경을 atomic 으로 묶는다. 본 PR 의 단일 커밋이 그 형태다.

---

## 4. 회귀 방어선 통합

### 4.1 OPS-022 (parity) — 양방향 회귀 차단

본 작업에서 ignore 파일 한쪽만 수정한 PR 은 doc-sync-check.yml 의 `Run vuln-ignore parity check` 스텝에서 즉시 실패한다. 2026-04-22 silent miss 패턴은 본 검사기 이후 구조적으로 재발 불가.

### 4.2 OPS-026 (expiry) — 잔존 ignore 차단

만약 본 작업이 누락되어 lxml ignore 가 2026-06-06 을 넘기면, `Run vuln-ignore expiry check` 스텝이 만료 당일 PR 을 차단한다. 본 OPS-021 의 목표일 (2026-05-23) 은 expiry checker 가 강제하는 일자보다 14일 이른 self-imposed deadline.

### 4.3 본 OPS-021 의 후속 — silent ignore 잔존 검사 후보

CLAUDE.md §9 line 139 의 silent miss 시나리오 — "만료일 이전에 누군가 `iterparse` 또는 `ETCompatXMLParser` 를 새로 도입" — 은 본 작업으로 ignore 자체가 사라지므로 무의미해졌다. 단, 향후 다른 패키지에서 동일 패턴이 발생할 경우 ("ignore 의 코드 경로 미사용 전제가 코드 추가로 깨짐") 을 자동 감지하는 검사기는 별도 OPS 로 검토할 가치가 있다 (현시점 우선순위 낮음 — 본 OPS-021 범위 외).

---

## 5. 검증 결과

### 5.1 정적 검사기

```bash
$ pyenv exec python scripts/check_vuln_ignore_parity.py
vuln-ignore parity OK (grype=25, pip-audit=3, shared=3)

$ pyenv exec python scripts/check_vuln_ignore_expiry.py
vuln-ignore expiry OK (grype=25, pip-audit=3, reference=2026-04-25 UTC)
```

### 5.2 Smoke test

§2 전 항목 PASS.

### 5.3 후속 (CI에서 자동 검증)

- `pip-audit -r backend/requirements.txt --strict` — GHSA-vfmq-68hx-4jfw 미노출 확인
- `anchore/scan-action@v6` (grype) — high severity 0건 확인
- main CI 그린 확인 후 본 OPS 의 `branch-only` 가능 상태가 활성으로 자연 전환

---

## 6. 관련 문서

- `docs/operations/check-vuln-ignore-parity-2026-04-23.md` (OPS-022) — 본 작업의 회귀 방어선이 된 정적 검사기
- `docs/operations/check-vuln-ignore-expiry-2026-04-23.md` (OPS-026) — 만료일 정적 검사기
- `docs/operations/ops-numbering.md` — OPS 번호 발급 SSOT (본 OPS-021 은 2026-04-22 lxml 업그레이드 PR 용으로 예약된 번호를 활성화)
- `docs/security/supply-chain-policy.md` — 공급망 보안 정책 (ignore 파일 헤더 규칙의 SSOT)
- `agent_docs/development-policies.md §13` — pip-audit + grype + cosign + sbom 5축 공급망 가드
