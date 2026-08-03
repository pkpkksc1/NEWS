import hashlib
import html
import json
import os
import re
import smtplib
import time
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from openai import OpenAI
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright
from pypinyin import Style, lazy_pinyin


BAIDU_URL = "https://top.baidu.com/board?tab=realtime"
OUTPUT_FILE = Path("products.json")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini").strip()

EMAIL_USER = os.getenv("EMAIL_USER", "").strip()
EMAIL_APP_PASSWORD = os.getenv("EMAIL_APP_PASSWORD", "").replace(" ", "").strip()
EMAIL_TO = os.getenv("EMAIL_TO", "").strip()


def clean_text(value: str) -> str:
    """공백과 줄바꿈을 정리합니다."""
    return re.sub(r"\s+", " ", value or "").strip()


def is_possible_news_title(title: str, href: str) -> bool:
    """링크가 바이두 인기 검색어 제목인지 판단합니다."""
    if not title or not href:
        return False

    normalized = re.sub(r"[>＞]+$", "", clean_text(title)).strip()
    excluded_titles = {
        "首页", "热搜榜", "民生榜", "财经榜", "体育榜", "文娱榜", "国际榜",
        "挑战榜", "电影榜", "电视剧榜", "小说榜", "短剧榜", "查看更多",
        "榜单规则", "查看详情", "更多"
    }

    if normalized in excluded_titles:
        return False
    if normalized.isdigit():
        return False
    if len(normalized) < 2 or len(normalized) > 100:
        return False

    return (
        "baidu.com/s?" in href
        or "baidu.com/from=" in href
        or "baijiahao.baidu.com" in href
    )


def extract_summary_from_anchor(anchor: Any, title: str) -> str:
    """제목 주변에서 바이두의 짧은 설명을 찾습니다."""
    try:
        text = anchor.evaluate(
            """
            (element) => {
                let current = element;
                for (let i = 0; i < 8 && current; i += 1) {
                    const value = (current.innerText || "").trim();
                    if (value.includes("热搜指数") && value.length < 1500) {
                        return value;
                    }
                    current = current.parentElement;
                }
                return "";
            }
            """
        )
    except Exception:
        return ""

    lines = [clean_text(line) for line in str(text).splitlines() if clean_text(line)]
    candidates: list[str] = []

    for line in lines:
        normalized = re.sub(r"[>＞]+$", "", line).strip()
        if normalized == title:
            continue
        if normalized in {"热", "新", "爆", "沸", "热搜指数", "查看更多", "查看详情"}:
            continue
        if re.fullmatch(r"[\d.,万亿]+", normalized):
            continue
        if len(normalized) < 12:
            continue
        candidates.append(normalized)

    if not candidates:
        return ""

    # 제목 주변에 표시되는 여러 설명 문장을 순서대로 합쳐 더 많은 원문을 확보합니다.
    unique_candidates: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate not in seen:
            unique_candidates.append(candidate)
            seen.add(candidate)

    return " ".join(unique_candidates)[:2400]


def fetch_baidu_top10() -> list[dict[str, Any]]:
    """바이두 실시간 검색어 TOP10을 가져옵니다."""
    results: list[dict[str, Any]] = []
    used_titles: set[str] = set()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        context = browser.new_context(
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            viewport={"width": 1440, "height": 1200},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()

        try:
            page.goto(BAIDU_URL, wait_until="domcontentloaded", timeout=60000)
            try:
                page.wait_for_load_state("networkidle", timeout=20000)
            except PlaywrightTimeoutError:
                pass
            page.wait_for_timeout(4000)

            for anchor in page.locator("a").all():
                try:
                    title = clean_text(anchor.inner_text(timeout=2000))
                    href = anchor.get_attribute("href") or ""
                except Exception:
                    continue

                if not is_possible_news_title(title, href):
                    continue

                title = re.sub(r"\s+(热|新|爆|沸)$", "", title).strip()
                title = re.sub(r"[>＞]+$", "", title).strip()

                if title in used_titles:
                    continue

                results.append(
                    {
                        "rank": len(results) + 1,
                        "chinese": title,
                        "sourceSummary": extract_summary_from_anchor(anchor, title),
                        "url": href,
                    }
                )
                used_titles.add(title)

                if len(results) >= 10:
                    break
        finally:
            context.close()
            browser.close()

    if len(results) < 10:
        raise RuntimeError(f"바이두 TOP10을 충분히 가져오지 못했습니다. 가져온 항목: {len(results)}개")

    return results


def make_pinyin(text: str) -> str:
    """중국어 문장을 성조가 포함된 병음으로 바꿉니다."""
    result = lazy_pinyin(
        text,
        style=Style.TONE,
        neutral_tone_with_five=False,
        errors=lambda value: list(value),
    )
    return " ".join(result)


def extract_json_object(text: str) -> dict[str, Any]:
    """모델 응답에서 JSON 객체를 안전하게 추출합니다."""
    value = text.strip()
    value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\s*```$", "", value)

    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        start = value.find("{")
        end = value.rfind("}")
        if start < 0 or end <= start:
            raise RuntimeError("GPT 응답에서 JSON을 찾지 못했습니다.")
        parsed = json.loads(value[start : end + 1])

    if not isinstance(parsed, dict):
        raise RuntimeError("GPT 응답의 최상위 값이 JSON 객체가 아닙니다.")
    return parsed


def build_learning_prompt(raw_news: list[dict[str, Any]]) -> str:
    """TOP10 전체를 한 번에 처리할 프롬프트를 만듭니다."""
    source_items = [
        {
            "rank": item["rank"],
            "chinese": item["chinese"],
            "sourceSummary": item.get("sourceSummary", ""),
        }
        for item in raw_news
    ]

    return f"""
당신은 중국어 뉴스 학습자료 편집자입니다.
아래 바이두 실시간 검색어 10개를 한국인 학습자용으로 정확하고 자연스럽게 가공하세요.

중요 규칙:
1. 입력의 rank와 chinese는 절대 변경하지 마세요.
2. translation은 제목의 자연스러운 한국어 번역입니다.
3. summary는 sourceSummary가 있으면 그 내용만 이용해 자연스러운 한국어 4~6문장으로 자세히 설명하세요.
   중요한 배경, 사건, 반응, 의미가 있으면 빠뜨리지 마세요. 원문에 없는 사실은 절대 추가하지 마세요.
   sourceSummary가 비어 있으면 추측하지 말고 "바이두에 상세 설명이 표시되지 않았습니다."라고 쓰세요.
4. keyPoints는 sourceSummary에서 확인되는 핵심 내용을 한국어로 정확히 3개 작성하세요. 각 항목은 완전한 문장으로 쓰세요.
   sourceSummary가 비어 있으면 빈 배열로 출력하세요.
5. expressions는 제목에서 학습 가치가 높은 중국어 표현 2개를 고르세요. 제목이 매우 짧아 2개가 어렵다면 1개만 출력해도 됩니다.
6. 각 expression.example은 실제로 자연스러운 짧은 중국어 예문이어야 합니다.
7. words는 제목을 의미 단위로 나눈 핵심 단어입니다. 조사·숫자만 있는 항목과 문장부호는 제외하고 최대 8개만 출력하세요.
8. words.meaning은 해당 뉴스 제목 문맥에 맞는 한국어 뜻이어야 합니다.
9. 병음은 출력하지 마세요. 프로그램이 별도로 생성합니다.
10. summary와 keyPoints에서 같은 문장을 반복하지 마세요.
11. 반드시 설명 없이 유효한 JSON 하나만 출력하세요.

출력 형식:
{{
  "news": [
    {{
      "rank": 1,
      "chinese": "입력 제목 그대로",
      "translation": "자연스러운 한국어 제목",
      "summary": "한국어 요약",
      "expression": {{
        "chinese": "핵심 표현",
        "meaning": "문맥에 맞는 한국어 뜻",
        "example": "자연스러운 중국어 예문",
        "exampleMeaning": "예문의 자연스러운 한국어 뜻"
      }},
      "words": [
        {{"chinese": "단어", "meaning": "문맥에 맞는 뜻"}}
      ]
    }}
  ]
}}

입력 데이터:
{json.dumps(source_items, ensure_ascii=False, indent=2)}
""".strip()


def validate_and_merge_learning_data(
    raw_news: list[dict[str, Any]],
    model_data: dict[str, Any],
) -> list[dict[str, Any]]:
    """GPT 결과를 검사하고 URL·병음을 결합합니다."""
    model_news = model_data.get("news")
    if not isinstance(model_news, list) or len(model_news) != len(raw_news):
        raise RuntimeError("GPT가 반환한 뉴스 개수가 10개가 아닙니다.")

    by_rank = {item.get("rank"): item for item in model_news if isinstance(item, dict)}
    result: list[dict[str, Any]] = []

    for raw in raw_news:
        rank = raw["rank"]
        generated = by_rank.get(rank)
        if not isinstance(generated, dict):
            raise RuntimeError(f"GPT 응답에서 {rank}위 항목을 찾지 못했습니다.")

        title = raw["chinese"]
        if clean_text(str(generated.get("chinese", ""))) != title:
            raise RuntimeError(f"GPT가 {rank}위 중국어 제목을 변경했습니다.")

        translation = clean_text(str(generated.get("translation", "")))
        summary = clean_text(str(generated.get("summary", "")))
        key_points_raw = generated.get("keyPoints", [])
        expressions_raw = generated.get("expressions")
        # 이전 형식과도 호환합니다.
        if not isinstance(expressions_raw, list):
            legacy_expression = generated.get("expression")
            expressions_raw = [legacy_expression] if isinstance(legacy_expression, dict) else []
        words_raw = generated.get("words")

        if not translation or not summary:
            raise RuntimeError(f"GPT의 {rank}위 번역 또는 요약이 비어 있습니다.")
        if not isinstance(key_points_raw, list):
            raise RuntimeError(f"GPT의 {rank}위 핵심 내용 형식이 잘못되었습니다.")
        key_points = [
            clean_text(str(point))
            for point in key_points_raw
            if clean_text(str(point))
        ][:4]

        if not expressions_raw:
            raise RuntimeError(f"GPT의 {rank}위 핵심 표현이 비어 있습니다.")
        if not isinstance(words_raw, list) or not words_raw:
            raise RuntimeError(f"GPT의 {rank}위 단어 목록이 비어 있습니다.")

        expressions: list[dict[str, str]] = []
        for expression_raw in expressions_raw[:2]:
            if not isinstance(expression_raw, dict):
                continue
            expression_chinese = clean_text(str(expression_raw.get("chinese", "")))
            expression = {
                "chinese": expression_chinese,
                "pinyin": make_pinyin(expression_chinese),
                "meaning": clean_text(str(expression_raw.get("meaning", ""))),
                "example": clean_text(str(expression_raw.get("example", ""))),
                "exampleMeaning": clean_text(str(expression_raw.get("exampleMeaning", ""))),
            }
            if all(expression.values()):
                expressions.append(expression)
        if not expressions:
            raise RuntimeError(f"GPT의 {rank}위 핵심 표현에 유효한 값이 없습니다.")

        words: list[dict[str, str]] = []
        seen_words: set[str] = set()
        for word in words_raw:
            if not isinstance(word, dict):
                continue
            chinese = clean_text(str(word.get("chinese", "")))
            meaning = clean_text(str(word.get("meaning", "")))
            if not chinese or not meaning or chinese in seen_words:
                continue
            if re.fullmatch(r"[\W_]+", chinese):
                continue
            words.append(
                {
                    "chinese": chinese,
                    "pinyin": make_pinyin(chinese),
                    "meaning": meaning,
                }
            )
            seen_words.add(chinese)

        if not words:
            raise RuntimeError(f"GPT의 {rank}위 유효 단어 목록이 비어 있습니다.")

        result.append(
            {
                "rank": rank,
                "chinese": title,
                "pinyin": make_pinyin(title),
                "translation": translation,
                "summary": summary,
                "sourceExcerpt": clean_text(str(raw.get("sourceSummary", ""))),
                "keyPoints": key_points,
                "url": raw["url"],
                # 기존 화면과의 호환을 위해 첫 표현도 expression에 저장합니다.
                "expression": expressions[0],
                "expressions": expressions,
                "words": words,
            }
        )

    return result


def create_learning_data(raw_news: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """OpenAI API로 TOP10 학습자료를 한 번에 생성합니다."""
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY가 설정되지 않았습니다.")

    client = OpenAI(api_key=OPENAI_API_KEY, timeout=180.0, max_retries=1)
    prompt = build_learning_prompt(raw_news)
    last_error: Exception | None = None

    for attempt in range(2):
        try:
            print(f"GPT API 호출 {attempt + 1}/2 · 모델: {OPENAI_MODEL}")
            response = client.responses.create(
                model=OPENAI_MODEL,
                instructions=(
                    "중국어 뉴스 학습자료를 만드는 전문 편집자입니다. "
                    "사실을 추측하지 않고, 요청된 JSON 형식만 반환합니다."
                ),
                input=prompt,
            )
            if not response.output_text:
                raise RuntimeError("GPT 응답이 비어 있습니다.")
            parsed = extract_json_object(response.output_text)
            if getattr(response, "usage", None):
                print(f"API 사용량: {response.usage}")
            return validate_and_merge_learning_data(raw_news, parsed)
        except Exception as error:
            last_error = error
            print(f"GPT 처리 실패 {attempt + 1}/3: {error}")
            if attempt < 1:
                time.sleep(4)

    raise RuntimeError(f"GPT 학습자료 생성에 최종 실패했습니다: {last_error}")


def make_source_fingerprint(raw_news: list[dict[str, Any]]) -> str:
    """제목과 원문 설명이 이전 실행과 같은지 확인할 해시를 만듭니다."""
    payload = [
        {
            "rank": item["rank"],
            "chinese": clean_text(str(item.get("chinese", ""))),
            "sourceSummary": clean_text(str(item.get("sourceSummary", ""))),
        }
        for item in raw_news
    ]
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_cached_news(fingerprint: str) -> list[dict[str, Any]] | None:
    """동일한 뉴스라면 기존 GPT 결과를 재사용합니다."""
    if not OUTPUT_FILE.exists():
        return None
    try:
        existing = json.loads(OUTPUT_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if existing.get("sourceFingerprint") != fingerprint:
        return None
    cached_news = existing.get("news")
    if not isinstance(cached_news, list) or len(cached_news) != 10:
        return None
    return cached_news


def save_products_json(news: list[dict[str, Any]], fingerprint: str, api_used: bool) -> dict[str, Any]:
    """홈페이지가 읽는 JSON 파일을 원자적으로 저장합니다."""
    now = datetime.now(ZoneInfo("Asia/Seoul"))
    data = {
        "updatedAt": now.strftime("%Y-%m-%d %H:%M"),
        "source": "Baidu Hot Search",
        "sourceUrl": BAIDU_URL,
        "translationMethod": f"OpenAI API ({OPENAI_MODEL})",
        "sourceFingerprint": fingerprint,
        "apiUsedThisRun": api_used,
        "news": news,
    }

    temporary_file = OUTPUT_FILE.with_suffix(".json.tmp")
    temporary_file.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary_file.replace(OUTPUT_FILE)
    return data


def make_word_html(words: list[dict[str, Any]]) -> str:
    """이메일용 단어 목록을 만듭니다."""
    items = []
    for word in words:
        chinese = html.escape(str(word.get("chinese", "")))
        pinyin = html.escape(str(word.get("pinyin", "")))
        meaning = html.escape(str(word.get("meaning", "")))
        items.append(
            f"""
            <div style="border:1px solid #e5e7eb;border-radius:10px;padding:12px;margin:8px 0;background:#fafafa;">
                <div style="font-size:22px;font-weight:700;">{chinese}</div>
                <div style="margin-top:4px;color:#315efb;font-size:18px;font-weight:600;">{pinyin}</div>
                <div style="margin-top:5px;color:#475467;font-size:17px;">{meaning}</div>
            </div>
            """
        )
    return "".join(items)


def make_email_html(data: dict[str, Any]) -> str:
    """TOP10 전체를 이메일 HTML로 만듭니다."""
    cards = []
    for item in data["news"]:
        rank = int(item.get("rank", 0))
        chinese = html.escape(str(item.get("chinese", "")))
        pinyin = html.escape(str(item.get("pinyin", "")))
        translation = html.escape(str(item.get("translation", "")))
        summary = html.escape(str(item.get("summary", "")))
        source_excerpt = html.escape(str(item.get("sourceExcerpt", "")))
        key_points = item.get("keyPoints", [])
        expression = item.get("expression", {})
        words = item.get("words", [])

        key_points_html = ""
        if key_points:
            key_points_html = "<ul style=\"margin:0 0 20px;padding-left:22px;font-size:16px;line-height:1.8;\">" + "".join(
                f"<li>{html.escape(str(point))}</li>" for point in key_points
            ) + "</ul>"

        source_excerpt_html = ""
        if source_excerpt:
            source_excerpt_html = f"""
                <h3 style="margin:0 0 6px;color:#7c3aed;font-size:15px;">중국어 원문 발췌</h3>
                <p style="margin:0 0 18px;padding:14px;border-radius:10px;background:#f7f2ff;font-size:16px;line-height:1.9;">{source_excerpt}</p>
            """

        expression_html = f"""
            <div style="margin:0 0 18px;padding:14px;border-radius:12px;background:#fff8dc;">
                <h3 style="margin:0 0 8px;color:#8a6500;font-size:15px;">핵심 표현</h3>
                <div style="font-size:23px;font-weight:700;">{html.escape(str(expression.get('chinese', '')))}</div>
                <div style="margin-top:4px;color:#315efb;font-size:17px;">{html.escape(str(expression.get('pinyin', '')))}</div>
                <div style="margin-top:5px;color:#16794a;font-size:16px;">{html.escape(str(expression.get('meaning', '')))}</div>
                <div style="margin-top:10px;font-size:15px;line-height:1.7;">{html.escape(str(expression.get('example', '')))}<br>{html.escape(str(expression.get('exampleMeaning', '')))}</div>
            </div>
        """

        cards.append(
            f"""
            <section style="border:1px solid #e5e7eb;border-radius:16px;margin:0 0 20px;overflow:hidden;background:#ffffff;">
                <div style="padding:18px;background:#f7f9ff;">
                    <div style="color:#e5484d;font-size:15px;font-weight:700;">{rank}위</div>
                    <h2 style="margin:6px 0 0;color:#18202f;font-size:23px;line-height:1.5;">{chinese}</h2>
                </div>
                <div style="padding:18px;">
                    <h3 style="margin:0 0 6px;color:#2149d8;font-size:15px;">병음</h3>
                    <p style="margin:0 0 18px;color:#315efb;font-size:18px;font-weight:600;line-height:1.7;">{pinyin}</p>
                    {expression_html}
                    <h3 style="margin:0 0 6px;color:#16794a;font-size:15px;">한국어 해석</h3>
                    <p style="margin:0 0 18px;font-size:17px;line-height:1.7;">{translation}</p>
                    {source_excerpt_html}
                    <h3 style="margin:0 0 6px;color:#8a6500;font-size:15px;">자세한 내용</h3>
                    <p style="margin:0 0 18px;font-size:16px;line-height:1.85;">{summary}</p>
                    <h3 style="margin:0 0 6px;color:#b54708;font-size:15px;">핵심 내용</h3>
                    {key_points_html}
                    <h3 style="margin:0 0 9px;font-size:18px;">전체 단어 {len(words)}개</h3>
                    {make_word_html(words)}
                </div>
            </section>
            """
        )

    updated_at = html.escape(str(data["updatedAt"]))
    method = html.escape(str(data.get("translationMethod", "OpenAI API")))
    return f"""
    <!DOCTYPE html>
    <html lang="ko">
    <body style="margin:0;padding:0;background:#f4f6fa;font-family:Arial,sans-serif;color:#18202f;">
        <div style="max-width:760px;margin:0 auto;padding:24px 14px;">
            <header style="padding:22px;margin-bottom:20px;border-radius:16px;background:#315efb;color:#ffffff;">
                <div style="font-size:14px;font-weight:700;">百度热搜 TOP 10</div>
                <h1 style="margin:6px 0 8px;font-size:29px;">바이두 실시간 중국어</h1>
                <div style="font-size:14px;">업데이트: {updated_at}</div>
                <div style="margin-top:6px;font-size:12px;opacity:0.85;">{method}</div>
            </header>
            {''.join(cards)}
        </div>
    </body>
    </html>
    """


def send_email(data: dict[str, Any]) -> None:
    """Gmail을 통해 이메일을 전송합니다."""
    if not EMAIL_USER or not EMAIL_APP_PASSWORD or not EMAIL_TO:
        print("이메일 Secret이 완전하지 않아 이메일 발송을 건너뜁니다.")
        return

    date_part, time_part = data["updatedAt"].split(" ")
    hour = int(time_part.split(":")[0])
    period = "아침 뉴스" if hour < 10 else "점심 뉴스" if hour < 15 else "오후 뉴스"

    message = EmailMessage()
    message["Subject"] = f"🇨🇳 오늘의 바이두 중국어 TOP10 | {date_part} {period}"
    message["From"] = EMAIL_USER
    recipients = [address.strip() for address in re.split(r"[,;]", EMAIL_TO) if address.strip()]
    if not recipients:
        print("유효한 이메일 수신자가 없어 발송을 건너뜁니다.")
        return
    message["To"] = ", ".join(recipients)
    message.set_content("HTML 이메일을 지원하는 메일 앱에서 확인해 주세요.")
    message.add_alternative(make_email_html(data), subtype="html")

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
        smtp.login(EMAIL_USER, EMAIL_APP_PASSWORD)
        smtp.send_message(message, to_addrs=recipients)
    print(f"Gmail 전송 완료 · 수신자 {len(recipients)}명")


def main() -> None:
    print("1. 바이두 TOP10 수집 시작")
    raw_news = fetch_baidu_top10()

    fingerprint = make_source_fingerprint(raw_news)
    cached_news = load_cached_news(fingerprint)

    if cached_news is not None:
        print("2. 이전과 동일한 뉴스입니다. GPT API 호출 없이 기존 결과를 재사용합니다.")
        learning_news = cached_news
        api_used = False
    else:
        print("2. 새 뉴스 감지 · OpenAI API 학습자료 생성 시작")
        learning_news = create_learning_data(raw_news)
        api_used = True

    print("3. products.json 안전 저장")
    data = save_products_json(learning_news, fingerprint, api_used)

    print("4. 이메일 발송")
    send_email(data)
    print("완료")


if __name__ == "__main__":
    main()
