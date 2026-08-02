const newsList = document.querySelector("#newsList");
const searchInput = document.querySelector("#searchInput");
const newsCount = document.querySelector("#newsCount");
const updatedAt = document.querySelector("#updatedAt");
const refreshButton = document.querySelector("#refreshButton");

let newsData = [];

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function createWordItems(words = []) {
  return words
    .map((word) => {
      return `
        <div class="word-item">
          <span class="word-chinese">
            ${escapeHtml(word.chinese)}
          </span>

          <span class="word-pinyin">
            ${escapeHtml(word.pinyin)}
          </span>

          <span class="word-meaning">
            ${escapeHtml(word.meaning)}
          </span>
        </div>
      `;
    })
    .join("");
}

function createNewsCard(news) {
  const rankClass = news.rank <= 3 ? "top-three" : "";
  const wordTotal = Array.isArray(news.words)
    ? news.words.length
    : 0;

  return `
    <article class="news-card">
      <div class="news-header">
        <span class="rank ${rankClass}">
          ${escapeHtml(news.rank)}
        </span>

        <div class="title-group">
          <span class="chinese-title">
            ${escapeHtml(news.chinese)}
          </span>

          <span class="korean-title">
            ${escapeHtml(news.translation)}
          </span>
        </div>
      </div>

      <div class="news-content">
        <div class="learning-block pinyin-block">
          <h3>병음</h3>
          <p>${escapeHtml(news.pinyin)}</p>
        </div>

        <div class="learning-block translation-block">
          <h3>한국어 해석</h3>
          <p>${escapeHtml(news.translation)}</p>
        </div>

        <div class="learning-block summary-block">
          <h3>내용 요약</h3>
          <p>${escapeHtml(news.summary)}</p>
        </div>

        <div class="word-block">
          <h3>전체 단어 ${wordTotal}개</h3>

          <div class="word-list">
            ${createWordItems(news.words)}
          </div>
        </div>
      </div>
    </article>
  `;
}

function renderNews() {
  const keyword = searchInput.value
    .trim()
    .toLowerCase();

  const filteredNews = newsData.filter((news) => {
    const searchableText = [
      news.chinese,
      news.pinyin,
      news.translation,
      news.summary,
      ...(news.words ?? []).flatMap((word) => [
        word.chinese,
        word.pinyin,
        word.meaning
      ])
    ]
      .join(" ")
      .toLowerCase();

    return searchableText.includes(keyword);
  });

  newsCount.textContent = `${filteredNews.length}개`;

  if (filteredNews.length === 0) {
    newsList.innerHTML = `
      <p class="empty-message">
        검색 결과가 없습니다.
      </p>
    `;

    return;
  }

  newsList.innerHTML = filteredNews
    .map(createNewsCard)
    .join("");
}

async function loadNews() {
  try {
    newsList.innerHTML = `
      <p class="loading-message">
        데이터를 불러오는 중입니다.
      </p>
    `;

    const response = await fetch(
      `./products.json?time=${Date.now()}`
    );

    if (!response.ok) {
      throw new Error(
        `데이터를 불러오지 못했습니다: ${response.status}`
      );
    }

    const result = await response.json();

    if (!Array.isArray(result.news)) {
      throw new Error(
        "products.json의 news 항목이 올바르지 않습니다."
      );
    }

    newsData = result.news;

    updatedAt.textContent =
      result.updatedAt || "시간 없음";

    renderNews();
  } catch (error) {
    console.error(error);

    newsCount.textContent = "0개";
    updatedAt.textContent = "불러오기 실패";

    newsList.innerHTML = `
      <p class="error-message">
        데이터를 불러오지 못했습니다.<br>
        products.json 파일을 확인해 주세요.
      </p>
    `;
  }
}

searchInput.addEventListener("input", renderNews);
refreshButton.addEventListener("click", loadNews);

loadNews();
