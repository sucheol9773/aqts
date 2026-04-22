---
from: lead
to: 2
subject: pat-setup-guide
created: 2026-04-22T14:58:47Z
priority: Ask  # [P0] 긴급, [Ask] 응답 요청, [FYI] 참고, [Lead-Approval] 리드 승인
---

# pat-setup-guide

## 요약

Phase 4 migration (PR #28) 로 `.mcp.json` 에 `github` MCP 서버가 등록됐습니다. 각자 GitHub PAT 를 셸 rc 파일 (`~/.zshrc` 또는 `~/.bashrc`) 에 export 해야 MCP 가 정상 작동합니다.

## 맥락

- 관련 PR: [#28](https://github.com/sucheol9773/aqts/pull/28) Cowork → 4-session migration Phase 1~4
- SSOT 문서: `docs/operations/mcp-setup-2026-04-22.md` (OPS-024) §2.1
- 팀 2 권장 MCP (OPS-024 §4): `github` — CD 실패 이슈 triage
- 미설정 시 동작: MCP 서버는 기동되나 GitHub API 호출만 401. 세션은 정상 진행 (silent miss 아님, development-policies.md §8 준수)

## 요청 / 정보

### 1. GitHub 설정에서 PAT 생성

GitHub → Settings → Developer settings → Personal access tokens → **Tokens (classic)** → Generate new token (classic)

- **Note**: `aqts-mcp-<본인이름>` (예: `aqts-mcp-team2`)
- **Expiration**: 최대 1년 (GitHub 정책)
- **Scope** (최소):
  - [x] `repo` — private 리포 접근 (AQTS public 이나 PR/issue 본문 때때로 민감)
  - [x] `read:org` — 조직 리뷰어 조회
  - [ ] `workflow` — (팀 2 는 권장) Actions 실행 이력 조회에 사용. CD triage 가 주 업무라 유용

### 2. 로그인 셸 rc 파일에 export 추가

로그인 셸이 zsh 면 `~/.zshrc`, bash 면 `~/.bashrc` (macOS 최신은 zsh 기본, Linux 서버는 bash 기본). 본인 셸 확인: `echo $SHELL`.

```bash
# 해당 rc 파일 하단에 추가 (~/.zshrc 또는 ~/.bashrc)
export GITHUB_PERSONAL_ACCESS_TOKEN="ghp_xxxxx..."
```

```bash
source ~/.zshrc   # bash 사용 시: source ~/.bashrc
```

### 3. Claude 세션 재기동 후 검증

```bash
cd /Users/ahnsucheol/Desktop/aqts-team2-scheduler
claude
# 세션 안에서: "list MCP servers" 요청 → github 서버가 응답에 포함되면 OK
```

### 4. 응답 방법 — **토큰 자체는 절대 회신 금지**

본 inbox 에 응답 메시지를 만들 때 `scripts/team/mailbox_new.sh 2 lead pat-setup-done` 으로 새 메일 생성 후 "설정 완료" 한 줄만 기록하세요.

### 5. 만료 관리

PAT 만료 임박 시 본인 rc 파일 (`~/.zshrc` 또는 `~/.bashrc`) 만 업데이트 (재생성 + export 값 교체). 일괄 관리는 향후 secret manager 도입 검토 (OPS-024 §5.1).

## 응답 기한

**2026-04-29** (1주일)
