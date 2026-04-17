/**
 * ============================================================
 * 出勤希望フォーム 自動生成スクリプト  (Google Apps Script)
 * ============================================================
 *
 * 【使い方】
 *   1. Google スプレッドシートを新規作成
 *   2. 拡張機能 > Apps Script でこのコードを貼り付け
 *   3. CONFIG を編集（期間・スタッフ名・シフトパターン）
 *   4. createShiftForm() を実行 → 認証を許可
 *   5. ログに表示された「配布URL」をスタッフに共有
 *
 * 【フォームの流れ】
 *   Q1 : お名前（プルダウン・必須）
 *   ↓ 日付ごとに繰り返し（日数分のセクション）
 *   Q2n: X日（曜）は出勤できますか？（ラジオ・任意）
 *     ├─ 朝食のみ       → 朝食シフト選択セクションへ
 *     ├─ ディナーのみ    → ディナーシフト選択セクションへ
 *     ├─ ダブル(両方)   → 次の日へ（ダブルシフト確定・時間指定なし）
 *     └─ 休み           → 次の日へ（スキップ）
 *   Q3n: 朝食 / ディナー の希望シフトをプルダウンで選択
 *   ↓
 *   Q4 : 備考欄（記述式・任意）
 *
 * 【CSV インポート時の注意】
 *   回答収集後、スプレッドシートの
 *   「ファイル > ダウンロード > CSV」でエクスポートし、
 *   シフト管理アプリの「Google Forms CSV をインポート」から読み込んでください。
 * ============================================================
 */

// ============================================================
// ★ ここを毎月編集してください
// ============================================================
const CONFIG = {
  PERIOD_START : '2025-04-01',   // シフト期間開始 (YYYY-MM-DD)
  PERIOD_END   : '2025-04-14',   // シフト期間終了 (YYYY-MM-DD)
  FORM_TITLE   : '4月 出勤希望フォーム',
  FORM_DESC    : [
    '【回答期限】◯月◯日（◯）23:59 までに回答してください。',
    '',
    '【注意事項】',
    '・お名前はリストから正確に選んでください',
    '・日付ごとに出勤の可否と希望シフト時間を入力します',
    '・ダブルは朝食・ディナー両方フル出勤（時間帯は確定）',
    '・リストにない時間帯は「その他」を選択して備考欄に記入',
  ].join('\n'),

  // ── 登録スタッフ名（アプリの登録名と完全一致させること）──
  EMPLOYEES: [
    '田中 太郎',
    '山田 花子',
    '佐藤 次郎',
    '鈴木 美咲',
    '伊藤 健太',
  ],

  // ── 朝食シフトの選択肢（アプリの「パターンラベル」と一致させること）──
  BREAKFAST_PATTERNS: [
    '朝食短番 (6:00〜10:00)',
    '朝食 (6:30〜11:30)',
    '朝食ロング (5:45〜14:45)',
    '午前半日 (6:30〜15:30)',
    'その他（備考欄に時刻を記入）',
  ],

  // ── ディナーシフトの選択肢 ──
  DINNER_PATTERNS: [
    '通し早番 (13:00〜22:30)',
    '通し遅番 (14:00〜23:00)',
    'ディナー (17:00〜22:30)',
    'ディナー (17:30〜23:00)',
    'ディナー (18:00〜23:00)',
    'ディナー前半 (17:00〜21:00)',
    'ディナー前半 (17:30〜21:30)',
    'ディナー短番 (18:00〜22:00)',
    'その他（備考欄に時刻を記入）',
  ],
};

// ============================================================
// ヘルパー
// ============================================================
const _DOW_JA = ['日', '月', '火', '水', '木', '金', '土'];

/** YYYY-MM-DD → Date オブジェクトの配列（タイムゾーンずれを防ぐためローカル解釈） */
function _getDates(startStr, endStr) {
  const [sy, sm, sd] = startStr.split('-').map(Number);
  const [ey, em, ed] = endStr.split('-').map(Number);
  const dates = [];
  let d = new Date(sy, sm - 1, sd);
  const end = new Date(ey, em - 1, ed);
  while (d <= end) {
    dates.push(new Date(d));
    d.setDate(d.getDate() + 1);
  }
  return dates;
}

/** Date → 「4月1日（月）」形式 */
function _label(d) {
  return `${d.getMonth() + 1}月${d.getDate()}日（${_DOW_JA[d.getDay()]}）`;
}

// ============================================================
// メイン: フォームを生成
// ============================================================
function createShiftForm() {
  const dates = _getDates(CONFIG.PERIOD_START, CONFIG.PERIOD_END);

  const form = FormApp.create(CONFIG.FORM_TITLE);
  form.setDescription(CONFIG.FORM_DESC);
  form.setCollectEmail(false);
  form.setAllowResponseEdits(true);  // 回答後の編集を許可

  // ── Q1: お名前（プルダウン・必須）──────────────────────────────
  form.addListItem()
      .setTitle('お名前')
      .setHelpText('リストから自分の名前を選択してください。')
      .setRequired(true)
      .setChoiceValues(CONFIG.EMPLOYEES);

  // ── 各日のページを順番に作成 ────────────────────────────────────
  // ルーティング設定のためにページ参照・質問参照を保持する
  const availPages = [];  // 出勤可否ページ（各日1つ）
  const bPages     = [];  // 朝食シフト選択ページ（各日1つ）
  const dPages     = [];  // ディナーシフト選択ページ（各日1つ）
  const availItems = [];  // 出勤可否ラジオの質問アイテム

  for (let i = 0; i < dates.length; i++) {
    const dl = _label(dates[i]);

    // ── 出勤可否セクション ──────────────────────────────────────
    availPages.push(
      form.addPageBreakItem().setTitle(`${dl} の出勤希望`)
    );
    availItems.push(
      form.addMultipleChoiceItem()
          .setTitle(`${dl} は出勤できますか？`)
          .setRequired(false)   // ④ 未回答 = スキップ
    );

    // ── 朝食シフト選択セクション ────────────────────────────────
    bPages.push(
      form.addPageBreakItem().setTitle(`${dl} 朝食 ─ 希望シフト`)
    );
    form.addListItem()
        .setTitle(`${dl} 朝食の希望シフトを選んでください`)
        .setHelpText('リストにない場合は「その他」を選択し、最後の備考欄に時刻を記入してください。')
        .setRequired(false)
        .setChoiceValues(CONFIG.BREAKFAST_PATTERNS);

    // ── ディナーシフト選択セクション ────────────────────────────
    dPages.push(
      form.addPageBreakItem().setTitle(`${dl} ディナー ─ 希望シフト`)
    );
    form.addListItem()
        .setTitle(`${dl} ディナーの希望シフトを選んでください`)
        .setHelpText('リストにない場合は「その他」を選択し、最後の備考欄に時刻を記入してください。')
        .setRequired(false)
        .setChoiceValues(CONFIG.DINNER_PATTERNS);
  }

  // ── 備考セクション（最終ページ）──────────────────────────────────
  const finalPage = form.addPageBreakItem().setTitle('備考');
  form.addParagraphTextItem()
      .setTitle('その他、シフトに関する連絡事項があれば記入してください')
      .setHelpText('「その他」を選んだ日の時刻、遅刻・早退の可能性など\n例: 4/3 朝食 8:00〜12:00、4/10 18:00以降は早退希望')
      .setRequired(false);

  // ── 条件分岐（ページルーティング）の設定 ────────────────────────
  for (let i = 0; i < dates.length; i++) {
    // 次に進むべきページ（最終日なら備考ページ）
    const nextPage = (i + 1 < dates.length) ? availPages[i + 1] : finalPage;

    // 朝食・ディナーページの終了後は次の日（または備考ページ）へ
    bPages[i].setGoToPage(nextPage);
    dPages[i].setGoToPage(nextPage);

    // 出勤可否の選択肢ごとのルーティング
    const aq = availItems[i];
    aq.setChoices([
      aq.createChoice(
        '朝食のみ',
        FormApp.PageNavigationType.GO_TO_PAGE,
        bPages[i]    // 朝食シフト選択へ
      ),
      aq.createChoice(
        'ディナーのみ',
        FormApp.PageNavigationType.GO_TO_PAGE,
        dPages[i]    // ディナーシフト選択へ
      ),
      aq.createChoice(
        'ダブル（朝食＋ディナー両方）',
        FormApp.PageNavigationType.GO_TO_PAGE,
        nextPage     // 次の日へ（ダブルシフト確定）
      ),
      aq.createChoice(
        '休み',
        FormApp.PageNavigationType.GO_TO_PAGE,
        nextPage     // 次の日へ（スキップ）
      ),
    ]);
  }

  // ── ログ出力 ───────────────────────────────────────────────────
  const editUrl  = form.getEditUrl();
  const pubUrl   = form.getPublishedUrl();
  const shortUrl = form.shortenFormUrl(pubUrl);

  Logger.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━');
  Logger.log(`✅ 生成完了: ${CONFIG.FORM_TITLE}`);
  Logger.log(`📋 編集URL  (管理者): ${editUrl}`);
  Logger.log(`🔗 配布URL (スタッフ): ${shortUrl}`);
  Logger.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━');

  // スプレッドシートが紐付いている場合はA列にURLを記録
  try {
    SpreadsheetApp.getActiveSpreadsheet()
      .getActiveSheet()
      .getRange('A1:B3')
      .setValues([
        ['生成日時', new Date().toLocaleString('ja-JP')],
        ['編集URL（管理者）', editUrl],
        ['配布URL（スタッフ）', shortUrl],
      ]);
  } catch (e) { /* スタンドアロンスクリプトの場合は無視 */ }

  return shortUrl;
}
