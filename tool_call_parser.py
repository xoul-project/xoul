"""
모델별 Tool Call 파싱 모듈
각 LLM의 네이티브 tool call 출력 형식에 맞춰 파싱합니다.
"""
import re
import json
from typing import List, Dict, Optional


# ───────────────────────────────────────────
# 1. 모델 타입 감지
# ───────────────────────────────────────────

def detect_model_type(model_name: str) -> str:
    """모델명에서 타입을 감지합니다.
    
    Returns: qwen, nemotron, glm, nanbeige, exaone, xlam, generic
    """
    name = (model_name or "").lower()
    if "qwen" in name:
        return "qwen"
    if "gemma" in name:
        return "gemma"
    if "nemotron" in name:
        return "nemotron"
    if "glm" in name:
        return "glm"
    if "nanbeige" in name:
        return "nanbeige"
    if "exaone" in name:
        return "exaone"
    if "xlam" in name:
        return "xlam"
    return "generic"


# ───────────────────────────────────────────
# 2. 모델별 Tool Call 추출
# ───────────────────────────────────────────

def _fix_json_newlines(text: str) -> str:
    """JSON 문자열 값 안의 이스케이프되지 않은 줄바꿈/탭을 \\n/\\t로 변환.
    
    LLM이 send_email body 등에 raw newline을 넣는 경우 json.loads()가 실패하므로
    문자열 값 내부의 제어 문자만 이스케이프한다.
    """
    result = []
    in_string = False
    i = 0
    while i < len(text):
        c = text[i]
        if c == '\\' and in_string and i + 1 < len(text):
            result.append(c)
            result.append(text[i + 1])
            i += 2
            continue
        if c == '"':
            in_string = not in_string
            result.append(c)
        elif c == '\n' and in_string:
            result.append('\\n')
        elif c == '\r' and in_string:
            result.append('\\r')
        elif c == '\t' and in_string:
            result.append('\\t')
        else:
            result.append(c)
        i += 1
    return ''.join(result)


def _parse_json_tool(raw: str) -> Optional[Dict]:
    """JSON 문자열에서 tool call 데이터를 파싱합니다."""
    try:
        # 여러 JSON이 연결된 경우 첫 번째만 추출
        raw = raw.strip()
        # 배열인 경우 첫 번째 요소
        if raw.startswith("["):
            arr = json.loads(raw)
            if isinstance(arr, list) and arr:
                raw_obj = arr[0]
            else:
                return None
        else:
            # 닫는 중괄호 이후 잔여 텍스트 제거
            depth = 0
            end_idx = 0
            for i, c in enumerate(raw):
                if c == '{': depth += 1
                elif c == '}': depth -= 1
                if depth == 0 and i > 0:
                    end_idx = i + 1
                    break
            if end_idx:
                raw = raw[:end_idx]
            # 1차: 그대로 파싱
            try:
                raw_obj = json.loads(raw)
            except json.JSONDecodeError:
                # 2차: 문자열 값 안의 raw newline을 이스케이프하여 재시도
                raw_obj = json.loads(_fix_json_newlines(raw))
        
        name = raw_obj.get("name", "")
        arguments = raw_obj.get("arguments", raw_obj.get("parameters", {}))
        
        # arguments가 문자열인 경우 JSON 파싱
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except (json.JSONDecodeError, Exception):
                pass
        
        if name:
            return {"name": name, "arguments": arguments if isinstance(arguments, dict) else {}}
    except (json.JSONDecodeError, Exception):
        pass
    return None


def _extract_qwen(text: str) -> List[Dict]:
    """Qwen3 모델: <tool_call>{...}</tool_call>"""
    results = []
    
    # 1) 닫는 태그 있는 경우
    matches = re.findall(r'<tool_call>\s*(.*?)\s*</tool_call>', text, re.DOTALL)
    for raw in matches:
        td = _parse_json_tool(raw)
        if td:
            results.append(td)
    
    # 2) 닫는 태그 없는 폴백
    if not results:
        matches = re.findall(r'<tool_call>\s*(\{.*)', text, re.DOTALL)
        for raw in matches:
            td = _parse_json_tool(raw)
            if td:
                results.append(td)

    return results


# ───────────────────────────────────────────
# Gemma4 Tool Call Parser
# 공식 포맷: <|tool_call>call:func_name{key:<|"|>value<|"|>}<tool_call|>
# Ollama 경유 시: call:func_name{key:<|"|>value<|"|>} 또는 func_name{key:<|"|>value<|"|>}
# ───────────────────────────────────────────

_GEMMA_STRING_DELIM = '<|"|>'

def _parse_gemma4_value(value_str: str):
    """Gemma4 단일 값 파싱 (key: 이후)"""
    value_str = value_str.strip()
    if not value_str:
        return value_str
    if value_str == "true":
        return True
    if value_str == "false":
        return False
    try:
        if "." in value_str:
            return float(value_str)
        return int(value_str)
    except ValueError:
        pass
    return value_str


def _parse_gemma4_array(arr_str: str) -> list:
    """Gemma4 배열 파싱"""
    items = []
    i = 0
    n = len(arr_str)
    while i < n:
        while i < n and arr_str[i] in (" ", ",", "\n", "\t"):
            i += 1
        if i >= n:
            break
        if arr_str[i:].startswith(_GEMMA_STRING_DELIM):
            i += len(_GEMMA_STRING_DELIM)
            end_pos = arr_str.find(_GEMMA_STRING_DELIM, i)
            if end_pos == -1:
                items.append(arr_str[i:])
                break
            items.append(arr_str[i:end_pos])
            i = end_pos + len(_GEMMA_STRING_DELIM)
        elif arr_str[i] == "{":
            depth = 1
            obj_start = i + 1
            i += 1
            while i < n and depth > 0:
                if arr_str[i:].startswith(_GEMMA_STRING_DELIM):
                    i += len(_GEMMA_STRING_DELIM)
                    nd = arr_str.find(_GEMMA_STRING_DELIM, i)
                    i = nd + len(_GEMMA_STRING_DELIM) if nd != -1 else n
                    continue
                if arr_str[i] == "{":
                    depth += 1
                elif arr_str[i] == "}":
                    depth -= 1
                i += 1
            items.append(_parse_gemma4_args(arr_str[obj_start:i - 1]))
        elif arr_str[i] == "[":
            depth = 1
            sub_start = i + 1
            i += 1
            while i < n and depth > 0:
                if arr_str[i] == "[":
                    depth += 1
                elif arr_str[i] == "]":
                    depth -= 1
                i += 1
            items.append(_parse_gemma4_array(arr_str[sub_start:i - 1]))
        else:
            val_start = i
            while i < n and arr_str[i] not in (",", "]"):
                i += 1
            items.append(_parse_gemma4_value(arr_str[val_start:i]))
    return items


def _parse_gemma4_args(args_str: str) -> dict:
    """Gemma4 key:value 포맷 파싱 → Python dict
    
    예시:
        message:<|"|>hello<|"|>,title:<|"|>test<|"|>
        count:42,flag:true
        nested:{inner_key:<|"|>val<|"|>}
    """
    if not args_str or not args_str.strip():
        return {}
    result = {}
    i = 0
    n = len(args_str)
    while i < n:
        while i < n and args_str[i] in (" ", ",", "\n", "\t"):
            i += 1
        if i >= n:
            break
        # key 파싱 (: 까지)
        key_start = i
        while i < n and args_str[i] != ":":
            i += 1
        if i >= n:
            break
        key = args_str[key_start:i].strip()
        i += 1  # skip ':'
        if i >= n:
            result[key] = ""
            break
        while i < n and args_str[i] in (" ", "\n", "\t"):
            i += 1
        if i >= n:
            result[key] = ""
            break
        # 문자열 값: <|"|>...<|"|>
        if args_str[i:].startswith(_GEMMA_STRING_DELIM):
            i += len(_GEMMA_STRING_DELIM)
            val_start = i
            end_pos = args_str.find(_GEMMA_STRING_DELIM, i)
            if end_pos == -1:
                result[key] = args_str[val_start:]
                break
            result[key] = args_str[val_start:end_pos]
            i = end_pos + len(_GEMMA_STRING_DELIM)
        # 중첩 오브젝트: {...}
        elif args_str[i] == "{":
            depth = 1
            obj_start = i + 1
            i += 1
            while i < n and depth > 0:
                if args_str[i:].startswith(_GEMMA_STRING_DELIM):
                    i += len(_GEMMA_STRING_DELIM)
                    nd = args_str.find(_GEMMA_STRING_DELIM, i)
                    i = n if nd == -1 else nd + len(_GEMMA_STRING_DELIM)
                    continue
                if args_str[i] == "{":
                    depth += 1
                elif args_str[i] == "}":
                    depth -= 1
                i += 1
            result[key] = _parse_gemma4_args(args_str[obj_start:i - 1])
        # 배열: [...]
        elif args_str[i] == "[":
            depth = 1
            arr_start = i + 1
            i += 1
            while i < n and depth > 0:
                if args_str[i:].startswith(_GEMMA_STRING_DELIM):
                    i += len(_GEMMA_STRING_DELIM)
                    nd = args_str.find(_GEMMA_STRING_DELIM, i)
                    i = n if nd == -1 else nd + len(_GEMMA_STRING_DELIM)
                    continue
                if args_str[i] == "[":
                    depth += 1
                elif args_str[i] == "]":
                    depth -= 1
                i += 1
            result[key] = _parse_gemma4_array(args_str[arr_start:i - 1])
        # bare 값 (숫자, boolean 등)
        else:
            val_start = i
            while i < n and args_str[i] not in (",", "}", "]"):
                i += 1
            result[key] = _parse_gemma4_value(args_str[val_start:i])
    return result


def _extract_gemma(text: str) -> List[Dict]:
    """Gemma4 모델 tool call 추출
    
    패턴 1: <|tool_call>call:func_name{args}<tool_call|>  (vLLM 공식)
    패턴 2: call:func_name{args}  (Ollama 경유, 스페셜 토큰 제거됨)
    패턴 3: func_name{args}  (Ollama 경유, call: 접두사도 없음)
    """
    from tools import TOOLS
    results = []
    
    # 패턴 1: <|tool_call>call:func_name{...}<tool_call|>
    for m in re.finditer(
        r'<\|tool_call>\s*call:(\w+)\s*\{(.*?)\}\s*<tool_call\|>',
        text, re.DOTALL
    ):
        func_name, args_str = m.group(1), m.group(2)
        if func_name in TOOLS:
            results.append({"name": func_name, "arguments": _parse_gemma4_args(args_str)})
    if results:
        return results
    
    # 패턴 2: call:func_name{...}
    for m in re.finditer(
        r'\bcall:(\w+)\s*\{(.*?)\}',
        text, re.DOTALL
    ):
        func_name, args_str = m.group(1), m.group(2)
        if func_name in TOOLS:
            results.append({"name": func_name, "arguments": _parse_gemma4_args(args_str)})
    if results:
        return results
    
    # 패턴 3: func_name{key:<|"|>value<|"|>}  (Ollama가 토큰을 완전히 스트립)
    for m in re.finditer(
        r'\b(\w+)\s*\{([^{}]*(?:<\|"\|>[^{}]*)*(?:\{[^{}]*\}[^{}]*)*)\}',
        text, re.DOTALL
    ):
        func_name, args_str = m.group(1), m.group(2)
        if func_name in TOOLS and (':' in args_str):
            results.append({"name": func_name, "arguments": _parse_gemma4_args(args_str)})
    
    return results


def _extract_nemotron(text: str) -> List[Dict]:
    """Nemotron 모델: <function=name><parameter=key>value</parameter></function> 형식"""
    results = []
    
    # 1) Nemotron 네이티브 XML 형식: <function=name><parameter=key>value</parameter></function>
    func_matches = re.findall(
        r'<function=(\w+)>(.*?)</function>', text, re.DOTALL
    )
    for func_name, func_body in func_matches:
        args = {}
        param_matches = re.findall(
            r'<parameter=(\w+)>\s*(.*?)\s*</parameter>', func_body, re.DOTALL
        )
        for param_name, param_value in param_matches:
            # 값이 JSON인 경우 파싱 시도
            try:
                args[param_name] = json.loads(param_value)
            except (json.JSONDecodeError, ValueError):
                args[param_name] = param_value.strip()
        
        from tools import TOOLS
        if func_name in TOOLS:
            results.append({"name": func_name, "arguments": args})
        else:
            # 퍼지 매칭
            for tname in TOOLS:
                if tname in func_name or func_name in tname:
                    results.append({"name": tname, "arguments": args})
                    break
    
    if results:
        return results
    
    # 2) <TOOLCALL>[{...}]</TOOLCALL> 대문자 태그
    matches = re.findall(r'<TOOLCALL>\s*(.*?)\s*</TOOLCALL>', text, re.DOTALL)
    for raw in matches:
        td = _parse_json_tool(raw)
        if td:
            results.append(td)
    
    if results:
        return results
    
    # 3) 소문자 <tool_call> JSON 폴백
    results = _extract_qwen(text)
    if results:
        return results
    
    # 4) raw JSON 폴백
    return _extract_raw_json(text)


def _extract_glm(text: str) -> List[Dict]:
    """GLM 모델: Python 함수 호출 스타일, raw JSON, 또는 Qwen 호환"""
    results = []
    
    # 1) Python 함수 호출 스타일: <tool_call>func_name(key="value", ...)</tool_call>
    #    또는 태그 없이: func_name(key="value")
    func_call_matches = re.findall(
        r'(?:<tool_call>)?\s*(\w+)\s*\(\s*(.*?)\s*\)\s*(?:</tool_call>)?',
        text, re.DOTALL
    )
    for func_name, args_str in func_call_matches:
        from tools import TOOLS
        # 함수명이 실제 도구 이름인지 확인
        if func_name not in TOOLS:
            continue
        
        args = {}
        # key="value" 또는 key='value' 패턴 파싱
        kv_matches = re.findall(
            r'(\w+)\s*=\s*(?:"((?:[^"\\]|\\.)*)"|\'((?:[^\'\\]|\\.)*)\'|(\S+))',
            args_str
        )
        for key, val_dq, val_sq, val_bare in kv_matches:
            value = val_dq or val_sq or val_bare
            # JSON 값 시도
            try:
                value = json.loads(f'"{value}"') if isinstance(value, str) else value
            except:
                pass
            args[key] = value
        
        results.append({"name": func_name, "arguments": args})
    
    if results:
        return results
    
    # 2) <tool_call> JSON 형식 (Qwen 호환)
    results = _extract_qwen(text)
    if results:
        return results
    
    # 3) raw JSON 추출
    return _extract_raw_json(text)


def _extract_raw_json(text: str) -> List[Dict]:
    """태그 없이 raw JSON에서 tool call 추출 (GLM, 기타 폴백)
    
    중첩된 JSON도 처리 (depth 카운터 사용).
    """
    from tools import TOOLS
    results = []
    
    # "name" 키를 포함하는 JSON 오브젝트 시작점 찾기
    i = 0
    while i < len(text):
        # { 찾기
        brace_pos = text.find('{', i)
        if brace_pos == -1:
            break
        
        # depth 카운터로 매칭하는 } 찾기
        depth = 1
        j = brace_pos + 1
        in_string = False
        escape_next = False
        while j < len(text) and depth > 0:
            c = text[j]
            if escape_next:
                escape_next = False
                j += 1
                continue
            if c == '\\' and in_string:
                escape_next = True
                j += 1
                continue
            if c == '"' and not escape_next:
                in_string = not in_string
            elif not in_string:
                if c == '{':
                    depth += 1
                elif c == '}':
                    depth -= 1
            j += 1
        
        if depth != 0:
            i = brace_pos + 1
            continue
        
        candidate = text[brace_pos:j]
        i = j  # 다음 검색 위치
        
        # "name" 키가 있는지 빠르게 확인
        if '"name"' not in candidate:
            continue
        
        try:
            # 1차: 그대로 파싱
            try:
                raw_obj = json.loads(candidate)
            except json.JSONDecodeError:
                # 2차: 문자열 값 안의 raw newline을 이스케이프하여 재시도
                try:
                    raw_obj = json.loads(_fix_json_newlines(candidate))
                except json.JSONDecodeError:
                    # 3차: name과 arguments를 개별 추출
                    name_m = re.search(r'"name"\s*:\s*"([^"]+)"', candidate)
                    if name_m and name_m.group(1) in TOOLS:
                        args_m = re.search(r'"arguments"\s*:\s*(\{.*)', candidate, re.DOTALL)
                        args = {}
                        if args_m:
                            args_raw = args_m.group(1).strip()
                            depth = 0
                            end = 0
                            for ci, cc in enumerate(args_raw):
                                if cc == '{': depth += 1
                                elif cc == '}': depth -= 1
                                if depth == 0 and ci > 0:
                                    end = ci + 1
                                    break
                            if end > 0:
                                try:
                                    args = json.loads(_fix_json_newlines(args_raw[:end]))
                                except json.JSONDecodeError:
                                    args = {}
                        results.append({"name": name_m.group(1), "arguments": args})
                    i = j
                    continue

            raw_name = raw_obj.get("name", "")
            
            # GLM ::: 구분자 처리
            if ":::" in raw_name:
                parts = raw_name.split(":::")
                raw_name = parts[-1].replace("/", "_")
            
            # OpenAI functions. prefix 처리 (e.g. "functions.recall" → "recall")
            if "." in raw_name:
                raw_name = raw_name.split(".")[-1]
            
            if raw_name in TOOLS:
                arguments = raw_obj.get("arguments", raw_obj.get("parameters", {}))
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except:
                        pass
                results.append({"name": raw_name, "arguments": arguments if isinstance(arguments, dict) else {}})
            else:
                # 퍼지 매칭
                for tname in TOOLS:
                    if tname in raw_name or raw_name in tname:
                        arguments = raw_obj.get("arguments", raw_obj.get("parameters", {}))
                        if isinstance(arguments, str):
                            try:
                                arguments = json.loads(arguments)
                            except:
                                pass
                        results.append({"name": tname, "arguments": arguments if isinstance(arguments, dict) else {}})
                        break
        except (json.JSONDecodeError, Exception):
            pass
    
    return results


def extract_tool_calls(text: str, model_type: str) -> List[Dict]:
    """모델 타입에 따라 텍스트에서 모든 tool call을 추출합니다.
    
    모델-특화 파서를 먼저 시도하고, 실패 시 다른 모든 파서를 순차 시도합니다.
    
    Args:
        text: LLM 응답 텍스트
        model_type: detect_model_type()의 반환값
    
    Returns:
        [{"name": "tool_name", "arguments": {...}}, ...]
    """

    print('모델 파서 테스트 :', text)
    if not text or not text.strip():
        return []
    
    # 모든 파서 목록 (우선순위순)
    all_parsers = [_extract_gemma, _extract_qwen, _extract_nemotron, _extract_glm, _extract_raw_json]
    
    # 모델별 우선 파서
    preferred = {
        "gemma": _extract_gemma,
        "qwen": _extract_qwen,
        "nemotron": _extract_nemotron,
        "glm": _extract_glm,
        "nanbeige": _extract_qwen,
        "exaone": _extract_qwen,
        "xlam": _extract_qwen,
    }
    
    # 우선 파서 먼저 시도
    pref = preferred.get(model_type)
    if pref:
        results = pref(text)
        if results:
            return results
    
    # 모든 파서 순차 시도 (이미 시도한 것 제외)
    for ext_fn in all_parsers:
        if ext_fn == pref:
            continue
        results = ext_fn(text)
        if results:
            return results
    
    return []


# ───────────────────────────────────────────
# 3. 모델별 프롬프트 지시
# ───────────────────────────────────────────

def get_tool_call_instruction(model_type: str) -> str:
    """모델 타입에 맞는 도구 호출 형식 예시를 반환합니다."""
    
    if model_type == "qwen":
        return (
            "\n⚠ 도구 호출 시 아래 형식을 사용하세요:\n"
            "<tool_call>{\"name\":\"도구명\",\"arguments\":{...}}</tool_call>\n"
            "여러 도구를 호출하려면 <tool_call>을 여러 번 출력하세요.\n"
        )
    
    if model_type == "nemotron":
        return (
            "\n⚠ 도구 호출 시 아래 형식을 사용하세요:\n"
            "<tool_call>\n"
            "<function=도구명>\n"
            "<parameter=인자명>값</parameter>\n"
            "</function>\n"
            "</tool_call>\n"
            "예시: <tool_call><function=web_search><parameter=query>서울 날씨</parameter></function></tool_call>\n"
        )
    
    if model_type == "glm":
        return (
            "\n⚠ 도구 호출 시 아래 JSON 형식으로 출력하세요:\n"
            "{\"name\":\"도구명\",\"arguments\":{...}}\n"
            "여러 도구를 호출하려면 JSON을 여러 번 출력하세요.\n"
        )
    
    if model_type == "gemma":
        return (
            "\n⚠ 도구 호출 시 아래 형식을 사용하세요:\n"
            "<tool_call>{\"name\":\"도구명\",\"arguments\":{...}}</tool_call>\n"
            "여러 도구를 호출하려면 <tool_call>을 여러 번 출력하세요.\n"
        )
    
    # nanbeige, exaone, xlam, generic → Qwen 호환
    return (
        "\n⚠ 도구 호출 시 아래 형식을 사용하세요:\n"
        "<tool_call>{\"name\":\"도구명\",\"arguments\":{...}}</tool_call>\n"
        "여러 도구를 호출하려면 <tool_call>을 여러 번 출력하세요.\n"
    )


def get_tool_response_tag(model_type: str) -> str:
    """모델 타입에 맞는 도구 응답 태그를 반환합니다."""
    if model_type == "qwen":
        return "tool_response"  # <tool_response>...</tool_response>
    return ""  # 일반 텍스트 형식
