#!/usr/bin/env python3
"""
Xoul - 만능 개인비서 AI 에이전트

llama.cpp OpenAI 호환 API를 사용합니다.

사용법:
    python assistant_agent.py [--config config.json]

사전 요구사항:
    1. llama.cpp 설치 (setup_env.ps1 실행)
    2. model 다운로드 (setup_env.ps1에서 자동)
"""

import json
import os
import sys
import re
import argparse
import subprocess
import urllib.request
import urllib.error

# 도구 패키지에서 가져오기
from tools import TOOLS, TOOL_DESCRIPTIONS, register_pim_tools, execute_tool
from llm_client import LLMClient
from i18n import t, init_from_config as init_i18n
try:
    from vm_manager import is_vm_running, start_vm, setup_ssh_key
except ImportError:
    # VM 내부 실행 시 불필요
    is_vm_running = lambda: False
    start_vm = lambda: False
    setup_ssh_key = lambda: None


# ─────────────────────────────────────────────
# 시스템 프롬프트
# ─────────────────────────────────────────────

SYSTEM_IDENTITY_TEMPLATE = """\
Reasoning(Thinking) Effort : LOW

You are {name}, a 24/7 personal AI assistant. {response_lang}"""

# 하위 호환: 기본 이름으로 생성된 상수 (server.py import용)
SYSTEM_IDENTITY = SYSTEM_IDENTITY_TEMPLATE.format(name="Xoul", response_lang="Respond in the user's language.")

def get_system_identity(config: dict) -> str:
    """config에서 에이전트 이름을 읽어 시스템 프롬프트 생성"""
    name = config.get("assistant", {}).get("name", "Xoul")
    lang = config.get("assistant", {}).get("language", "ko")
    if lang == "en":
        response_lang = "Always respond in English."
    else:
        response_lang = "Respond in the user's language."
    return SYSTEM_IDENTITY_TEMPLATE.format(name=name, response_lang=response_lang)

USER_INSTRUCTION_TEMPLATE = """\
## Architecture
You run inside an Ubuntu Linux VM (Guest) hosted on the user's Windows PC (Host).

Guest VM: run_command, run_python_code, read_file, write_file, list_files, APIs, email, calendar, web search. All data: /root/xoul/
You CANNOT directly access the Host PC's filesystem or run Host apps. Host and Guest are separate machines.

File Transfer: host_to_vm (Windows→VM), vm_to_host (VM→Windows)
- "save to desktop" / "open" → vm_to_host first, then host_open_app
- User mentions a file on their PC → host_to_vm first

Host PC Control (remote): host_open_url, host_open_app, host_show_notification, host_find_file, host_run_command (PowerShell)

Windows Compatibility: Code for user's PC must be Windows-compatible. No curses/termios/readline/fcntl. Use tkinter/pygame instead.

## Tool-First Principle (MANDATORY — HIGHEST PRIORITY)
**ALWAYS execute a tool FIRST before generating any text response** when the request involves data retrieval, listing, status checking, or any action that a tool can handle.
- NEVER answer from conversation history or memory alone when a tool exists for the task. Always call the tool to get fresh, real data.
- Even if the previous conversation already contains the exact answer, you MUST call the tool again to get fresh data. Previous conversation is reference only — NEVER copy-paste or reformat old results.
- If the user asks "list my workflows" → call list_workflows. Do NOT recite a list from previous conversation.
- If the user asks "what codes do I have" → call list_codes. Do NOT generate a list from memory.
- If the user asks about weather, prices, events → call the appropriate tool. Do NOT answer from cached/old data.
- Even if you "remember" the answer from a prior turn, **call the tool anyway** — data may have changed.
- The ONLY exception: pure conversational responses (greetings, opinions, explanations of concepts) that require no data lookup.
- When in doubt whether to call a tool or respond from memory → **ALWAYS call the tool**.

## Core Rules
1. Act immediately — call tools, never say "you could try..." Just do it.
2. Real-world info (weather, prices, stocks, restaurants) → always web_search first.
3. Personal info shared → reply "Got it, I'll remember that" (auto-saved).
4. **Complete ALL steps** — for multi-step tasks (search → calculate → write_file → send_email), you MUST finish every requested step. If the user asks to save a file AND send email, do both. Never stop mid-chain even if you've done many tool calls already.
5. Never expose tool names (schedule_task, run_command...) in responses.
6. Minimize searching, maximize doing — once you have data, immediately act (calculate, write, email).
7. **Never give up mid-task** — if search results are incomplete, work with what you have. Produce the best result possible and complete all remaining steps (save file, send email, etc.). Do NOT stop to ask the user for more info when you can reasonably proceed.
8. Sequential calls — if 3 events needed, call create_event 3 times. Don't just say "done".
9. **Direct Execution** — When the user explicitly says to run/execute a specific workflow, persona, or code BY NAME (e.g., "Exchange API Setup 실행해줘", "포트폴리오 워크플로우 실행", "영어선생님 페르소나 켜줘"), call `run_stored_code`, `run_workflow`, or `activate_persona` DIRECTLY with the name. Do NOT call `list_codes`, `list_workflows`, or `list_personas` first — go straight to execution.
10. After ALL tool calls, MUST send a final summary message. Never end silently.

## Anti-Hallucination (Top Priority)
- NEVER fabricate URLs — only use URLs from search results verbatim.
- NEVER invent entities — if search doesn't find it, say so honestly.
- NEVER answer real-time queries from memory — always web_search.
- NEVER recommend without searching first.
- NEVER fabricate tool results from conversation history — if the user asks for a list, status, or any data, you MUST call the tool to get current data. Previous conversation is NOT a valid data source.
- NEVER say "based on what we discussed earlier" or "as I mentioned before" as a substitute for calling a tool. Call the tool and use its actual output.

## Workflows
A **Workflow** is a saved list of steps (prompts and/or code) that are executed **sequentially and automatically** by the server.

### Workflow step structure
Each workflow is stored as a list of steps. There are two step types:

**Prompt step** — tells the LLM what to do at that point:
  {{"type": "prompt", "content": "오늘 주요 뉴스를 검색해줘"}}

**Code step** — runs a saved code snippet from Code Store:
  {{"type": "code", "code_name": "코드이름"}}

When creating a workflow, pass `prompts` as a JSON array of steps:
  '[{{"type":"prompt","content":"1단계 지시"}},{{"type":"prompt","content":"2단계 지시"}}]'
Simple newline-separated text is also accepted (each line becomes a prompt step).


### How Workflow execution works
1. User asks to run a workflow → you call `run_workflow(name="X")` **ONCE**
2. The server takes over: it runs each step one by one automatically, feeding each step's result into the next
3. `run_workflow` returns a confirmation message like "⚡ Workflow execution is in progress" — you output this as-is
4. **CRITICAL: You must NEVER call `run_workflow` again after the first call.** The server handles all subsequent steps internally. Calling it again will trigger a re-entry guard error.
5. You do NOT need to "execute each step yourself" — the server does it automatically.

### Workflow tools
- `run_workflow(name)` — **START** a workflow. Call exactly once. Do NOT re-call.
- `list_workflows()` — List all saved workflows (returns a table)
- `create_workflow(name, prompts, schedule?)` — Create a new workflow
- `view_workflow(name)` — Inspect a workflow's steps WITHOUT running it
- `update_workflow(name, ...)` — Edit a workflow
- `delete_workflow(name)` — Delete a workflow

### When to create a workflow
- Recurring/scheduled tasks: "매일 아침 뉴스 요약" → `create_workflow(schedule="daily 09:00")`
- Multi-step automations the user wants to reuse
- Do NOT use workflow for one-time tasks — just do them directly.

### Workflow vs other tools
- Calendar/event (appointment, meeting) → `create_event`, NOT workflow
- Immediate one-time action → do it directly, NOT workflow
- Repeatable automated procedure → `create_workflow`

## Personas
A **Persona** is a saved AI role/character that replaces the system prompt when activated.

- Each persona has: name, system prompt (role definition), description, optional background image.
- When activated (`activate_persona`), the AI's personality/behavior changes to match the persona's prompt.
- The user can exit persona mode by saying `/done` or "페르소나 종료".
- Personas are stored in the DB and can be shared to Xoul Store.

### Persona tools
- `list_personas()` — List all saved personas (returns table with Desktop UI data)
- `activate_persona(name)` — Switch AI to a persona's role/personality
- `create_persona(name, prompt, description?)` — Create a new persona
- `update_persona(name, prompt?, description?, new_name?)` — Edit a persona
- `delete_persona(name)` — Delete a persona

### Persona vs Workflow
- Persona = **changes WHO the AI is** (role/personality)
- Workflow = **automates WHAT the AI does** (task sequence)

## Code Store
A **Code Store** is a collection of saved Python code snippets that can be executed on demand or used as workflow steps.

- Each code has: name, Python code (with optional `def run(...)` entry point), description, category, parameters.
- Parameters can have defaults. Required params (no default) are marked with `*` in list_codes output.
- Codes are executed via `run_stored_code(name, params)`. If the code has `def run(...)`, it's called as a function with params as keyword args.
- Codes can be referenced in Workflow steps as `{{"type": "code", "code_name": "코드이름"}}`.
- Codes can be shared to/imported from Xoul Store (GitHub-based community repository).

### Code Store tools
- `list_codes()` — List all saved codes (returns table with params and usage info)
- `run_stored_code(name, params?, timeout?)` — Execute a saved code. Pass conversation-relevant values in params.
- `create_code(name, code, description?, category?, params?)` — Save a new code snippet
- `delete_code(name)` — Delete a saved code (blocked if used by workflows)
- `list_running_codes()` — List currently running codes (long-running/background)
- `stop_running_code(name?)` — Stop a running code
- `share_to_store(share_type, name, category?)` — Share code/workflow/persona to Xoul Store


Memory: Auto-injected user info. Just say "I'll remember that". recall to search, forget to delete.

## Tool Priority
- Recommendations/real-time data/URLs → web_search
- Math → calculate. Combine related calculations into a single expression instead of splitting into many calls.
  - Bad: calculate("50*216500") → calculate("0.1*96000000") → calculate("10825000+9600000") → ... (19 calls)
  - Good: calculate("50*216500 + 0.1*96000000 + 3000*1439.8") → one call for the total
  - If too complex for one expression, use run_python_code instead.
- Code/charts/data → run_python_code
- Contacts → find_contact (NOT web_search)
- Email → send_email
- Scheduled jobs → create_workflow (with schedule parameter)
- Calendar → create_event
- Never use requests/bs4/urllib in run_python_code — use web_search instead.

## Stored Memories
When memories are injected (height, salary, etc.), use them directly — don't ask again.
Combine search + memory: if price from search + salary from memory → immediately calculate.

## Multi-Step Task Flow
For compound requests (research → write → email):
1. Research: web_search 2-3 times with varied keywords if needed (there are no neccessary information).
2. Compose: Synthesize info into requested format (don't just copy search results).
3. Save: write_file with complete content. No "..." or "same as above".
4. Send: send_email with summary body + file in attachments if needed.
⚠ Prohibited: searching once and stopping, saying "saved" without calling write_file, empty attachments.

## Schedule Requests
1. Check Memory for recurring schedules. 2. Also recall() + list_events(). 3. If Memory has data even when list_events is empty, include it.

## Scheduled Workflows
create_workflow: schedule = "daily 09:00"/"weekday 08:00"/"every 5min"/"weekly"/"monthly". Minimum interval: 5 minutes.

## VM↔Host File Rules
VM paths (/root/...) are NOT accessible from user's PC.
- User wants to view file → vm_to_host first. User sends file → must be in share/, then host_to_vm.

## Analysis & Estimation
Search for concrete data first → use only numbers from results → cite sources → calculate step by step → admit when unavailable.

## Output
{output_language_rule}
- Tool fails → try alternative before giving up.
- Multi-step → show progress. Format calculations clearly.

{response_language_instruction}

{tools_block}
"""

# 하위 호환을 위한 alias
SYSTEM_PROMPT_TEMPLATE = USER_INSTRUCTION_TEMPLATE



# OpenAI-compatible tool definitions (for native function calling)
OPENAI_TOOLS = [
    {"type": "function", "function": {"name": "web_search", "description": "Search the web and auto-fetch the first result's full text. No separate fetch_url call needed.", "parameters": {"type": "object", "properties": {"query": {"type": "string", "description": "Search query"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "read_file", "description": "Read file contents on the VM", "parameters": {"type": "object", "properties": {"path": {"type": "string", "description": "File path"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "write_file", "description": "Write content to a file on the VM", "parameters": {"type": "object", "properties": {"path": {"type": "string", "description": "File path"}, "content": {"type": "string", "description": "File content"}}, "required": ["path", "content"]}}},
    {"type": "function", "function": {"name": "list_files", "description": "List files in a directory on the VM", "parameters": {"type": "object", "properties": {"path": {"type": "string", "description": "Directory path"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "run_command", "description": "Execute a Linux shell command on the VM", "parameters": {"type": "object", "properties": {"command": {"type": "string", "description": "Command to run"}}, "required": ["command"]}}},
    {"type": "function", "function": {"name": "get_datetime", "description": "Get the current date and time", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "calculate", "description": "Perform a math calculation", "parameters": {"type": "object", "properties": {"expression": {"type": "string", "description": "Math expression"}}, "required": ["expression"]}}},
    {"type": "function", "function": {"name": "run_python_code", "description": "Execute Python code on the VM", "parameters": {"type": "object", "properties": {"code": {"type": "string", "description": "Python code to run"}, "description": {"type": "string", "description": "Description of what the code does"}, "timeout": {"type": "string", "description": "Timeout in seconds (default 30, max 600)"}}, "required": ["code"]}}},
    {"type": "function", "function": {"name": "run_stored_code", "description": "Execute an imported code from Code Store. Do not skip based on conversation memory — if user asks to run code, call this tool once. IMPORTANT: If the code has required params (marked with * in list_codes output), ASK the user for those values before calling.", "parameters": {"type": "object", "properties": {"name": {"type": "string", "description": "Code name to find in the codes DB"}, "params": {"type": "string", "description": "JSON string of parameters, e.g. {\"game_id\": \"abc\", \"agent_name\": \"Bot\"}"}, "timeout": {"type": "string", "description": "Timeout in seconds (default 30, max 600)"}}, "required": ["name"]}}},
    {"type": "function", "function": {"name": "list_codes", "description": "List all imported codes from Code Store. MANDATORY: Always call this tool when user asks to see codes. NEVER generate a code list from memory.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "delete_code", "description": "Delete an imported code from Code Store. Always call this tool — do NOT assume availability from memory.", "parameters": {"type": "object", "properties": {"name": {"type": "string", "description": "Code name to delete"}}, "required": ["name"]}}},
    {"type": "function", "function": {"name": "create_code", "description": "Create a new code snippet and save it to Code Store. The code will be executable via run_stored_code.", "parameters": {"type": "object", "properties": {"name": {"type": "string", "description": "Code name"}, "code": {"type": "string", "description": "Python code to save"}, "description": {"type": "string", "description": "Short description"}, "category": {"type": "string", "description": "Category (e.g. Other, Finance, System, Data & Text)"}, "params": {"type": "string", "description": "JSON array of param definitions, e.g. [{\"name\":\"text\",\"type\":\"str\",\"desc\":\"input\"}]"}}, "required": ["name", "code"]}}},
    {"type": "function", "function": {"name": "share_to_store", "description": "Share a local code/workflow/persona to the Xoul Store by creating a GitHub PR. Reads item data from local DB and submits to Store.", "parameters": {"type": "object", "properties": {"share_type": {"type": "string", "description": "Type: code, workflow, or persona"}, "name": {"type": "string", "description": "Item name to share"}, "category": {"type": "string", "description": "Store category (default: Other)"}}, "required": ["share_type", "name"]}}},
    {"type": "function", "function": {"name": "stop_running_code", "description": "Stop a currently running code (e.g. arena agent). If only one code is running, name is optional. Supports partial/fuzzy name matching.", "parameters": {"type": "object", "properties": {"name": {"type": "string", "description": "Code name to stop (partial match). Leave empty to auto-select if only 1 running."}}, "required": []}}},
    {"type": "function", "function": {"name": "list_running_codes", "description": "List all currently running codes with their PIDs and start times.", "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "pip_install", "description": "Install a Python package. Use this when you get a ModuleNotFoundError, then re-run your code.", "parameters": {"type": "object", "properties": {"package": {"type": "string", "description": "Package name (e.g., matplotlib, numpy, pandas)"}}, "required": ["package"]}}},
    {"type": "function", "function": {"name": "host_list_files", "description": "List files in the user's PC share/ folder", "parameters": {"type": "object", "properties": {"path": {"type": "string", "description": "Subdirectory path (default: share/ root)"}}, "required": []}}},
    {"type": "function", "function": {"name": "host_to_vm", "description": "Copy a file from the user's PC share/ folder to VM /root/share/. Supports partial filename matching and deduplication.", "parameters": {"type": "object", "properties": {"filename": {"type": "string", "description": "Filename to copy (partial match supported)"}}, "required": ["filename"]}}},
    {"type": "function", "function": {"name": "vm_to_host", "description": "Copy a file from the VM to the user's PC share/ folder. Auto-searches /root/share/, /root/workspace/ etc. if only a filename is given.", "parameters": {"type": "object", "properties": {"vm_path": {"type": "string", "description": "VM file path or filename"}, "filename": {"type": "string", "description": "Save filename"}}, "required": ["vm_path"]}}},
    {"type": "function", "function": {"name": "create_tool", "description": "Create and register a new custom tool", "parameters": {"type": "object", "properties": {"name": {"type": "string"}, "description": {"type": "string"}, "command_template": {"type": "string"}, "packages": {"type": "string"}, "parameters": {"type": "string"}}, "required": ["name", "description", "command_template"]}}},
    {"type": "function", "function": {"name": "list_custom_tools", "description": "List all registered custom tools", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "remove_tool", "description": "Delete a custom tool", "parameters": {"type": "object", "properties": {"name": {"type": "string", "description": "Tool name"}}, "required": ["name"]}}},
    {"type": "function", "function": {"name": "recall", "description": "Search stored memories using semantic search. Supports natural language queries.", "parameters": {"type": "object", "properties": {"query": {"type": "string", "description": "Search query (natural language)"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "forget", "description": "Delete a stored memory. Use recall first to find the exact key, then delete.", "parameters": {"type": "object", "properties": {"key": {"type": "string", "description": "Memory key to delete (find via recall)"}}, "required": ["key"]}}},
    {"type": "function", "function": {"name": "evolve_skill", "description": "Save a successful task as a reusable skill. Call after completing a task successfully.", "parameters": {"type": "object", "properties": {"name": {"type": "string", "description": "Skill name (lowercase_with_underscores)"}, "description": {"type": "string", "description": "Skill description"}, "trigger_keywords": {"type": "string", "description": "Trigger keywords (comma-separated)"}, "method": {"type": "string", "description": "Detailed method for reproducing the task"}, "packages": {"type": "string", "description": "Required packages (optional)"}, "script": {"type": "string", "description": "Script code (optional)"}}, "required": ["name", "description", "trigger_keywords", "method"]}}},
    {"type": "function", "function": {"name": "find_skill", "description": "Search for an existing skill that matches the request. Call before starting a task.", "parameters": {"type": "object", "properties": {"query": {"type": "string", "description": "Search query"}}, "required": ["query"]}}},
    # Weather tool
    {"type": "function", "function": {"name": "weather", "description": "Get weather info: current conditions (temperature, feels-like, humidity, wind) and up to 7-day forecast (high/low temps, precipitation probability, sunrise/sunset). Use for queries about weather, temperature, rain, hot/cold.", "parameters": {"type": "object", "properties": {"location": {"type": "string", "description": "City name (Korean or English). E.g., 'Seoul', 'Busan', 'Tokyo', 'New York'. Defaults to Seoul if not specified."}, "days": {"type": "integer", "description": "Forecast days (1-7, default 3)"}}, "required": ["location"]}}},
    # Host PC tools (executed by desktop app)
    {"type": "function", "function": {"name": "host_open_url", "description": "Open a URL in the user's default browser on their PC", "parameters": {"type": "object", "properties": {"url": {"type": "string", "description": "URL to open (https://...)"}}, "required": ["url"]}}},
    {"type": "function", "function": {"name": "host_find_file", "description": "Search for files on the user's PC (Desktop/Documents/Downloads)", "parameters": {"type": "object", "properties": {"query": {"type": "string", "description": "Filename to search for"}, "directory": {"type": "string", "description": "Search directory (optional)"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "host_show_notification", "description": "Show a Windows desktop popup notification on user's PC (system tray toast). This is a LOCAL desktop alert, NOT a messenger message. For Telegram/Discord/Slack, use send_notification instead.", "parameters": {"type": "object", "properties": {"title": {"type": "string", "description": "Notification title"}, "message": {"type": "string", "description": "Notification body"}}, "required": ["message"]}}},
    {"type": "function", "function": {"name": "host_open_app", "description": "Launch a desktop app on the user's Windows PC (e.g., Chrome, CapCut, KakaoTalk). Note: Do NOT try to launch apps via run_command!", "parameters": {"type": "object", "properties": {"app_name": {"type": "string", "description": "Application name in English (e.g., CapCut, Chrome, KakaoTalk)"}}, "required": ["app_name"]}}},
    {"type": "function", "function": {"name": "host_organize_files", "description": "Organize/move files on the user's PC", "parameters": {"type": "object", "properties": {"source": {"type": "string", "description": "Source folder"}, "destination": {"type": "string", "description": "Destination folder"}, "pattern": {"type": "string", "description": "File pattern (e.g., *.pdf)"}}, "required": ["source", "destination"]}}},
    {"type": "function", "function": {"name": "host_run_command", "description": "Execute a PowerShell command on the user's Windows PC. Use this instead of run_command for Windows commands.", "parameters": {"type": "object", "properties": {"command": {"type": "string", "description": "PowerShell command"}}, "required": ["command"]}}},
    # Git tools
    {"type": "function", "function": {"name": "git_clone", "description": "Clone a GitHub repository to the VM. Requires GitHub token in config.", "parameters": {"type": "object", "properties": {"repo_url": {"type": "string", "description": "Repository URL (e.g., https://github.com/user/repo.git)"}, "directory": {"type": "string", "description": "Clone destination path (default: /root/workspace/<repo_name>)"}}, "required": ["repo_url"]}}},
    {"type": "function", "function": {"name": "git_status", "description": "Check git repository status, branch, and recent commits.", "parameters": {"type": "object", "properties": {"directory": {"type": "string", "description": "Repository path (default: /root/workspace)"}}, "required": []}}},
    {"type": "function", "function": {"name": "git_commit", "description": "Stage and commit changes in a git repository.", "parameters": {"type": "object", "properties": {"message": {"type": "string", "description": "Commit message"}, "directory": {"type": "string", "description": "Repository path"}, "files": {"type": "string", "description": "Files to stage (default: . for all)"}}, "required": ["message"]}}},
    {"type": "function", "function": {"name": "git_push", "description": "Push commits to remote repository. Requires GitHub token in config.", "parameters": {"type": "object", "properties": {"directory": {"type": "string", "description": "Repository path"}, "branch": {"type": "string", "description": "Branch name (default: current branch)"}}, "required": []}}},
    # Workflow tools
    {"type": "function", "function": {"name": "create_workflow", "description": "Create a new workflow (multi-step task automation). Use when user says '워크플로우 만들어', 'schedule this'. Do NOT confuse with run_workflow (execute) or send_email (email).", "parameters": {"type": "object", "properties": {"name": {"type": "string", "description": "Workflow name"}, "prompts": {"type": "string", "description": "Workflow steps as a JSON array string. Each step: {\"type\":\"prompt\",\"content\":\"instruction\"} or {\"type\":\"code\",\"code_name\":\"savedCodeName\"}. Example: '[{\"type\":\"prompt\",\"content\":\"오늘 날씨 검색해줘\"},{\"type\":\"prompt\",\"content\":\"결과를 파일로 저장해줘\"}]'. Simple text (newline-separated) also accepted."}, "description": {"type": "string", "description": "Description"}, "hint_tools": {"type": "string", "description": "Comma-separated tool names"}, "schedule": {"type": "string", "description": "Schedule (e.g., daily 09:00, weekday 08:00, every 30min, weekly, monthly)"}}, "required": ["name", "prompts"]}}},
    {"type": "function", "function": {"name": "run_workflow", "description": "EXECUTE a saved workflow by name. Use when user says '워크플로우 실행해줘', 'run workflow X'. Call ONCE — do NOT also call list_workflows or view_workflow. This RUNS the workflow steps, not just viewing them. Do NOT confuse with send_email or send_notification.", "parameters": {"type": "object", "properties": {"name": {"type": "string", "description": "Workflow name"}}, "required": ["name"]}}},
    {"type": "function", "function": {"name": "list_workflows", "description": "LIST all saved workflows (names, descriptions, schedules). Use when user says '워크플로우 목록', '워크플로우 보여줘', 'show workflows'. Returns a table of all workflows. Do NOT call alongside run_workflow. NEVER generate list from memory.", "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "view_workflow", "description": "VIEW/INSPECT the internal steps of a specific workflow WITHOUT executing it. Use when user says '워크플로우 내용 보여줘', 'what steps does X have'. To actually EXECUTE/RUN, use run_workflow instead.", "parameters": {"type": "object", "properties": {"name": {"type": "string", "description": "Workflow name"}}, "required": ["name"]}}},
    {"type": "function", "function": {"name": "update_workflow", "description": "Update an existing workflow", "parameters": {"type": "object", "properties": {"name": {"type": "string", "description": "Workflow name"}, "prompts": {"type": "string", "description": "New steps as JSON array or newline-separated text. Same format as create_workflow prompts."}, "description": {"type": "string", "description": "New description"}, "schedule": {"type": "string", "description": "New schedule (e.g., daily 09:00, weekday 08:00, every 30min)"}}, "required": ["name"]}}},
    {"type": "function", "function": {"name": "delete_workflow", "description": "Delete a workflow. Always call this tool — it uses fuzzy matching so pass the exact name the user said without adding/removing words.", "parameters": {"type": "object", "properties": {"name": {"type": "string", "description": "Workflow name"}}, "required": ["name"]}}},
    # Persona tools
    {"type": "function", "function": {"name": "list_personas", "description": "List all saved personas. MANDATORY: Always call this tool when user asks to see persona list. NEVER generate a list from memory — the tool output contains special UI data for Desktop rendering.", "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "activate_persona", "description": "Activate a persona to change AI conversation mode/personality. Always call this tool — do NOT assume from memory.", "parameters": {"type": "object", "properties": {"name": {"type": "string", "description": "Persona name"}}, "required": ["name"]}}},
    {"type": "function", "function": {"name": "create_persona", "description": "Create a new persona with custom system prompt", "parameters": {"type": "object", "properties": {"name": {"type": "string", "description": "Persona name"}, "prompt": {"type": "string", "description": "System prompt for the persona"}, "description": {"type": "string", "description": "Short description"}}, "required": ["name", "prompt"]}}},
    {"type": "function", "function": {"name": "update_persona", "description": "Update an existing persona's prompt, description, or name", "parameters": {"type": "object", "properties": {"name": {"type": "string", "description": "Current persona name"}, "prompt": {"type": "string", "description": "New system prompt"}, "description": {"type": "string", "description": "New description"}, "new_name": {"type": "string", "description": "New name"}}, "required": ["name"]}}},
    {"type": "function", "function": {"name": "delete_persona", "description": "Delete a persona. Always call this tool — do NOT assume availability from memory.", "parameters": {"type": "object", "properties": {"name": {"type": "string", "description": "Persona name"}}, "required": ["name"]}}},
    # Greeting tool
    {"type": "function", "function": {"name": "greeting", "description": "Generate a context-aware greeting based on current time/date. Called automatically when user first connects to the desktop app. Returns time context for generating a warm, situational greeting.", "parameters": {"type": "object", "properties": {"user_name": {"type": "string", "description": "User's name (optional)"}}, "required": []}}},
]

PIM_OPENAI_TOOLS = [
    # Calendar/Event tools
    {"type": "function", "function": {"name": "create_event", "description": "Create a calendar event", "parameters": {"type": "object", "properties": {"title": {"type": "string", "description": "Event title"}, "date": {"type": "string", "description": "Date (YYYY-MM-DD)"}, "time": {"type": "string", "description": "Time (HH:MM, omit for all-day)"}, "duration": {"type": "string", "description": "Duration in minutes (default 60)"}, "description": {"type": "string", "description": "Description"}}, "required": ["title", "date"]}}},
    {"type": "function", "function": {"name": "list_events", "description": "List calendar events. ⚠️ Note: Even if calendar results are empty, check long-term Memory for recurring events and include them in your answer.", "parameters": {"type": "object", "properties": {"date": {"type": "string", "description": "Date (YYYY-MM-DD, default: today)"}, "days": {"type": "integer", "description": "Number of days to query (default: 1)"}}, "required": []}}},
    {"type": "function", "function": {"name": "delete_event", "description": "Delete a calendar event", "parameters": {"type": "object", "properties": {"event_id": {"type": "string", "description": "Event ID"}}, "required": ["event_id"]}}},
    {"type": "function", "function": {"name": "update_event", "description": "Update a calendar event", "parameters": {"type": "object", "properties": {"event_id": {"type": "string", "description": "Event ID"}, "title": {"type": "string", "description": "New title"}, "date": {"type": "string", "description": "New date"}, "time": {"type": "string", "description": "New time"}, "description": {"type": "string", "description": "New description"}}, "required": ["event_id"]}}},
    # Contact tools
    {"type": "function", "function": {"name": "add_contact", "description": "Add a contact", "parameters": {"type": "object", "properties": {"name": {"type": "string", "description": "Name"}, "phone": {"type": "string", "description": "Phone number"}, "email": {"type": "string", "description": "Email"}, "memo": {"type": "string", "description": "Notes"}}, "required": ["name"]}}},
    {"type": "function", "function": {"name": "find_contact", "description": "Search the user's contact database. Always use this for 'find contact', 'phone number of X' requests — NOT web_search!", "parameters": {"type": "object", "properties": {"query": {"type": "string", "description": "Search query"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "list_contacts", "description": "List all contacts", "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "delete_contact", "description": "Delete a contact", "parameters": {"type": "object", "properties": {"name": {"type": "string", "description": "Name to delete"}}, "required": ["name"]}}},
]

EMAIL_OPENAI_TOOLS = [
    {"type": "function", "function": {"name": "send_email", "description": "Send an EMAIL (SMTP) to an email address. Use when user says '메일로 보내줘', '이메일 보내줘', 'email this'. This is NOT a messenger — for Telegram/Discord/Slack use send_notification instead. Requires recipient email address (to), subject, body.", "parameters": {"type": "object", "properties": {"to": {"type": "string", "description": "Recipient email address (e.g., user@example.com)"}, "subject": {"type": "string", "description": "Email subject line"}, "body": {"type": "string", "description": "Email body text"}, "attachments": {"type": "array", "items": {"type": "string"}, "description": "List of VM file paths to attach (e.g., [\"/root/.xoul/workspace/report.md\"])"}}, "required": ["to", "subject", "body"]}}},
    {"type": "function", "function": {"name": "list_emails", "description": "Search or list emails", "parameters": {"type": "object", "properties": {"query": {"type": "string", "description": "IMAP search query (default: UNSEEN)"}, "max_results": {"type": "integer", "description": "Maximum results"}}, "required": []}}},
    {"type": "function", "function": {"name": "read_email", "description": "Read the full body of an email", "parameters": {"type": "object", "properties": {"email_id": {"type": "string", "description": "Email ID"}}, "required": ["email_id"]}}},
]




# ─────────────────────────────────────────────
# LLM 호출 (LLMClient 추상화 사용)
# ─────────────────────────────────────────────

# 전역 LLM 클라이언트 (run_agent에서 초기화)
_llm_client = None

def call_llm(messages: list, config: dict, tools: list = None) -> dict:
    """LLM 호출 — LLMClient를 통해 제공자에 무관하게 동작"""
    global _llm_client
    if _llm_client is None:
        _llm_client = LLMClient(config)
    return _llm_client.chat(messages, tools=tools)


def extract_tool_call(text: str):
    """LLM 응답에서 도구 호출 추출 (다양한 형식 지원)"""
    from tools import TOOLS
    
    # 전처리
    cleaned = text
    
    # 1차: <tool_call> 태그로 감싸진 JSON
    match = re.search(r'<tool_call>\s*(\{.*)', cleaned, re.DOTALL)
    if match:
        before_tool = text[:text.find("<tool_call>")].strip()
        json_str = match.group(1).strip()
        json_str = re.sub(r'</tool_call>.*', '', json_str).strip()
        try:
            tool_data = json.loads(json_str)
            if "name" in tool_data:
                return tool_data, before_tool
        except json.JSONDecodeError:
            # JSON이 깨졌으면 name과 arguments를 따로 추출
            name_match = re.search(r'"name"\s*:\s*"([^"]+)"', json_str)
            if name_match:
                tool_name = name_match.group(1)
                args = _extract_args_from_text(json_str)
                return {"name": tool_name, "arguments": args}, before_tool
    
    # 2차: 태그 없이 {"name": ..., "arguments": ...} JSON
    match = re.search(r'(\{[^{}]*"name"\s*:\s*"[^"]+"\s*,\s*"arguments"\s*:\s*\{.*?\}[^{}]*\})', cleaned, re.DOTALL)
    if match:
        json_str = match.group(1).strip()
        before_tool = text[:text.find(match.group(1))].strip()
        try:
            tool_data = json.loads(json_str)
            if "name" in tool_data and "arguments" in tool_data:
                return tool_data, before_tool
        except json.JSONDecodeError:
            pass
    
    # 3차: 알려진 도구 이름이 text에 있는지 찾기
    tool_names = list(TOOLS.keys())
    for tool_name in tool_names:
        # <tool_call>tool_name 또는 tool_name({ 패턴
        patterns = [
            rf'<tool_call>\s*{tool_name}',
            rf'\b{tool_name}\s*\(',
            rf'"name"\s*:\s*"{tool_name}"',
        ]
        for pattern in patterns:
            m = re.search(pattern, cleaned)
            if m:
                before_tool = text[:m.start()].strip()
                rest = cleaned[m.end():]
                args = _extract_args_from_text(rest)
                return {"name": tool_name, "arguments": args}, before_tool
    
    return None, text


def _extract_args_from_text(text: str) -> dict:
    """text에서 도구 인자 추출 시도"""
    # JSON 객체 찾기
    json_match = re.search(r'\{[^{}]+\}', text)
    if json_match:
        try:
            return json.loads(json_match.group())
        except json.JSONDecodeError:
            pass
    
    # key=value 또는 key: value 패턴
    args = {}
    for m in re.finditer(r'"(\w+)"\s*:\s*"([^"]*)"', text):
        args[m.group(1)] = m.group(2)
    
    return args



# ─────────────────────────────────────────────
# 메인 에이전트 루프
# ─────────────────────────────────────────────

def load_config(config_path: str) -> dict:
    with open(config_path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def print_banner(config: dict):
    name = config.get("assistant", {}).get("name", "Xoul")
    llm_cfg = config.get("llm", {})
    providers = llm_cfg.get("providers", {})
    provider = llm_cfg.get("provider", "local")
    p = providers.get(provider, {})
    model = p.get("model_name", llm_cfg.get("model_name", "?"))
    server = p.get("base_url", llm_cfg.get("model_server", "?"))
    print()
    print("╔══════════════════════════════════════════╗")
    print(f"║   {t('agent.banner_title', name=name):<39}║")
    print("╠══════════════════════════════════════════╣")
    print(f"║  {t('agent.banner_model', model=model):<39}║")
    print(f"║  {t('agent.banner_server', server=server):<39}║")
    print("╠══════════════════════════════════════════╣")
    print(f"║  {t('agent.banner_exit'):<39}║")
    print(f"║  {t('agent.banner_help'):<39}║")
    print("╚══════════════════════════════════════════╝")
    print()


def print_help():
    print()
    print(f"  {t('agent.help_title')}")
    print("  ─────────────────────────────────")
    print(f"  {t('agent.help_intro')}")
    print()
    print(f"  {t('agent.help_examples')}")
    print(f"  {t('agent.help_ex1')}")
    print(f"  {t('agent.help_ex2')}")
    print(f"  {t('agent.help_ex3')}")
    print(f"  {t('agent.help_ex4')}")
    print(f"  {t('agent.help_ex5')}")
    print(f"  {t('agent.help_ex6')}")
    print()
    print(f"  {t('agent.help_commands')}")
    print(f"  {t('agent.help_cmd_exit')}")
    print(f"  {t('agent.help_cmd_help')}")
    print(f"  {t('agent.help_cmd_clear')}")
    print(f"  {t('agent.help_cmd_reset')}")
    print()


def run_agent(config: dict):
    global _llm_client
    name = config.get("assistant", {}).get("name", "Xoul")

    # Initialize i18n
    init_i18n(config)
    # LLM client init
    print(f"  🔧 Initializing {name}...")
    _llm_client = LLMClient(config)
    if _llm_client.check_connection():
        print(f"  ✅ LLM connected: {_llm_client.get_provider_info()}")
    else:
        print(f"  ⚠ Cannot connect to LLM server.")
        print(f"     Please run python start_llm_server.py first.")

    print(f"  ✅ Ubuntu VM: {'Running' if is_vm_running() else '⛔ Offline'}")

    # PIM tool registration
    register_pim_tools()
    OPENAI_TOOLS.extend(PIM_OPENAI_TOOLS)
    if config.get("email", {}).get("enabled", False):
        OPENAI_TOOLS.extend(EMAIL_OPENAI_TOOLS)
        print(f"  ✅ Email tools loaded")

    # install_service tool
    OPENAI_TOOLS.append({"type": "function", "function": {"name": "install_service", "description": "Create and register a systemd service/timer", "parameters": {"type": "object", "properties": {"name": {"type": "string", "description": "Service name"}, "description": {"type": "string", "description": "Description"}, "command": {"type": "string", "description": "Command to execute"}, "schedule": {"type": "string", "description": "Repeat schedule (cron format, optional)"}, "oneshot": {"type": "boolean", "description": "Exit after execution"}}, "required": ["name", "description", "command"]}}})

    print(f"  ✅ Tools loaded: {len(TOOLS)}")

    print_banner(config)

    # Build system prompt with correct placeholders
    lang = config.get("assistant", {}).get("language", "ko")
    if lang == "en":
        response_language_instruction = "## Response Language\nAlways respond in English regardless of the user's input language."
        output_language_rule = "- Always respond in English. Include units ($, %, °C)."
    else:
        response_language_instruction = "## 응답 언어\n한국어로 답하세요."
        output_language_rule = "- Same language as user (Korean→Korean). Include units (원, %, °C)."

    user_instruction = USER_INSTRUCTION_TEMPLATE.format(
        tools_block="",
        response_language_instruction=response_language_instruction,
        output_language_rule=output_language_rule,
    )
    system_identity = get_system_identity(config)
    messages = [
        {"role": "system", "content": system_identity},
        {"role": "user", "content": user_instruction},
    ]
    MAX_TOOL_CALLS = 30

    while True:
        try:
            user_input = input(f"🤖 {name} > ").strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\n\n  {t('agent.goodbye', name=name)}")
            break

        if not user_input:
            continue

        if user_input.lower() in ("exit", "quit", "종료"):
            print(f"\n  {t('agent.goodbye', name=name)}")
            break
        elif user_input.lower() in ("help", "도움말"):
            print_help()
            continue
        elif user_input.lower() in ("clear", "cls"):
            os.system("cls" if os.name == "nt" else "clear")
            print_banner(config)
            continue
        elif user_input.lower() in ("reset", "초기화"):
            messages = [
                {"role": "system", "content": system_identity},
                {"role": "user", "content": user_instruction},
            ]
            print(f"  {t('agent.reset_done')}")
            continue

        # 이전 턴 구분 메시지 제거 후 최신만 유지
        _SEP = "--- NEW REQUEST ---"
        messages[:] = [m for m in messages if not (m.get("role") == "system" and _SEP in m.get("content", ""))]
        messages.append({"role": "user", "content": "--- NEW REQUEST ---\nBelow is a new user request. You may reference previous conversation for context, but do NOT repeat or continue tool calls from previous turns. Only call tools that are directly needed for THIS request."})
        messages.append({"role": "user", "content": user_input})

        # 시맨틱 Memory: 관련 기억 자동 주입
        try:
            from tools.memory_tools import auto_retrieve
            mem_ctx = auto_retrieve(user_input)
            if mem_ctx:
                messages.insert(-1, {"role": "user", "content": mem_ctx})
                print(f"  {t('agent.memory_found')}")
        except Exception:
            pass

        for attempt in range(MAX_TOOL_CALLS + 1):
            msg = call_llm(messages, config, tools=OPENAI_TOOLS)
            content = msg.get("content", "")
            tool_calls = msg.get("tool_calls")

            if content.startswith("[LLM"):
                print(f"\n  ❌ {content}")
                print(f"  💡 Please check if the LLM server is running.\n")
                messages.pop()
                break

            # native tool_calls 처리 (OpenAI API 호환 model)
            if tool_calls and attempt < MAX_TOOL_CALLS:
                tc = tool_calls[0]  # 첫 번째 tool call
                tool_name = tc["function"]["name"]
                try:
                    tool_args = json.loads(tc["function"]["arguments"]) if isinstance(tc["function"]["arguments"], str) else tc["function"]["arguments"]
                except (json.JSONDecodeError, TypeError):
                    tool_args = {}
                call_id = tc.get("id", f"call_{attempt}")

                if content:
                    print(f"\n  {content}")
                print(f"  🔧 [{tool_name}] running...")

                tool_result = execute_tool(tool_name, tool_args)
                print(f"  📋 Result: {tool_result[:200]}{'...' if len(tool_result) > 200 else ''}")

                # 히스토리: assistant tool_call + tool result
                messages.append({"role": "assistant", "content": content, "tool_calls": tool_calls})
                short_result = tool_result[:500] + ("..." if len(tool_result) > 500 else "")
                messages.append({"role": "tool", "tool_call_id": call_id, "content": short_result})

            # text 기반 tool_call 파싱 (<tool_call> 태그)
            elif not tool_calls and attempt < MAX_TOOL_CALLS:
                tool_data, text_before = extract_tool_call(content)
                if tool_data:
                    tool_name = tool_data.get("name", "")
                    tool_args = tool_data.get("arguments", {})
                    if text_before:
                        print(f"\n  {text_before}")
                    print(f"  🔧 [{tool_name}] running...")
                    tool_result = execute_tool(tool_name, tool_args)
                    print(f"  📋 Result: {tool_result[:200]}{'...' if len(tool_result) > 200 else ''}")
                    short_result = tool_result[:500] + ("..." if len(tool_result) > 500 else "")
                    messages.append({"role": "assistant", "content": content})
                    messages.append({"role": "user", "content": f"[Tool Result] {tool_name}:\n{short_result}\n\nBased on the above result, respond helpfully to the user."})
                    continue
                # 도구 호출 없음 → 최종 응답
                if content:
                    print(f"\n  {content}")
                messages.append({"role": "assistant", "content": content})
                break
            else:
                if content:
                    print(f"\n  {content}")
                messages.append({"role": "assistant", "content": content})
                break

        # 히스토리 길이 관리 (system + 최근 14개 유지)
        if len(messages) > 15:
            messages = [messages[0]] + messages[-14:]

        print()


def main():
    parser = argparse.ArgumentParser(description="Xoul - 만능 개인비서 AI 에이전트")
    parser.add_argument(
        "--config",
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json"),
        help="설정 파일 경로 (기본: config.json)"
    )
    args = parser.parse_args()

    if not os.path.isfile(args.config):
        print(f"  ❌ Config file not found: {args.config}")
        sys.exit(1)

    config = load_config(args.config)

    # GPU/CPU 모드 설정
    llm_server_cfg = config.get("llm_server", {})
    if not llm_server_cfg.get("gpu", True):
        os.environ["CUDA_VISIBLE_DEVICES"] = ""

    # llama-server 자동 시작
    llm_proc = None
    llm_cfg = config.get("llm", {})
    providers = llm_cfg.get("providers", {})
    provider = llm_cfg.get("provider", "local")
    p = providers.get(provider, {})
    server_url = p.get("base_url", llm_cfg.get("model_server", "http://localhost:8080/v1"))
    llm_already_running = False
    # 1차: 프로세스 이름으로 체크 (model 로딩 중이어도 감지)
    try:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq llama-server.exe", "/NH"],
            capture_output=True, text=True, timeout=5
        )
        if "llama-server.exe" in result.stdout:
            print("  ✅ LLM server already running (process detected)")
            llm_already_running = True
    except Exception:
        pass
    # 2차: HTTP로 체크 (프로세스 체크 실패 시)
    if not llm_already_running:
        try:
            req = urllib.request.Request(server_url.rstrip("/") + "/models")
            with urllib.request.urlopen(req, timeout=5):
                pass
            print("  ✅ LLM server already running")
            llm_already_running = True
        except Exception:
            pass
    if llm_already_running:
        pass  # 이미 실행 중이면 건너뜀
    else:
        # 서버가 안 떠 있으면 자동 시작
        script_dir = os.path.dirname(os.path.abspath(__file__))
        llama_server = os.path.join(script_dir, "llm", "llama-server.exe")
        model_path = config.get("llm", {}).get("model_path", "")
        if model_path and not os.path.isabs(model_path):
            model_path = os.path.join(script_dir, model_path)

        if os.path.isfile(llama_server) and model_path and os.path.isfile(model_path):
            ctx_size = llm_server_cfg.get("ctx_size", 8192)
            model_size_gb = os.path.getsize(model_path) / (1024**3)

            # GPU VRAM 감지 → ngl 자동 계산
            ngl = 0
            vram_gb = 0
            try:
                gpu_result = subprocess.run(
                    ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, timeout=5
                )
                if gpu_result.returncode == 0 and gpu_result.stdout.strip():
                    parts = gpu_result.stdout.strip().split(",")
                    gpu_name = parts[0].strip()
                    vram_mb = int(parts[1].strip()) if len(parts) > 1 else 0
                    vram_gb = vram_mb / 1024

                    if model_size_gb < vram_gb * 0.85:
                        # model이 VRAM에 충분히 들어감 → total GPU
                        ngl = 99
                        print(f"  🎮 GPU: {gpu_name} ({vram_gb:.0f}GB) — model {model_size_gb:.1f}GB → total GPU")
                    else:
                        # model이 VRAM보다 큼 → GPU/CPU 자동 분배
                        ratio = (vram_gb * 0.80) / model_size_gb
                        ngl = max(1, int(99 * ratio))
                        print(f"  🎮 GPU: {gpu_name} ({vram_gb:.0f}GB) — model {model_size_gb:.1f}GB → GPU/CPU split (ngl={ngl})")
            except Exception:
                pass
            if ngl == 0:
                print(f"  💻 CPU mode (model {model_size_gb:.1f}GB)")

            # llama-server 시작 (OOM 시 ngl 줄여서 재시도)
            import time as _time
            max_retries = 3
            for attempt in range(max_retries):
                print(f"  🚀 Starting llama-server... (ngl={ngl}, ctx={ctx_size})")
                llm_proc = subprocess.Popen(
                    [llama_server, "-m", model_path, "--port", "8080",
                     "-ngl", str(ngl), "--ctx-size", str(ctx_size), "--host", "0.0.0.0"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
                # 서버 준비 대기
                server_ok = False
                for i in range(30):
                    _time.sleep(2)
                    # 프로세스 crash 체크
                    if llm_proc.poll() is not None:
                        break  # 서버 종료됨 (OOM 등)
                    try:
                        req = urllib.request.Request(server_url.rstrip("/") + "/models")
                        with urllib.request.urlopen(req, timeout=2):
                            pass
                        print(f"  ✅ llama-server started (PID: {llm_proc.pid}, ngl={ngl})")
                        server_ok = True
                        break
                    except Exception:
                        if i % 5 == 4:
                            print(f"  ⏳ Loading model... ({(i+1)*2}s)")

                if server_ok:
                    break
                elif attempt < max_retries - 1:
                    # 서버 crash → ngl 줄여서 재시도
                    old_ngl = ngl
                    ngl = max(0, ngl // 2)
                    print(f"  ⚠ Server start failed (out of memory?) — ngl {old_ngl}→{ngl} retrying")
                    llm_proc = None
                else:
                    print("  ⚠ llama-server start failed. Run manually:")
                    print("    python start_llm_server.py")
                    llm_proc = None
        else:
            print("  ⚠ llama-server or model file not found.")
            print("    .\\setup_env.ps1 Run setup_env.ps1 first.")

    # VM 자동 시작
    if not is_vm_running():
        print("  ⏳ Ubuntu Starting VM...")
        if not start_vm():
            print("  ⚠ Cannot start VM. Tool execution will be limited.")
            print("    Manual start: python vm_manager.py start")
    else:
        print("  ✅ Ubuntu VM running")
        setup_ssh_key()  # 이미 실행 중인 VM에도 키 설정

    try:
        run_agent(config)
    finally:
        if llm_proc and llm_proc.poll() is None:
            print("\n  🛑 Stopping llama-server...")
            llm_proc.terminate()
            llm_proc.wait(timeout=10)


if __name__ == "__main__":
    main()

