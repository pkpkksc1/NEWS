import html
import json
import os
import re
import smtplib
import sys
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from openai import OpenAI
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


BAIDU_URL = "https://top.baidu.com/board?tab=realtime"
OUTPUT_FILE = Path("products.json")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini").strip()

EMAIL_USER = os.getenv("EMAIL_USER", "").strip()
EMAIL_APP_PASSWORD = os.getenv(
    "EMAIL_APP_PASSWORD",
    ""
).replace(" ", "").strip()

EMAIL_TO = os.getenv("EMAIL_TO", "").strip()


def clean_text(value: str) -> str:
    """여러 공백과 줄바꿈을 하나로 정리합니다."""
    return re.sub(r"\s+", " ", value or "").strip()


def is_possible_news_title(title: str, href: str) -> bool:
    """바이두 페이지의 링크가 뉴스 제목인지 대략 판단합니다."""
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

    # 바이두 검색 결과 또는 바이두 뉴스 연결 주소
    valid_link = (
        "baidu.com/s?" in href
        or "baidu.com/from=" in href
        or "baijiahao.baidu.com" in href
    )

    return valid_link


def extract_summary_from_anchor(anchor: Any, title: str) -> str:
    """
    뉴스 제목 링크의 상위 요소에서 바이두가 표시한
    짧은 설명을 찾아냅니다.
    """
    try:
        text = anchor.evaluate(
            """
            (element) => {
                let current = element;

                for (let i = 0; i < 8 && current; i += 1) {
                    const value = (current.innerText || "").trim();

                    if (
                        value.includes("热搜指数") &&
                        value.length < 1200
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

    # 보통 가장 긴 문장이 바이두의 뉴스 설명입니다.
    return max(candidates, key=len)[:500]


def fetch_baidu_top10() -> list[dict[str, Any]]:
    """Playwright로 바이두 실시간 검색어 TOP10을 가져옵니다."""
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
                # 광고나 추적 요청 때문에 networkidle이 되지 않아도
                # 페이지 내용이 이미 표시됐을 수 있습니다.
                pass

            page.wait_for_timeout(4000)

            anchors = page.locator("a").all()

            for anchor in anchors:
                try:
                    title = clean_text(anchor.inner_text(timeout=2000))
                    href = anchor.get_attribute("href") or ""
                except Exception:
                    continue

                if not is_possible_news_title(title, href):
                    continue

                if title in used_titles:
                    continue

                # "标题 热", "标题 新"처럼 붙어 나온 표시 제거
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


def remove_markdown_code_fence(text: str) -> str:
    """모델 응답에 ```json 코드 블록이 붙으면 제거합니다."""
    value = text.strip()

    value = re.sub(
        r"^```(?:json)?\s*",
        "",
        value,
        flags=re.IGNORECASE
    )

    value = re.sub(r"\s*```$", "", value)

    return value.strip()


def create_learning_data(
    raw_news: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """
    OpenAI API로 병음, 한국어 번역, 요약,
    제목의 전체 단어 정보를 생성합니다.
    """
    if not OPENAI_API_KEY:
        raise RuntimeError(
            "OPENAI_API_KEY가 설정되지 않았습니다."
        )

    client = OpenAI(api_key=OPENAI_API_KEY)

    input_data = [
        {
            "rank": item["rank"],
            "chinese": item["chinese"],
            "sourceSummary": item["sourceSummary"],
            "url": item["url"]
        }
        for item in raw_news
    ]

    prompt = f"""
다음은 바이두 실시간 검색어 TOP10 데이터이다.

각 항목을 한국인 중국어 학습자를 위한 자료로 변환하라.

중요 규칙:
1. 원래 순위와 중국어 제목을 절대로 변경하지 않는다.
2. pinyin에는 제목 전체의 정확한 성조 병음을 작성한다.
3. translation에는 자연스러운 한국어 제목 번역을 작성한다.
4. summary에는 제공된 중국어 설명만 근거로 한국어 1~2문장으로
   요약한다. 설명이 비어 있으면 제목을 바탕으로 추측하지 말고
   "바이두에 상세 설명이 표시되지 않았습니다."라고 작성한다.
5. words에는 제목에 사용된 단어를 처음부터 끝까지 빠짐없이
   순서대로 넣는다.
6. 조사, 개사, 수사, 양사, 고유명사도 생략하지 않는다.
7. 중국어 문장을 자연스러운 어절 단위로 분리한다.
8. words의 각 항목에는 chinese, pinyin, meaning을 넣는다.
9. 같은 위치의 글자를 단어와 숙어로 중복 등록하지 않는다.
10. JSON 이외의 설명, 마크다운, 코드 블록은 출력하지 않는다.

반드시 아래 구조의 JSON 객체로만 응답하라.

{{
  "news": [
    {{
      "rank": 1,
      "chinese": "중국어 원문",
      "pinyin": "전체 병음",
      "translation": "한국어 해석",
      "summary": "한국어 요약",
      "url": "원문 링크",
      "words": [
        {{
          "chinese": "단어",
          "pinyin": "병음",
          "meaning": "한국어 뜻"
        }}
      ]
    }}
  ]
}}

입력 데이터:
{json.dumps(input_data, ensure_ascii=False, indent=2)}
""".strip()

    response = client.responses.create(
        model=OPENAI_MODEL,
        instructions=(
            "당신은 중국어 뉴스 번역 및 중국어 교육 전문가이다. "
            "사실을 추가로 만들어내지 말고 반드시 유효한 JSON만 "
            "출력한다."
        ),
        input=prompt,
        store=False
    )

    output_text = remove_markdown_code_fence(
        response.output_text
    )

    try:
        parsed = json.loads(output_text)
    except json.JSONDecodeError as error:
        raise RuntimeError(
            "OpenAI 응답을 JSON으로 해석하지 못했습니다."
        ) from error

    news = parsed.get("news")

    if not isinstance(news, list) or len(news) != 10:
        raise RuntimeError(
            "OpenAI 응답의 뉴스 개수가 10개가 아닙니다."
        )

    # 모델이 URL이나 순위를 바꾸지 못하도록 원본 값을 다시 적용
    for index, item in enumerate(news):
        item["rank"] = raw_news[index]["rank"]
        item["chinese"] = raw_news[index]["chinese"]
        item["url"] = raw_news[index]["url"]

        words = item.get("words", [])

        if not isinstance(words, list):
            item["words"] = []

    return news


def save_products_json(news: list[dict[str, Any]]) -> dict[str, Any]:
    """홈페이지가 읽는 products.json을 새 내용으로 교체합니다."""
    now = datetime.now().astimezone()

    data = {
        "updatedAt": now.strftime("%Y-%m-%d %H:%M"),
        "source": "Baidu Hot Search",
        "sourceUrl": BAIDU_URL,
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


def make_word_html(words: list[dict[str, Any]]) -> str:
    """이메일 안에 표시할 단어 목록 HTML을 만듭니다."""
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
                    font-size:21px;
                    font-weight:700;
                ">
                    {chinese}
                </div>

                <div style="
                    margin-top:4px;
                    color:#315efb;
                    font-size:17px;
                    font-weight:600;
                ">
                    {pinyin}
                </div>

                <div style="
                    margin-top:5px;
                    color:#475467;
                    font-size:16px;
                ">
                    {meaning}
                </div>
            </div>
            """
        )

    return "".join(word_items)


def make_email_html(data: dict[str, Any]) -> str:
    """TOP10 학습자료 전체를 이메일 HTML로 만듭니다."""
    cards = []

    for item in data["news"]:
        rank = int(item.get("rank", 0))
        chinese = html.escape(str(item.get("chinese", "")))
        pinyin = html.escape(str(item.get("pinyin", "")))
        translation = html.escape(
            str(item.get("translation", ""))
        )
        summary = html.escape(str(item.get("summary", "")))
        url = html.escape(str(item.get("url", BAIDU_URL)))

        words_html = make_word_html(item.get("words", []))

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
                        내용 요약
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
                        전체 단어 {len(item.get("words", []))}개
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

    updated_at = html.escape(str(data["updatedAt"]))

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
                padding:24px;
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
            </header>

            {''.join(cards)}

            <footer style="
                padding:12px;
                color:#667085;
                font-size:12px;
                text-align:center;
            ">
                중국어 학습용 자동 메일입니다.
            </footer>
        </div>
    </body>
    </html>
    """


def send_email(data: dict[str, Any]) -> None:
    """Gmail SMTP를 사용해 학습자료를 전송합니다."""
    if not EMAIL_USER:
        raise RuntimeError("EMAIL_USER가 설정되지 않았습니다.")

    if not EMAIL_APP_PASSWORD:
        raise RuntimeError(
            "EMAIL_APP_PASSWORD가 설정되지 않았습니다."
        )

    if not EMAIL_TO:
        raise RuntimeError("EMAIL_TO가 설정되지 않았습니다.")

    message = EmailMessage()

    message["Subject"] = (
        f"[바이두 TOP10 중국어] {data['updatedAt']}"
    )

    message["From"] = EMAIL_USER
    message["To"] = EMAIL_TO

    message.set_content(
        "HTML 이메일을 지원하는 메일 앱에서 확인해 주세요."
    )

    message.add_alternative(
        make_email_html(data),
        subtype="html"
    )

    with smtplib.SMTP_SSL(
        "smtp.gmail.com",
        465,
        timeout=30
    ) as smtp:
        smtp.login(
            EMAIL_USER,
            EMAIL_APP_PASSWORD
        )

        smtp.send_message(message)


def main() -> None:
    print("1. 바이두 TOP10 수집 시작")
    raw_news = fetch_baidu_top10()

    for item in raw_news:
        print(
            f"{item['rank']}위: "
            f"{item['chinese']}"
        )

    print("2. 병음·번역·요약·단어 생성 시작")
    learning_news = create_learning_data(raw_news)

    print("3. products.json 저장")
    saved_data = save_products_json(learning_news)

    print("4. 이메일 발송")
    send_email(saved_data)

    print("완료")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"오류: {error}", file=sys.stderr)
        raise
