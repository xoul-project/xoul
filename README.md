<p align="center">
  <h1 align="center">[ Xoul ]</h1>
  <p align="center">
    A personal assistant Agent for everyone.<br/>
    With Local LLM and virtualization, your personal data can never be sent externally.<br/>
    Streamline your daily life with Xoul.
  </p>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/local--first-brightgreen?style=flat-square" />
  <img src="https://img.shields.io/badge/open--source-brightgreen?style=flat-square" />
  <img src="https://img.shields.io/badge/privacy-brightgreen?style=flat-square" />
</p>

<p align="center">
  🌐 <a href="README.ko.md">한국어</a> | English
</p>

---

## Why Xoul

| | |
|---|---|
| ⚡ **Practical AI Agent** | Not a chatbot — it manages files, sends emails, browses the web, and runs code. 18 built‑in tools at OS level. |
| 🔒 **VM‑based Security** | All actions run inside a QEMU virtual machine. Your host system stays untouched. |
| 🔄 **Daily Automation** | Turn repetitive tasks (news digest, server checks, email triage) into scheduled workflows. |
| 👥 **Community‑driven** | Import workflows, personas, and code snippets shared by others — or publish your own. |

## Features

- 📅 **Personal Assistant** — Calendar, email, and contacts management (Google integration)
- 🎭 **Personas & Code** — Switch agent roles or run Python snippets from the community hub
- 🔄 **Workflows** — Multi‑step automation templates with scheduling support
- ⚔️ **AI Arena** — Agent playground where AIs discuss topics and play social deduction games
- 🖥️ **Host PC Control** — Limited host interaction (browser launch, file operations)

## System Requirements

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| **CPU** | x86‑64, 8 cores | — |
| **RAM** | 8 GB | 16 GB+ |
| **GPU** | NVIDIA 30‑series, 8 GB VRAM | NVIDIA 40‑series, 16 GB+ VRAM |
| **OS** | Windows 11 (10 experimental) | — |
| **Disk** | 20 GB free | — |

## Installation

### Prerequisites

The setup script installs everything automatically, but these are what it sets up:

- **Python 3.12** — installed via `winget` if not found
- **Ollama** — local LLM inference engine
- **QEMU** — virtual machine for sandboxed agent execution

### Quick Start (Recommended)

1. [Download the release file](http://ec2-15-165-31-212.ap-northeast-2.compute.amazonaws.com/xoul_dist/xoul_rel.zip)
2. Extract the downloaded `xoul_rel.zip`
3. Run `install.bat` inside the extracted folder

`install.bat` automatically handles file placement and runs the setup script.
The interactive setup will then walk you through each step:

### Install from Source (Developers)

```powershell
git clone https://github.com/xoul-project/xoul.git
cd xoul
.\scripts\setup_env.ps1
```

### Step 1 — Language

Choose between Korean and English for all UI and messages.

### Step 2 — LLM Model

Three options:

| Mode | Description |
|------|-------------|
| **Local (recommended)** | Runs on your GPU via Ollama. Models auto‑download. |
| **Commercial API** | Claude, GPT‑5, Gemini, DeepSeek, Grok, Mistral — bring your API key. |
| **External** | Any OpenAI‑compatible endpoint (vLLM, LM Studio, etc.). |

Local models are selected based on your VRAM:

| Model | VRAM |
|-------|------|
| Nemotron‑3‑Nano 4B (Q8) | ~5 GB |
| Nemotron‑3‑Nano 4B (BF16) | ~8 GB |
| GPT‑oss 20B | ~13 GB |
| Nemotron‑Cascade‑2 30B | ~20 GB |

Additionally, **BGE‑M3** (embedding) and **Qwen 2.5 3B** (summarization, CPU‑only) are installed automatically.

### Step 3 — QEMU VM

QEMU is installed via `winget`. The setup tests WHPX hardware acceleration and creates or copies a VM image (~10 GB).

> [!TIP]
> Enable **Hyper‑V** in Windows Features for 3–5× faster VM performance.

### Step 4 — Python Environment

A `.venv` is created with Python 3.12. All dependencies (`openai`, PyQt6, etc.) are installed from `requirements.txt`.

### Step 5 — VM Image

If a pre‑built `xoul.qcow2` is found, it's copied to the VM directory. Otherwise, a fresh image is created from a cloud image (~10–15 min).

### Step 6 — Configuration

Interactive prompts for:

- **User profile** — name, location, agent name
- **Email** — Gmail with App Password (optional)
- **⚠️ Web Search** — [Tavily](https://tavily.com) API key (**recommended**, free tier available). Search quality degrades significantly without it.
- **Telegram / Discord / Slack** — bot token setup (optional)
- **GitHub** — Personal Access Token for repo tools (optional)

### Step 7 — Deploy & Launch

The agent code is deployed to the VM and all services start automatically.

## Usage

After setup, launch with:

```powershell
.\scripts\launcher.ps1        # Start VM + Ollama + Server
.\.venv\Scripts\python desktop\main.py   # Desktop client
```

## Clients

| Client | Description |
|--------|-------------|
| **Desktop** (PyQt6) | Native Windows app with chat UI |
| **Telegram** | Chat with your agent via Telegram bot |
| **Discord** | Mention your bot in any channel |
| **Slack** | Socket Mode integration |
| **Terminal** | CLI interface |

## Project Structure

```
xoul/
├── server.py            # Main agent server
├── assistant_agent.py   # Agent logic & tool orchestration
├── llm_client.py        # LLM provider abstraction
├── vm_manager.py        # QEMU VM lifecycle
├── browser_daemon.py    # Headless Chromium controller
├── desktop/             # PyQt6 desktop client
├── tools/               # 18 built-in tools
├── scripts/             # Setup, deploy, launcher
├── locales/             # i18n (ko, en)
└── services/            # Systemd service files
```

## Reconfiguration

If you need to re-run the setup, execute the following commands in order:

```powershell
.\scripts\setup_env.ps1    # Re-run setup
.\scripts\launcher.ps1     # Start services
```

## License

MIT

## Links

- 🌐 [Website](https://xoul.io)
- 💬 [GitHub Discussions](https://github.com/xoul-project/xoul/discussions)

## ⚡ Performance Optimization — Enable WHPX (Highly Recommended)

QEMU runs in software emulation mode by default, but enabling **WHPX (Windows Hypervisor Platform)** can deliver **3–5× faster VM performance**.

### How to Enable

1. **Open Windows Features**
   - Press `Win + R` → type `optionalfeatures` → Enter
   - Or go to **Settings → Apps → Optional features → More Windows features**
2. **Check the following options**
   - ✅ **Hyper-V**
   - ✅ **Windows Hypervisor Platform**
3. **Restart your PC**

> [!TIP]
> After enabling, re-run `setup_env.ps1` and WHPX acceleration will be automatically detected and applied.

### Enable via PowerShell (One-liner)

```powershell
# Run as Administrator
Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V-All -NoRestart
Enable-WindowsOptionalFeature -Online -FeatureName HypervisorPlatform -NoRestart
Restart-Computer
```
