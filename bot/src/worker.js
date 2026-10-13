/**
 * Telegram webhook for @chuvava_job_bot.
 *  - Buttons under feed cards: ⭐ В трекер / ✅ Подался / 🗑, then CV choice after "Подался".
 *  - A message with a vacancy link (e.g. shared from the LinkedIn app) → saved to the tracker + card.
 * Tracker writes go to the Apps Script web app (sheet-webapp.gs).
 *
 * Secrets: TELEGRAM_TOKEN, CHAT_ID, WEBHOOK_SECRET, SHEET_URL, SHEET_SECRET
 */

const CV_LABELS = ["C# classic", "Java classic"];
const SOURCE_BY_ICON = { "🟣": "justjoin", "🟢": "nofluff", "🔵": "linkedin" };
const WORKPLACE = { remote: "🏠 remote", hybrid: "🔀 hybrid", office: "🏢 office" };
const BROWSER_HEADERS = {
  "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
  "Accept-Language": "en-US,en;q=0.9",
};

export default {
  async fetch(request, env, ctx) {
    if (request.method !== "POST") return new Response("job-feed bot");
    if (request.headers.get("X-Telegram-Bot-Api-Secret-Token") !== env.WEBHOOK_SECRET) {
      return new Response("forbidden", { status: 403 });
    }
    const update = await request.json();
    // Answer Telegram at once; slow work (sheet, lookups) continues in the background
    ctx.waitUntil(handle(update, env).catch((e) => console.error("update failed", e.stack || e)));
    return new Response("ok");
  },
};

async function handle(update, env) {
  if (update.callback_query) return onButton(update.callback_query, env);
  if (update.message) return onMessage(update.message, env);
}

// ---------- buttons ----------

async function onButton(cb, env) {
  const msg = cb.message;
  if (!msg || String(msg.chat.id) !== String(env.CHAT_ID)) {
    return tg(env, "answerCallbackQuery", { callback_query_id: cb.id, text: "Not your bot" });
  }
  const data = cb.data || "";
  if (data === "noop") return tg(env, "answerCallbackQuery", { callback_query_id: cb.id });

  if (data === "hide") {
    const r = await tg(env, "deleteMessage", { chat_id: msg.chat.id, message_id: msg.message_id });
    return tg(env, "answerCallbackQuery", {
      callback_query_id: cb.id,
      text: r.ok ? "" : "Telegram не даёт удалить сообщения старше 48 ч — удали вручную",
    });
  }

  const job = parseCard(msg);
  if (!job.url) {
    return tg(env, "answerCallbackQuery", { callback_query_id: cb.id, text: "Не нашёл ссылку в карточке", show_alert: true });
  }

  let res, buttons, toast;
  if (data === "save") {
    res = await sheet(env, { action: "save", job });
    toast = res.existed ? `Уже в трекере, строка ${res.row}` : `Сохранено, строка ${res.row}`;
    buttons = savedButtons(res.row);
  } else if (data === "applied") {
    res = await sheet(env, { action: "applied", job });
    toast = `Отклик записан, строка ${res.row}. Какое CV?`;
    buttons = appliedButtons(res.row);
  } else if (data.startsWith("cv:")) {
    const cv = data.slice(3);
    res = await sheet(env, { action: "cv", job, cv });
    toast = `CV: ${cv}`;
    buttons = { inline_keyboard: [[{ text: `✅ Отклик · строка ${res.row} · ${cv}`, callback_data: "noop" }]] };
  } else {
    return tg(env, "answerCallbackQuery", { callback_query_id: cb.id });
  }

  if (res.error) {
    return tg(env, "answerCallbackQuery", { callback_query_id: cb.id, text: `Ошибка таблицы: ${res.error}`, show_alert: true });
  }
  await tg(env, "editMessageReplyMarkup", { chat_id: msg.chat.id, message_id: msg.message_id, reply_markup: buttons });
  return tg(env, "answerCallbackQuery", { callback_query_id: cb.id, text: toast });
}

function savedButtons(row) {
  return {
    inline_keyboard: [
      [{ text: `⭐ В трекере · строка ${row}`, callback_data: "noop" }],
      [{ text: "✅ Подался", callback_data: "applied" }, { text: "🗑", callback_data: "hide" }],
    ],
  };
}

function appliedButtons(row) {
  return {
    inline_keyboard: [
      [{ text: `✅ Отклик · строка ${row}`, callback_data: "noop" }],
      CV_LABELS.map((cv) => ({ text: cv, callback_data: `cv:${cv}` })),
    ],
  };
}

/** Reads the job back from a card produced by jobfeed/telegram.py format_job (or by cardHtml below). */
function parseCard(msg) {
  const text = msg.text || "";
  const link = (msg.entities || []).find((e) => e.type === "text_link");
  const lines = text.split("\n").map((l) => l.trim()).filter(Boolean);
  const noB2b = lines[0]?.startsWith("⚠️");
  const body = noB2b ? lines.slice(1) : lines;
  const pick = (prefix) => body.find((l) => l.startsWith(prefix))?.slice(prefix.length).trim() || "";
  const sourceLine = body[body.length - 1] || "";
  return {
    url: link?.url || "",
    title: body[0] || "",
    company: pick("🏛"),
    where: body.find((l) => /^(🏠|🔀|🏢)/u.test(l)) || "",
    salary: pick("💰"),
    skills: pick("🧰"),
    source: SOURCE_BY_ICON[[...sourceLine][0]] || "ссылка",
    noB2b,
  };
}

// ---------- shared links ----------

async function onMessage(msg, env) {
  if (String(msg.chat.id) !== String(env.CHAT_ID)) return;
  const text = msg.text || msg.caption || "";
  const url = (text.match(/https?:\/\/\S+/) || [])[0];
  if (!url) {
    return send(env, "Пришли ссылку на вакансию (JustJoin, NoFluff, LinkedIn или любой сайт) — сохраню её в трекер.");
  }
  let job;
  try {
    job = await lookup(url.replace(/[).,]+$/, ""));
  } catch (e) {
    job = { url, title: "", company: "", source: "ссылка" };
  }
  if (!job.company) job.company = guessCompanyFromShare(text);
  const res = await sheet(env, { action: "save", job });
  if (res.error) return send(env, `Не смог записать в таблицу: ${res.error}`);
  return send(env, cardHtml(job, res.existed ? "уже был в трекере" : "сохранено"), savedButtons(res.row));
}

/** LinkedIn app shares look like "Check out this job at Company: https://…" */
function guessCompanyFromShare(text) {
  const m = text.match(/job at (.+?):\s*https?:/i);
  return m ? m[1].trim() : "";
}

function cardHtml(job, status) {
  const e = escapeHtml;
  const icon = { justjoin: "🟣 JJ", nofluff: "🟢 NFJ", linkedin: "🔵 LinkedIn" }[job.source] || "🔗";
  const lines = [];
  if (job.noB2b) lines.push("⚠️ без B2B");
  lines.push(`<b>${e(job.title || "(название не определил)")}</b>`);
  lines.push(`🏛 ${e(job.company || "?")}`);
  if (job.where) lines.push(e(job.where));
  if (job.salary) lines.push(`💰 ${e(job.salary)}`);
  if (job.skills) lines.push(`🧰 ${e(job.skills)}`);
  lines.push(`${icon} · <a href="${e(job.url)}">open</a> · ${e(status)}`);
  return lines.join("\n");
}

async function lookup(url) {
  const host = new URL(url).hostname;
  if (host.includes("justjoin.it")) return justjoin(url);
  if (host.includes("nofluffjobs.com")) return nofluff(url);
  if (host.includes("linkedin.com")) return linkedin(url);
  return generic(url);
}

const money = (v) => (v == null ? "?" : Math.round(v).toLocaleString("ru-RU").replace(/ /g, " "));
const lastPathPart = (url) => new URL(url).pathname.replace(/\/+$/, "").split("/").pop();

async function justjoin(url) {
  const slug = lastPathPart(url);
  const o = await (await fetch(`https://justjoin.it/api/candidate-api/offers/${slug}`, { headers: BROWSER_HEADERS })).json();
  const units = { Hour: "h", Month: "mo", Day: "day", Year: "yr" };
  const types = o.employmentTypes || [];
  const salary = types
    .filter((t) => t.currencySource === "original" && (t.fromPerUnit != null || t.toPerUnit != null))
    .map((t) => `${money(t.fromPerUnit)}–${money(t.toPerUnit)} ${t.currency}/${units[t.unit] || t.unit} ${t.type}`)
    .join("; ");
  const contracts = [...new Set(types.map((t) => t.type).filter(Boolean))];
  const cities = [...new Set((o.locations || []).map((l) => l.city).filter(Boolean))].join(", ");
  return {
    url: `https://justjoin.it/job-offer/${slug}`,
    title: o.title,
    company: o.companyName,
    where: [WORKPLACE[o.workplaceType] || o.workplaceType, cities].filter(Boolean).join(" · "),
    salary,
    skills: (o.requiredSkills || []).map((s) => s.name).slice(0, 8).join(", "),
    source: "justjoin",
    noB2b: contracts.length > 0 && !contracts.includes("b2b") && !contracts.includes("any"),
  };
}

async function nofluff(url) {
  const id = lastPathPart(url);
  const p = await (await fetch(`https://nofluffjobs.com/api/posting/${id}`, { headers: BROWSER_HEADERS })).json();
  const sal = p.essentials?.originalSalary || {};
  const types = Object.entries(sal.types || {});
  const salary = types
    .filter(([, t]) => t.range)
    .map(([kind, t]) => `${money(t.range[0])}–${money(t.range[1])} ${sal.currency}/${(t.period || "").toLowerCase()} ${kind}`)
    .join("; ");
  const remoteDays = p.location?.remote;
  const workplace = remoteDays === 5 ? "remote" : remoteDays === 0 ? "office" : "hybrid";
  const cities = [...new Set((p.location?.places || []).map((pl) => pl.city).filter(Boolean))].join(", ");
  return {
    url: `https://nofluffjobs.com/pl/job/${id}`,
    title: p.title,
    company: p.company?.name,
    where: [WORKPLACE[workplace], cities].filter(Boolean).join(" · "),
    salary,
    skills: (p.requirements?.musts || []).map((m) => m.value).slice(0, 8).join(", "),
    source: "nofluff",
    noB2b: types.length > 0 && !types.some(([kind]) => kind === "b2b"),
  };
}

async function linkedin(url) {
  const id = (url.match(/(\d{8,})/) || [])[1];
  const clean = id ? `https://www.linkedin.com/jobs/view/${id}` : url;
  const html = await (await fetch(clean, { headers: { ...BROWSER_HEADERS, Accept: "text/html" } })).text();
  const grab = (re) => decodeEntities(((html.match(re) || [])[1] || "").replace(/<[^>]+>/g, "").trim());
  return {
    url: clean,
    title: grab(/class="[^"]*topcard__title[^"]*"[^>]*>([\s\S]*?)</) || grab(/<title>([\s\S]*?)<\/title>/),
    company: grab(/class="[^"]*topcard__org-name-link[^"]*"[^>]*>([\s\S]*?)<\/a>/),
    where: grab(/class="[^"]*topcard__flavor--bullet[^"]*"[^>]*>([\s\S]*?)</),
    source: "linkedin",
  };
}

async function generic(url) {
  const html = await (await fetch(url, { headers: { ...BROWSER_HEADERS, Accept: "text/html" } })).text();
  const og = (html.match(/property="og:title"\s+content="([^"]*)"/) || [])[1];
  const title = og || (html.match(/<title[^>]*>([\s\S]*?)<\/title>/) || [])[1] || "";
  return { url, title: decodeEntities(title.trim()), company: "", source: new URL(url).hostname };
}

// ---------- plumbing ----------

async function sheet(env, body) {
  try {
    const r = await fetch(env.SHEET_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...body, secret: env.SHEET_SECRET }),
      redirect: "follow", // Apps Script answers via a 302 to googleusercontent.com
    });
    return await r.json();
  } catch (e) {
    return { error: String(e) };
  }
}

async function tg(env, method, body) {
  const r = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_TOKEN}/${method}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return r.json();
}

function send(env, html, buttons) {
  return tg(env, "sendMessage", {
    chat_id: env.CHAT_ID,
    text: html,
    parse_mode: "HTML",
    link_preview_options: { is_disabled: true },
    ...(buttons ? { reply_markup: buttons } : {}),
  });
}

function escapeHtml(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function decodeEntities(s) {
  return s.replace(/&amp;/g, "&").replace(/&#39;|&apos;/g, "'").replace(/&quot;/g, '"').replace(/&lt;/g, "<").replace(/&gt;/g, ">");
}
