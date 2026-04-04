# GCP 프로비저닝 가이드

> **문서 번호**: OPS-009
>
> **버전**: 1.0 | **최종 수정**: 2026-04-05
>
> **목적**: Google Cloud Platform에서 AQTS 운영 환경을 구성하는 단계별 절차를 안내합니다.

---

## 1. 사전 준비

### 1.1 GCP 계정 생성 및 크레딧 확보

1. [Google Cloud Console](https://console.cloud.google.com) 접속
2. 신규 계정이면 **$300 무료 크레딧 (90일)** 자동 지급
3. 결제 계정 등록 (크레딧 소진 전까지 실제 과금 없음)

### 1.2 gcloud CLI 설치

```bash
# macOS
brew install google-cloud-sdk

# Ubuntu/Debian
curl https://sdk.cloud.google.com | bash
exec -l $SHELL

# 초기 설정
gcloud init
gcloud auth login
```

### 1.3 프로젝트 생성

```bash
# 프로젝트 생성
gcloud projects create aqts-trading --name="AQTS Trading System"

# 프로젝트 선택
gcloud config set project aqts-trading

# Compute Engine API 활성화
gcloud services enable compute.googleapis.com
```

---

## 2. VM 인스턴스 생성

### 2.1 권장 사양

| 항목 | Phase 1 (DEMO) | Phase 2+ (LIVE) |
|------|----------------|-----------------|
| 머신 타입 | e2-standard-2 (2vCPU/8GB) | e2-standard-4 (4vCPU/16GB) |
| 디스크 | 50GB SSD (pd-balanced) | 100GB SSD (pd-ssd) |
| 리전 | asia-northeast3 (서울) | asia-northeast3 (서울) |
| 월 예상 비용 | ~$49 | ~$98 |

### 2.2 인스턴스 생성 명령

```bash
gcloud compute instances create aqts-server \
    --zone=asia-northeast3-a \
    --machine-type=e2-standard-2 \
    --image-family=ubuntu-2404-lts-amd64 \
    --image-project=ubuntu-os-cloud \
    --boot-disk-size=50GB \
    --boot-disk-type=pd-balanced \
    --tags=aqts-server \
    --metadata=startup-script='#!/bin/bash
        apt-get update
        apt-get install -y ca-certificates curl gnupg
        install -m 0755 -d /etc/apt/keyrings
        curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
        chmod a+r /etc/apt/keyrings/docker.gpg
        echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null
        apt-get update
        apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
        systemctl enable docker
        usermod -aG docker $(ls /home | head -1)
    '
```

> startup-script가 Docker를 자동 설치합니다. 인스턴스 생성 후 1~2분 대기 후 SSH 접속하세요.

### 2.3 고정 IP 할당

```bash
# 고정 외부 IP 예약
gcloud compute addresses create aqts-ip \
    --region=asia-northeast3

# IP 주소 확인
gcloud compute addresses describe aqts-ip \
    --region=asia-northeast3 --format='value(address)'

# 인스턴스에 고정 IP 연결
gcloud compute instances delete-access-config aqts-server \
    --zone=asia-northeast3-a \
    --access-config-name="External NAT"

gcloud compute instances add-access-config aqts-server \
    --zone=asia-northeast3-a \
    --access-config-name="External NAT" \
    --address=$(gcloud compute addresses describe aqts-ip --region=asia-northeast3 --format='value(address)')
```

> 이 고정 IP를 KIS OpenAPI 접속 IP로 등록해야 합니다.

---

## 3. 방화벽 설정

```bash
# API 포트 (8000) 개방
gcloud compute firewall-rules create aqts-allow-api \
    --direction=INGRESS \
    --priority=1000 \
    --network=default \
    --action=ALLOW \
    --rules=tcp:8000 \
    --target-tags=aqts-server \
    --source-ranges=0.0.0.0/0

# SSH (22) — 기본 허용됨

# DB 포트 (5432/27017/6379) — 개방하지 않음 (Docker 내부 네트워크만)
# ⚠️ DB 포트는 절대 외부에 노출하지 마세요
```

### 3.1 방화벽 확인

```bash
gcloud compute firewall-rules list --filter="targetTags=aqts-server"
```

예상 결과:
```
NAME              DIRECTION  PRIORITY  ALLOW     TARGET_TAGS
aqts-allow-api    INGRESS    1000      tcp:8000  aqts-server
default-allow-ssh INGRESS    65534     tcp:22    (all)
```

---

## 4. SSH 접속 및 초기 설정

### 4.1 SSH 접속

```bash
gcloud compute ssh aqts-server --zone=asia-northeast3-a
```

### 4.2 Docker 설치 확인

```bash
docker --version          # Docker version 24.0+
docker compose version    # Docker Compose v2.20+
```

만약 Docker가 아직 설치되지 않았다면 (startup-script 완료 전):
```bash
# 수동 설치
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
sudo usermod -aG docker $USER
newgrp docker
```

### 4.3 스왑 메모리 설정 (권장)

e2-standard-2 (8GB RAM)에서 Docker 빌드 시 메모리 부족 방지:

```bash
sudo fallocate -l 4G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

---

## 5. AQTS 배포

### 5.1 소스 코드 클론

```bash
cd ~
git clone <REPOSITORY_URL> aqts
cd aqts
```

### 5.2 환경변수 설정

```bash
cp .env.example .env
nano .env   # 또는 vim .env
```

**필수 수정 항목**:

```env
# 데이터베이스 비밀번호 (안전한 값으로 변경)
DB_PASSWORD=<강력한_비밀번호>
MONGO_PASSWORD=<강력한_비밀번호>
REDIS_PASSWORD=<강력한_비밀번호>

# KIS 모의투자 API (Phase 1: DEMO)
KIS_TRADING_MODE=DEMO
KIS_DEMO_APP_KEY=<발급받은_앱키>
KIS_DEMO_APP_SECRET=<발급받은_앱시크릿>
KIS_DEMO_ACCOUNT_NO=<모의투자_계좌번호>

# Claude API
ANTHROPIC_API_KEY=<발급받은_API키>

# 텔레그램 알림 (선택)
TELEGRAM_BOT_TOKEN=<봇_토큰>
TELEGRAM_CHAT_ID=<채팅_ID>
```

> 비밀번호 생성: `openssl rand -base64 24`

### 5.3 배포 실행

```bash
bash scripts/deploy.sh --prod
```

### 5.4 배포 검증

```bash
bash scripts/verify_deployment.sh
```

---

## 6. SSL/HTTPS 설정 (선택)

도메인이 있는 경우 Let's Encrypt로 무료 SSL 인증서를 설정합니다.

### 6.1 도메인 DNS 설정

도메인 관리 서비스에서 A 레코드를 GCP 고정 IP로 지정:
```
A   aqts.yourdomain.com   →   <GCP_고정_IP>
```

### 6.2 Nginx + Certbot 설치

```bash
sudo apt-get install -y nginx certbot python3-certbot-nginx

# Nginx 리버스 프록시 설정
sudo tee /etc/nginx/sites-available/aqts << 'NGINX'
server {
    listen 80;
    server_name aqts.yourdomain.com;

    location / {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
NGINX

sudo ln -sf /etc/nginx/sites-available/aqts /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx

# SSL 인증서 발급
sudo certbot --nginx -d aqts.yourdomain.com --non-interactive --agree-tos -m your@email.com
```

### 6.3 HTTPS 방화벽 추가

```bash
gcloud compute firewall-rules create aqts-allow-https \
    --direction=INGRESS \
    --priority=1000 \
    --network=default \
    --action=ALLOW \
    --rules=tcp:443,tcp:80 \
    --target-tags=aqts-server \
    --source-ranges=0.0.0.0/0
```

---

## 7. 모니터링 및 유지보수

### 7.1 로그 확인

```bash
# 전체 서비스 로그
docker compose logs -f --tail=100

# 백엔드만
docker compose logs -f backend --tail=200

# 에러만
docker compose logs backend --tail=500 | grep ERROR
```

### 7.2 리소스 모니터링

```bash
# Docker 리소스 사용량
docker stats --no-stream

# 디스크 사용량
df -h
docker system df
```

### 7.3 자동 재시작 확인

Docker Compose의 `restart: unless-stopped` 설정으로 서버 재부팅 시 자동 재시작됩니다.

```bash
# 확인: 서버 재부팅 후 자동 시작되는지 테스트
sudo systemctl enable docker   # Docker 데몬 자동 시작
```

### 7.4 디스크 정리 (주기적)

```bash
# 미사용 Docker 이미지/볼륨 정리
docker system prune -f

# 오래된 로그 정리 (30일 이상)
docker compose logs --no-log-prefix backend | head -1   # 로그 시작일 확인
```

---

## 8. 비용 최적화

### 8.1 무료 크레딧 기간 (90일)

- Phase 1 DEMO 검증 전체를 무료 크레딧 내에서 완료 가능
- 모니터링: [GCP 결제 > 예산 및 알림](https://console.cloud.google.com/billing)에서 알림 설정

### 8.2 크레딧 소진 후

| 방법 | 절감율 | 비고 |
|------|--------|------|
| 1년 약정 (CUD) | ~30% | LIVE 전환 확정 시 |
| 선점형 VM (Spot) | ~60-70% | 중단 가능성 있어 LIVE에는 부적합 |
| 머신 타입 조정 | 가변 | 리소스 모니터링 후 판단 |

### 8.3 NCP 이전 시점

다음 조건 충족 시 NCP 이전 검토:
- Phase 1 DEMO 검증 완료
- GCP 무료 크레딧 소진 임박
- LIVE 전환 결정

이전 절차:
1. `pg_dump` / `mongodump`로 데이터 백업
2. NCP 서버 프로비저닝 (동일 Docker 구성)
3. 데이터 복원 → `deploy.sh --prod` 실행
4. KIS OpenAPI 접속 IP를 NCP 서버 IP로 변경
5. DNS A 레코드 변경 (도메인 사용 시)

---

## 9. 체크리스트

### 9.1 프로비저닝 완료 체크리스트

- [ ] GCP 계정 생성 + $300 크레딧 확인
- [ ] VM 인스턴스 생성 (e2-standard-2, 서울 리전)
- [ ] 고정 IP 할당
- [ ] 방화벽 설정 (8000 포트만 개방)
- [ ] SSH 접속 확인
- [ ] Docker / Docker Compose 설치 확인
- [ ] 스왑 메모리 설정

### 9.2 배포 완료 체크리스트

- [ ] `.env` 파일 설정 완료
- [ ] `bash scripts/deploy.sh --prod` 성공
- [ ] `bash scripts/verify_deployment.sh` 전 항목 PASS
- [ ] KIS OpenAPI에 GCP 고정 IP 등록
- [ ] 텔레그램 테스트 알림 수신 확인

### 9.3 KIS OpenAPI IP 등록

1. [KIS 개발자센터](https://apiportal.koreainvestment.com) 접속
2. 내 앱 관리 > 해당 앱 선택
3. 접속 IP 등록에 GCP 고정 IP 추가
4. DEMO/LIVE 앱 모두 등록

---

## 참고 문서

| 문서 | 참조 시점 |
|------|----------|
| OPS-007 docker-setup-guide.md | Docker Compose 상세 구성 |
| OPS-008 deployment-roadmap.md | Phase 0~4 전체 로드맵 |
| .env.example | 환경변수 전체 목록 및 설명 |
| scripts/deploy.sh | 배포 자동화 |
| scripts/verify_deployment.sh | 배포 검증 |
