/**
 * Google Apps Script web app that writes bot actions into the "2026 New" tab.
 * Paste into the spreadsheet: Extensions → Apps Script, then Deploy → New deployment → Web app
 * (Execute as: Me, Who has access: Anyone). Set Script property SECRET (Project Settings → Script properties)
 * to the same value as the Worker's SHEET_SECRET.
 *
 * POST JSON: {secret, action: "save"|"applied"|"cv", job: {url, title, company, where, salary, skills, source, noB2b}, cv}
 * Returns JSON: {row, existed} or {error}.
 */
const SPREADSHEET_ID = '1ApL9PkVKOzU47T550zH_spfzKoDVlBBnBaH9xAZIYqo';
const TAB = '2026 New';
const FIRST_DATA_ROW = 3;   // row 1 = headers, row 2 = fictional example
const LAST_ROW = 501;       // conditional formatting covers rows 2..501
const TZ = 'Europe/Warsaw';
// Columns (1-based): A Компания, B Роль, C Этап, D След. шаг, E Дата шага, F Посл. контакт,
// G Контакт, H Вакансия, I Условия, J Заметки, K Дата отклика, L Версия CV
const COL = {company: 1, role: 2, stage: 3, next: 4, nextDate: 5, url: 8, terms: 9, notes: 10, applied: 11, cv: 12};

function doPost(e) {
  try {
    const req = JSON.parse(e.postData.contents);
    if (req.secret !== PropertiesService.getScriptProperties().getProperty('SECRET')) {
      return json({error: 'forbidden'});
    }
    const lock = LockService.getScriptLock();   // two quick taps must not take the same empty row
    lock.waitLock(20000);
    try {
      const sheet = SpreadsheetApp.openById(SPREADSHEET_ID).getSheetByName(TAB);
      if (req.action === 'save') return json(save(sheet, req.job));
      if (req.action === 'applied') return json(applied(sheet, req.job));
      if (req.action === 'cv') return json(setCv(sheet, req.job, req.cv));
      return json({error: 'unknown action ' + req.action});
    } finally {
      lock.releaseLock();
    }
  } catch (err) {
    return json({error: String(err)});
  }
}

function json(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}

function normUrl(url) {
  return String(url || '').trim().replace(/[?#].*$/, '').replace(/\/+$/, '').toLowerCase();
}

function findRow(sheet, url) {
  const target = normUrl(url);
  if (!target) return null;
  const urls = sheet.getRange(FIRST_DATA_ROW, COL.url, LAST_ROW - FIRST_DATA_ROW + 1, 1).getValues();
  for (let i = 0; i < urls.length; i++) {
    if (normUrl(urls[i][0]) === target) return FIRST_DATA_ROW + i;
  }
  return null;
}

function firstEmptyRow(sheet) {
  const companies = sheet.getRange(FIRST_DATA_ROW, COL.company, LAST_ROW - FIRST_DATA_ROW + 1, 1).getValues();
  let last = FIRST_DATA_ROW - 1;
  companies.forEach((r, i) => { if (String(r[0]).trim()) last = FIRST_DATA_ROW + i; });
  return last + 1;
}

function today() {
  // midnight in Warsaw, independent of the script project's own time zone
  return Utilities.parseDate(Utilities.formatDate(new Date(), TZ, 'yyyy-MM-dd'), TZ, 'yyyy-MM-dd');
}

function addDays(date, n) {
  const d = new Date(date);
  d.setDate(d.getDate() + n);
  return d;
}

function setDate(sheet, row, col, date) {
  const cell = sheet.getRange(row, col);
  cell.setValue(date);
  if (date) cell.setNumberFormat('M/d/yyyy');
}

function logLine(text) {
  return Utilities.formatDate(new Date(), TZ, 'dd.MM') + ' — ' + text;
}

function prependNote(sheet, row, line) {
  const cell = sheet.getRange(row, COL.notes);
  const old = String(cell.getValue() || '');
  cell.setValue(old ? line + '\n' + old : line);
}

function createRow(sheet, job, stage) {
  const row = firstEmptyRow(sheet);
  if (row > LAST_ROW) throw new Error('tracker is full (row ' + LAST_ROW + ')');
  const terms = [job.salary, job.where, job.noB2b ? '⚠️ без B2B' : ''].filter(Boolean).join('\n');
  const note = logLine('сохранено из бота (' + (job.source || 'ссылка') + ').' +
                       (job.skills ? ' Стек: ' + job.skills + '.' : ''));
  sheet.getRange(row, COL.company, 1, 4).setValues([[job.company || '', job.title || '', stage, 'Решить, подаваться ли']]);
  sheet.getRange(row, COL.url).setValue(job.url || '');
  sheet.getRange(row, COL.terms).setValue(terms);
  sheet.getRange(row, COL.notes).setValue(note);
  setDate(sheet, row, COL.nextDate, addDays(today(), 2));
  return row;
}

function save(sheet, job) {
  const existing = findRow(sheet, job.url);
  if (existing) return {row: existing, existed: true};
  return {row: createRow(sheet, job, 'Сохранено'), existed: false};
}

function applied(sheet, job) {
  let row = findRow(sheet, job.url);
  const existed = !!row;
  if (!row) row = createRow(sheet, job, 'Отклик');
  sheet.getRange(row, COL.stage).setValue('Отклик');
  sheet.getRange(row, COL.next).setValue('Ждать обратной связи');
  sheet.getRange(row, COL.nextDate).clearContent();   // a form application has nobody to remind
  setDate(sheet, row, COL.applied, today());
  prependNote(sheet, row, logLine('подался (кнопка в боте).'));
  return {row: row, existed: existed};
}

function setCv(sheet, job, cv) {
  const row = findRow(sheet, job.url);
  if (!row) return {error: 'row not found'};
  sheet.getRange(row, COL.cv).setValue(cv);
  return {row: row, existed: true};
}
