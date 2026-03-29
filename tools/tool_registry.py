"""
Xoul Tool Registry — Base Tools + Toolkit 계층 구조

Free toolkit: 로컬 JSON 파일 (tools/toolkits/*.json)
Premium toolkit: REST API → 컴파일된 .so 다운로드 → import
"""

import os
import json
import struct

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TOOLKITS_DIR = os.path.join(SCRIPT_DIR, "toolkits")
PREMIUM_CACHE_DIR = os.path.join(os.path.expanduser("~"), ".xoul", "premium_toolkits")

# ── Embedding 설정 ──
EMBED_URL = "http://10.0.2.2:11434/api/embeddings"
EMBED_MODEL = "bge-m3"
TOOLKIT_THRESHOLD = 0.55  # toolkit 활성화 임계값 (0.40→0.55 정밀도 개선)

# ── Base Tools (항상 포함 — 16개) ──
BASE_TOOL_NAMES = [
    # Core
    "web_search", "run_command", "get_datetime", "calculate", "run_python_code",
    # Code Store
    "run_stored_code", "list_codes",
    # Memory
    "recall", "forget",
    # Host (자주 사용)
    "host_open_app", "host_run_command",
    # Workflow (run/list만, create/update/delete는 toolkit)
    "run_workflow", "list_workflows",
    # Persona (list/activate만)
    "list_personas", "activate_persona",
    # Greeting
    "greeting",
]

# ── 내부 상태 ──
_all_openai_tools = []     # 전체 OpenAI tool 정의 (base + 모든 toolkit)
_base_tools = []           # base tool 정의만
_toolkits = {}             # name → {description, keywords, tasks, embedding, tier}
_premium_modules = {}      # name → imported module
_initialized = False


def _get_embedding(text: str) -> bytes | None:
    """Ollama embedding (BGE-M3)"""
    import urllib.request
    try:
        data = json.dumps({"model": EMBED_MODEL, "prompt": text, "options": {"num_gpu": 0}}).encode()
        req = urllib.request.Request(EMBED_URL, data=data,
                                     headers={"Content-Type": "application/json"})
        resp = urllib.request.urlopen(req, timeout=10)
        vec = json.loads(resp.read().decode())["embedding"]
        return struct.pack(f"{len(vec)}f", *vec)
    except Exception as e:
        print(f"[ToolRegistry] embedding error: {e}")
        return None


def _cosine_similarity(a: bytes, b: bytes) -> float:
    """cosine similarity between two embedding byte vectors"""
    if not a or not b or len(a) != len(b):
        return 0.0
    n = len(a) // 4
    va = struct.unpack(f"{n}f", a)
    vb = struct.unpack(f"{n}f", b)
    dot = sum(x * y for x, y in zip(va, vb))
    na = sum(x * x for x in va) ** 0.5
    nb = sum(x * x for x in vb) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def init(all_openai_tools: list = None):
    """
    서버 시작 시 호출.
    - all_openai_tools: 기존 OPENAI_TOOLS 리스트 (호환성 유지)
    - toolkit JSON 로드 + embedding 사전 계산
    """
    global _all_openai_tools, _base_tools, _toolkits, _initialized

    if all_openai_tools:
        _all_openai_tools = all_openai_tools

    # 1) Base tools 분리
    _base_tools = [t for t in _all_openai_tools
                   if t.get("function", {}).get("name") in BASE_TOOL_NAMES]
    print('_base_tools', _base_tools)

    # 2) Free toolkit JSON 로드
    if os.path.isdir(TOOLKITS_DIR):
        for fname in os.listdir(TOOLKITS_DIR):
            if not fname.endswith(".json"):
                continue
            fpath = os.path.join(TOOLKITS_DIR, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    tk = json.load(f)
                name = tk["name"]
                _toolkits[name] = {
                    "description": tk.get("description", ""),
                    "keywords": tk.get("keywords", []),
                    "tasks": tk.get("tasks", []),
                    "tier": tk.get("tier", "free"),
                    "embedding": None,
                }
                # toolkit의 tasks를 _all_openai_tools에서 찾아서 연결
                # (이미 있으면 JSON의 것으로 대체하지 않음 — 실행 함수 매핑 유지)
                tk_task_names = [t["function"]["name"] for t in tk.get("tasks", [])]
                existing_names = {t.get("function", {}).get("name") for t in _all_openai_tools}
                for task_def in tk.get("tasks", []):
                    if task_def["function"]["name"] not in existing_names:
                        _all_openai_tools.append(task_def)

                print(f"  📦 Toolkit loaded: {name} ({len(tk_task_names)} tasks, {tk['tier']})")
            except Exception as e:
                print(f"  ⚠ Toolkit load error ({fname}): {e}")

    # 3) Embedding 사전 계산 (비동기적으로 하면 좋지만, 간단히 동기)
    print("  🧠 Computing toolkit embeddings...", end="", flush=True)
    computed = 0
    for name, tk in _toolkits.items():
        embed = _get_embedding(tk["description"])
        if embed:
            tk["embedding"] = embed
            computed += 1
    print(f" {computed}/{len(_toolkits)} done")

    _initialized = True
    print(f"  ✅ ToolRegistry: {len(_base_tools)} base + {len(_toolkits)} toolkits")


def select_tools(user_input: str) -> list:
    """
    사용자 입력 기반으로 LLM에 전달할 도구 목록 선택.
    Base tools + 매칭된 toolkit의 tasks만 반환.
    """
    if not _initialized:
        return _all_openai_tools  # fallback: 전체 반환

    # 1) Base tools 항상 포함
    selected = list(_base_tools)
    selected_names = set(BASE_TOOL_NAMES)
    activated_toolkits = []

    # 2) 키워드 fallback (빠름, embedding 전에 먼저 체크)
    input_lower = user_input.lower()
    for name, tk in _toolkits.items():
        for kw in tk.get("keywords", []):
            if kw.lower() in input_lower:
                activated_toolkits.append((name, 1.0, "keyword"))
                break

    # 3) Embedding 유사도 (키워드에서 못 잡은 것만)
    activated_names = {a[0] for a in activated_toolkits}
    input_embed = _get_embedding(user_input)
    if input_embed:
        for name, tk in _toolkits.items():
            if name in activated_names:
                continue  # 이미 키워드로 활성화됨
            if tk["embedding"]:
                sim = _cosine_similarity(input_embed, tk["embedding"])
                if sim >= TOOLKIT_THRESHOLD:
                    activated_toolkits.append((name, sim, "embed"))

    # 4) 활성화된 toolkit의 tasks 추가
    for tk_name, score, method in activated_toolkits:
        tk = _toolkits[tk_name]
        for task_def in tk["tasks"]:
            task_name = task_def["function"]["name"]
            if task_name not in selected_names:
                # _all_openai_tools에서 찾기 (실행 함수 매핑 유지)
                for t in _all_openai_tools:
                    if t.get("function", {}).get("name") == task_name:
                        selected.append(t)
                        selected_names.add(task_name)
                        break

    # 5) Premium toolkit tasks 추가
    for name, mod in _premium_modules.items():
        if hasattr(mod, "TOOLKIT_INFO"):
            info = mod.TOOLKIT_INFO
            for task in info.get("tasks", []):
                if task["name"] not in selected_names:
                    # premium task의 OpenAI 정의 생성
                    selected.append(task.get("openai_def", {}))
                    selected_names.add(task["name"])

    activated_str = ", ".join(f"{n}({m}:{s:.2f})" for n, s, m in activated_toolkits)
    print(f"[ToolRouter] {len(selected)}/{len(_all_openai_tools)} tools | "
          f"activated: [{activated_str}]", flush=True)

    return selected


def register_premium_toolkit(name: str, module):
    """다운로드된 premium toolkit 모듈 등록"""
    global _premium_modules
    _premium_modules[name] = module
    # TOOLS dict에도 실행 함수 등록
    if hasattr(module, "TOOLKIT_INFO"):
        info = module.TOOLKIT_INFO
        from tools import TOOLS
        for task in info.get("tasks", []):
            TOOLS[task["name"]] = task["function"]
        print(f"  💎 Premium toolkit registered: {name} ({len(info['tasks'])} tasks)")


def get_toolkit_info() -> dict:
    """현재 로드된 toolkit 정보 반환 (디버그/상태 확인용)"""
    return {
        "base_tools": len(_base_tools),
        "free_toolkits": {n: {"tasks": len(t["tasks"]), "tier": t["tier"]}
                          for n, t in _toolkits.items()},
        "premium_toolkits": list(_premium_modules.keys()),
        "total_tools": len(_all_openai_tools),
    }
