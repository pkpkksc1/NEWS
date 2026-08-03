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

          ${escapeHtml(expression.exampleMeaning)}

        </div>

      </div>

    </div>
  `;
}




function createKeyPoints(points = []) {
  if (!Array.isArray(points) || points.length === 0) {
    return "";
  }

  return `
    <div class="learning-block key-points-block">
      <h3>핵심 내용</h3>
      <ul class="key-points-list">
        ${points.map((point) => `<li>${escapeHtml(point)}</li>`).join("")}
      </ul>
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





        <!-- 자세한 내용 -->

        <div class="learning-block summary-block detail-learning-block">

          <h3>
            자세한 내용
          </h3>

          <div class="detail-language-row">
            <span class="detail-label chinese-label">중국어</span>
            <p class="detail-chinese">
              ${escapeHtml(news.detailChinese || "바이두에 상세 설명이 표시되지 않았습니다.")}
            </p>
          </div>

          <div class="detail-language-row">
            <span class="detail-label pinyin-label">병음</span>
            <p class="detail-pinyin">
              ${escapeHtml(news.detailPinyin || "-")}
            </p>
          </div>

          <div class="detail-language-row">
            <span class="detail-label korean-label">한국어</span>
            <p class="detail-korean">
              ${escapeHtml(news.summary)}
            </p>
          </div>

        </div>


        <!-- 핵심 내용 -->

        ${createKeyPoints(news.keyPoints)}






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
