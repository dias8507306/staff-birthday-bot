// EduApp туған күндер — автоматика (Apps Script жобасындағы ЕКІНШІ файл, Code.gs-тен кейін тұруы керек).
// Мұнда құпия жоқ: ботқа керек SECRET — Code.gs-те. Бұл файлдағы doPost Code.gs-тегі doPost-ты алмастырады.
// setupAutomation — бір рет іске қосу: бағандар, «Қызметкерлер» парағы (бос болса — толтырады), сағат сайын синхрондау.

const MAIN_SHEET = 'Тізім';
const HR_TAB = 'Қызметкерлер';
const ARCHIVE_TAB = 'Архив';
const MAX_ARCHIVE_SHARE = 0.4;
const RENAMES = {
  'Saparov Zhasulan': 'Сапаров Жасұлан', 'Zhaqypov Qanat Maratuly': 'Жақыпов Қанат', 'Мөлдір': 'Омарова Мөлдір',
};
const KEEP = ['Бейсен Дастан Асқарұлы'];
const STAFF = [
  // Демо-кадр тізімі (жалған адамдар, data/birthdays.json-дағыдай — scripts/generate_demo_data.py).
  ['Жұмабаева Балнұр', 'Sales', 'Отдел продаж'],
  ['Сапаров Жасұлан', 'Content', 'Главный эксперт'],
  ['Оспанов Темірлан', 'Analytics', 'Дата-аналитик'],
  ['Бекетов Қанат', 'Office', 'HR-менеджер'],
  ['Әлімова Аяжан', 'Content', 'Старший эксперт'],
  ['Сұлтанов Жасұлан', 'Content', 'Автор-разработчик контента'],
  ['Сұлтанов Әлихан', 'Content', 'Старший эксперт'],
  ['Жақыпов Қанат', 'Marketing', 'Таргетолог'],
  ['Оспанов Азамат', 'Analytics', 'Дата-аналитик'],
  ['Есенов Нұрлан', 'Content', 'Контент инженер'],
  ['Мұратов Тимур', 'Analytics', 'Дата-аналитик'],
  ['Омарова Мөлдір', 'IT', 'Дата-сайнтист'],
  ['Тұрсынова Ләззат', 'Marketing', 'Смм-менеджер'],
  ['Дәулетов Азамат', 'IT', 'Мобайл-разработчик'],
  ['Жұмабаева Камила Ержанқызы', 'Content', 'Автор-разработчик контента'],
  ['Нұрпейісова Айым', 'Content', 'Контент инженер'],
];

function setupAutomation() {
  const book = SpreadsheetApp.getActiveSpreadsheet();
  let hr = book.getSheetByName(HR_TAB);
  if (!hr) hr = book.insertSheet(HR_TAB, book.getSheets().length);
  if (hr.getLastRow() < 2) {
    hr.clear();
    hr.getRange(1, 1, 1, 3).setValues([['Аты-жөні / ФИО', 'Бөлім', 'Лауазымы']]).setFontWeight('bold');
    hr.getRange(2, 1, STAFF.length, 3).setValues(STAFF);
    hr.setFrozenRows(1);
  }
  ScriptApp.getProjectTriggers().filter(t => t.getHandlerFunction() === 'hourlySync').forEach(t => ScriptApp.deleteTrigger(t));
  ScriptApp.newTrigger('hourlySync').timeBased().everyHours(1).create();
  hourlySync();
}

function hourlySync() {
  withLock(() => applyHr(readHr()));
}

function mainSheet() {
  const book = SpreadsheetApp.getActiveSpreadsheet();
  return book.getSheetByName(MAIN_SHEET) || book.getSheets()[0];
}

function headerIndex(sheet) {
  const width = Math.max(sheet.getLastColumn(), 1);
  const headers = sheet.getRange(1, 1, 1, width).getValues()[0].map(h => String(h).trim().toLowerCase());
  const find = vs => { for (const v of vs) { const i = headers.findIndex(h => h.indexOf(v) >= 0); if (i >= 0) return i + 1; } return 0; };
  return { name: find(['аты-жөні', 'аты жөні', 'фио']), date: find(['туған күні']), position: find(['лауазым']),
           gender: find(['жыныс']), card: find(['карточка']), text: find(['мәтін']), dept: find(['бөлім']),
           keep: find(['архивке жібермеу']), width: width };
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
  return last < 2 ? [] : sheet.getRange(2, 1, last - 1, sheet.getLastColumn()).getValues();
}

function cell(row, col) {
  return col ? String(row[col - 1] === null ? '' : row[col - 1]).trim() : '';
}

function norm(s) {
  return String(s).replace(/\s+/g, ' ').trim().toLowerCase();
}

const SAME_LETTER = { 'ә': 'а', 'ғ': 'г', 'қ': 'к', 'ң': 'н', 'ө': 'о', 'ұ': 'у', 'ү': 'у', 'һ': 'х', 'і': 'и', 'ё': 'е',
                      'a': 'а', 'c': 'с', 'e': 'е', 'o': 'о', 'p': 'р', 'x': 'х' };

function personKey(name) {
  return norm(name).replace(/[әғқңөұүһіёaceopx]/g, ch => SAME_LETTER[ch]).split(' ').slice(0, 2).join(' ');
}

function findRow(sheet, name) {
  const cols = headerIndex(sheet);
  const rows = readRows(sheet);
  for (let i = rows.length - 1; i >= 0; i--) if (norm(cell(rows[i], cols.name)) === norm(name)) return i + 2;
  const same = rows.map((r, i) => i).filter(i => personKey(cell(rows[i], cols.name)) === personKey(name));
  return same.length === 1 ? same[0] + 2 : 0;
}

function withLock(fn) {
  const lock = LockService.getScriptLock();
  lock.waitLock(30000);
  try { return fn(); } finally { lock.releaseLock(); }
}

function readHr() {
  const sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(HR_TAB);
  if (!sheet) return [];
  const values = sheet.getDataRange().getValues();
  const headers = (values[0] || []).map(x => String(x).trim().toLowerCase());
  const col = vs => headers.findIndex(x => vs.some(v => x.indexOf(v) >= 0));
  const name = col(['аты-жөні', 'аты жөні', 'фио']), dept = col(['бөлім', 'отдел']), pos = col(['лауазым', 'должность']);
  if (name < 0) throw new Error('«Қызметкерлер» парағында «Аты-жөні / ФИО» бағаны жоқ');
  return values.slice(1).map(r => ({ name: String(r[name]).replace(/\s+/g, ' ').trim(),
    dept: dept >= 0 ? String(r[dept]).trim() : '', position: pos >= 0 ? String(r[pos]).trim() : '' })).filter(p => p.name);
}

function applyHr(staff) {
  if (staff.length < 5) { Logger.log(`«${HR_TAB}» парағында тым аз адам (${staff.length}) — синхрондау жоқ`); return; }
  const sheet = mainSheet();
  const cols = ensureColumns(sheet);
  const rows = readRows(sheet);
  const renames = {};
  Object.keys(RENAMES).forEach(k => { renames[norm(k)] = RENAMES[k]; });
  const keep = new Set(KEEP.map(norm));
  rows.forEach((r, i) => {
    const n = norm(cell(r, cols.name));
    if (renames[n]) { sheet.getRange(i + 2, cols.name).setValue(renames[n]); r[cols.name - 1] = renames[n]; }
    if (keep.has(n) && cols.keep && !cell(r, cols.keep)) { sheet.getRange(i + 2, cols.keep).setValue('иә'); r[cols.keep - 1] = 'иә'; }
  });
  const byKey = {};
  rows.forEach((r, i) => { const k = personKey(cell(r, cols.name)); if (k) (byKey[k] = byKey[k] || []).push(i); });
  const seen = new Set();
  const added = [];
  for (const p of staff) {
    const match = (byKey[personKey(p.name)] || []).find(i => !seen.has(i));
    if (match !== undefined) {
      seen.add(match);
      if (p.position && cols.position && cell(rows[match], cols.position) !== p.position) sheet.getRange(match + 2, cols.position).setValue(p.position);
      if (p.dept && cols.dept && cell(rows[match], cols.dept) !== p.dept) sheet.getRange(match + 2, cols.dept).setValue(p.dept);
      continue;
    }
    if (restoreFromArchive(sheet, cols, p)) continue;
    const row = new Array(sheet.getLastColumn()).fill('');
    row[cols.name - 1] = p.name;
    if (cols.position) row[cols.position - 1] = p.position;
    if (cols.dept) row[cols.dept - 1] = p.dept;
    if (cols.card) row[cols.card - 1] = p.name.split(' ').slice(0, 2).join(' ');
    sheet.getRange(sheet.getLastRow() + 1, 1, 1, row.length).setNumberFormat('@').setValues([row]);
    added.push(p.name);
  }
  const gone = rows.map((r, i) => i).filter(i => !seen.has(i) && cell(rows[i], cols.name) && !cell(rows[i], cols.keep));
  if (gone.length > rows.length * MAX_ARCHIVE_SHARE) {
    Logger.log(`«${HR_TAB}» парағында ${gone.length} адам жоқ — тым көп, архивтеу өткізілді`);
  } else if (gone.length) {
    const archive = archiveSheet(sheet);
    gone.sort((a, b) => b - a).forEach(i => { archive.appendRow(rows[i]); sheet.deleteRow(i + 2); });
  }
  Logger.log(`Жаңа: ${added.length} ${added.join(', ')}; архивке: ${gone.length}`);
}

function archiveSheet(main) {
  const book = main.getParent();
  let archive = book.getSheetByName(ARCHIVE_TAB);
  if (!archive) {
    archive = book.insertSheet(ARCHIVE_TAB, book.getSheets().length);
    const header = main.getRange(1, 1, 1, main.getLastColumn()).getValues();
    archive.getRange(1, 1, 1, header[0].length).setValues(header).setFontWeight('bold');
    archive.setFrozenRows(1);
    archive.getRange('A:Z').setNumberFormat('@');
  }
  return archive;
}

function restoreFromArchive(main, cols, p) {
  const archive = main.getParent().getSheetByName(ARCHIVE_TAB);
  if (!archive || archive.getLastRow() < 2) return false;
  const rows = archive.getRange(2, 1, archive.getLastRow() - 1, archive.getLastColumn()).getValues();
  const i = rows.findIndex(r => personKey(cell(r, cols.name)) === personKey(p.name));
  if (i < 0) return false;
  const row = rows[i].slice(0, main.getLastColumn()).map(String);
  while (row.length < main.getLastColumn()) row.push('');
  if (p.position && cols.position) row[cols.position - 1] = p.position;
  if (p.dept && cols.dept) row[cols.dept - 1] = p.dept;
  main.getRange(main.getLastRow() + 1, 1, 1, row.length).setNumberFormat('@').setValues([row]);
  archive.deleteRow(i + 2);
  return true;
}

function doPost(e) {
  let data;
  try { data = JSON.parse(e.postData.contents); } catch (err) { return reply({ ok: false, error: 'bad request' }); }
  if (data.secret !== SECRET) return reply({ ok: false, error: 'forbidden' });
  try {
    return withLock(() => {
      const sheet = mainSheet();
      const cols = headerIndex(sheet);
      if (data.action === 'add') {
        const row = data.row.map(String);
        const r = sheet.getLastRow() + 1;
        sheet.getRange(r, 1, 1, row.length).setNumberFormat('@').setValues([row]);
        return reply({ ok: true, row: r });
      }
      const r = findRow(sheet, data.name);
      if (!r) return reply({ ok: false, error: 'not found' });
      if (data.action === 'delete') { sheet.deleteRow(r); return reply({ ok: true, row: r }); }
      if (data.action === 'set_text') { sheet.getRange(r, cols.text || 6).setValue(String(data.text)); return reply({ ok: true, row: r }); }
      if (data.action === 'set_date') { sheet.getRange(r, cols.date || 2).setNumberFormat('@').setValue(String(data.date)); return reply({ ok: true, row: r }); }
      if (data.action === 'set_gender' && cols.gender) { sheet.getRange(r, cols.gender).setValue(String(data.gender)); return reply({ ok: true, row: r }); }
      return reply({ ok: false, error: 'unknown action' });
    });
  } catch (err) {
    return reply({ ok: false, error: String(err && err.message || err) });
  }
}

function reply(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}
