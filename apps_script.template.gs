// EduApp туған күндер боты — Google-таблицаның автоматикасы (Extensions → Apps Script).
// Бұл кодты ешкімге бермеңіз — ішінде ботқа арналған құпия кілттер бар.
//
// Не істейді:
//  1. Таңғы будильник: күн сайын 09:00-ден бастап (Алматы) ботқа /morning сұрауын жібереді.
//     Сәтсіз болса — 15 минут сайын қайталайды, бір күнде екі рет жібермейді.
//  2. Кадр кестесімен синхрондау (HR_SHEET_ID): жаңа қызметкер — тізімге қосылады,
//     кестеде жоқ адам — «Архив» парағына көшеді, лауазымы мен бөлімі жаңарады.
//     «Архивке жібермеу» бағанында белгі тұрса — адам ешқашан архивке кетпейді.
//  3. AI (Gemini): негізінде мәтінді боттың сервері жазады (GEMINI_API_KEY серверде). Мұнда — тек запас,
//     Script properties-те GEMINI_API_KEY болса ғана.
//  4. Бот үшін веб-қосымша (doPost): add / delete / set_text / set_date / generate.
//
// Бір рет баптау: кодты қойып, setup функциясын іске қосыңыз (Run), содан кейін Deploy → New version.
// Толығырақ — README.md.

const SECRET = '__SHEET_WEBAPP_SECRET__';           // ботпен ортақ құпия (.env → SHEET_WEBAPP_SECRET)
const MORNING_URL = '__MORNING_URL__';              // https://<логин>.pythonanywhere.com/morning
const MORNING_SECRET = '__WEBHOOK_SECRET__';        // .env → WEBHOOK_SECRET
// Кадр тізімі: осы таблицадағы «Қызметкерлер» парағы (Аты-жөні / ФИО, Бөлім, Лауазымы).
// Тізім басқа Google-таблицада болса — HR_SHEET_ID-ге соның ID-ін жазыңыз.
const HR_SHEET_ID = '';
const HR_SHEET_NAME = 'Қызметкерлер';

const TZ = 'Asia/Almaty';
const MORNING_HOUR = 9;
const HR_SYNC_EVERY_MINUTES = 60;
const ARCHIVE_SHEET = 'Архив';
const MAX_ARCHIVE_SHARE = 0.4;  // бір синхрондауда тізімнің 40%-нан көбі кетсе — кесте бұзылған деп, ешкімді архивтемейміз
const AI_PER_RUN = 5;
const DEFAULT_MODEL = 'gemini-flash-latest';

// Бот тізіміндегі аты кадр кестесінде басқаша жазылған адамдар: синхрондау алдында тізімде атын кадр кестесіндегідей
// етеміз — әйтпесе бот оны «кеткен» деп архивтеп, «жаңа» адамды күнсіз қосып қояды. Бір рет орындалады.
const RENAMES = {
  'Saparov Zhasulan': 'Сапаров Жасұлан',
  'Zhaqypov Qanat Maratuly': 'Жақыпов Қанат',
  'Мөлдір': 'Омарова Мөлдір',
};
// Кадр кестесінде жоқ, бірақ тізімнен кетпеуі керек адамдар («Архивке жібермеу» бағанына «иә» қойылады).
const KEEP = ['Бейсен Дастан Асқарұлы'];

// ---------- Баптау ----------

function setup() {
  ScriptApp.getProjectTriggers()
    .filter(t => t.getHandlerFunction() === 'tick')
    .forEach(t => ScriptApp.deleteTrigger(t));
  ScriptApp.newTrigger('tick').timeBased().everyMinutes(15).create();
  withLock(() => ensureColumns(mainSheet()));
  tick();
  Logger.log('Дайын: tick 15 минут сайын іске қосылады.');
}

/** Әр 15 минут: таңғы хабарлама, кадр кестесімен синхрондау, AI мәтіндері. */
function tick() {
  const steps = [morning, syncHr, fillTexts];
  for (const step of steps) {
    try {
      step();
    } catch (err) {
      Logger.log(`${step.name}: ${err && err.stack || err}`);
    }
  }
}

// ---------- 1. Таңғы будильник ----------

function morning(force) {
  if (!MORNING_URL || MORNING_URL.indexOf('http') !== 0) return;
  const now = new Date();
  const today = Utilities.formatDate(now, TZ, 'yyyy-MM-dd');
  const hour = Number(Utilities.formatDate(now, TZ, 'H'));
  const store = PropertiesService.getScriptProperties();
  if (!force && (hour < MORNING_HOUR || store.getProperty('MORNING_DONE') === today)) return;
  const response = UrlFetchApp.fetch(MORNING_URL, {
    method: 'post',
    headers: { 'X-Morning-Secret': MORNING_SECRET },
    muteHttpExceptions: true,
  });
  const code = response.getResponseCode();
  Logger.log(`morning ${code}: ${response.getContentText().slice(0, 300)}`);
  if (code === 200) store.setProperty('MORNING_DONE', today);
}

/** Қолмен тексеру үшін: таңғы хабарламаны қазір жіберу (бүгін әлі жіберілмесе). */
function morningNow() {
  morning(true);
}

// ---------- Кесте ----------

function mainSheet() {
  return SpreadsheetApp.getActiveSpreadsheet().getSheets()[0];
}

function headerIndex(sheet) {
  const width = Math.max(sheet.getLastColumn(), 1);
  const headers = sheet.getRange(1, 1, 1, width).getValues()[0].map(h => String(h).trim().toLowerCase());
  const find = variants => {
    for (const v of variants) {
      const i = headers.findIndex(h => h.indexOf(v) >= 0);
      if (i >= 0) return i + 1;
    }
    return 0;
  };
  return {
    name: find(['аты-жөні', 'аты жөні', 'фио']),
    date: find(['туған күні']),
    position: find(['лауазым']),
    gender: find(['жыныс']),
    card: find(['карточка']),
    text: find(['мәтін']),
    dept: find(['бөлім']),
    keep: find(['архивке жібермеу']),
    width: width,
  };
}

function ensureColumns(sheet) {
  let cols = headerIndex(sheet);
  if (!cols.dept) sheet.getRange(1, cols.width + 1).setValue('Бөлім');
  cols = headerIndex(sheet);
  if (!cols.keep) sheet.getRange(1, cols.width + 1).setValue('Архивке жібермеу');
  return headerIndex(sheet);
}

function readRows(sheet) {
  const last = sheet.getLastRow();
  if (last < 2) return [];
  return sheet.getRange(2, 1, last - 1, sheet.getLastColumn()).getValues();
}

function cell(row, col) {
  return col ? String(row[col - 1] === null ? '' : row[col - 1]).trim() : '';
}

function norm(s) {
  return String(s).replace(/\s+/g, ' ').trim().toLowerCase();
}

// Қазақ әріптері мен латын «егіздерін» бір түрге келтіреміз: Ғазиза = Газиза, Cәкен (латын C) = Сәкен.
const SAME_LETTER = { 'ә': 'а', 'ғ': 'г', 'қ': 'к', 'ң': 'н', 'ө': 'о', 'ұ': 'у', 'ү': 'у', 'һ': 'х', 'і': 'и', 'ё': 'е',
                      'a': 'а', 'c': 'с', 'e': 'е', 'o': 'о', 'p': 'р', 'x': 'х' };

/** Адамның кілті: тегі мен аты (әкесінің аты ескерілмейді). */
function personKey(name) {
  return norm(name).replace(/[әғқңөұүһіёaceopx]/g, ch => SAME_LETTER[ch]).split(' ').slice(0, 2).join(' ');
}

function findRow(sheet, name) {
  const cols = headerIndex(sheet);
  const rows = readRows(sheet);
  const exact = norm(name);
  for (let i = rows.length - 1; i >= 0; i--) {
    if (norm(cell(rows[i], cols.name)) === exact) return i + 2;
  }
  const key = personKey(name);
  const same = rows.map((r, i) => i).filter(i => personKey(cell(rows[i], cols.name)) === key);
  return same.length === 1 ? same[0] + 2 : 0;
}

function withLock(fn) {
  const lock = LockService.getScriptLock();
  lock.waitLock(30000);
  try {
    return fn();
  } finally {
    lock.releaseLock();
  }
}

// ---------- 2. Кадр кестесімен синхрондау ----------

function hrSheet() {
  const book = HR_SHEET_ID ? SpreadsheetApp.openById(HR_SHEET_ID) : SpreadsheetApp.getActiveSpreadsheet();
  const sheet = HR_SHEET_NAME ? book.getSheetByName(HR_SHEET_NAME) : HR_SHEET_ID ? book.getSheets()[0] : null;
  return sheet && sheet.getSheetId() !== mainSheet().getSheetId() ? sheet : null;
}

function syncHr(force) {
  if (!hrSheet()) return;  // кадр тізімі әлі қосылмаған
  const store = PropertiesService.getScriptProperties();
  const last = Number(store.getProperty('HR_SYNC_AT') || 0);
  if (!force && Date.now() - last < HR_SYNC_EVERY_MINUTES * 60 * 1000) return;
  const staff = readHr();
  withLock(() => applyHr(staff));
  store.setProperty('HR_SYNC_AT', String(Date.now()));
}

/** Кадр кестесі: [{name, dept, position}]. */
function readHr() {
  const values = hrSheet().getDataRange().getValues();
  for (let h = 0; h < Math.min(values.length, 10); h++) {
    const headers = values[h].map(x => String(x).trim().toLowerCase());
    const col = variants => headers.findIndex(x => variants.some(v => x.indexOf(v) >= 0));
    const name = col(['аты-жөні', 'аты жөні', 'фио']);
    if (name < 0) continue;
    const dept = col(['бөлім', 'отдел', 'департамент']);
    const position = col(['лауазым', 'должность']);
    return values.slice(h + 1)
      .map(r => ({
        name: String(r[name]).replace(/\s+/g, ' ').trim(),
        dept: dept >= 0 ? String(r[dept]).trim() : '',
        position: position >= 0 ? String(r[position]).trim() : '',
      }))
      .filter(p => p.name);
  }
  throw new Error('Кадр кестесінде «Аты-жөні / ФИО» бағаны табылмады');
}

function applyHr(staff) {
  if (staff.length < 5) throw new Error(`Кадр кестесінде тым аз адам (${staff.length}) — синхрондау тоқтатылды`);
  const sheet = mainSheet();
  const cols = ensureColumns(sheet);
  const rows = readRows(sheet);
  const renames = {};
  Object.keys(RENAMES).forEach(k => { renames[norm(k)] = RENAMES[k]; });
  const keep = new Set(KEEP.map(norm));
  rows.forEach((r, i) => {
    const name = norm(cell(r, cols.name));
    if (renames[name]) {
      sheet.getRange(i + 2, cols.name).setValue(renames[name]);
      r[cols.name - 1] = renames[name];
    }
    if (keep.has(name) && cols.keep && !cell(r, cols.keep)) {
      sheet.getRange(i + 2, cols.keep).setValue('иә');
      r[cols.keep - 1] = 'иә';
    }
  });
  const byKey = {};
  rows.forEach((r, i) => {
    const key = personKey(cell(r, cols.name));
    if (key) (byKey[key] = byKey[key] || []).push(i);
  });
  const seen = new Set();
  const added = [];
  const restored = [];
  for (const person of staff) {
    const key = personKey(person.name);
    const match = (byKey[key] || []).find(i => !seen.has(i));
    if (match !== undefined) {
      seen.add(match);
      const r = match + 2;
      if (person.position && cols.position && cell(rows[match], cols.position) !== person.position) {
        sheet.getRange(r, cols.position).setValue(person.position);
      }
      if (person.dept && cols.dept && cell(rows[match], cols.dept) !== person.dept) {
        sheet.getRange(r, cols.dept).setValue(person.dept);
      }
      continue;
    }
    if (restoreFromArchive(sheet, cols, person)) {
      restored.push(person.name);
      continue;
    }
    const row = new Array(sheet.getLastColumn()).fill('');
    row[cols.name - 1] = person.name;
    if (cols.position) row[cols.position - 1] = person.position;
    if (cols.dept) row[cols.dept - 1] = person.dept;
    if (cols.card) row[cols.card - 1] = person.name.split(' ').slice(0, 2).join(' ');
    const r = sheet.getLastRow() + 1;
    sheet.getRange(r, 1, 1, row.length).setNumberFormat('@').setValues([row]);
    added.push(person.name);
  }
  const gone = rows.map((r, i) => i)
    .filter(i => !seen.has(i) && cell(rows[i], cols.name) && !cell(rows[i], cols.keep));
  if (gone.length > rows.length * MAX_ARCHIVE_SHARE) {
    Logger.log(`Кадр кестесінде ${gone.length} адам жоқ — тым көп, архивтеу өткізілді`);
  } else if (gone.length) {
    const archive = archiveSheet(sheet);
    gone.sort((a, b) => b - a).forEach(i => {
      archive.appendRow(rows[i]);
      sheet.deleteRow(i + 2);
    });
  }
  Logger.log(`HR: жаңа ${added.length} ${added.join(', ')}; архивтен ${restored.length}; архивке ${gone.length}`);
}

function archiveSheet(main) {
  const book = main.getParent();
  let archive = book.getSheetByName(ARCHIVE_SHEET);
  if (!archive) {
    archive = book.insertSheet(ARCHIVE_SHEET, book.getSheets().length);
    const header = main.getRange(1, 1, 1, main.getLastColumn()).getValues();
    archive.getRange(1, 1, 1, header[0].length).setValues(header).setFontWeight('bold');
    archive.setFrozenRows(1);
    archive.getRange('A:Z').setNumberFormat('@');
  }
  return archive;
}

/** Қайта жұмысқа келген адам: архивтегі жолын (күні, мәтіні бар) тізімге қайтарамыз. */
function restoreFromArchive(main, cols, person) {
  const archive = main.getParent().getSheetByName(ARCHIVE_SHEET);
  if (!archive || archive.getLastRow() < 2) return false;
  const rows = archive.getRange(2, 1, archive.getLastRow() - 1, archive.getLastColumn()).getValues();
  const key = personKey(person.name);
  const i = rows.findIndex(r => personKey(cell(r, cols.name)) === key);
  if (i < 0) return false;
  const row = rows[i].slice(0, main.getLastColumn());
  while (row.length < main.getLastColumn()) row.push('');
  if (person.position && cols.position) row[cols.position - 1] = person.position;
  if (person.dept && cols.dept) row[cols.dept - 1] = person.dept;
  main.getRange(main.getLastRow() + 1, 1, 1, row.length).setNumberFormat('@').setValues([row.map(String)]);
  archive.deleteRow(i + 2);
  return true;
}

// ---------- 3. AI: құттықтау мәтіндері ----------

function fillTexts() {
  const key = PropertiesService.getScriptProperties().getProperty('GEMINI_API_KEY');
  if (!key) return;
  const started = Date.now();
  const sheet = mainSheet();
  const cols = headerIndex(sheet);
  const todo = readRows(sheet)
    .filter(r => cell(r, cols.name) && !cell(r, cols.text))
    .map(r => cell(r, cols.name))
    .slice(0, AI_PER_RUN);
  for (const name of todo) {
    if (Date.now() - started > 3 * 60 * 1000) break;
    generateFor(name, false);
  }
}

/** Мәтін жазып, таблицаға қояды. force — бар мәтінді де қайта жазу. Қайтарады: {text, gender}. */
function generateFor(name, force) {
  const sheet = mainSheet();
  const cols = headerIndex(sheet);
  const r = findRow(sheet, name);
  if (!r) throw new Error('not found');
  const row = sheet.getRange(r, 1, 1, sheet.getLastColumn()).getValues()[0];
  if (!force && cell(row, cols.text)) return { text: cell(row, cols.text), gender: cell(row, cols.gender) };
  const examples = readRows(sheet).map(x => cell(x, cols.text)).filter(Boolean);
  const result = askGemini({
    name: cell(row, cols.name),
    position: cell(row, cols.position),
    dept: cell(row, cols.dept),
    gender: cell(row, cols.gender),
    examples: shuffle(examples).slice(0, 3),
  });
  return withLock(() => {
    const again = findRow(sheet, name);  // AI жазып жатқанда жолдың орны өзгеруі мүмкін
    if (!again) throw new Error('not found');
    const current = sheet.getRange(again, cols.text).getValue();
    if (force || !String(current).trim()) sheet.getRange(again, cols.text).setValue(result.text);
    if (cols.gender && !String(sheet.getRange(again, cols.gender).getValue()).trim() && result.gender) {
      sheet.getRange(again, cols.gender).setValue(result.gender);
    }
    return result;
  });
}

function askGemini(p) {
  const store = PropertiesService.getScriptProperties();
  const key = store.getProperty('GEMINI_API_KEY');
  if (!key) throw new Error('GEMINI_API_KEY бапталмаған (Script properties)');
  const model = store.getProperty('GEMINI_MODEL') || DEFAULT_MODEL;
  const prompt = [
    'Сен EduApp білім беру компаниясының HR-ісің. Қызметкерге туған күнге арналған құттықтау мәтінін ҚАЗАҚ тілінде жаз.',
    '',
    `Қызметкер: ${p.name}`,
    p.position ? `Лауазымы: ${p.position}` : '',
    p.dept ? `Бөлімі: ${p.dept}` : '',
    p.gender ? `Жынысы: ${p.gender}` : 'Жынысы белгісіз — есімі бойынша анықта.',
    '',
    'Ережелер:',
    '- Бірінші жол: «<Есімі>, туған күніңізбен! 🎉». Есімі — тегі емес, аты (әдетте екінші сөз: «Сапаров Ерлан» → «Ерлан»). Есімді қазақша дұрыс жаз.',
    '- Содан кейін бос жол және 2–3 сөйлем: оның жұмысына, лауазымына қатысты жылы, нақты тілек. Жалпы, жасанды сөздерсіз.',
    '- «Сіз» деп құрметпен жаз. Жасын атама.',
    '- Соңғы жол: «Ізгі тілекпен, EduApp командасы 💜».',
    '- Барлығы 200–330 таңба.',
    '',
    p.examples.length ? 'Біздің бұрынғы мәтіндердің үлгілері (стилі осындай болсын, бірақ сөзбе-сөз қайталама):\n\n'
      + p.examples.map(e => '---\n' + e).join('\n') : '',
    '',
    'Жауапты тек JSON түрінде қайтар: {"gender": "ер" немесе "әйел", "text": "құттықтау мәтіні"}',
  ].filter(line => line !== null).join('\n');
  const response = UrlFetchApp.fetch(
    `https://generativelanguage.googleapis.com/v1beta/models/${encodeURIComponent(model)}:generateContent`, {
      method: 'post',
      contentType: 'application/json',
      headers: { 'x-goog-api-key': key },
      payload: JSON.stringify({
        contents: [{ role: 'user', parts: [{ text: prompt }] }],
        generationConfig: { temperature: 0.9, maxOutputTokens: 4096, responseMimeType: 'application/json' },
      }),
      muteHttpExceptions: true,
    });
  const code = response.getResponseCode();
  const body = response.getContentText();
  if (code !== 200) throw new Error(`Gemini ${code}: ${body.slice(0, 300)}`);
  const parts = (((JSON.parse(body).candidates || [])[0] || {}).content || {}).parts || [];
  const raw = parts.filter(x => !x.thought).map(x => x.text || '').join('').trim();
  const json = JSON.parse(raw.replace(/^```(?:json)?\s*|\s*```$/g, ''));
  const text = String(json.text || '').trim();
  if (text.length < 40) throw new Error('Gemini бос мәтін қайтарды');
  const gender = /әйел|жен|female/i.test(String(json.gender)) ? 'әйел' : /ер|муж|male/i.test(String(json.gender)) ? 'ер' : '';
  return { text: text, gender: gender };
}

function shuffle(list) {
  const a = list.slice();
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

/** Тексеру: мәтіні жоқ бірінші адамға қазір мәтін жазу. */
function testAi() {
  fillTexts();
}

// ---------- 4. Бот үшін веб-қосымша ----------

function doPost(e) {
  let data;
  try {
    data = JSON.parse(e.postData.contents);
  } catch (err) {
    return reply({ ok: false, error: 'bad request' });
  }
  if (data.secret !== SECRET) return reply({ ok: false, error: 'forbidden' });
  try {
    if (data.action === 'generate') {
      const result = generateFor(data.name, Boolean(data.force));
      return reply({ ok: true, text: result.text, gender: result.gender });
    }
    return withLock(() => handle(data));
  } catch (err) {
    return reply({ ok: false, error: String(err && err.message || err) });
  }
}

function handle(data) {
  const sheet = mainSheet();
  const cols = headerIndex(sheet);
  if (data.action === 'add') {
    const row = data.row.map(String);
    const r = sheet.getLastRow() + 1;
    sheet.getRange(r, 1, 1, row.length).setNumberFormat('@').setValues([row]);  // дата мәтін болып қалады: 20.08.2004
    return reply({ ok: true, row: r });
  }
  const r = findRow(sheet, data.name);
  if (!r) return reply({ ok: false, error: 'not found' });
  if (data.action === 'delete') {
    sheet.deleteRow(r);
    return reply({ ok: true, row: r });
  }
  if (data.action === 'set_text') {
    sheet.getRange(r, cols.text || 6).setValue(String(data.text));
    return reply({ ok: true, row: r });
  }
  if (data.action === 'set_date') {
    sheet.getRange(r, cols.date || 2).setNumberFormat('@').setValue(String(data.date));
    return reply({ ok: true, row: r });
  }
  return reply({ ok: false, error: 'unknown action' });
}

function reply(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}
