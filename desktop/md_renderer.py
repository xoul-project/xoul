"""
Xoul Desktop Client — 마크다운 → HTML 변환 + 코드 하이라이팅
"""

import markdown
from markdown.extensions.codehilite import CodeHiliteExtension
from markdown.extensions.fenced_code import FencedCodeExtension
from markdown.extensions.tables import TableExtension
from markdown.extensions.nl2br import Nl2BrExtension

from styles import BUBBLE_CSS


# Pygments 다크 테마 CSS (Monokai 기반, Catppuccin 조화)
PYGMENTS_CSS = """
.codehilite { background: #181825; border-radius: 8px; padding: 12px; overflow-x: auto; margin: 8px 0; }
.codehilite pre { margin: 0; }
.codehilite .hll { background-color: #313244 }
.codehilite .c { color: #6c7086; font-style: italic }
.codehilite .k { color: #cba6f7; font-weight: bold }
.codehilite .o { color: #89dceb }
.codehilite .p { color: #cdd6f4 }
.codehilite .cm { color: #6c7086; font-style: italic }
.codehilite .cp { color: #f9e2af }
.codehilite .c1 { color: #6c7086; font-style: italic }
.codehilite .cs { color: #6c7086; font-style: italic }
.codehilite .gd { color: #f38ba8 }
.codehilite .gi { color: #a6e3a1 }
.codehilite .ge { font-style: italic }
.codehilite .gs { font-weight: bold }
.codehilite .kc { color: #cba6f7 }
.codehilite .kd { color: #cba6f7 }
.codehilite .kn { color: #f5c2e7 }
.codehilite .kp { color: #cba6f7 }
.codehilite .kr { color: #cba6f7; font-weight: bold }
.codehilite .kt { color: #f9e2af }
.codehilite .m { color: #fab387 }
.codehilite .s { color: #a6e3a1 }
.codehilite .na { color: #89b4fa }
.codehilite .nb { color: #f5e0dc }
.codehilite .nc { color: #f9e2af; font-weight: bold }
.codehilite .no { color: #fab387 }
.codehilite .nd { color: #89dceb }
.codehilite .ni { color: #cdd6f4; font-weight: bold }
.codehilite .nf { color: #89b4fa }
.codehilite .nl { color: #cdd6f4 }
.codehilite .nn { color: #f9e2af }
.codehilite .nt { color: #cba6f7 }
.codehilite .nv { color: #cdd6f4 }
.codehilite .ow { color: #89dceb }
.codehilite .w { color: #cdd6f4 }
.codehilite .mb { color: #fab387 }
.codehilite .mf { color: #fab387 }
.codehilite .mh { color: #fab387 }
.codehilite .mi { color: #fab387 }
.codehilite .mo { color: #fab387 }
.codehilite .sa { color: #a6e3a1 }
.codehilite .sb { color: #a6e3a1 }
.codehilite .sc { color: #a6e3a1 }
.codehilite .dl { color: #a6e3a1 }
.codehilite .sd { color: #6c7086; font-style: italic }
.codehilite .s2 { color: #a6e3a1 }
.codehilite .se { color: #fab387 }
.codehilite .sh { color: #a6e3a1 }
.codehilite .si { color: #fab387 }
.codehilite .sx { color: #a6e3a1 }
.codehilite .sr { color: #f5c2e7 }
.codehilite .s1 { color: #a6e3a1 }
.codehilite .ss { color: #a6e3a1 }
.codehilite .bp { color: #f5e0dc }
.codehilite .fm { color: #89b4fa }
.codehilite .vc { color: #cdd6f4 }
.codehilite .vg { color: #cdd6f4 }
.codehilite .vi { color: #cdd6f4 }
.codehilite .vm { color: #cdd6f4 }
.codehilite .il { color: #fab387 }
"""

# 마크다운 변환기
_md = markdown.Markdown(
    extensions=[
        FencedCodeExtension(),
        CodeHiliteExtension(guess_lang=True, css_class="codehilite"),
        TableExtension(),
        Nl2BrExtension(),
    ]
)

# 전체 CSS (버블 + 코드 하이라이팅)
_FULL_CSS = f"<style>{BUBBLE_CSS}\n{PYGMENTS_CSS}</style>"

import re

_URL_RE = re.compile(
    r'(https?://[^\s<>\'")\]]+|www\.[^\s<>\'")\]]+)',
)

# <a> 태그 매칭 (이미 링크된 부분 건너뛰기)
_A_TAG_RE = re.compile(r'(<a\s[^>]*>.*?</a>)', re.DOTALL)


def _auto_link_urls(html_text: str) -> str:
    """HTML에서 <a> 태그 안에 없는 bare URL을 클릭 가능한 링크로 변환"""
    parts = _A_TAG_RE.split(html_text)
    result = []
    for i, part in enumerate(parts):
        if i % 2 == 1:
            # 이미 <a> 태그로 감싸진 부분 — 그대로 유지
            result.append(part)
        else:
            # <a> 태그 외부 — bare URL을 링크로 변환
            def replacer(m):
                url = m.group(1)
                # 끝에 붙은 구두점 제거
                while url and url[-1] in '.,;:!?)':
                    url = url[:-1]
                href = url if url.startswith("http") else f"https://{url}"
                return f'<a href="{href}" target="_blank">{url}</a>'
            result.append(_URL_RE.sub(replacer, part))
    return "".join(result)


def _clean_broken_html(text: str) -> str:
    """LLM이 출력하는 깨진 HTML 조각 정리"""
    # 패턴: url">텍스트  →  url (텍스트)
    text = re.sub(r'">\s*([^<\n]+)', r' (\1)', text)
    # 남은 깨진 태그 조각
    text = re.sub(r'</?a[^>]*>', '', text)
    return text


def render_markdown(text: str) -> str:
    """마크다운 텍스트를 HTML로 변환"""
    text = _clean_broken_html(text)
    _md.reset()
    html = _md.convert(text)
    html = _auto_link_urls(html)
    return html


def wrap_user_bubble(text: str) -> str:
    """유저 메시지를 버블 HTML로 감싸기"""
    import html
    safe = html.escape(text)
    return f'<div class="user-bubble">{safe}</div>'


def wrap_bot_bubble(md_text: str) -> str:
    """봇 메시지를 마크다운 렌더링 + 버블 HTML로 감싸기"""
    html_content = render_markdown(md_text)
    return f'<div class="bot-bubble">{html_content}</div>'


def wrap_tool_chip(tool_name: str, args: dict = None) -> str:
    """도구 실행 칩"""
    import html
    label = html.escape(tool_name)
    detail = ""
    if args:
        # 주요 인자 하나만 표시
        for key in ("query", "url", "command", "expression", "key", "path"):
            if key in args:
                val = str(args[key])[:40]
                detail = f': "{html.escape(val)}"'
                break
    return f'<div class="tool-chip">🔧 {label}{detail}</div>'


def wrap_tool_result_chip(tool_name: str, result: str) -> str:
    """도구 결과 칩"""
    import html
    short = html.escape(result[:80])
    return f'<div class="tool-chip-result">📋 {html.escape(tool_name)}: {short}</div>'


def build_full_html(body_html: str) -> str:
    """전체 HTML 페이지 빌드 (CSS 포함)"""
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8">{_FULL_CSS}</head>
<body>{body_html}</body>
</html>"""
