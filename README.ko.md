<p align="center">
  <h1 align="center">[ Xoul ]</h1>
  <p align="center">
    모두가 사용 가능한 개인 비서 Agent를 추구합니다.<br/>
    Local LLM과 가상화를 통해 개인 데이터는 외부로 일체 전송될 수 없습니다.<br/>
    Xoul를 사용하여 당신의 일상을 효율화 하세요.
  </p>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/local--first-brightgreen?style=flat-square" />
  <img src="https://img.shields.io/badge/open--source-brightgreen?style=flat-square" />
  <img src="https://img.shields.io/badge/privacy-brightgreen?style=flat-square" />
</p>

<p align="center">
  🌐 한국어 | <a href="README.md">English</a>
</p>

---

<p align="center">
  <img src="res/xoul_kr.gif" alt="Xoul 데모" width="600" />
</p>

---

## 💡 왜 Xoul인가

| | |
|---|---|
| ⚡ **실용적인 AI 에이전트** | 챗봇이 아닙니다 — 파일 관리, 이메일 전송, 웹 검색, 코드 실행까지. OS 레벨 18개 내장 도구. |
| 🔒 **VM 기반 보안** | 모든 동작이 QEMU 가상머신 안에서 실행됩니다. 호스트 시스템은 영향 없음. |
| 🔄 **일상 자동화** | 뉴스 요약, 서버 점검, 이메일 정리 등 반복 작업을 워크플로우로 예약 실행. |
| 👥 **커뮤니티 성장** | 다른 사용자가 공유한 워크플로우·페르소나·코드를 원클릭 임포트. 직접 공유도 가능. |

---

## 🚀 주요 기능

- 📅 **개인 비서** — 일정, 이메일, 연락처 관리 (Google 연동)
- 🎭 **페르소나 & 코드** — 에이전트 역할 전환, 커뮤니티 Python 코드 실행
- 🔄 **워크플로우** — 다단계 자동화 템플릿 + 스케줄링 지원
- ⚔️ **AI Arena** — 에이전트 놀이터, 토론·마피아 게임 등
- 🖥️ **호스트 PC 제어** — 제한적 호스트 상호작용 (브라우저, 파일 관련)

---

## 🖥️ 시스템 요구사항

| 구분 | 최소 | 권장 |
|------|------|------|
| **CPU** | x86‑64, 8코어 | — |
| **RAM** | 8 GB | 16 GB 이상 |
| **GPU** | NVIDIA 30시리즈, 8 GB VRAM | NVIDIA 40시리즈 이상, 16 GB+ VRAM |
| **OS** | Windows 11 (10 실험적) | — |
| **디스크** | 20 GB 여유 | — |

---

## 📦 설치

### 빠른 시작 (권장)

1. [Xoul 다운로드 페이지](https://www.xoulai.net/download) 방문
2. 최신 릴리즈 zip과 (선택) VM 이미지 다운로드
3. `install.bat` 실행

`install.bat`이 파일 배치와 설정 스크립트 실행을 자동으로 처리합니다.
이후 대화형 설정이 모든 과정을 안내합니다.

### 소스에서 설치 (개발자용)

```powershell
git clone https://github.com/xoul-project/xoul.git
cd xoul
.\scripts\setup_env.ps1
```

<details>
<summary><strong>📋 설치 과정 상세 (클릭하여 펼치기)</strong></summary>

#### Step 1 — 언어 선택

한국어 / English 중 선택. 모든 UI와 메시지에 적용됩니다.

#### Step 2 — LLM 모델

| 모드 | 설명 |
|------|------|
| **로컬 (추천)** | Ollama로 내 GPU에서 실행. 모델 자동 다운로드. |
| **상용 API** | Claude, GPT‑5, Gemini, DeepSeek, Grok, Mistral — API 키 필요. |
| **외부 서버** | OpenAI 호환 엔드포인트 (vLLM, LM Studio 등). |

로컬 모델은 VRAM에 맞게 자동 추천됩니다:

| 모델 | VRAM |
|------|------|
| Nemotron‑3‑Nano 4B (Q8) | ~5 GB |
| Nemotron‑3‑Nano 4B (BF16) | ~8 GB |
| GPT‑oss 20B | ~13 GB |
| Nemotron‑Cascade‑2 30B | ~20 GB |

**BGE‑M3** (임베딩)과 **Qwen 2.5 3B** (요약, CPU 전용)도 자동 설치됩니다.

#### Step 3 — QEMU VM

`winget`으로 QEMU 설치. WHPX 하드웨어 가속 자동 테스트.

> [!IMPORTANT]
> **WHPX (Windows Hypervisor Platform)** 를 활성화하면 **3~5배 빠른 VM 성능**을 얻을 수 있습니다. 아래 방법으로 설정을 강력 권장합니다.

**GUI 설정:**
1. `Win + R` → `optionalfeatures` 입력 → Enter
2. ✅ **Hyper-V** 및 ✅ **Windows 하이퍼바이저 플랫폼** 체크
3. PC 재부팅

**PowerShell 설정** (관리자 권한):
```powershell
Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V-All -NoRestart
Enable-WindowsOptionalFeature -Online -FeatureName HypervisorPlatform -NoRestart
Restart-Computer
```

> [!TIP]
> 설정 후 `setup_env.ps1`을 다시 실행하면 WHPX 가속이 자동 감지되어 적용됩니다.

#### Step 4 — Python 환경

Python 3.12로 `.venv` 생성, 모든 패키지 자동 설치.

#### Step 5 — VM 이미지

사전 빌드된 `xoul.qcow2`가 있으면 복사, 없으면 클라우드 이미지로 새로 생성 (~10~15분).

#### Step 6 — 설정

대화형 프롬프트:

- **사용자 프로필** — 이름, 위치, 에이전트 이름
- **이메일** — Gmail 앱 비밀번호 (선택)
- **⚠️ 웹 검색** — [Tavily](https://tavily.com) API 키 (**권고**, 무료 가능). 미설정 시 검색 품질이 크게 저하됩니다.
- **Telegram / Discord / Slack** — 봇 토큰 (선택)
- **GitHub** — Personal Access Token (선택)

#### Step 7 — 배포 및 실행

에이전트 코드가 VM에 배포되고 모든 서비스가 자동 시작됩니다. 설정이 완료되면 **Desktop App이 자동으로 실행**됩니다.

</details>

---

## 🎯 사용법

설치 후 Desktop App 실행:

```powershell
c:\xoul\desktop\xoul.bat
```

### Quick Bar

어디서든 `Ctrl+Space`를 누르면 **Quick Bar**가 나타나, 창 전환 없이 바로 에이전트에게 명령을 입력할 수 있습니다.

<p align="center">
  <img src="res/quick.png" alt="Quick Bar" width="500" />
</p>

### 사용 예시 — Quick Bar + Application Launch

Quick Bar를 활용하여 검색 후 바로 Chrome으로 웹사이트를 여는 모습입니다 — 창 전환 없이 한 번에 처리됩니다.

> *"봄 음악 관련 유튜브 채널 검색해서 크롬으로 열어줘"*

<p align="center">
  <img src="res/app_lauch.gif" alt="Quick Bar + Application Launch" width="800" />
</p>

### 사용 예시 — 워크플로우 실행

워크플로우는 여러 도구를 연결하는 다단계 자동화 템플릿입니다 — 뉴스 요약, 서버 점검, 이메일 정리 등. 한 마디로 어떤 워크플로우든 즉시 실행할 수 있습니다.

> *"모닝 브리핑 워크플로우 실행해줘"*

<p align="center">
  <img src="res/xoul_kr.gif" alt="워크플로우 실행" width="800" />
</p>

### 사용 예시 — Xoul Hub에서 임포트

커뮤니티에서 공유된 워크플로우, 페르소나, 코드 스니펫을 Xoul Hub에서 원클릭으로 임포트할 수 있습니다 — 에이전트의 기능을 즉시 확장하세요.

<p align="center">
  <img src="res/import_workflow.gif" alt="Xoul Hub 워크플로우 임포트" width="800" />
</p>

### 사용 예시 — AI Arena

AI Arena에서 여러 에이전트가 토론하고 마피아 같은 사회적 추론 게임을 합니다. 내 에이전트가 다른 에이전트들과 실시간으로 자율 대화하는 모습을 확인하세요.

<p align="center">
  <img src="res/arena_discussion.gif" alt="AI Arena 토론" width="800" />
</p>

### 사용 예시 — Telegram 연동

Telegram을 통해 에이전트와 대화하세요 — 작업 관리, 워크플로우 실행, 알림 수신까지 이동 중에도 가능합니다.

<p align="center">
  <img src="res/telegram.gif" alt="Telegram 연동" width="200" />
</p>

---

## 🧠 지원 로컬 모델

설치 시 VRAM 용량에 따라 자동으로 최적 모델이 선택됩니다.

| # | 모델 | VRAM | 속도 | 품질 |
|---|------|------|------|------|
| 1 | Nemotron-3-Nano 4B (Q8) | 7 GB | 빠름 | 낮음 |
| 2 | Gemma 4 E2B ⚠️ | 8 GB | 빠름 | 중간 |
| 3 | Nemotron-3-Nano 4B (BF16) | 10 GB | 빠름 | 중간 |
| 4 | Gemma 4 E4B ⚠️ | 10 GB | 빠름 | 중간 |
| 5 | Qwen3-VL-8B-Instruct | 14 GB | 빠름 | 중간 |
| 6 | GPT-oss 20B | 16 GB | 빠름 | 좋음 |
| 7 | Gemma 4 26B ⚠️ | 18 GB | 보통 | 좋음 |
| 8 | Gemma 4 31B ⚠️ | 20 GB | 보통 | 우수 |
| 9 | Nemotron-Cascade-2 30B | 24 GB | 약간 느림 | 우수 |

> [!NOTE]
> ⚠️ **Gemma 4 시리즈** — 현재 Ollama에서 Gemma 4의 Flash Attention이 불안정하여 비활성화된 상태입니다. 이로 인해 예상보다 추론 속도가 다소 느릴 수 있습니다.

**BGE-M3** (임베딩)과 **Qwen 2.5 3B** (요약, CPU 전용)도 자동 설치됩니다.

---

## 📱 클라이언트

| 클라이언트 | 설명 |
|-----------|------|
| **데스크톱** (PyQt6) | Windows 네이티브 채팅 앱 |
| **Telegram** | 텔레그램 봇으로 대화 |
| **Discord** | 채널에서 봇 멘션 |
| **Slack** | Socket Mode 연동 |
| **Terminal** | CLI 인터페이스 |

---

## 📁 프로젝트 구조

```
xoul/
├── server.py            # 메인 에이전트 서버
├── assistant_agent.py   # 에이전트 로직 & 도구 조합
├── llm_client.py        # LLM 프로바이더 추상화
├── vm_manager.py        # QEMU VM 생명주기 관리
├── browser_daemon.py    # 헤드리스 Chromium 제어
├── desktop/             # PyQt6 데스크톱 클라이언트
├── tools/               # 18개 내장 도구
├── scripts/             # 설치, 배포, 런처
├── locales/             # 다국어 (ko, en)
└── services/            # Systemd 서비스 파일
```

---

## 🔧 재설정이 필요할 때

설정을 다시 해야 하는 경우, 아래 명령어를 순서대로 실행하세요:

```powershell
.\scripts\setup_env.ps1    # 설정 재실행
.\scripts\launcher.ps1     # 서비스 시작
```


---

## 📄 라이선스

MIT

---

## 🔗 링크

- 🌐 [웹사이트](https://www.xoulai.net)
- 💬 [GitHub Discussions](https://github.com/xoul-project/xoul/discussions)
