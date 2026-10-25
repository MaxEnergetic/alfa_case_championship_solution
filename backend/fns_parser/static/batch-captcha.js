"use strict";
const $ = id => document.getElementById(id);
let current = null, timer = null, busy = false;
const names = {egrul: "ЕГРЮЛ / ЕГРИП", transparent_business: "Прозрачный бизнес"};
function error(message = "") { $("error").textContent = message; $("error").hidden = !message; }
async function api(url, options = {}) {
  const r = await fetch(url, {cache: "no-store", ...options});
  const data = await r.json();
  if (!r.ok) throw new Error(data.error?.message || `Ошибка HTTP ${r.status}`);
  return data;
}
function disable(value) { for (const id of ["answer", "refresh", "skip"]) $(id).disabled = value; }
async function poll() {
  clearTimeout(timer);
  if (busy) { timer = setTimeout(poll, 1500); return; }
  try {
    const data = await api("/api/v1/batch/captchas");
    const next = data.captchas.find(c => c.id === current?.id) || data.captchas[0];
    $("state").textContent = next ? "Нужен ваш ответ" : "Капч в очереди нет";
    $("challenge").hidden = !next;
    if (next && next.id !== current?.id) {
      current = next; $("image").src = next.image_url; $("code").value = ""; disable(false);
      $("description").textContent = `${names[next.source]} · ИНН / ОГРН ${next.inn} · картинка ${next.attempt} из 5`;
      $("expiry").textContent = `Ожидаем ввод до ${new Date(next.expires_at).toLocaleTimeString("ru-RU")}. Если ФНС отклонит код, появится новая картинка.`;
      $("code").focus();
    } else if (!next) { current = null; $("image").removeAttribute("src"); }
    error();
  } catch (e) { error(e.message + ". Повторяем подключение автоматически."); }
  timer = setTimeout(poll, 1500);
}
async function submit(action) {
  if (!current || busy) return;
  busy = true; disable(true); error(); clearTimeout(timer);
  try {
    const body = {action}; if (action === "answer") body.code = $("code").value.trim();
    await api(current.answer_url, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
    current = null; $("code").value = ""; $("challenge").hidden = true;
    $("hint").textContent = action === "skip" ? "Сбор остановится после сохранения результата компании." : "Ответ передан сборщику. ФНС проверит код; при ошибке появится новая картинка.";
  } catch (e) { error(e.message); disable(false); }
  finally { busy = false; timer = setTimeout(poll, 1500); }
}
$("answer-form").addEventListener("submit", event => { event.preventDefault(); submit("answer"); });
$("refresh").addEventListener("click", () => submit("refresh"));
$("skip").addEventListener("click", () => submit("skip"));
poll();
