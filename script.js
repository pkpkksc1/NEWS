const newsList = document.querySelector("#newsList");
const searchInput = document.querySelector("#searchInput");
const newsCount = document.querySelector("#newsCount");
const updatedAt = document.querySelector("#updatedAt");
const refreshButton = document.querySelector("#refreshButton");

let newsData = [];
let dailyConversation = [];


function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}



function speakChinese(text) {
  const content = String(text ?? "").trim();

  if (!content) {
    return;
  }

  if (!("speechSynthesis" in window)) {
    alert("이 브라우저는 음성 읽기를 지원하지 않습니다.");
    return;
  }

  window.speechSynthesis.cancel();

  const utterance = new SpeechSynthesisUtterance(content);
  utterance.lang = "zh-CN";
  utterance.rate = 0.86;
  utterance.pitch = 1;

  const voices = window.speechSynthesis.getVoices();
  const preferredNames = ["xiaoxiao", "huihui", "yunxi", "google 普通话", "mandarin"];
  const chineseVoices = voices.filter((voice) =>
    voice.lang?.toLowerCase().startsWith("zh")
  );
  const chineseVoice = chineseVoices.find((voice) =>
    preferredNames.some((name) => voice.name.toLowerCase().includes(name))
  ) || chineseVoices[0];

  if (chineseVoice) {
    utterance.voice = chineseVoice;
  } else if (voices.length > 0) {
    alert("이 기기에 중국어 음성이 없습니다. Windows 언어 설정에서 중국어 음성을 설치해 주세요.");
    return;
  }

  utterance.onerror = () => {
    alert("음성을 재생하지 못했습니다. Chrome 또는 Edge에서 다시 시도해 주세요.");
  };
  window.speechSynthesis.speak(utterance);
}


function createTtsButton(text, label = "중국어 듣기") {
  const encodedText = encodeURIComponent(String(text ?? ""));

  return `
    <button
      type="button"
      class="tts-button"
      data-tts-text="${encodedText}"
      aria-label="${escapeHtml(label)}"
      title="${escapeHtml(label)}"
    >
      🔊 듣기
    </button>
  `;
}


function createWordItems(words = []) {

  return words
    .map((word) => {

      return `
        <div class="word-item">

          <div class="word-title-row">
            <span class="word-chinese">
              ${escapeHtml(word.chinese)}
            </span>
            ${createTtsButton(word.chinese, `${word.chinese} 듣기`)}
          </div>

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

        <div class="expression-title-row">
          <div class="expression-chinese">
            ${escapeHtml(expression.chinese)}
          </div>
          ${createTtsButton(expression.chinese, "핵심 표현 듣기")}
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


          <div class="chinese-title-row">
            <span class="chinese-title">
              ${escapeHtml(news.chinese)}
            </span>
            ${createTtsButton(news.chinese, "뉴스 제목 듣기")}
          </div>


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
            <div class="detail-label-row">
              <span class="detail-label chinese-label">중국어</span>
              ${createTtsButton(news.detailChinese || news.chinese, "자세한 중국어 내용 듣기")}
            </div>
            <p class="detail-chinese">
              ${escapeHtml(news.detailChinese || "상세 중국어 내용이 없습니다.")}
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





function createConversationSection(items = []) {
  if (!Array.isArray(items) || items.length === 0) {
    return "";
  }

  return `
    <section class="conversation-section">
      <div class="conversation-heading">
        <span>💬 매일 쓰는 중국어 회화</span>
        <h2>오늘의 회화 3문장</h2>
      </div>
      <div class="conversation-list">
        ${items.map((item, index) => `
          <article class="conversation-item">
            <div class="conversation-number">${index + 1}</div>
            <div class="conversation-content">
              <div class="conversation-chinese-row">
                <strong class="conversation-chinese">${escapeHtml(item.chinese)}</strong>
                ${createTtsButton(item.chinese, "회화 문장 듣기")}
              </div>
              <div class="conversation-pinyin">${escapeHtml(item.pinyin)}</div>
              <div class="conversation-meaning">${escapeHtml(item.meaning)}</div>
            </div>
          </article>
        `).join("")}
      </div>
    </section>
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
      .join("") +
    (keyword ? "" : createConversationSection(dailyConversation));

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

    dailyConversation = Array.isArray(result.dailyConversation)
      ? result.dailyConversation
      : [];



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




newsList.addEventListener("click", (event) => {
  const button = event.target.closest(".tts-button");

  if (!button) {
    return;
  }

  try {
    speakChinese(decodeURIComponent(button.dataset.ttsText || ""));
  } catch (error) {
    console.error("TTS 실행 오류", error);
  }
});


if ("speechSynthesis" in window) {
  window.speechSynthesis.getVoices();
  window.speechSynthesis.onvoiceschanged = () => window.speechSynthesis.getVoices();
}


loadNews();
