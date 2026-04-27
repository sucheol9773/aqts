---
from: lead
to: lead
subject: boot-disk-resize-todo
created: 2026-04-25T15:14:37Z
priority: Ask  # [P0] 긴급, [Ask] 응답 요청, [FYI] 참고, [Lead-Approval] 리드 승인
---

# boot-disk-resize-todo

## 요약

OPS-028 사건 후 self-mailbox — `aqts-server` boot disk 48GB → 100GB 영구 증설을 24시간 내 처리. 현재 임시 회복 (50% 사용률) 이지만 archive catch-up 으로 wal_archive 가 12GB 까지 다시 자라면 디스크 60% 도달, 이후 다른 시스템 (logs, builder cache) 이 더 자라면 80% warning 임계 진입 가능.

## 맥락

### 사건 내 disk resize 시도 결과

```bash
gcloud compute disks resize aqts-server --size=100GB --zone=asia-northeast3-a --quiet
# ERROR: (gcloud.compute.disks.resize) Could not fetch resource:
#  - Request had insufficient authentication scopes.
```

VM 의 service account 가 `https://www.googleapis.com/auth/compute` scope 미부여 → in-VM gcloud 로 disk admin 작업 불가. 우회 경로:

1. **Mac 측 로컬 gcloud** — 본인 GCP project owner 권한이 있으므로 로컬에서 즉시 수행 가능
2. **GCP console UI** — Compute Engine → Disks → aqts-server → Edit → Size 100GB
3. **VM service account scope 추가** — 영구 fix (장래 in-VM 자동화 가능) 단, VM stop 필요

### 왜 100GB 로 가는가

- 현재 48GB. archive catch-up 후 정상 운영시 사용량 ≈ 25-30GB (postgres data + mongodb + docker images + logs).
- 19일 catch-up 폭주 시점 wal_archive = 12GB. 이런 spike 가 다시 발생해도 100GB 면 여유 50%.
- 100GB 미만 (예: 64GB) 은 마진이 좁아 1년 운영시 다시 재증설 필요. 100GB 가 1년+ 운영 마진.
- e2-standard-2 의 권장 boot disk size 는 100GB+. 50GB 는 PoC 셋업의 잔재.

### 비용

GCP `pd-balanced` 100GB ≈ $10/월. 48GB 대비 $5/월 증가. P0 incident 재발 방어선 대비 충분히 정당화.

## 작업 절차

### Mac 측 로컬 gcloud (권장)

```bash
# Mac 터미널 — 로컬 gcloud 인증된 상태
gcloud auth login   # 필요시
gcloud config set project <PROJECT_ID>
gcloud compute disks resize aqts-server --size=100GB --zone=asia-northeast3-a

# 그 후 서버 들어가서 partition + filesystem 확장
gcloud compute ssh aqts-server -- '
  set -eu
  echo "=== 증설 전 ==="
  df -h /
  echo
  sudo growpart /dev/sda 1
  DISK_DEV=$(df / | tail -1 | awk "{print \$1}")
  sudo resize2fs "$DISK_DEV"
  echo
  echo "=== 증설 후 ==="
  df -h /
'
```

기대 결과: `df -h /` 가 48GB → 100GB 로 보고. 사용률 50% → 24% 수준.

### 서비스 영향

- **Disk resize 자체는 online** — VM 무중단. postgres/backend 등 모두 그대로 동작.
- `growpart` + `resize2fs` 도 mounted filesystem 에서 실행 가능 (`resize2fs` 가 ext4 online resize 지원).
- 작업 시간 < 1분. 야간 (한국 거래 외) 권장이지만 emergency 시 즉시 가능.

## 게이트

- 작업 후 `df -h /` 가 100GB 보고
- `docker ps --filter status=running` 모두 healthy 유지 (resize 가 컨테이너 영향 0)
- backend `/api/system/health: healthy` 유지

## 응답 기한

**리드 self-deadline**: **2026-04-26 23:59 KST 까지** (사건 발생 후 24시간 내). 이 시점 이후로 미루지 말 것 — archive catch-up 에 따라 wal_archive 가 다시 자라면 디스크 압박 재진입 가능.

## 후속 조치

본 메일 처리 후:

1. CLAUDE.md §9 의 OPS-028 후속 TODO 항목 `[x]` 갱신 (리드 전용 deny — 본인 직접 처리)
2. `docs/operations/ops-numbering.md §2` 의 OPS-028 row 가 `활성` 인지 재확인 (본 incident PR 머지 시 함께 활성 처리)
3. 본 메일을 `agent_docs/mailboxes/lead/processed/` 로 이동

## 참조

- `docs/operations/postgres-wal-archive-permission-2026-04-26.md` (OPS-028 §4.3 — gcloud scope 부재 분석)
- `docs/operations/cd-auto-prune-2026-04-16.md §6.2 step 6` (boot disk 증설 = 표준 런북 마지막 단계)
- 자매 메일 (팀 2 위임) — `agent_docs/mailboxes/team2/inbox/20260426-0014-wal-archive-auto-chown.md`
- 자매 메일 (팀 2 위임) — `agent_docs/mailboxes/team2/inbox/20260426-0014-pg-stat-archiver-alert.md`
