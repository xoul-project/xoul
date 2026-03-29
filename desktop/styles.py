"""
Xoul Desktop Client — Premium Black + Gray 테마
"""

# ─────────────────────────────────────────────
# Premium Black + Gray 팔레트
# ─────────────────────────────────────────────

COLORS = {
    "rosewater": "#e8d5d0",
    "flamingo": "#e0c0c0",
    "pink": "#d4a0b8",
    "mauve": "#b090d0",
    "red": "#e06070",
    "maroon": "#d08090",
    "peach": "#d0a070",
    "yellow": "#d0c090",
    "green": "#70c080",
    "teal": "#60b0a0",
    "sky": "#60b0d0",
    "sapphire": "#5098c0",
    "blue": "#6090d0",
    "lavender": "#90a0d0",
    "text": "#e8e8e8",
    "subtext1": "#c0c0c0",
    "subtext0": "#a0a0a0",
    "overlay2": "#808080",
    "overlay1": "#606060",
    "overlay0": "#505050",
    "surface2": "#2a2a2a",
    "surface1": "#1e1e1e",
    "surface0": "#161616",
    "base": "#0d0d0d",
    "mantle": "#080808",
    "crust": "#050505",
}

C = COLORS  # 축약

# ─────────────────────────────────────────────
# 채팅 창 스타일
# ─────────────────────────────────────────────

CHAT_WINDOW_QSS = f"""
QWidget#ChatWindow {{
    background-color: {C['base']};
    border-radius: 16px;
}}

/* 타이틀바 */
QWidget#TitleBar {{
    background-color: {C['mantle']};
    border-top-left-radius: 16px;
    border-top-right-radius: 16px;
    padding: 8px 12px;
}}
QLabel#TitleLabel {{
    color: {C['text']};
    font-size: 14px;
    font-weight: bold;
    font-family: 'Segoe UI', sans-serif;
}}
QLabel#StatusLabel {{
    color: {C['green']};
    font-size: 11px;
    font-family: 'Segoe UI', sans-serif;
}}
QPushButton#TitleButton {{
    background: transparent;
    color: {C['overlay1']};
    border: none;
    font-size: 14px;
    padding: 4px 8px;
    border-radius: 6px;
}}
QPushButton#TitleButton:hover {{
    background-color: {C['surface0']};
    color: {C['text']};
}}

/* 채팅 영역 */
QScrollArea#ChatScroll {{
    background-color: {C['base']};
    border: none;
}}
QWidget#ChatContainer {{
    background-color: {C['base']};
}}

/* 입력 필드 */
QLineEdit#ChatInput {{
    background-color: {C['surface0']};
    color: {C['text']};
    border: 2px solid {C['surface1']};
    border-radius: 12px;
    padding: 10px 16px;
    font-size: 14px;
    font-family: 'Segoe UI', sans-serif;
}}
QLineEdit#ChatInput:focus {{
    border: 2px solid {C['blue']};
}}
QLineEdit#ChatInput::placeholder {{
    color: {C['overlay0']};
}}
"""


# ─────────────────────────────────────────────
# Spotlight 입력바 스타일
# ─────────────────────────────────────────────

INPUT_BAR_QSS = f"""
QWidget#InputBar {{
    background-color: {C['mantle']};
    border-radius: 14px;
    border: 1px solid {C['surface1']};
}}
QLineEdit#SpotlightInput {{
    background-color: transparent;
    color: {C['text']};
    border: none;
    font-size: 16px;
    font-family: 'Segoe UI', sans-serif;
    padding: 0px;
    selection-background-color: {C['blue']};
}}
QLineEdit#SpotlightInput::placeholder {{
    color: {C['overlay0']};
}}
QLabel#SpotlightIcon {{
    color: {C['blue']};
    font-size: 20px;
}}
"""


# ─────────────────────────────────────────────
# 채팅 버블 HTML/CSS
# ─────────────────────────────────────────────

BUBBLE_CSS = f"""
body {{
    background-color: {C['base']};
    margin: 0;
    padding: 0;
    font-family: 'Segoe UI', -apple-system, sans-serif;
    font-size: 14px;
    color: {C['text']};
    line-height: 1.5;
}}
.user-bubble {{
    background-color: {C['blue']};
    color: {C['crust']};
    border-radius: 16px 16px 4px 16px;
    padding: 10px 16px;
    margin: 4px 8px 12px auto;
    max-width: 80%;
    width: fit-content;
    word-wrap: break-word;
    font-weight: 500;
}}
.bot-bubble {{
    background-color: {C['surface0']};
    color: {C['text']};
    border-radius: 16px 16px 16px 4px;
    padding: 12px 16px;
    margin: 4px auto 12px 8px;
    max-width: 88%;
    width: fit-content;
    word-wrap: break-word;
}}
.bot-bubble h2 {{
    font-size: 15px;
    margin: 8px 0 4px 0;
    color: {C['blue']};
}}
.bot-bubble h3 {{
    font-size: 14px;
    margin: 6px 0 3px 0;
    color: {C['lavender']};
}}
.bot-bubble strong {{
    color: {C['peach']};
}}
.bot-bubble em {{
    color: {C['pink']};
}}
.bot-bubble a {{
    color: {C['sapphire']};
    text-decoration: none;
}}
.bot-bubble a:hover {{
    text-decoration: underline;
}}
.bot-bubble ul, .bot-bubble ol {{
    padding-left: 20px;
    margin: 4px 0;
}}
.bot-bubble li {{
    margin: 2px 0;
}}
.bot-bubble table {{
    border-collapse: collapse;
    width: 100%;
    margin: 8px 0;
}}
.bot-bubble th {{
    background-color: {C['surface1']};
    padding: 6px 10px;
    text-align: left;
    font-size: 12px;
    color: {C['subtext1']};
    border-bottom: 1px solid {C['surface2']};
}}
.bot-bubble td {{
    padding: 5px 10px;
    border-bottom: 1px solid {C['surface1']};
    font-size: 13px;
}}
.bot-bubble hr {{
    border: none;
    border-top: 1px solid {C['surface1']};
    margin: 10px 0;
}}
/* 코드 블록 — Pygments에서 별도 스타일 */
.bot-bubble code {{
    background-color: {C['mantle']};
    color: {C['peach']};
    padding: 2px 6px;
    border-radius: 4px;
    font-family: 'Cascadia Code', 'Consolas', monospace;
    font-size: 13px;
}}
.bot-bubble pre {{
    background-color: {C['mantle']};
    border-radius: 8px;
    padding: 12px;
    overflow-x: auto;
    margin: 8px 0;
}}
.bot-bubble pre code {{
    background: transparent;
    padding: 0;
    color: {C['text']};
}}
@keyframes tool-spin {{
    from {{ transform: rotate(0deg); }}
    to {{ transform: rotate(360deg); }}
}}
@keyframes tool-pulse {{
    0%, 100% {{ opacity: 0.7; }}
    50% {{ opacity: 1; }}
}}
.tool-chip {{
    display: inline-block;
    background-color: {C['surface1']};
    color: {C['yellow']};
    font-size: 12px;
    padding: 3px 10px;
    border-radius: 10px;
    margin: 3px 8px;
    transition: all 0.3s ease;
}}
.tool-chip.active {{
    border: 1px solid {C['yellow']}44;
    animation: tool-pulse 1.5s ease-in-out infinite;
}}
.tool-chip.done {{
    animation: none;
    border: none;
    opacity: 0.7;
}}
.tool-chip .tool-spinner {{
    display: inline-block;
    animation: tool-spin 1s linear infinite;
    margin-right: 2px;
}}
.tool-chip .tool-elapsed {{
    color: {C['subtext0']};
    font-size: 11px;
    margin-left: 4px;
}}
.tool-chip-result {{
    display: inline-block;
    background-color: {C['surface0']};
    color: {C['subtext0']};
    font-size: 11px;
    padding: 2px 8px;
    border-radius: 8px;
    margin: 2px 8px;
}}
.timestamp {{
    color: {C['overlay0']};
    font-size: 11px;
    text-align: center;
    margin: 12px 0 4px 0;
}}
"""
