const newsList = document.querySelector("#newsList");
const searchInput = document.querySelector("#searchInput");
const newsCount = document.querySelector("#newsCount");
const updatedAt = document.querySelector("#updatedAt");
const refreshButton = document.querySelector("#refreshButton");
const dailyDashboard = document.querySelector("#dailyDashboard");

let newsData = [];


function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}




function expressionCard(title, icon, item, className) {
  if (!item || !item.chinese) {
    return `
      <article class="daily-card ${className} empty-daily-card">
        <div class="daily-card-label">${icon} ${escapeHtml(title)}</div>
        <p>표현 이력이 쌓이면 표시됩니다.</p>
      </article>
    `;
  }
  return `
    <article class="daily-card ${className}">
      <div class="daily-card-label">${icon} ${escapeHtml(title)}</div>
      <div class="daily-card-chinese">${escapeHtml(item.chinese)}</div>
      <div class="daily-card-pinyin">${escapeHtml(item.pinyin || "")}</div>
      <div class="daily-card-meaning">${escapeHtml(item.meaning || "")}</div>
      ${item.example ? `
        <div class="daily-card-example">
          <strong>예문</strong>
          <div>${escapeHtml(item.example)}</div>
          <div class="daily-card-pinyin small">${escapeHtml(item.examplePinyin || "")}</div>
          <div>${escapeHtml(item.exampleMeaning || "")}</div>
        </div>
      ` : ""}
      ${item.date ? `<div class="review-date">${escapeHtml(item.date)} 학습 표현</div>` : ""}
    </article>
  `;
}

function conversationCard(items = []) {
  const rows = items.map((item, index) => `
    <div class="conversation-row">
      <div class="conversation-number">${index + 1}</div>
      <div class="conversation-copy">
        <div class="conversation-chinese">${escapeHtml(item.chinese)}</div>
        <div class="daily-card-pinyin">${escapeHtml(item.pinyin || "")}</div>
        <div class="conversation-meaning">${escapeHtml(item.meaning || "")}</div>
      </div>
    </div>
  `).join("");
  return `
    <article class="daily-card conversation-card">
      <div class="daily-card-label">💬 오늘의 회화 3문장</div>
      <div class="conversation-list">${rows || "회화 데이터가 없습니다."}</div>
    </article>
  `;
}

function renderDashboard(result) {
  if (!dailyDashboard) return;
  dailyDashboard.innerHTML = `
    <div class="daily-dashboard-heading">
      <p class="section-eyebrow">每天十五分钟</p>
      <h2>오늘의 중국어</h2>
      <p>표현과 단어를 익히고, 회화로 말해 본 뒤 3일 전 내용을 복습하세요.</p>
    </div>
    <div class="daily-grid">
      ${expressionCard("오늘의 표현", "✨", result.todayExpression, "expression-daily-card")}
      ${expressionCard("오늘의 단어", "⭐", result.todayWord, "word-daily-card")}
      ${conversationCard(result.dailyConversation || [])}
      ${expressionCard("3일 전 복습", "🔁", result.reviewExpression, "review-daily-card")}
    </div>
  `;
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



function createExpression(news) {

  const expression = news.expression;

  if (!expression) {
    return "";
  }


  return `
    <div class="learning-block expression-block">

      <h3>
        ⭐ 중국어 핵심 표현
      </h3>


      <div class="expression-box">

        <div class="expression-chinese">
          ${escapeHtml(expression.chinese)}
        </div>


        <div class="expression-pinyin">
          ${escapeHtml(expression.pinyin)}
        </div>


        <div class="expression-meaning">
          ${escapeHtml(expression.meaning)}
        </div>


        <div class="expression-example">

          <strong>
            예문
          </strong>

          <br>

          ${escapeHtml(expression.example)}

          <br>

          <span class="expression-example-pinyin">
            ${escapeHtml(expression.examplePinyin || "")}
          </span>

          <br>

          ${escapeHtml(expression.exampleMeaning)}

        </div>

      </div>

    </div>
  `;
}




function createKeyPoint(news) {
  const legacyPoints = Array.isArray(news.keyPoints) ? news.keyPoints : [];
  const point = String(news.keyPoint || legacyPoints.filter(Boolean).join(" ") || "").trim();

  if (!point) {
    return "";
  }

  return `
    <div class="learning-block key-points-block">
      <h3>핵심 내용</h3>
      <p class="key-point-text">${escapeHtml(point)}</p>
    </div>
  `;
}


function createSourceExcerpt(news) {
  if (!news.sourceExcerpt) {
    return "";
  }

  return `
    <div class="learning-block source-excerpt-block">
      <h3>중국어 원문 발췌</h3>
      <p>${escapeHtml(news.sourceExcerpt)}</p>
    </div>
  `;
}



function createNewsCard(news) {

  const rankClass =
    news.rank <= 3
      ? "top-three"
      : "";


  const wordTotal =
    Array.isArray(news.words)
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


        <!-- 병음 -->

        <div class="learning-block pinyin-block">

          <h3>
            병음
          </h3>


          <p>
            ${escapeHtml(news.pinyin)}
          </p>

        </div>





        <!-- 핵심 표현 -->

        ${createExpression(news)}






        <!-- 한국어 해석 -->

        <div class="learning-block translation-block">


          <h3>
            한국어 해석
          </h3>


          <p>
            ${escapeHtml(news.translation)}
          </p>


        </div>





        <!-- 중국어 원문 발췌 -->

        ${createSourceExcerpt(news)}


        <!-- 자세한 내용 -->

        <div class="learning-block summary-block">

          <h3>
            자세한 내용
          </h3>

          <p>
            ${escapeHtml(news.summary)}
          </p>

        </div>


        <!-- 핵심 내용 -->

        ${createKeyPoint(news)}






        <!-- 단어 -->

        <div class="word-block">


          <h3>
            전체 단어 ${wordTotal}개
          </h3>


          <div class="word-list">

            ${createWordItems(news.words)}

          </div>


        </div>



      </div>



    </article>

  `;
}





function renderNews() {


  const keyword =
    searchInput.value
      .trim()
      .toLowerCase();



  const filteredNews =
    newsData.filter((news) => {


      const searchableText = [

        news.chinese,

        news.pinyin,

        news.translation,

        news.summary,


        news.expression?.chinese,

        news.expression?.meaning,


        ...(news.words ?? [])
          .flatMap((word) => [

            word.chinese,

            word.pinyin,

            word.meaning

          ])

      ]

      .join(" ")

      .toLowerCase();



      return searchableText.includes(keyword);


    });




  newsCount.textContent =
    `${filteredNews.length}개`;



  if(filteredNews.length === 0){


    newsList.innerHTML = `

      <p class="empty-message">

        검색 결과가 없습니다.

      </p>

    `;


    return;

  }




  newsList.innerHTML =
    filteredNews
      .map(createNewsCard)
      .join("");

}





async function loadNews(){


  try{


    newsList.innerHTML = `

      <p class="loading-message">

        데이터를 불러오는 중입니다.

      </p>

    `;



    const response =
      await fetch(
        `./products.json?time=${Date.now()}`
      );



    if(!response.ok){

      throw new Error(
        `데이터 오류: ${response.status}`
      );

    }




    const result =
      await response.json();




    if(!Array.isArray(result.news)){

      throw new Error(
        "news 데이터 없음"
      );

    }




    newsData =
      result.news;

    renderDashboard(result);

    updatedAt.textContent =
      result.updatedAt || "시간 없음";



    renderNews();




  }catch(error){


    console.error(error);



    newsCount.textContent =
      "0개";



    updatedAt.textContent =
      "불러오기 실패";



    newsList.innerHTML = `

      <p class="error-message">

        데이터를 불러오지 못했습니다.

        <br>

        products.json 확인 필요

      </p>

    `;


  }


}







searchInput.addEventListener(
  "input",
  renderNews
);



refreshButton.addEventListener(
  "click",
  loadNews
);



loadNews();
