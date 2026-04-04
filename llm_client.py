"""
LLM 클라이언트 추상화

로컬 llama.cpp, OpenAI, Claude 등 다양한 LLM 제공자를 
통일된 인터페이스로 사용합니다.

config.json의 llm.provider에 따라 자동 전환:
  - "local": llama.cpp 서버 (OpenAI 호환 API)
  - "openai": OpenAI API (GPT-4o 등)
  - "claude": Anthropic Claude API
"""

import json
import re
import urllib.request
import urllib.error


class LLMClient:
    """LLM 제공자 추상화 클라이언트"""

    def __init__(self, config: dict):
        """
        Args:
            config: total config.json 딕셔너리
        """
        llm_cfg = config.get("llm", {})
        self.provider = llm_cfg.get("provider", "local")
        self.engine = llm_cfg.get("engine", "ollama")
        self.is_commercial = (self.engine == "commercial")

        # Local 전용 파라미터 (commercial은 API 기본값 사용)
        self.temperature = llm_cfg.get("temperature") if not self.is_commercial else None
        self.top_p = llm_cfg.get("top_p") if not self.is_commercial else None
        self.max_tokens = llm_cfg.get("max_tokens") if not self.is_commercial else None

        # 제공자별 설정 로드
        providers = llm_cfg.get("providers", {})
        if self.provider in providers:
            p = providers[self.provider]
            self.base_url = p.get("base_url", "")
            self.api_key = p.get("api_key", "none")
            self.model_name = p.get("model_name", "")
        else:
            # 레거시 호환 (providers 없이 직접 설정된 경우)
            self.base_url = llm_cfg.get("model_server", "http://localhost:8080/v1")
            self.api_key = llm_cfg.get("api_key", "none")
            self.model_name = llm_cfg.get("model_name", "")

    def chat(self, messages: list, tools: list = None) -> dict:
        """
        LLM에 messages를 보내고 응답을 받습니다.

        Args:
            messages: OpenAI 형식 messages 리스트
            tools: OpenAI 형식 도구 정의 리스트 (선택)

        Returns:
            {"role": "assistant", "content": "응답 text", "tool_calls": [...]}
        """
        if self.provider == "claude":
            return self._call_claude(messages, tools)
        else:
            # local, openai 둘 다 OpenAI 호환 API 사용
            return self._call_openai_compatible(messages, tools)

    def check_connection(self) -> bool:
        """LLM 서버 연결 확인"""
        try:
            url = self.base_url.rstrip("/") + "/models"
            req = urllib.request.Request(url, headers={
                "Authorization": f"Bearer {self.api_key}",
                "User-Agent": "Xoul/1.0"
            })
            with urllib.request.urlopen(req, timeout=5):
                pass
            return True
        except Exception:
            return False

    def get_provider_info(self) -> str:
        """현재 제공자 정보 문자열"""
        icons = {"local": "💻", "openai": "☁️", "claude": "☁️"}
        icon = icons.get(self.provider, "🤖")
        return f"{icon} {self.provider}: {self.model_name}"

    # ─────────────────────────────────────────
    # OpenAI 호환 API (local llama.cpp, OpenAI)
    # ─────────────────────────────────────────

    def _call_openai_compatible(self, messages: list, tools: list = None) -> dict:
        """OpenAI 호환 API 호출 (Ollama /v1, OpenAI, 기타 클라우드 모두 통합)"""
        url = self.base_url.rstrip("/") + "/chat/completions"
        payload = {
            "model": self.model_name,
            "messages": messages,
        }
        # Local: temperature/top_p/max_tokens 명시, Commercial: API 기본값 사용
        if not self.is_commercial:
            token_key = "max_tokens"
            payload[token_key] = self.max_tokens or 4096
            payload["temperature"] = self.temperature if self.temperature is not None else 0.7
            payload["top_p"] = self.top_p if self.top_p is not None else 0.8
        else:
            # Commercial: Groq는 max_tokens 사용, OpenAI newer models는 max_completion_tokens
            if self.provider in ("groq", "deepseek", "xai", "mistral", "google"):
                payload["max_tokens"] = 4096
            else:
                payload["max_completion_tokens"] = 4096
        # 모델별 호환성 처리
        model_lower = (self.model_name or "").lower()
        is_nanbeige = "nanbeige" in model_lower
        is_gemma = "gemma" in model_lower

        # Thinking 모델(Modelfile에 think=true 내장)이 tool call 시
        # reasoning만 하고 content/tool_calls를 비우는 문제 방지
        if not self.is_commercial and tools:
            payload["think"] = False

        # Nanbeige: max_tokens 상한 제한 (131072 → 32768, 무한 생성 방지)
        if is_nanbeige and payload.get("max_tokens", 0) > 32768:
            payload["max_tokens"] = 32768

        # Nanbeige, Gemma: Ollama native tool calling과 호환되지 않음
        # Gemma4는 func{key:<|"|>val<|"|>} 형태 malformed 출력 → 프롬프트 기반으로 전환
        _skip_native_tools = is_nanbeige or is_gemma
        if tools and not _skip_native_tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        try:
            data = json.dumps(payload).encode("utf-8")
            # 디버그: 전체 payload 덤프 (마지막 요청만 보관)
            try:
                with open("/tmp/llm_payload_last.json", "w") as _pd:
                    _pd.write(json.dumps(payload, ensure_ascii=False, indent=2))
            except Exception:
                pass
            req = urllib.request.Request(
                url, data=data,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                    "User-Agent": "Xoul/1.0"
                }
            )
            with urllib.request.urlopen(req, timeout=300) as resp:
                result = json.loads(resp.read().decode("utf-8"))
            choice = result["choices"][0]
            finish_reason = choice.get("finish_reason", "unknown")
            msg = choice["message"]
            cleaned, thinking = self._clean_content(msg.get("content", "") or "")
            ollama_reasoning = msg.get("reasoning", "")
            msg["content"] = cleaned
            msg["_thinking"] = ollama_reasoning or thinking
            msg["_finish_reason"] = finish_reason
            # 디버그 로그 (파일)
            import datetime
            with open("/tmp/llm_debug.log", "a") as dbg:
                tc_names = [t["function"]["name"] for t in (msg.get("tool_calls") or [])]
                dbg.write(f"{datetime.datetime.now()} finish={finish_reason} content_len={len(cleaned)} tool_calls={tc_names}\n")
            return msg
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            # Ollama가 LLM의 malformed tool call JSON을 파싱 못 한 경우 → tools 없이 재시도
            # tools=None인 경우도 포함 (planning phase에서 모델이 tool_call 형식 생성 시)
            if e.code == 500 and ("error parsing tool call" in body or "EOF" in body):
                print(f"\033[93m[LLM] 500 error (tool call parse) → retrying without tools\033[0m", flush=True)
                try:
                    retry_payload = dict(payload)
                    retry_payload.pop("tools", None)
                    retry_payload.pop("tool_choice", None)
                    retry_data = json.dumps(retry_payload).encode("utf-8")
                    retry_req = urllib.request.Request(
                        url, data=retry_data,
                        headers={
                            "Content-Type": "application/json",
                            "Authorization": f"Bearer {self.api_key}"
                        }
                    )
                    with urllib.request.urlopen(retry_req, timeout=120) as resp2:
                        result2 = json.loads(resp2.read().decode("utf-8"))
                    choice2 = result2["choices"][0]
                    msg2 = choice2["message"]
                    cleaned2, thinking2 = self._clean_content(msg2.get("content", "") or "")
                    msg2["content"] = cleaned2
                    msg2["_thinking"] = msg2.get("reasoning", "") or thinking2
                    msg2["_finish_reason"] = choice2.get("finish_reason", "unknown")
                    return msg2
                except Exception:
                    pass
            return {"content": f"[LLM 오류 {e.code}]: {body[:300]}", "role": "assistant"}
        except Exception as e:
            return {"content": f"[LLM 연결 오류]: {e}", "role": "assistant"}

    # ─────────────────────────────────────────
    # Anthropic Claude API
    # ─────────────────────────────────────────

    def _call_claude(self, messages: list, tools: list = None) -> dict:
        """Anthropic Claude API 호출"""
        url = self.base_url.rstrip("/") + "/messages"

        # system messages 분리 (Claude는 system을 별도로 받음)
        system_text = ""
        chat_messages = []
        for m in messages:
            if m["role"] == "system":
                system_text += m["content"] + "\n"
            elif m["role"] == "tool":
                # Claude는 tool_result 형식 사용
                chat_messages.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": m.get("tool_call_id", ""),
                        "content": m["content"]
                    }]
                })
            else:
                chat_messages.append({"role": m["role"], "content": m["content"]})

        payload = {
            "model": self.model_name,
            "messages": chat_messages,
        }
        # Commercial: max_tokens는 Claude에서 필수이므로 기본값 사용
        payload["max_tokens"] = self.max_tokens or 4096
        if system_text:
            payload["system"] = system_text.strip()

        # Claude 도구 형식 변환 (OpenAI → Claude)
        if tools:
            claude_tools = []
            for t in tools:
                f = t.get("function", {})
                claude_tools.append({
                    "name": f.get("name", ""),
                    "description": f.get("description", ""),
                    "input_schema": f.get("parameters", {"type": "object", "properties": {}})
                })
            payload["tools"] = claude_tools

        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                url, data=data,
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01"
                }
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                result = json.loads(resp.read().decode("utf-8"))

            # Claude 응답 → OpenAI 형식 변환
            content = ""
            tool_calls = []
            for block in result.get("content", []):
                if block.get("type") == "text":
                    content += block.get("text", "")
                elif block.get("type") == "tool_use":
                    tool_calls.append({
                        "id": block.get("id", ""),
                        "type": "function",
                        "function": {
                            "name": block.get("name", ""),
                            "arguments": json.dumps(block.get("input", {}))
                        }
                    })

            cleaned, thinking = self._clean_content(content)
            msg = {"role": "assistant", "content": cleaned, "_thinking": thinking}
            if tool_calls:
                msg["tool_calls"] = tool_calls
            return msg
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            return {"content": f"[Claude 오류 {e.code}]: {body[:300]}", "role": "assistant"}
        except Exception as e:
            return {"content": f"[Claude 연결 오류]: {e}", "role": "assistant"}

    # ─────────────────────────────────────────
    # 공통 유틸
    # ─────────────────────────────────────────

    @staticmethod
    def _clean_content(content: str) -> tuple:
        """응답에서 <think> 블록을 추출하고 제거. (cleaned, thinking) 튜플 반환"""
        if not content:
            return "", ""
        thinking = ""
        # <think>...</think> 블록 추출
        think_match = re.search(r'<think>(.*?)</think>', content, flags=re.DOTALL)
        if think_match:
            thinking = think_match.group(1).strip()
            content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()
        # 닫히지 않은 <think> 블록 (끝까지)
        elif '<think>' in content:
            parts = content.split('<think>', 1)
            thinking = parts[1].strip() if len(parts) > 1 else ""
            content = parts[0].strip()
        # </think>만 있는 경우 (이전 청크에서 시작)
        if '</think>' in content:
            content = content.split('</think>', 1)[-1].strip()
        return content, thinking
