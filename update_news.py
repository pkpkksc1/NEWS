import hashlib
import html
import json
import os
import re
import smtplib
import time
import traceback
from datetime import datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

from openai import OpenAI
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright
from pypinyin import Style, lazy_pinyin
from holiday import get_china_holiday_name


BAIDU_URL = "https://top.baidu.com/board?tab=realtime"
BAIDU_HOT_URL = "https://top.baidu.com/board?tab=realtime"
BAIDU_LIVELIHOOD_URL = "https://top.baidu.com/board?tab=livelihood"
HOT_COUNT = 5
LIVELIHOOD_COUNT = 10
TOTAL_NEWS_COUNT = HOT_COUNT + LIVELIHOOD_COUNT
SEARCH_SUMMARY_MAX_CHARS = 500
MAX_WORDS_PER_NEWS = 12
OUTPUT_FILE = Path("products.json")
CONVERSATION_FILE = Path("daily_conversations.json")
DATA_SCHEMA_VERSION = "v2.6-sentence-study"

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini").strip()

EMAIL_USER = os.getenv("EMAIL_USER", "").strip()
EMAIL_APP_PASSWORD = os.getenv("EMAIL_APP_PASSWORD", "").replace(" ", "").strip()
EMAIL_TO = os.getenv("EMAIL_TO", "").strip()
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "").strip()
FORCE_RUN = os.getenv("FORCE_RUN", "").strip().lower() in {"1", "true", "yes", "y"}


CONVERSATION_FILE = Path("daily_conversations.json")



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


def should_skip_for_calendar() -> tuple[bool, str]:
    """한국 시간 기준 주말 또는 중국 법정 공휴일이면 실행을 건너뜁니다."""
    today = datetime.now(ZoneInfo("Asia/Seoul")).date()
    if today.weekday() >= 5:
        return True, "주말"

    holiday_name = get_china_holiday_name(today)
    if holiday_name:
        return True, f"중국 공휴일({holiday_name})"

    return False, ""


def load_existing_titles() -> set[str]:
    """기존 products.json에서 뉴스 제목을 읽습니다."""
    if not OUTPUT_FILE.exists():
        return set()
    try:
        existing = json.loads(OUTPUT_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    news = existing.get("news", [])
    if not isinstance(news, list):
        return set()
    return {
        clean_text(str(item.get("chinese", "")))
        for item in news
        if isinstance(item, dict) and clean_text(str(item.get("chinese", "")))
    }


def count_new_news(raw_news: list[dict[str, Any]], existing_titles: set[str]) -> int:
    """기존 목록에 없던 제목의 수를 계산합니다."""
    if not existing_titles:
        return len(raw_news)
    return sum(1 for item in raw_news if clean_text(str(item.get("chinese", ""))) not in existing_titles)


def get_daily_conversations() -> list[dict[str, str]]:
    """365일 회화 파일에서 오늘의 3문장을 읽습니다."""
    if not CONVERSATION_FILE.exists():
        raise RuntimeError(f"회화 데이터 파일이 없습니다: {CONVERSATION_FILE}")

    try:
        payload = json.loads(CONVERSATION_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"회화 데이터 파일을 읽지 못했습니다: {error}") from error

    days = payload.get("days", []) if isinstance(payload, dict) else []
    if not isinstance(days, list) or len(days) != 365:
        raise RuntimeError("daily_conversations.json에는 정확히 365일 데이터가 필요합니다.")

    today = datetime.now(ZoneInfo("Asia/Seoul")).date()
    day_index = (today.timetuple().tm_yday - 1) % 365
    selected_day = days[day_index]
    sentences = selected_day.get("sentences", []) if isinstance(selected_day, dict) else []
    if not isinstance(sentences, list) or len(sentences) != 3:
        raise RuntimeError(f"회화 {day_index + 1}일차 데이터는 3문장이어야 합니다.")

    result: list[dict[str, str]] = []
    for sentence in sentences:
        if not isinstance(sentence, dict):
            continue
        chinese = clean_text(str(sentence.get("chinese", "")))
        meaning = clean_text(str(sentence.get("meaning", "")))
        if not chinese or not meaning:
            continue
        result.append({
            "chinese": chinese,
            "pinyin": clean_text(str(sentence.get("pinyin", ""))) or make_pinyin(chinese),
            "meaning": meaning,
        })

    if len(result) != 3:
        raise RuntimeError(f"회화 {day_index + 1}일차의 유효 문장이 3개가 아닙니다.")
    return result


def ensure_expression_pinyin(news_items: list[dict[str, Any]]) -> None:
    """핵심 표현의 병음이 빠진 데이터도 로컬에서 자동 보완합니다."""
    for item in news_items:
        if not isinstance(item, dict):
            continue

        expressions = item.get("expressions")
        if not isinstance(expressions, list):
            expressions = []

        legacy = item.get("expression")
        if isinstance(legacy, dict) and not expressions:
            expressions = [legacy]

        for expression in expressions:
            if not isinstance(expression, dict):
                continue
            chinese = clean_text(str(expression.get("chinese", "")))
            if chinese and not clean_text(str(expression.get("pinyin", ""))):
                expression["pinyin"] = make_pinyin(chinese)
            example = clean_text(str(expression.get("example", "")))
            if example and not clean_text(str(expression.get("examplePinyin", ""))):
                expression["examplePinyin"] = make_pinyin(example)

        key_point = clean_text(str(item.get("keyPoint", "")))
        if not key_point:
            legacy_points = item.get("keyPoints", [])
            if isinstance(legacy_points, list):
                key_point = clean_text(" ".join(
                    clean_text(str(point)) for point in legacy_points if clean_text(str(point))
                ))
        if len(key_point) > 120:
            key_point = key_point[:117].rstrip() + "..."
        item["keyPoint"] = key_point
        item["keyPoints"] = [key_point] if key_point else []

        if expressions:
            item["expressions"] = expressions
            item["expression"] = expressions[0]


def fetch_baidu_search_summary(page: Any, title: str) -> str:
    """랭킹에 설명이 없을 때 제목으로 바이두 검색 후 요약문을 보완합니다.

    사용자에게 검색 링크를 노출하지 않으며, 토큰 증가를 막기 위해
    최대 SEARCH_SUMMARY_MAX_CHARS 글자까지만 사용합니다.
    """
    search_url = f"https://www.baidu.com/s?wd={quote(title)}"
    try:
        page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except PlaywrightTimeoutError:
            pass
        page.wait_for_timeout(2200)
    except Exception as error:
        print(f"검색 보완 실패 · {title}: {error}")
        return ""

    # 바이두 검색 결과 DOM은 수시로 바뀌므로 여러 대표 컨테이너를 순서대로 확인합니다.
    selectors = [
        "#content_left .result",
        "#content_left .c-container",
        "#content_left > div",
        ".result",
        ".c-container",
    ]
    candidates: list[str] = []
    seen: set[str] = set()

    for selector in selectors:
        try:
            locator = page.locator(selector)
            count = min(locator.count(), 8)
        except Exception:
            continue

        for idx in range(count):
            try:
                value = clean_text(locator.nth(idx).inner_text(timeout=1500))
            except Exception:
                continue
            if not value or value in seen:
                continue
            seen.add(value)

            # 검색 제목 자체만 있는 결과, 지나치게 짧은 결과는 제외합니다.
            if value == title or len(value) < 35:
                continue

            # 메뉴/검색도구 등 불필요한 문구를 간단히 제거합니다.
            value = re.sub(
                r"(百度一下|网页|图片|资讯|视频|笔记|地图|贴吧|文库|更多|搜索工具)",
                " ",
                value,
            )
            value = clean_text(value)
            if len(value) >= 35:
                candidates.append(value)

    if not candidates:
        # 마지막 수단으로 검색 본문 전체에서 제목 주변의 긴 텍스트를 사용합니다.
        try:
            body_text = clean_text(page.locator("#content_left").inner_text(timeout=2500))
        except Exception:
            body_text = ""
        if len(body_text) >= 35:
            candidates.append(body_text)

    if not candidates:
        return ""

    # 제목과 겹치거나 설명성이 높은 첫 검색결과를 우선합니다.
    def score(value: str) -> tuple[int, int]:
        contains_title = 1 if title in value else 0
        return (contains_title, len(value))

    best = max(candidates, key=score)

    # 검색 결과 카드의 제목이 앞에 붙어 있으면 한 번 제거합니다.
    if best.startswith(title):
        best = clean_text(best[len(title):])

    result = best[:SEARCH_SUMMARY_MAX_CHARS].strip()
    if len(best) > SEARCH_SUMMARY_MAX_CHARS:
        result = result.rstrip("，。；;、 ") + "…"
    return result


def fetch_baidu_board(page: Any, url: str, category: str, limit: int, rank_offset: int = 0) -> list[dict[str, Any]]:
    """바이두의 지정 랭킹에서 필요한 개수만 수집합니다."""
    results: list[dict[str, Any]] = []
    used_titles: set[str] = set()

    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    try:
        page.wait_for_load_state("networkidle", timeout=20000)
    except PlaywrightTimeoutError:
        pass
    page.wait_for_timeout(3500)

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

        results.append({
            "rank": rank_offset + len(results) + 1,
            "categoryRank": len(results) + 1,
            "category": category,
            "chinese": title,
            "sourceSummary": extract_summary_from_anchor(anchor, title),
            "url": href,
        })
        used_titles.add(title)

        if len(results) >= limit:
            break

    if len(results) < limit:
        raise RuntimeError(f"바이두 {category}에서 {limit}개를 가져오지 못했습니다. 가져온 항목: {len(results)}개")

    # 랭킹 카드에 상세 설명이 없는 항목만 제목 검색으로 보완합니다.
    missing = [item for item in results if not clean_text(str(item.get("sourceSummary", "")))]
    if missing:
        print(f"{category}: 상세 설명 없는 항목 {len(missing)}개 · 제목 검색으로 보완")
    for item in missing:
        title = clean_text(str(item.get("chinese", "")))
        summary = fetch_baidu_search_summary(page, title)
        if summary:
            item["sourceSummary"] = summary
            print(f"  보완 성공 · {title} · {len(summary)}자")
        else:
            print(f"  보완 실패 · {title}")

    return results


def fetch_baidu_top15() -> list[dict[str, Any]]:
    """热搜榜 5개 + 民生榜 10개, 총 15개를 가져옵니다."""
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
            hot = fetch_baidu_board(page, BAIDU_HOT_URL, "热搜榜", HOT_COUNT, 0)
            livelihood = fetch_baidu_board(
                page, BAIDU_LIVELIHOOD_URL, "民生榜", LIVELIHOOD_COUNT, HOT_COUNT
            )
            return hot + livelihood
        finally:
            context.close()
            browser.close()


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
    """15개 콘텐츠 전체를 한 번에 처리할 프롬프트를 만듭니다."""
    source_items = [
        {"rank": item["rank"], "category": item.get("category", ""), "categoryRank": item.get("categoryRank", item["rank"]), "chinese": item["chinese"], "sourceSummary": item.get("sourceSummary", "")}
        for item in raw_news
    ]

    return f"""
당신은 중국어 뉴스 학습자료 편집자입니다.
아래 바이두 콘텐츠 15개(热搜榜 5개 + 民生榜 10개)를 한국인 학습자용으로 정확하고 자연스럽게 가공하세요.

규칙:
1. rank, category, categoryRank, chinese는 절대 변경하지 마세요.
2. translation은 제목의 자연스러운 한국어 번역입니다.
3. sourceSummary를 문장 단위로 나누어 detailPairs 배열을 만드세요.
   각 항목은 chinese와 korean 두 필드만 포함합니다.
   chinese는 sourceSummary에 실제로 있는 중국어 문장을 의미가 자연스럽게 끊기는 단위로 사용하고,
   korean은 해당 chinese 문장만 정확하고 자연스럽게 한국어로 번역하세요.
   문장을 합치거나 순서를 바꾸지 말고 원문에 없는 사실을 추가하지 마세요.
   sourceSummary가 비어 있으면 detailPairs는 빈 배열입니다.
4. expressions는 제목에서 학습 가치가 높은 중국어 표현 1~2개입니다.
5. 각 표현은 chinese, meaning, example, exampleMeaning을 모두 포함합니다. 예문 병음은 출력하지 마세요. 프로그램이 로컬에서 생성합니다.
6. words는 제목과 상세 내용에서 학습 가치가 높은 핵심 단어 최대 12개이며 meaning은 문맥에 맞는 한국어 뜻입니다.
7. 병음은 출력하지 마세요. 프로그램이 로컬에서 생성합니다.
8. 반드시 설명 없이 유효한 JSON 객체 하나만 출력하세요.

출력 형식:
{{
  "news": [
    {{
      "rank": 1,
      "category": "热搜榜",
      "categoryRank": 1,
      "chinese": "입력 제목 그대로",
      "translation": "한국어 제목",
      "detailPairs": [
        {"chinese": "중국어 문장 1", "korean": "해당 문장의 한국어 번역"},
        {"chinese": "중국어 문장 2", "korean": "해당 문장의 한국어 번역"}
      ],
      "expressions": [
        {{"chinese": "표현", "meaning": "한국어 뜻", "example": "중국어 예문", "exampleMeaning": "한국어 예문 뜻"}}
      ],
      "words": [{{"chinese": "단어", "meaning": "한국어 뜻"}}]
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
        raise RuntimeError(f"GPT가 반환한 뉴스 개수가 {len(raw_news)}개가 아닙니다.")

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
        detail_pairs_raw = generated.get("detailPairs", [])
        detail_pairs: list[dict[str, str]] = []
        if isinstance(detail_pairs_raw, list):
            for pair_raw in detail_pairs_raw:
                if not isinstance(pair_raw, dict):
                    continue
                pair_chinese = clean_text(str(pair_raw.get("chinese", "")))
                pair_korean = clean_text(str(pair_raw.get("korean", "")))
                if not pair_chinese or not pair_korean:
                    continue
                detail_pairs.append({
                    "chinese": pair_chinese,
                    "pinyin": make_pinyin(pair_chinese),
                    "korean": pair_korean,
                })

        source_summary = clean_text(str(raw.get("sourceSummary", "")))
        if source_summary and not detail_pairs:
            raise RuntimeError(f"GPT의 {rank}위 문장별 상세 번역이 비어 있습니다.")
        summary = " ".join(pair["korean"] for pair in detail_pairs)
        key_point_raw = generated.get("keyPoint", generated.get("keyPoints", ""))
        expressions_raw = generated.get("expressions")
        # 이전 형식과도 호환합니다.
        if not isinstance(expressions_raw, list):
            legacy_expression = generated.get("expression")
            expressions_raw = [legacy_expression] if isinstance(legacy_expression, dict) else []
        words_raw = generated.get("words")

        if not translation:
            raise RuntimeError(f"GPT의 {rank}위 한국어 제목이 비어 있습니다.")
        korean_chars = len(re.findall(r"[가-힣]", summary))
        chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", summary))
        if chinese_chars > 8 and korean_chars < chinese_chars:
            raise RuntimeError(f"GPT의 {rank}위 상세 한국어가 중국어로 반환되었습니다.")
        if isinstance(key_point_raw, list):
            key_point = clean_text(" ".join(
                clean_text(str(point))
                for point in key_point_raw
                if clean_text(str(point))
            ))
        else:
            key_point = clean_text(str(key_point_raw))
        if len(key_point) > 120:
            key_point = key_point[:117].rstrip() + "..."

        # 핵심 표현은 모델이 간혹 빈 배열로 반환할 수 있으므로 전체 실행을
        # 실패시키지 않습니다. 아래에서 단어 목록 또는 제목으로 안전하게 보완합니다.
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
                "examplePinyin": make_pinyin(clean_text(str(expression_raw.get("example", "")))),
                "exampleMeaning": clean_text(str(expression_raw.get("exampleMeaning", ""))),
            }
            if all(expression.values()):
                expressions.append(expression)
        words: list[dict[str, str]] = []
        seen_words: set[str] = set()
        for word in words_raw[:MAX_WORDS_PER_NEWS]:
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

        # GPT가 핵심 표현을 누락했거나 일부 필드를 비워 반환한 경우에도
        # API를 재호출하지 않고 첫 번째 유효 단어로 대체합니다.
        if not expressions:
            fallback = words[0]
            fallback_chinese = fallback["chinese"]
            fallback_meaning = fallback["meaning"]
            expressions = [
                {
                    "chinese": fallback_chinese,
                    "pinyin": fallback["pinyin"],
                    "meaning": fallback_meaning,
                    "example": f"“{fallback_chinese}”是今天新闻中的重要表达。",
                    "examplePinyin": make_pinyin(f"“{fallback_chinese}”是今天新闻中的重要表达。"),
                    "exampleMeaning": f"‘{fallback_chinese}’는 오늘 뉴스의 중요한 표현입니다.",
                }
            ]
            print(f"{rank}위 핵심 표현 누락 · 단어 '{fallback_chinese}'로 자동 보완")

        result.append(
            {
                "rank": rank,
                "category": raw.get("category", ""),
                "categoryRank": raw.get("categoryRank", rank),
                "chinese": title,
                "pinyin": make_pinyin(title),
                "translation": translation,
                "summary": summary,
                "detailPairs": detail_pairs,
                "detailChinese": source_summary,
                "detailPinyin": make_pinyin(source_summary),
                "sourceExcerpt": "",
                "keyPoint": key_point,
                "keyPoints": [key_point] if key_point else [],
                "url": raw["url"],
                # 기존 화면과의 호환을 위해 첫 표현도 expression에 저장합니다.
                "expression": expressions[0],
                "expressions": expressions,
                "words": words,
            }
        )

    return result


def create_learning_data(raw_news: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """OpenAI API로 15개 학습자료를 한 번에 생성합니다."""
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
            print(f"GPT 처리 실패 {attempt + 1}/2: {error}")
            if attempt < 1:
                time.sleep(4)

    raise RuntimeError(f"GPT 학습자료 생성에 최종 실패했습니다: {last_error}")


def make_source_fingerprint(raw_news: list[dict[str, Any]]) -> str:
    """뉴스 원문과 데이터 구조 버전의 지문을 만듭니다."""
    payload = {
        "schemaVersion": DATA_SCHEMA_VERSION,
        "items": [
            {
                "rank": item.get("rank"),
                "category": item.get("category", ""),
                "categoryRank": item.get("categoryRank"),
                "chinese": clean_text(str(item.get("chinese", ""))),
                "sourceSummary": clean_text(str(item.get("sourceSummary", ""))),
            }
            for item in raw_news
        ],
    }
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
    if not isinstance(cached_news, list) or len(cached_news) != TOTAL_NEWS_COUNT:
        return None
    return cached_news


def _first_expression(news: list[dict[str, Any]]) -> dict[str, Any]:
    """첫 번째 뉴스에서 오늘의 표현을 고릅니다."""
    if not news:
        return {}
    first = news[0] if isinstance(news[0], dict) else {}
    expressions = first.get("expressions", [])
    if isinstance(expressions, list):
        for expression in expressions:
            if isinstance(expression, dict) and clean_text(str(expression.get("chinese", ""))):
                return dict(expression)
    legacy = first.get("expression")
    return dict(legacy) if isinstance(legacy, dict) else {}


def _today_word(news: list[dict[str, Any]], expression: dict[str, Any]) -> dict[str, Any]:
    """뉴스 단어 중 오늘의 표현과 겹치지 않는 첫 단어를 고릅니다."""
    expression_chinese = clean_text(str(expression.get("chinese", "")))
    for item in news:
        if not isinstance(item, dict):
            continue
        words = item.get("words", [])
        if not isinstance(words, list):
            continue
        for word in words:
            if not isinstance(word, dict):
                continue
            chinese = clean_text(str(word.get("chinese", "")))
            if chinese and chinese != expression_chinese:
                result = dict(word)
                result["pinyin"] = clean_text(str(result.get("pinyin", ""))) or make_pinyin(chinese)
                return result
    return {}


def _build_expression_history(today_expression: dict[str, Any], today_date: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """오늘의 표현 이력을 보관하고 3일 전 복습 표현을 찾습니다."""
    existing: dict[str, Any] = {}
    if OUTPUT_FILE.exists():
        try:
            loaded = json.loads(OUTPUT_FILE.read_text(encoding="utf-8"))
            existing = loaded if isinstance(loaded, dict) else {}
        except (OSError, json.JSONDecodeError):
            existing = {}

    history = existing.get("expressionHistory", [])
    if not isinstance(history, list):
        history = []
    cleaned = [item for item in history if isinstance(item, dict) and item.get("date") != today_date]

    if today_expression:
        entry = dict(today_expression)
        entry["date"] = today_date
        cleaned.append(entry)
    cleaned = cleaned[-40:]

    target_date = (datetime.strptime(today_date, "%Y-%m-%d").date() - timedelta(days=3)).isoformat()
    review = next((dict(item) for item in cleaned if item.get("date") == target_date), {})
    if not review and len(cleaned) >= 4:
        review = dict(cleaned[-4])
    return cleaned, review


def save_products_json(news: list[dict[str, Any]], fingerprint: str, api_used: bool) -> dict[str, Any]:
    """홈페이지가 읽는 JSON 파일을 원자적으로 저장합니다."""
    now = datetime.now(ZoneInfo("Asia/Seoul"))
    today_date = now.strftime("%Y-%m-%d")
    today_expression = _first_expression(news)
    today_word = _today_word(news, today_expression)
    expression_history, review_expression = _build_expression_history(today_expression, today_date)
    data = {
        "updatedAt": now.strftime("%Y-%m-%d %H:%M"),
        "source": "Baidu Hot Search",
        "sourceUrl": BAIDU_URL,
        "translationMethod": f"OpenAI API ({OPENAI_MODEL})",
        "schemaVersion": DATA_SCHEMA_VERSION,
        "sourceFingerprint": fingerprint,
        "apiUsedThisRun": api_used,
        "todayExpression": today_expression,
        "todayWord": today_word,
        "reviewExpression": review_expression,
        "expressionHistory": expression_history,
        "dailyConversation": get_daily_conversations(),
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
    """이메일용 핵심 단어를 3열 가로형 표로 만듭니다."""
    valid_words = [
        word for word in words[:MAX_WORDS_PER_NEWS]
        if isinstance(word, dict)
    ]
    if not valid_words:
        return ""

    rows: list[str] = []
    for row_start in range(0, len(valid_words), 3):
        chunk = valid_words[row_start:row_start + 3]
        cells: list[str] = []

        for word in chunk:
            chinese = html.escape(str(word.get("chinese", "")))
            pinyin = html.escape(str(word.get("pinyin", "")))
            meaning = html.escape(str(word.get("meaning", "")))
            cells.append(
                f"""
                <td width="33.33%" valign="top" style="width:33.33%;padding:5px;">
                    <div style="min-height:96px;border:1px solid #e5e7eb;border-radius:10px;padding:11px;background:#fafafa;">
                        <div style="font-size:19px;font-weight:800;line-height:1.35;color:#18202f;">{chinese}</div>
                        <div style="margin-top:4px;color:#315efb;font-size:14px;font-weight:700;line-height:1.45;">{pinyin}</div>
                        <div style="margin-top:5px;color:#475467;font-size:14px;line-height:1.45;">{meaning}</div>
                    </div>
                </td>
                """
            )

        while len(cells) < 3:
            cells.append('<td width="33.33%" style="width:33.33%;padding:5px;"></td>')

        rows.append(f"<tr>{''.join(cells)}</tr>")

    return (
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" '
        'style="width:100%;border-collapse:collapse;">'
        + "".join(rows)
        + "</table>"
    )


def make_email_html(data: dict[str, Any]) -> str:
    """15개 콘텐츠 전체를 보기 좋은 이메일 HTML로 만듭니다."""
    news_items = data.get("news", [])

    # 오늘의 표현/오늘의 단어 상단 카드는 표시하지 않습니다.

    # 3일 전 복습 카드는 표시하지 않습니다.

    cards = []
    for item in news_items:
        rank = int(item.get("rank", 0))
        category = str(item.get("category", "热搜榜"))
        category_rank = int(item.get("categoryRank", rank))
        category_label = "🔥 热搜榜" if category == "热搜榜" else "🏠 民生榜"
        chinese = html.escape(str(item.get("chinese", "")))
        pinyin = html.escape(str(item.get("pinyin", "")))
        translation = html.escape(str(item.get("translation", "")))
        summary = html.escape(str(item.get("summary", "")))
        detail_chinese = html.escape(str(item.get("detailChinese", "")))
        detail_pinyin = html.escape(str(item.get("detailPinyin", "")))
        detail_pairs = item.get("detailPairs", [])
        if not isinstance(detail_pairs, list):
            detail_pairs = []
        key_point = clean_text(str(item.get("keyPoint", ""))) or clean_text(" ".join(str(x) for x in item.get("keyPoints", []) if x))
        expressions = item.get("expressions", [])
        if not isinstance(expressions, list) or not expressions:
            expression = item.get("expression", {})
            expressions = [expression] if isinstance(expression, dict) else []
        words = item.get("words", [])

        key_points_html = ""
        if key_point:
            key_points_html = f"""
            <div style="display:flex;gap:10px;margin:9px 0;padding:11px 13px;border-radius:11px;background:#fff8e7;line-height:1.65;">
                <span style="color:#b54708;font-weight:900;">✓</span>
                <span>{html.escape(key_point)}</span>
            </div>
            """

        detail_pairs_html = ""
        if detail_pairs:
            sentence_cards = []
            for sentence_index, pair in enumerate(detail_pairs, start=1):
                if not isinstance(pair, dict):
                    continue
                sentence_chinese = html.escape(str(pair.get("chinese", "")))
                sentence_pinyin = html.escape(str(pair.get("pinyin", "")))
                sentence_korean = html.escape(str(pair.get("korean", "")))
                if not sentence_chinese or not sentence_korean:
                    continue
                sentence_cards.append(
                    f"""
                    <div style="margin:0 0 12px;padding:15px 16px;border:1px solid #f2d675;border-radius:13px;background:#fffdf4;">
                        <div style="margin-bottom:7px;color:#b54708;font-size:12px;font-weight:800;">문장 {sentence_index}</div>
                        <div style="font-size:18px;line-height:1.8;font-weight:800;color:#18202f;">{sentence_chinese}</div>
                        <div style="margin-top:7px;font-size:15px;line-height:1.8;color:#315efb;font-weight:700;">{sentence_pinyin}</div>
                        <div style="margin-top:8px;padding-top:8px;border-top:1px dashed #ead58b;font-size:16px;line-height:1.8;color:#344054;">{sentence_korean}</div>
                    </div>
                    """
                )
            detail_pairs_html = "".join(sentence_cards)
        else:
            detail_pairs_html = """
            <div style="padding:15px 16px;border:1px solid #f2d675;border-radius:13px;background:#fffdf4;color:#667085;font-size:15px;line-height:1.7;">
                관련 상세 내용을 확인하지 못했습니다.
            </div>
            """

        expressions_html = ""
        for expression in expressions[:2]:
            if not isinstance(expression, dict):
                continue
            expressions_html += f"""
            <div style="margin:9px 0;padding:14px;border:1px solid #f2d675;border-radius:12px;background:#fffdf2;">
                <div style="font-size:22px;font-weight:800;">{html.escape(str(expression.get('chinese', '')))}</div>
                <div style="margin-top:4px;color:#315efb;font-size:17px;font-weight:700;">{html.escape(str(expression.get('pinyin', '')))}</div>
                <div style="margin-top:5px;color:#16794a;font-size:16px;font-weight:700;">{html.escape(str(expression.get('meaning', '')))}</div>
                <div style="margin-top:10px;padding-top:10px;border-top:1px dashed #ead58b;font-size:15px;line-height:1.7;">
                    {html.escape(str(expression.get('example', '')))}<br>
                    <span style="color:#315efb;font-weight:600;">{html.escape(str(expression.get('examplePinyin', '')))}</span><br>
                    <span style="color:#667085;">{html.escape(str(expression.get('exampleMeaning', '')))}</span>
                </div>
            </div>
            """

        cards.append(
            f"""
            <section style="margin:0 0 22px;border:1px solid #e4e7ec;border-radius:18px;overflow:hidden;background:#ffffff;box-shadow:0 8px 28px rgba(16,24,40,.06);">
                <div style="padding:19px 20px;background:linear-gradient(135deg,#f8faff 0%,#eef3ff 100%);border-bottom:1px solid #e4e7ec;">
                    <div style="display:inline-block;padding:5px 9px;border-radius:999px;background:#e5484d;color:#ffffff;font-size:13px;font-weight:800;">{category_label} TOP {category_rank}</div>
                    <h2 style="margin:10px 0 5px;color:#18202f;font-size:23px;line-height:1.5;">{chinese}</h2>
                    <div style="color:#315efb;font-size:17px;font-weight:700;line-height:1.65;">{pinyin}</div>
                </div>
                <div style="padding:20px;">
                    <div style="margin-bottom:18px;padding:15px;border-radius:12px;background:#edfff5;border-left:4px solid #16794a;">
                        <div style="margin-bottom:5px;color:#16794a;font-size:13px;font-weight:800;">한국어 제목</div>
                        <div style="font-size:17px;line-height:1.7;font-weight:700;">{translation}</div>
                    </div>

                    <h3 style="margin:0 0 10px;color:#8a6500;font-size:16px;">📰 자세한 내용 · 문장별 학습</h3>
                    <div style="margin-bottom:19px;">
                        {detail_pairs_html}
                    </div>

                    <h3 style="margin:0 0 8px;color:#8a6500;font-size:16px;">💬 뉴스 표현</h3>
                    <div style="margin-bottom:20px;">{expressions_html}</div>

                    <h3 style="margin:0 0 10px;color:#18202f;font-size:17px;">📚 핵심 단어 {min(len(words), MAX_WORDS_PER_NEWS)}개</h3>
                    {make_word_html(words)}
                </div>
            </section>
            """
        )

    conversation_items = data.get("dailyConversation", [])
    conversation_html = ""
    if isinstance(conversation_items, list) and conversation_items:
        rows = "".join(
            f"""
            <div style="margin:10px 0;padding:15px;border:1px solid #d6e4ff;border-radius:13px;background:#ffffff;">
                <div style="font-size:21px;font-weight:800;color:#18202f;">{html.escape(str(item.get('chinese', '')))}</div>
                <div style="margin-top:5px;font-size:17px;font-weight:700;color:#315efb;">{html.escape(str(item.get('pinyin', '')))}</div>
                <div style="margin-top:7px;font-size:16px;color:#475467;">{html.escape(str(item.get('meaning', '')))}</div>
            </div>
            """
            for item in conversation_items
            if isinstance(item, dict)
        )
        conversation_html = f"""
        <section style="margin:26px 0 18px;padding:20px;border:1px solid #b2ccff;border-radius:18px;background:linear-gradient(135deg,#f5f8ff 0%,#eaf1ff 100%);">
            <div style="margin-bottom:6px;color:#2149d8;font-size:13px;font-weight:800;letter-spacing:.06em;">💬 매일 쓰는 중국어 회화</div>
            <h2 style="margin:0 0 13px;font-size:22px;color:#18202f;">오늘의 회화 3문장</h2>
            {rows}
        </section>
        """

    updated_at = html.escape(str(data["updatedAt"]))
    method = html.escape(str(data.get("translationMethod", "OpenAI API")))
    return f"""
    <!DOCTYPE html>
    <html lang="ko">
    <head>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <meta charset="UTF-8">
    </head>
    <body style="margin:0;padding:0;background:#f4f6fa;font-family:Arial,'Apple SD Gothic Neo','Noto Sans KR',sans-serif;color:#18202f;">
        <div style="display:none;max-height:0;overflow:hidden;">오늘의 중국어 학습과 바이두 热搜榜 5 · 民生榜 10</div>
        <div style="max-width:760px;margin:0 auto;padding:24px 12px 36px;">
            <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="width:100%;margin:0 0 20px;border-collapse:separate;background-color:#315efb;border-radius:19px;overflow:hidden;box-shadow:0 10px 30px rgba(49,94,251,.20);" bgcolor="#315efb">
                <tr>
                    <td style="padding:25px 22px;background-color:#315efb;color:#ffffff !important;-webkit-text-fill-color:#ffffff !important;opacity:1;" bgcolor="#315efb">
                        <div style="font-size:13px;line-height:1.4;font-weight:800;letter-spacing:.08em;color:#ffffff !important;-webkit-text-fill-color:#ffffff !important;opacity:1;">百度热搜 5 · 民生 10</div>
                        <div style="margin:7px 0 8px;font-size:30px;line-height:1.3;font-weight:800;color:#ffffff !important;-webkit-text-fill-color:#ffffff !important;opacity:1;">🇨🇳 오늘의 중국어 + 바이두 뉴스</div>
                        <div style="font-size:14px;line-height:1.7;color:#ffffff !important;-webkit-text-fill-color:#ffffff !important;opacity:1;">실시간 인기 뉴스로 배우는 중국어 표현 · 해석 · 핵심 단어</div>
                        <table role="presentation" cellspacing="0" cellpadding="0" border="0" style="margin-top:13px;border-collapse:separate;">
                            <tr>
                                <td style="padding:7px 10px;border:1px solid #8ea6ff;border-radius:9px;background-color:#4d73ff;color:#ffffff !important;-webkit-text-fill-color:#ffffff !important;font-size:12px;line-height:1.4;opacity:1;" bgcolor="#4d73ff">업데이트 {updated_at} · {method}</td>
                            </tr>
                        </table>
                    </td>
                </tr>
            </table>

            {conversation_html}

            <div style="margin:24px 0 14px;padding:0 4px;color:#667085;font-size:13px;line-height:1.6;">
                바이두 热搜榜 TOP 5와 民生榜 TOP 10, 총 15개를 중국어 학습용으로 정리했습니다.
            </div>

            {''.join(cards)}

            <footer style="padding:18px 8px;color:#98a2b3;font-size:12px;line-height:1.7;text-align:center;">
                중국어 학습용 자동 뉴스 메일입니다.<br>
                뉴스 상세 내용은 수집된 바이두 정보 범위 안에서 정리됩니다.<br>
            </footer>
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
    message["Subject"] = f"오늘의 중국어 + 바이두 뉴스 TOP10 | {date_part} {period}"
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



def get_github_actions_url() -> str:
    """현재 GitHub Actions 실행 주소를 만듭니다."""
    server = os.getenv("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
    repository = os.getenv("GITHUB_REPOSITORY", "").strip()
    run_id = os.getenv("GITHUB_RUN_ID", "").strip()
    if repository and run_id:
        return f"{server}/{repository}/actions/runs/{run_id}"
    return ""


def send_failure_email(stage: str, error: BaseException) -> None:
    """자동화 실패 내용을 관리자 메일로 보냅니다.

    이 함수 자체가 실패해도 원래 오류를 가리지 않도록 예외를 내부에서 처리합니다.
    """
    if not EMAIL_USER or not EMAIL_APP_PASSWORD:
        print("실패 알림 생략: EMAIL_USER 또는 EMAIL_APP_PASSWORD가 없습니다.")
        return

    recipients = [
        address.strip()
        for address in re.split(r"[,;]", ADMIN_EMAIL)
        if address.strip()
    ]
    if not recipients:
        print("실패 알림 생략: ADMIN_EMAIL이 설정되지 않았습니다.")
        return

    now = datetime.now(ZoneInfo("Asia/Seoul"))
    actions_url = get_github_actions_url()
    traceback_text = "".join(
        traceback.format_exception(type(error), error, error.__traceback__)
    )[-8000:]

    safe_stage = html.escape(stage)
    safe_error = html.escape(f"{type(error).__name__}: {error}")
    safe_traceback = html.escape(traceback_text)
    safe_actions_url = html.escape(actions_url)

    actions_html = (
        f'<p style="margin:14px 0 0;"><a href="{safe_actions_url}" '
        'style="display:inline-block;padding:11px 16px;border-radius:10px;'
        'background:#315efb;color:#fff;text-decoration:none;font-weight:700;">'
        'GitHub Actions 실행 로그 열기</a></p>'
        if actions_url else ""
    )

    body_html = f"""
    <!DOCTYPE html>
    <html lang="ko">
    <body style="margin:0;padding:24px;background:#f4f6fa;font-family:Arial,'Noto Sans KR',sans-serif;color:#18202f;">
      <div style="max-width:720px;margin:0 auto;background:#fff;border:1px solid #e4e7ec;border-radius:18px;overflow:hidden;box-shadow:0 8px 28px rgba(16,24,40,.08);">
        <div style="padding:22px;background:#fff1f1;border-bottom:1px solid #f6c7c7;">
          <div style="font-size:13px;font-weight:800;color:#b42318;letter-spacing:.06em;">🚨 자동화 실패 알림</div>
          <h1 style="margin:8px 0 0;font-size:24px;line-height:1.4;">바이두 뉴스 자동화가 실패했습니다</h1>
        </div>
        <div style="padding:22px;">
          <table style="width:100%;border-collapse:collapse;font-size:15px;">
            <tr><td style="padding:9px 0;color:#667085;width:120px;">실행 시간</td><td style="padding:9px 0;font-weight:700;">{now.strftime('%Y-%m-%d %H:%M:%S')} KST</td></tr>
            <tr><td style="padding:9px 0;color:#667085;">실패 단계</td><td style="padding:9px 0;font-weight:700;">{safe_stage}</td></tr>
            <tr><td style="padding:9px 0;color:#667085;">오류</td><td style="padding:9px 0;color:#b42318;font-weight:700;">{safe_error}</td></tr>
          </table>
          {actions_html}
          <h2 style="margin:24px 0 10px;font-size:16px;">오류 상세</h2>
          <pre style="margin:0;padding:14px;border-radius:12px;background:#101828;color:#f2f4f7;white-space:pre-wrap;word-break:break-word;font-size:12px;line-height:1.6;">{safe_traceback}</pre>
        </div>
      </div>
    </body>
    </html>
    """

    message = EmailMessage()
    message["Subject"] = f"🚨 [오류] 바이두 뉴스 자동화 실패 | {now.strftime('%Y-%m-%d %H:%M')}"
    message["From"] = EMAIL_USER
    message["To"] = ", ".join(recipients)
    message.set_content(
        f"바이두 뉴스 자동화 실패\n실패 단계: {stage}\n"
        f"오류: {type(error).__name__}: {error}\n"
        f"GitHub Actions: {actions_url or '확인 불가'}"
    )
    message.add_alternative(body_html, subtype="html")

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
            smtp.login(EMAIL_USER, EMAIL_APP_PASSWORD)
            smtp.send_message(message, to_addrs=recipients)
        print(f"실패 알림 메일 전송 완료 · 관리자 {len(recipients)}명")
    except Exception as mail_error:
        print(f"실패 알림 메일 전송 실패: {mail_error}")


def main() -> None:
    stage = "초기화"
    try:
        stage = "달력 확인"
        if FORCE_RUN:
            print("수동 실행 감지 · 주말/중국 공휴일 검사를 건너뛰고 테스트 실행합니다.")
        else:
            skip, reason = should_skip_for_calendar()
            if skip:
                print(f"실행 건너뜀: 오늘은 {reason}입니다. API 호출과 이메일 발송을 하지 않습니다.")
                return

        stage = "바이두 热搜榜·民生榜 수집"
        print("1. 바이두 热搜榜 5개 + 民生榜 10개 수집 시작")
        existing_titles = load_existing_titles()
        raw_news = fetch_baidu_top15()
        new_news_count = count_new_news(raw_news, existing_titles)
        print(f"2. 새로운 뉴스 제목: {new_news_count}개 · 최소 발송 기준 없이 계속 진행합니다.")

        stage = "기존 데이터 비교"
        fingerprint = make_source_fingerprint(raw_news)
        cached_news = load_cached_news(fingerprint)

        if cached_news is not None:
            print("3. 이전과 동일한 뉴스입니다. GPT API 호출 없이 기존 결과를 재사용합니다.")
            learning_news = cached_news
            api_used = False
        else:
            stage = "OpenAI 학습자료 생성"
            print("3. 새 데이터 감지 · OpenAI API 학습자료 생성 시작")
            learning_news = create_learning_data(raw_news)
            api_used = True

        stage = "핵심 표현 병음 보완"
        ensure_expression_pinyin(learning_news)

        stage = "products.json 저장"
        print("4. products.json 안전 저장")
        data = save_products_json(learning_news, fingerprint, api_used)

        stage = "뉴스 이메일 발송"
        print("5. 이메일 발송")
        send_email(data)
        print("완료")

    except Exception as error:
        print(f"자동화 실패 · 단계: {stage} · 오류: {error}")
        send_failure_email(stage, error)
        raise


if __name__ == "__main__":
    main()
