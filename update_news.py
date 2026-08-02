import html
import json
import os
import re
import smtplib
import sys
import time
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import jieba
from deep_translator import GoogleTranslator
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright
from pypinyin import Style, lazy_pinyin


BAIDU_URL = "https://top.baidu.com/board?tab=realtime"
OUTPUT_FILE = Path("products.json")

EMAIL_USER = os.getenv("EMAIL_USER", "").strip()
EMAIL_APP_PASSWORD = os.getenv(
    "EMAIL_APP_PASSWORD",
    ""
).replace(" ", "").strip()
EMAIL_TO = os.getenv("EMAIL_TO", "").strip()


def clean_text(value: str) -> str:
    """공백과 줄바꿈을 정리합니다."""
    return re.sub(r"\s+", " ", value or "").strip()


def is_possible_news_title(title: str, href: str) -> bool:
    """링크가 바이두 인기 검색어 제목인지 판단합니다."""
    if not title or not href:
        return False

    excluded_titles = {
        "首页",
        "热搜榜",
        "民生榜",
        "财经榜",
        "体育榜",
        "文娱榜",
        "国际榜",
        "挑战榜",
        "电影榜",
        "电视剧榜",
        "小说榜",
        "短剧榜",
        "查看更多",
        "榜单规则"
    }

    if title in excluded_titles:
        return False

    if title.isdigit():
        return False

    if len(title) < 2 or len(title) > 100:
        return False

    valid_link = (
        "baidu.com/s?" in href
        or "baidu.com/from=" in href
        or "baijiahao.baidu.com" in href
    )

    return valid_link


def extract_summary_from_anchor(anchor: Any, title: str) -> str:
    """제목 주변에서 바이두의 짧은 설명을 찾습니다."""
    try:
        text = anchor.evaluate(
            """
            (element) => {
                let current = element;

                for (let i = 0; i < 8 && current; i += 1) {
                    const value = (current.innerText || "").trim();

                    if (
                        value.includes("热搜指数") &&
                        value.length < 1500
                    ) {
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

    lines = [
        clean_text(line)
        for line in str(text).splitlines()
        if clean_text(line)
    ]

    candidates = []

    for line in lines:
        if line == title:
            continue

        if line in {
            "热",
            "新",
            "爆",
            "沸",
            "热搜指数",
            "查看更多",
            "查看更多>"
        }:
            continue

        if re.fullmatch(r"[\d.,万亿]+", line):
            continue

        if len(line) < 12:
            continue

        candidates.append(line)

    if not candidates:
        return ""

    return max(candidates, key=len)[:500]


def fetch_baidu_top10() -> list[dict[str, Any]]:
    """바이두 실시간 검색어 TOP10을 가져옵니다."""
    results: list[dict[str, Any]] = []
    used_titles: set[str] = set()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox"
            ]
        )

        context = browser.new_context(
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            viewport={"width": 1440, "height": 1200},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            )
        )

        page = context.new_page()

        try:
            page.goto(
                BAIDU_URL,
                wait_until="domcontentloaded",
                timeout=60000
            )

            try:
                page.wait_for_load_state(
                    "networkidle",
                    timeout=20000
                )
            except PlaywrightTimeoutError:
                pass

            page.wait_for_timeout(4000)

            anchors = page.locator("a").all()

            for anchor in anchors:
                try:
                    title = clean_text(
                        anchor.inner_text(timeout=2000)
                    )
                    href = anchor.get_attribute("href") or ""
                except Exception:
                    continue

                if not is_possible_news_title(title, href):
                    continue

                title = re.sub(
                    r"\s+(热|新|爆|沸)$",
                    "",
                    title
                ).strip()

                if title in used_titles:
                    continue

                summary = extract_summary_from_anchor(
                    anchor,
                    title
                )

                results.append(
                    {
                        "rank": len(results) + 1,
                        "chinese": title,
                        "sourceSummary": summary,
                        "url": href
                    }
                )

                used_titles.add(title)

                if len(results) >= 10:
                    break

        finally:
            context.close()
            browser.close()

    if len(results) < 10:
        raise RuntimeError(
            "바이두 TOP10을 충분히 가져오지 못했습니다. "
            f"가져온 항목: {len(results)}개"
        )

    return results


def translate_to_korean(
    text: str,
    fallback: str = "번역하지 못했습니다."
) -> str:
    """중국어를 한국어로 무료 번역합니다."""
    text = clean_text(text)

    if not text:
        return fallback

    for attempt in range(3):
        try:
            translated = GoogleTranslator(
                source="zh-CN",
                target="ko"
            ).translate(text)

            if translated:
                return clean_text(translated)

        except Exception as error:
            print(
                f"번역 재시도 {attempt + 1}/3: {error}"
            )
            time.sleep(2 + attempt)

    return fallback


def make_pinyin(text: str) -> str:
    """중국어 문장을 성조가 포함된 병음으로 바꿉니다."""
    result = lazy_pinyin(
        text,
        style=Style.TONE,
        neutral_tone_with_five=False,
        errors=lambda value: list(value)
    )

    return " ".join(result)


def is_useful_word(word: str) -> bool:
    """공백과 문장부호만 있는 항목을 제외합니다."""
    value = clean_text(word)

    if not value:
        return False

    if re.fullmatch(r"[\W_]+", value):
        return False

    return True


def split_chinese_words(text: str) -> list[str]:
    """중국어 제목을 어절 단위로 나눕니다."""
    segmented = jieba.lcut(
        text,
        cut_all=False
    )

    words = []

    for word in segmented:
        word = clean_text(word)

        if not is_useful_word(word):
            continue

        words.append(word)

    return words


def create_word_data(title: str) -> list[dict[str, str]]:
    """제목의 모든 단어에 병음과 뜻을 붙입니다."""
    words = split_chinese_words(title)
    result = []

    for word in words:
        meaning = translate_to_korean(
            word,
            fallback="뜻을 불러오지 못했습니다."
        )

        result.append(
            {
                "chinese": word,
                "pinyin": make_pinyin(word),
                "meaning": meaning
            }
        )

        # 무료 번역 서버에 너무 빠르게 요청하지 않도록 대기
        time.sleep(0.4)

    return result


def create_learning_data(
    raw_news: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """병음, 번역, 설명 번역, 단어 자료를 생성합니다."""
    learning_news = []

    for item in raw_news:
        rank = item["rank"]
        title = item["chinese"]
        source_summary = item.get("sourceSummary", "")

        print(f"{rank}위 제목 번역 중: {title}")

        translation = translate_to_korean(
            title,
            fallback="제목을 번역하지 못했습니다."
        )

        if source_summary:
            summary = translate_to_korean(
                source_summary,
                fallback="내용 설명을 번역하지 못했습니다."
            )
        else:
            summary = (
                "바이두에 상세 설명이 표시되지 않았습니다."
            )

        words = create_word_data(title)

        learning_news.append(
            {
                "rank": rank,
                "chinese": title,
                "pinyin": make_pinyin(title),
                "translation": translation,
                "summary": summary,
                "url": item["url"],
                "words": words
            }
        )

        time.sleep(1)

    return learning_news


def save_products_json(
    news: list[dict[str, Any]]
) -> dict[str, Any]:
    """홈페이지가 읽는 JSON 파일을 저장합니다."""
    now = datetime.now(
        ZoneInfo("Asia/Seoul")
    )

    data = {
        "updatedAt": now.strftime("%Y-%m-%d %H:%M"),
        "source": "Baidu Hot Search",
        "sourceUrl": BAIDU_URL,
        "translationMethod": "무료 자동 번역",
        "news": news
    }

    OUTPUT_FILE.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2
        )
        + "\n",
        encoding="utf-8"
    )

    return data


def make_word_html(
    words: list[dict[str, Any]]
) -> str:
    """이메일용 단어 목록을 만듭니다."""
    word_items = []

    for word in words:
        chinese = html.escape(
            str(word.get("chinese", ""))
        )
        pinyin = html.escape(
            str(word.get("pinyin", ""))
        )
        meaning = html.escape(
            str(word.get("meaning", ""))
        )

        word_items.append(
            f"""
            <div style="
                border:1px solid #e5e7eb;
                border-radius:10px;
                padding:12px;
                margin:8px 0;
                background:#fafafa;
            ">
                <div style="
                    font-size:22px;
                    font-weight:700;
                ">
                    {chinese}
                </div>

                <div style="
                    margin-top:4px;
                    color:#315efb;
                    font-size:18px;
                    font-weight:600;
                ">
                    {pinyin}
                </div>

                <div style="
                    margin-top:5px;
                    color:#475467;
                    font-size:17px;
                ">
                    {meaning}
                </div>
            </div>
            """
        )

    return "".join(word_items)


def make_email_html(data: dict[str, Any]) -> str:
    """TOP10 전체를 이메일 HTML로 만듭니다."""
    cards = []

    for item in data["news"]:
        rank = int(item.get("rank", 0))
        chinese = html.escape(
            str(item.get("chinese", ""))
        )
        pinyin = html.escape(
            str(item.get("pinyin", ""))
        )
        translation = html.escape(
            str(item.get("translation", ""))
        )
        summary = html.escape(
            str(item.get("summary", ""))
        )
        url = html.escape(
            str(item.get("url", BAIDU_URL))
        )

        words = item.get("words", [])
        words_html = make_word_html(words)

        cards.append(
            f"""
            <section style="
                border:1px solid #e5e7eb;
                border-radius:16px;
                margin:0 0 20px;
                overflow:hidden;
                background:#ffffff;
            ">
                <div style="
                    padding:18px;
                    background:#f7f9ff;
                ">
                    <div style="
                        color:#e5484d;
                        font-size:15px;
                        font-weight:700;
                    ">
                        {rank}위
                    </div>

                    <h2 style="
                        margin:6px 0 0;
                        color:#18202f;
                        font-size:23px;
                        line-height:1.5;
                    ">
                        {chinese}
                    </h2>
                </div>

                <div style="padding:18px;">
                    <h3 style="
                        margin:0 0 6px;
                        color:#2149d8;
                        font-size:15px;
                    ">
                        병음
                    </h3>

                    <p style="
                        margin:0 0 18px;
                        color:#315efb;
                        font-size:18px;
                        font-weight:600;
                        line-height:1.7;
                    ">
                        {pinyin}
                    </p>

                    <h3 style="
                        margin:0 0 6px;
                        color:#16794a;
                        font-size:15px;
                    ">
                        한국어 해석
                    </h3>

                    <p style="
                        margin:0 0 18px;
                        font-size:17px;
                        line-height:1.7;
                    ">
                        {translation}
                    </p>

                    <h3 style="
                        margin:0 0 6px;
                        color:#8a6500;
                        font-size:15px;
                    ">
                        내용
                    </h3>

                    <p style="
                        margin:0 0 20px;
                        font-size:16px;
                        line-height:1.7;
                    ">
                        {summary}
                    </p>

                    <h3 style="
                        margin:0 0 9px;
                        font-size:18px;
                    ">
                        전체 단어 {len(words)}개
                    </h3>

                    {words_html}

                    <p style="margin:18px 0 0;">
                        <a
                            href="{url}"
                            style="
                                color:#315efb;
                                font-weight:700;
                                text-decoration:none;
                            "
                        >
                            바이두에서 확인하기 →
                        </a>
                    </p>
                </div>
            </section>
            """
        )

    updated_at = html.escape(
        str(data["updatedAt"])
    )

    return f"""
    <!DOCTYPE html>
    <html lang="ko">
    <body style="
        margin:0;
        padding:0;
        background:#f4f6fa;
        font-family:Arial, sans-serif;
        color:#18202f;
    ">
        <div style="
            max-width:760px;
            margin:0 auto;
            padding:24px 14px;
        ">
            <header style="
                padding:22px;
                margin-bottom:20px;
                border-radius:16px;
                background:#315efb;
                color:#ffffff;
            ">
                <div style="
                    font-size:14px;
                    font-weight:700;
                ">
                    百度热搜 TOP 10
                </div>

                <h1 style="
                    margin:6px 0 8px;
                    font-size:29px;
                ">
                    바이두 실시간 중국어
                </h1>

                <div style="font-size:14px;">
                    업데이트: {updated_at}
                </div>

                <div style="
                    margin-top:6px;
                    font-size:12px;
                    opacity:0.85;
                ">
                    무료 자동 번역을 사용했습니다.
                </div>
            </header>

            {''.join(cards)}
        </div>
    </body>
    </html>
    """


def send_email(data: dict[str, Any]) -> None:
    """Gmail을 통해 이메일을 전송합니다."""

    if not EMAIL_USER:
        raise RuntimeError(
            "EMAIL_USER가 설정되지 않았습니다."
        )

    if not EMAIL_APP_PASSWORD:
        raise RuntimeError(
            "EMAIL_APP_PASSWORD가 설정되지 않았습니다."
        )

    if not EMAIL_TO:
        raise RuntimeError(
            "EMAIL_TO가 설정되지 않았습니다."
        )


    # ==========================
    # 이메일 제목 자동 생성
    # ==========================

    updated_time = data["updatedAt"]

    date_part, time_part = updated_time.split(" ")

    hour = int(
        time_part.split(":")[0]
    )


    if hour < 10:
        period = "아침 뉴스"

    elif hour < 15:
        period = "점심 뉴스"

    else:
        period = "오후 뉴스"


    email_subject = (
        f"🇨🇳 오늘의 바이두 중국어 TOP10 | "
        f"{date_part} {period}"
    )


    # ==========================
    # 이메일 생성
    # ==========================

    message = EmailMessage()

    message["Subject"] = email_subject

    message["From"] = EMAIL_USER

    message["To"] = EMAIL_TO


    message.set_content(
        "HTML 이메일을 지원하는 메일 앱에서 확인해 주세요."
    )


    message.add_alternative(
        make_email_html(data),
        subtype="html"
    )


    # ==========================
    # Gmail 발송
    # ==========================

    with smtplib.SMTP_SSL(
        "smtp.gmail.com",
        465,
        timeout=30
    ) as smtp:

        smtp.login(
            EMAIL_USER,
            EMAIL_APP_PASSWORD
        )

        smtp.send_message(
            message
        )
