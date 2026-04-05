"""
웹/인터넷 도구 - 웹 검색, URL 콘텐츠 추출

검색 전략:
  1차: Tavily API (LLM 최적화, 광고 없음, API 키 필요)
  2차: DuckDuckGo HTML lite (무료, API 키 불필요)
  3차: Google 검색 스크래핑 (fallback)
"""
import re
import json
import urllib.request
import urllib.parse
import html as html_mod
import os
from i18n import t as _t


def _get_llm_config() -> dict:
    """config.json에서 LLM base_url, api_key, model_name 반환"""
    cfg_path = os.path.join(os.path.dirname(__file__), "..", "config.json")
    if not os.path.exists(cfg_path):
        cfg_path = "config.json"
    with open(cfg_path, "r", encoding="utf-8-sig") as f:
        cfg = json.load(f)
    llm_cfg = cfg.get("llm", {})
    provider = llm_cfg.get("provider", "local")
    p = llm_cfg.get("providers", {}).get(provider, {})
    model = p.get("model_name", "") or llm_cfg.get("ollama_model", "")
    base_url = p.get("base_url", "http://10.0.2.2:11434/v1")
    api_key = p.get("api_key", "none")
    if not model:
        raise RuntimeError("config.json에 LLM 모델이 설정되지 않았습니다")
    return {"model": model, "base_url": base_url, "api_key": api_key}


def _get_llm_model() -> str:
    """config.json에서 선택된 LLM 모델명 반환 (없으면 오류)"""
    return _get_llm_config()["model"]


def _clean_html(raw_html: str) -> str:
    """HTML에서 텍스트 추출 (script, style, nav, header, footer 등 비본문 요소 제거)"""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(raw_html, "html.parser")
        # 비본문 요소 제거
        for tag in soup.find_all(["script", "style", "nav", "header", "footer",
                                   "noscript", "svg", "iframe", "form"]):
            tag.decompose()
        # 숨겨진 요소 제거 (display:none, hidden 등)
        for tag in soup.find_all(attrs={"style": re.compile(r"display\s*:\s*none", re.I)}):
            tag.decompose()
        for tag in soup.find_all(attrs={"hidden": True}):
            tag.decompose()
        # class명에 cookie, banner, popup, ad 포함 요소 제거
        for tag in soup.find_all(class_=re.compile(r"cookie|banner|popup|modal|overlay|ad-|ads-", re.I)):
            tag.decompose()
        text = soup.get_text(separator=" ", strip=True)
        return html_mod.unescape(text)
    except ImportError:
        # bs4 없으면 regex 폴백
        text = re.sub(r'<script[^>]*>.*?</script>', '', raw_html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<nav[^>]*>.*?</nav>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<header[^>]*>.*?</header>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<footer[^>]*>.*?</footer>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<noscript[^>]*>.*?</noscript>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()
        return html_mod.unescape(text)


def _http_get(url: str, timeout: int = 10) -> str:
    """HTTP GET 요청"""
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Encoding": "gzip, deflate",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        # gzip 압축 해제
        if resp.headers.get("Content-Encoding") == "gzip":
            import gzip
            raw = gzip.decompress(raw)
        charset = resp.headers.get_content_charset() or "utf-8"
        return raw.decode(charset, errors="replace")


# ─────────────────────────────────────────────
# 검색 엔진들
# ─────────────────────────────────────────────

def _get_tavily_key() -> str:
    """config.json에서 Tavily API 키 반환 (없으면 빈 문자열)"""
    try:
        cfg_path = os.path.join(os.path.dirname(__file__), "..", "config.json")
        if not os.path.exists(cfg_path):
            cfg_path = "config.json"
        with open(cfg_path, "r", encoding="utf-8-sig") as f:
            cfg = json.load(f)
        return cfg.get("search", {}).get("tavily_api_key", "")
    except Exception:
        return ""


def _search_tavily(query: str, max_results: int = 6) -> list:
    """Tavily API 검색 → 구조화된 결과 (광고 없음, LLM 최적화)"""
    api_key = _get_tavily_key()
    if not api_key:
        return []

    results = []
    try:
        payload = json.dumps({
            "query": query,
            "max_results": max_results,
            "include_answer": False,
            "include_images": False,
            "include_raw_content": True,  # 전체 본문 함께 받기 (browse 필요 최소화)
        }).encode("utf-8")

        req = urllib.request.Request(
            "https://api.tavily.com/search",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        for item in data.get("results", [])[:max_results]:
            title = (item.get("title") or "").strip()
            url = (item.get("url") or "").strip()
            content = (item.get("content") or "").strip()
            raw_content = (item.get("raw_content") or "").strip()
            if title and url:
                results.append({
                    "title": title,
                    "url": url,
                    "snippet": content[:300] if content else "",
                    "raw_content": raw_content[:4000] if raw_content else (content[:1500] if content else ""),
                })

    except Exception as e:
        print(f"[web_search] Tavily error: {e}", flush=True)

    return results


def _search_ddg(query: str, max_results: int = 8) -> list:
    """DuckDuckGo HTML lite 검색 → 구조화된 결과"""
    results = []
    try:
        encoded_q = urllib.parse.quote(query)
        url = f"https://html.duckduckgo.com/html/?q={encoded_q}"
        html = _http_get(url)

        # DuckDuckGo HTML lite의 결과 파싱
        # 각 결과는 <a class="result__a" href="...">제목</a> 형태
        links = re.findall(
            r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
            html, re.DOTALL
        )
        snippets = re.findall(
            r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
            html, re.DOTALL
        )

        for i, (link, title) in enumerate(links[:max_results]):
            # URL 디코딩 (DuckDuckGo 리다이렉트 URL 처리)
            actual_url = link
            if "uddg=" in link:
                m = re.search(r'uddg=([^&]+)', link)
                if m:
                    actual_url = urllib.parse.unquote(m.group(1))

            clean_title = _clean_html(title).strip()
            snippet = _clean_html(snippets[i]).strip() if i < len(snippets) else ""

            if clean_title and actual_url:
                results.append({
                    "title": clean_title,
                    "url": actual_url,
                    "snippet": snippet[:200],
                })

    except Exception as e:
        pass  # fallback으로 넘어감

    return results


def _search_google(query: str, max_results: int = 8) -> list:
    """Google 검색 스크래핑 (fallback)"""
    results = []
    try:
        encoded_q = urllib.parse.quote(query)
        url = f"https://www.google.com/search?q={encoded_q}&hl=ko&num={max_results}"
        html = _http_get(url)

        # Google 검색 결과 파싱: <a href="/url?q=...">
        links = re.findall(r'<a[^>]+href="/url\?q=([^&"]+)[^"]*"[^>]*>(.*?)</a>', html, re.DOTALL)

        for link, title_html in links[:max_results]:
            actual_url = urllib.parse.unquote(link)
            clean_title = _clean_html(title_html).strip()

            if clean_title and "google.com" not in actual_url:
                results.append({
                    "title": clean_title,
                    "url": actual_url,
                    "snippet": "",
                })

    except Exception:
        pass

    return results



# ─────────────────────────────────────────────
# 공개 도구 함수
# ─────────────────────────────────────────────

def tool_web_search(query: str) -> str:
    """웹 검색 (Tavily → DuckDuckGo → Google) + 상위 4개 browse + 나머지 URL 안내"""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    # 1차: Tavily (API 키 있으면)
    results = _search_tavily(query)
    used_tavily = bool(results)
    if results:
        print(f"[web_search] Tavily results: {len(results)}", flush=True)
        for i, r in enumerate(results):
            print(f"[web_search]   {i+1}. {r['title'][:50]} → {r['url']}", flush=True)
    else:
        # 2차: DuckDuckGo
        results = _search_ddg(query)
        print(f"[web_search] DDG results: {len(results)}", flush=True)
        for i, r in enumerate(results):
            print(f"[web_search]   {i+1}. {r['title'][:50]} → {r['url']}", flush=True)

    # 3차: Google fallback
    if len(results) < 2:
        g_results = _search_google(query)
        print(f"[web_search] Google results: {len(g_results)}", flush=True)
        results.extend(g_results)

    if not results:
        return _t("web.no_results", query=query)

    # URL 수집 (최대 8개)
    output = [_t("web.search_header", query=query)]
    seen_urls = set()
    all_urls = []

    for r in results:
        if r["url"] in seen_urls or len(all_urls) >= 8:
            continue
        seen_urls.add(r["url"])
        all_urls.append(r)

        idx = len(all_urls)
        line = f"{idx}. **{r['title']}**\n   {r['url']}"
        if r.get("snippet"):
            line += f"\n   {r['snippet']}"
        output.append(line)

    # 브라우즈 전략: 상위 4개 URL browse (병렬 실행)
    browse_count = min(4, len(all_urls))
    print(f"[web_search] all_urls: {len(all_urls)}, browse: {browse_count} (tavily={used_tavily})", flush=True)

    # browse_url로 본문 가져오기
    browse_urls = [r["url"] for r in all_urls[:browse_count]]
    browsed = []

    if browse_urls:
        import time as _time

        def _browse_one(url):
            """경량 browse: daemon(8s) → http_get(8s). SSH 폴백 생략하여 속도 확보."""
            t0 = _time.time()
            # 1차: browser daemon (SSH 폴백 없는 경량 호출)
            try:
                import urllib.request as _ur
                import json as _js
                encoded = urllib.parse.quote(url, safe='')
                daemon_url = f"http://127.0.0.1:9223/fetch?url={encoded}"
                req = _ur.Request(daemon_url)
                with _ur.urlopen(req, timeout=15) as resp:
                    data = _js.loads(resp.read().decode("utf-8"))
                    content = data.get("content", "")
                    screenshot = data.get("screenshot", "")
                    elapsed = _time.time() - t0
                    if content and not content.startswith("❌") and len(content) > 100:
                        print(f"[web_search] 🌐 daemon {url[:60]} → {len(content)}자 ({elapsed:.1f}s) ss={len(screenshot)}B", flush=True)
                        return (url, content[:4000], screenshot)
            except Exception:
                pass
            # 2차: 직접 HTTP GET (빠른 폴백)
            try:
                html = _http_get(url, timeout=5)
                text = _clean_html(html)
                elapsed = _time.time() - t0
                print(f"[web_search] 🌐 http_get {url[:60]} → {len(text) if text else 0}자 ({elapsed:.1f}s)", flush=True)
                if text and len(text) > 100:
                    return (url, text[:4000], "")
            except Exception:
                pass
            return None

        # 병렬 browse (url_idx로 그리드 슬롯 구분)
        screenshots = []  # [(url_idx, screenshot_base64), ...]
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(_browse_one, url): (i, url) for i, url in enumerate(browse_urls)}
            for f in as_completed(futures):
                idx, url = futures[f]
                result = f.result()
                if result:
                    url, content, ss = result
                    browsed.append((url, content))
                    if ss:
                        screenshots.append((idx, ss))

    # Tavily raw_content로 본문 보충 (browse 못한 URL + 나머지 URL)
    if used_tavily:
        browsed_urls = {url for url, _ in browsed}
        for r in all_urls:
            if r["url"] not in browsed_urls and r.get("raw_content"):
                browsed.append((r["url"], r["raw_content"]))

    # 본문 결과 출력
    if browsed:
        output.append(f"\n{'═' * 40}")
        for i, (url, content) in enumerate(browsed):
            output.append(_t("web.result_body", idx=i+1, content=content))
            if i < len(browsed) - 1:
                output.append(f"{'─' * 40}")

    # 나머지 URL 안내 (Tavily raw_content도 없는 URL들)
    all_covered = {url for url, _ in browsed}
    remaining = [r["url"] for r in all_urls if r["url"] not in all_covered]
    if remaining:
        output.append(_t("web.browse_hint"))
        for url in remaining:
            output.append(f"   → {url}")
    elif not browsed:
        output.append(_t("web.extract_fail"))

    text_result = "\n\n".join(output)

    # 스크린샷이 있으면 dict로 반환 (server.py가 browser_frame 이벤트 생성)
    if screenshots:
        return {"text": text_result, "__screenshots__": screenshots}
    return text_result


def _extract_metadata(html: str) -> str:
    """HTML에서 JSON-LD, OpenGraph, meta 태그로 구조화된 메타데이터 추출"""
    meta_parts = []

    # 1. JSON-LD (가장 정확한 구조화 데이터)
    try:
        ld_blocks = re.findall(
            r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
            html, re.DOTALL | re.IGNORECASE
        )
        for block in ld_blocks:
            try:
                data = json.loads(block.strip())
                # 배열인 경우 첫 번째 또는 Book/Product 타입 찾기
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict):
                            data = item
                            break
                if isinstance(data, dict):
                    items = []
                    # 페이지 수
                    pages = data.get("numberOfPages") or data.get("pageCount")
                    if pages:
                        items.append(_t("web.meta_pages", pages=pages))
                    # 저자
                    author = data.get("author")
                    if author:
                        if isinstance(author, dict):
                            author = author.get("name", "")
                        elif isinstance(author, list):
                            author = ", ".join(a.get("name", str(a)) if isinstance(a, dict) else str(a) for a in author[:3])
                        if author:
                            items.append(_t("web.meta_author", author=author))
                    # 출판사
                    publisher = data.get("publisher")
                    if publisher:
                        if isinstance(publisher, dict):
                            publisher = publisher.get("name", "")
                        if publisher:
                            items.append(_t("web.meta_publisher", publisher=publisher))
                    # 발행일
                    date = data.get("datePublished") or data.get("dateCreated")
                    if date:
                        items.append(_t("web.meta_date", date=date))
                    # 가격
                    offers = data.get("offers")
                    if offers:
                        if isinstance(offers, dict):
                            price = offers.get("price")
                            currency = offers.get("priceCurrency", "")
                            if price:
                                items.append(_t("web.meta_price", price=price, currency=currency))
                    # ISBN
                    isbn = data.get("isbn") or data.get("ISBN")
                    if isbn:
                        items.append(f"ISBN: {isbn}")
                    # 설명
                    desc = data.get("description", "")
                    if desc and len(desc) > 10:
                        items.append(_t("web.meta_desc", desc=desc[:200]))
                    if items:
                        meta_parts.append(" | ".join(items))
            except (json.JSONDecodeError, TypeError, AttributeError):
                continue
    except Exception:
        pass

    # 2. OpenGraph + meta description 폴백
    if not meta_parts:
        og_items = []
        # og:description
        og_desc = re.search(r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)', html, re.I)
        if og_desc:
            og_items.append(_t("web.meta_desc", desc=og_desc.group(1).strip()[:200]))
        # meta description
        elif not og_desc:
            meta_desc = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)', html, re.I)
            if meta_desc:
                og_items.append(_t("web.meta_desc", desc=meta_desc.group(1).strip()[:200]))
        if og_items:
            meta_parts.append(" | ".join(og_items))

    return "\n".join(meta_parts)


def _batch_summarize(batch_text: str, query: str) -> str:
    """여러 웹페이지를 한꺼번에 요약 (config LLM, 1회 호출)"""
    try:
        cfg = _get_llm_config()
        url = cfg["base_url"].rstrip("/") + "/chat/completions"
        prompt = (
            f"Search query: {query}\n\n"
            "Below are contents from multiple web pages. "
            "Summarize the key findings from ALL pages in a structured format.\n"
            "Rules:\n"
            "- Include specific facts, numbers, dates, names from each page\n"
            "- Mark which page each fact comes from (PAGE 1, PAGE 2, etc.)\n"
            "- Do NOT fabricate information\n"
            "- Keep total summary under 500 words\n\n"
            f"{batch_text[:16000]}"
        )
        req = urllib.request.Request(
            url,
            data=json.dumps({
                "model": cfg["model"],
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 800,
                "temperature": 0.1,
            }).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {cfg['api_key']}",
            }
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        summary = (data["choices"][0]["message"].get("content") or "").strip()
        if summary and len(summary) > 50:
            return summary
    except Exception:
        pass
    return ""


def _summarize_with_llm(text: str, url: str) -> str:
    """config LLM으로 웹페이지 핵심 내용 요약"""
    try:
        cfg = _get_llm_config()
        api_url = cfg["base_url"].rstrip("/") + "/chat/completions"
        prompt = (
            "Summarize the key content of this webpage in 3-5 lines.\n"
            "Rules:\n"
            "- Ignore menus, ads, navigation, cookie notices\n"
            "- Include factual info (numbers, dates, names) accurately\n"
            "- Do NOT fabricate information\n\n"
            f"Webpage text:\n{text[:4000]}"
        )
        req = urllib.request.Request(
            api_url,
            data=json.dumps({
                "model": cfg["model"],
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 300,
                "temperature": 0.1,
            }).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {cfg['api_key']}",
            }
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        summary = (data["choices"][0]["message"].get("content") or "").strip()
        if summary and len(summary) > 20:
            return f"📄 {url}\n{summary}"
    except Exception:
        pass
    return ""  # 실패 시 빈 문자열 → 폴백


def tool_fetch_url(url: str, max_length: int = 8000) -> str:
    """URL에서 웹 페이지 텍스트 추출 (브라우저 → LLM 요약 → urllib 폴백)"""
    try:
        # 1차: 브라우저로 JS 렌더링된 결과 시도
        browse_result = None
        try:
            browse_result = tool_browse_url(url)
            if browse_result and not browse_result.startswith("❌") and not browse_result.startswith("⏱") and len(browse_result) > 100:
                # LLM 요약으로 핵심만 추출 (토큰 절약)
                summary = _summarize_with_llm(browse_result, url)
                if summary:
                    return summary
                # 요약 실패 → 원본 잘라서 반환
                if len(browse_result) > max_length:
                    browse_result = browse_result[:max_length] + "\n" + _t("web.truncated")
                return browse_result
        except Exception:
            pass  # 브라우저 실패 → urllib 폴백

        # 2차: urllib (정적 HTML)
        html = _http_get(url)

        # <title> 추출
        title_match = re.search(r'<title[^>]*>(.*?)</title>', html, re.DOTALL | re.IGNORECASE)
        title = _clean_html(title_match.group(1)).strip() if title_match else ""

        # 구조화된 메타데이터 추출 (JSON-LD, OG 태그)
        metadata = _extract_metadata(html)

        # <article> 또는 <main> 우선 추출 (본문 영역)
        main_content = ""
        for tag in ["article", "main", "div[^>]+class=\"[^\"]*content[^\"]*\""]:
            match = re.search(
                rf'<{tag}[^>]*>(.*?)</{tag.split("[")[0]}>',
                html, re.DOTALL | re.IGNORECASE
            )
            if match and len(match.group(1)) > 200:
                main_content = match.group(1)
                break

        if main_content:
            text = _clean_html(main_content)
        else:
            text = _clean_html(html)

        # 너무 짧으면 전체 HTML에서 재추출
        if len(text) < 100:
            text = _clean_html(html)

        if len(text) > max_length:
            text = text[:max_length] + "\n" + _t("web.truncated")

        # 헤더 구성: 제목 + 메타데이터 + URL
        header = f"📄 {title}\n" if title else ""
        if metadata:
            header += f"📋 {metadata}\n"
        header += f"🔗 {url}\n{'─' * 40}\n"

        return header + text if text else _t("web.no_text")

    except Exception as e:
        return _t("web.url_error", error=e)


def tool_summarize_url(url: str) -> str:
    """URL 페이지 내용을 요약용으로 추출합니다."""
    try:
        html = _http_get(url, timeout=20)

        # 제목 추출
        title_match = re.search(r'<title[^>]*>(.*?)</title>', html, re.DOTALL | re.IGNORECASE)
        title = _clean_html(title_match.group(1)).strip() if title_match else ""

        # 메타 description
        desc_match = re.search(
            r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)',
            html, re.IGNORECASE
        )
        meta_desc = desc_match.group(1).strip() if desc_match else ""

        # 본문 추출 (article > main > body)
        main_content = ""
        for tag in ["article", "main"]:
            match = re.search(
                rf'<{tag}[^>]*>(.*?)</{tag}>',
                html, re.DOTALL | re.IGNORECASE
            )
            if match and len(match.group(1)) > 200:
                main_content = match.group(1)
                break

        text = _clean_html(main_content) if main_content else _clean_html(html)

        # 핵심 3000자만 추출 (LLM 컨텍스트 절약)
        if len(text) > 3000:
            text = text[:3000] + "..."

        result = _t("web.title", title=title)
        if meta_desc:
            result += _t("web.description", desc=meta_desc)
        result += f"🔗 URL: {url}\n{'─' * 40}\n{text}\n\n"
        result += "💡 위 내용을 바탕으로 사용자에게 요약해주세요."
        return result

    except Exception as e:
        return _t("web.url_summary_error", error=e)


def tool_browse_url(url: str, wait: int = 3) -> str:
    """JavaScript 렌더링이 필요한 페이지를 읽습니다. VM 데몬 우선, SSH 폴백."""
    import urllib.request
    import json as _json

    encoded_url = urllib.parse.quote(url, safe='')

    # 1차: VM 브라우저 데몬 (port 9223)
    try:
        daemon_url = f"http://127.0.0.1:9223/fetch?url={encoded_url}"
        req = urllib.request.Request(daemon_url)
        with urllib.request.urlopen(req, timeout=13) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
            content = data.get("content", "")
            if content and not content.startswith("❌") and len(content) > 50:
                return content
    except Exception:
        pass

    # 3차: VM SSH로 chromium + wget 실행
    try:
        from vm_manager import ssh_exec
        ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        html = ssh_exec(
            f"timeout 15 chromium-browser --headless=new --disable-gpu "
            f"--no-sandbox --disable-blink-features=AutomationControlled "
            f"--user-agent='{ua}' --dump-dom '{url}' 2>/dev/null",
            timeout=20, quiet=True
        )
        if not html or len(html) < 100:
            html = ssh_exec(
                f"wget -qO- --timeout=10 '{url}' 2>/dev/null",
                timeout=15, quiet=True
            )
        if not html:
            return _t("web.page_load_fail", url=url)

        title_match = re.search(r'<title[^>]*>(.*?)</title>', html, re.DOTALL | re.IGNORECASE)
        title = _clean_html(title_match.group(1)).strip() if title_match else ""

        main_content = ""
        for tag in ["article", "main"]:
            match = re.search(
                rf'<{tag}[^>]*>(.*?)</{tag}>',
                html, re.DOTALL | re.IGNORECASE
            )
            if match and len(match.group(1)) > 200:
                main_content = match.group(1)
                break

        text = _clean_html(main_content) if main_content else _clean_html(html)

        if len(text) > 8000:
            text = text[:8000] + "\n" + _t("web.truncated")

        header = f"🌐 {title}\n🔗 {url}\n{'─' * 40}\n" if title else f"🔗 {url}\n{'─' * 40}\n"
        return header + text if text else _t("web.no_text")

    except ImportError:
        return _t("web.vm_connect_fail", url=url)
    except Exception as e:
        return _t("web.browser_error", error=e)

