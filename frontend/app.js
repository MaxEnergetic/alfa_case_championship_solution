"use strict";
(() => {
  const root = document.getElementById("app");
  const API = "/api/v1";
  const s = {token: sessionStorage.getItem("credit-token"), user: null, session: null,
    items: [], jobs: {}, pending: null, edit: null, draft: undefined, error: "", busy: false, imageUrl: null, imageKey: null};
  const esc = v => String(v ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const money = v => v == null ? "Нет данных" : new Intl.NumberFormat("ru-RU", {maximumFractionDigits:0}).format(v) + " ₽";
  const date = v => v ? new Date(v).toLocaleString("ru-RU") : "Нет данных";
  const status = {ok:"Получено",partial:"Частично",error:"Источник недоступен",not_found:"Не найдено",not_requested:"Не запрашивалось",manual_required:"Нужна ручная проверка",not_applicable:"Не применимо"};
  const btn = (label, action, cls="primary", extra="") => `<button type="button" class="${cls}" data-action="${action}" ${extra}>${label}</button>`;
  const title = x => x.data.company.entities.find(e => e.ogrn === x.data.company.selected_ogrn)?.name || x.data.company.inn || "Новый подбор";
  const activeQuestion = () => s.edit?.question || s.session?.next_question;
  function setSession(value) {
    const previous = s.session?.next_question?.id;
    s.session = value;
    if (value.next_question?.id !== previous) s.draft = undefined;
    sessionStorage.setItem("credit-session", value.session_id);
  }
  async function api(path, options={}) {
    let r;
    try {r = await fetch(API + path, {...options, headers:{...(options.body ? {"Content-Type":"application/json"}:{}), ...(s.token ? {Authorization:"Bearer " + s.token}:{}), ...options.headers}});}
    catch (_) {const e=new Error("Связь с сервером прервалась. Ответы, отправленные ранее, сохранены.");e.network=true;throw e;}
    if (r.status === 401) {
      s.token = null; s.user = null; s.session = null; sessionStorage.removeItem("credit-token");
    }
    const data = r.status === 204 ? null : await r.json().catch(()=>({detail:"Сервис временно недоступен. Повторите запрос позже."}));
    if (!r.ok) {
      let message = data?.error?.message || data?.detail || `Ошибка HTTP ${r.status}`;
      if (typeof message !== "string") message = JSON.stringify(message);
      const error = new Error(message); error.status = r.status; error.network=[502,503,504].includes(r.status); throw error;
    }
    return data;
  }
  const post = (path, body) => api(path, {method:"POST", ...(body === undefined ? {} : {body:JSON.stringify(body)})});
  const sessionPath = () => "/credit/sessions/" + s.session.session_id;
  async function refresh() {setSession(await api(sessionPath()));}
  async function loadList() {s.items = await api("/credit/sessions");}
  async function openSession(id) {
    s.edit = null; s.pending = null; s.jobs = {}; s.draft = undefined; s.groupDraft = {};
    setSession(await api("/credit/sessions/" + id));
    const list = await api(sessionPath() + "/jobs");
    for (const kind of ["registry", "scoring"]) {
      const saved = list.find(x => x.kind === kind);
      if (!saved) continue;
      try {
        const job = await api("/jobs/" + saved.id);
        s.jobs[kind] = job;
        if (kind === "registry" && job.state === "completed" && job.result?.checked_at !== s.session.data.company.checked_at) s.pending = job;
      } catch(e) {if (e.status !== 404) throw e;}
    }
  }
  function loginPage() {
    root.innerHTML = `<main class="login"><section class="intro"><div class="brand">А<span>Подбор финансирования</span></div><p class="eyebrow">Данные бизнеса. Понятный выбор.</p><h1>Найдём подходящий<br>формат финансирования</h1><p>Расскажите о задаче. Мы проверим сведения компании в открытых источниках и объясним, какие продукты стоит рассмотреть.</p><div class="steps"><span>01 · Потребность</span><span>02 · Данные компании</span><span>03 · Рекомендации</span></div></section><section class="login-form"><h2>Войти в кабинет</h2><p class="muted">Ваши ответы и результаты сохраняются.</p><form id="login"><label>Электронная почта<input name="email" type="email" autocomplete="username" required></label><label>Пароль<input name="password" type="password" autocomplete="current-password" required></label><button class="primary wide" ${s.busy?"disabled":""}>Войти</button></form><p class="error" role="alert">${esc(s.error)}</p><p class="muted small">Учётная запись задаётся при запуске сервиса.</p></section></main>`;
  }
  function render() {
    if (!s.user) {loginPage(); return;}
    root.innerHTML = `<header class="top"><a class="brand" href="/">А<span>Подбор финансирования</span></a><div class="user"><span>${esc(s.user.email)}</span>${btn("Выйти","logout","ghost")}</div></header><div class="layout"><aside class="sidebar"><p class="eyebrow">Мой кабинет</p>${btn("+ Новый подбор","new","primary wide")}<nav>${s.items.map(x=>`<button class="session-link ${x.id===s.session?.session_id?"active":""}" data-session="${esc(x.id)}"><strong>${x.id===s.session?.session_id?esc(title(s.session)):"Подбор финансирования"}</strong><span>${esc(date(x.created_at))}</span></button>`).join("")}</nav><div class="side-note">Сведения — из ФНС и открытой финансовой отчётности. Отсутствующие данные отмечаем явно.</div></aside><main class="content"><div class="heading"><div><p class="eyebrow">Кредитный кабинет</p><h1>${s.session ? esc(title(s.session)) : "С чего начнём?"}</h1><p class="muted">Рекомендация помогает выбрать продукт. Решение о выдаче принимает банк.</p></div>${s.session?btn("Скачать JSON","download","secondary"):""}</div><div id="message" class="error" role="alert">${esc(s.error)}</div>${s.session?sessionView():`<section class="card empty"><h2>Опишите задачу бизнеса</h2><p>Анкета подстроится под ваши ответы. Подтверждённые сведения компании загрузим из реестров.</p>${btn("Начать подбор","new")}</section>`}</main></div>`;
    updateJobs();
  }
  function sessionView() {
    const x = s.session, inn=x.data.company.inn;
    return `<div class="columns"><section class="main-column">${questionView()}${x.assessment?recommendationView():""}${historyView()}</section><aside class="facts"><section class="card"><p class="eyebrow">Открытые источники</p><h2>Данные компании</h2>${inn?btn("Обновить сведения","refresh-registry","secondary wide",isRunning("registry")?"disabled":""):`<p class="muted">Укажите ИНН в анкете — сведения загрузятся автоматически.</p>`}<div id="jobs"></div>${companyView()}</section></aside></div>`;
  }
  function questionView() {
    const q = activeQuestion();
    if (!q) return "";
    if (!s.edit && s.session.question_group?.length>1) {
      return `<section class="card question"><p class="eyebrow">Бюджет покупки</p><h2>Стоимость и сумма кредита</h2><form id="purchase">${s.session.question_group.map((f,i)=>`<label>${i===0?"Стоимость покупки, ₽":"Из неё в кредит, ₽"}<input name="${f.id}" type="number" min="1" max="${f.maximum}" step="any" value="${esc(s.groupDraft?.[f.id]??"")}" placeholder="Если пока неизвестно, оставьте пустым"></label>`).join("")}<button class="primary">Продолжить</button></form></section>`;
    }
    const value = s.draft !== undefined ? s.draft : s.edit?.value;
    let control;
    if (q.type === "choice") control = `<div class="choices">${q.options.map(o=>`<button class="choice ${o.value===value?"selected":""}" data-answer="${esc(o.value)}">${esc(o.label)}<span>→</span></button>`).join("")}</div>`;
    else control = `<form id="answer"><label>${q.type === "date" ? "Выберите дату" : "Ваш ответ"}<input id="answer-value" name="value" type="${q.type === "date" ? "date" : ["number","integer"].includes(q.type)?"number":"text"}" ${q.minimum!=null?`min="${q.minimum}"`:""} ${q.maximum!=null?`max="${q.maximum}"`:""} ${q.max_length?`maxlength="${q.max_length}"`:""} step="${q.type === "integer"?"1":"any"}" value="${esc(value ?? "")}" required></label><button class="primary" ${s.busy?"disabled":""}>${s.edit?"Сохранить исправление":"Продолжить"}</button>${q.allow_unknown?btn("Не знаю","unknown","ghost"):""}</form>`;
    return `<section class="card question"><p class="eyebrow">${s.edit?"Исправление ответа":"Анкета"}</p><h2>${esc(q.text)}</h2>${s.edit?`<p class="notice">Изменение ответа перестроит следующие вопросы и сбросит предыдущую финансовую оценку.</p>`:""}${control}${s.edit?btn("Отмена","cancel-edit","ghost"):""}</section>`;
  }
  function historyView() {
    const rows = s.session.answered_questions || [];
    if (!rows.length) return "";
    return `<details class="card"><summary>Ваши ответы · ${rows.length}</summary><div class="history">${rows.map((x,i)=>`<div><p>${esc(x.question.text)}</p><strong>${esc(x.value == null || x.value === "unknown" ? "Не знаю" : x.question.options.find(o=>o.value===x.value)?.label || x.value)}</strong><button class="ghost" data-edit="${i}">Изменить</button></div>`).join("")}</div></details>`;
  }
  function recommendationView() {
    const a = s.session.assessment;
    const score = s.session.scoring, financial = a.financial_assessment;
    const running=isRunning("scoring")||isRunning("registry");
    const metric = financial.probability_financial_stability;
    const financialLabel=metric!=null?`${(metric*100).toFixed(1)}%`:running?"Рассчитываем…":financial.status==="not_applicable"?"Для ИП не рассчитана":"Недостаточно данных";
    const explanation=score?.error?.message || (financial.status==="not_applicable"?"Модель рассчитана на юридические лица.":metric==null&&!running?"Не удалось получить достаточную отчётность для оценки.":"");
    return `<section class="recommendations"><div class="card result"><span class="badge">Ваш результат</span><h2>${esc(a.title)}</h2><div class="metrics"><div><span class="muted small">Финансовая устойчивость</span><strong>${esc(financialLabel)}</strong>${financial.report_year?`<small>Отчётность за ${financial.report_year} год</small>`:""}</div><div><span class="muted small">Вероятность одобрения</span><strong>Не рассчитана</strong><details><summary>Почему?</summary><p class="small">${esc(a.approval_probability.message)} Финансовая устойчивость — другой показатель.</p></details></div></div>${explanation?`<p class="notice">${esc(explanation)}</p>`:""}${metric!=null?`<details class="score-details"><summary>Как получена оценка</summary><p class="small">${financial.time_period_validation==="not_validated"?"Качество модели на этом отчётном периоде не подтверждено.":"Использован период проверки модели."}</p><dl>${Object.entries(score.financials_rub).map(([k,v])=>`<dt>${esc(k)}</dt><dd>${esc(money(v))}</dd>`).join("")}</dl><pre>${esc(JSON.stringify(score.source,null,2))}</pre></details>`:""}${s.session.data.company.inn?.length===10?btn(metric!=null?"Обновить оценку":"Повторить оценку","score","ghost",running?"disabled":""):""}</div>${a.clarifications.length?`<div class="notice">${a.clarifications.map(esc).join("<br>")}</div>`:""}${a.products.map(c=>`<article class="card product"><span class="badge">${{candidate:"Подходит по анкете",clarify:"Нужно уточнение",risk_review:"Проверить финансовый риск"}[c.readiness]||"Рассмотреть"}</span><h3>${esc(c.name)}</h3><p>${esc(c.reason)}</p>${c.checks.length?`<ul>${c.checks.map(v=>`<li>${esc(v)}</li>`).join("")}</ul>`:""}</article>`).join("")}</section>`;
  }
  function companyView() {
    const c = s.session.data.company;
    const entity = c.entities.find(e=>e.ogrn===c.selected_ogrn);
    return `<div class="company"><p><span class="muted">Состояние</span><br>${esc(status[c.lookup_status]||c.lookup_status)}</p>${entity?`<h3>${esc(entity.name)}</h3><dl><dt>ИНН</dt><dd>${esc(entity.inn)}</dd><dt>ОГРН / ОГРНИП</dt><dd>${esc(entity.ogrn)}</dd><dt>Статус</dt><dd>${esc(entity.status||"Нет данных")}</dd><dt>Регистрация</dt><dd>${esc(entity.registered_on||"Нет данных")}</dd><dt>ОКВЭД</dt><dd>${esc(entity.main_okved||"Нет данных")}</dd><dt>Адрес</dt><dd>${esc(entity.address||"Нет данных")}</dd></dl>`:""}${Object.entries(c.sources).map(([name,v])=>`<p class="source"><b>${esc({egrul:"ЕГРЮЛ / ЕГРИП",transparent_business:"Прозрачный бизнес",girbo:"ГИР БО"}[name]||name)}</b><span>${esc(status[v.status]||v.status)}</span>${v.error?`<small>${esc(v.error.message)}</small>`:""}</p>`).join("")}<p class="muted small">${c.checked_at?"Проверено: "+esc(date(c.checked_at)):"Сведения появятся после ввода ИНН в анкете."}</p>${s.session.conflicts.length?`<div class="notice">Подтверждённые сведения реестра имеют приоритет:<ul>${s.session.conflicts.map(c=>`<li>${esc(c.field)}: ${esc(c.user_value)} → ${esc(c.registry_value)}</li>`).join("")}</ul></div>`:""}</div>`;
  }
  function isRunning(kind) {return ["running","waiting_for_captcha"].includes(s.jobs[kind]?.state);}
  function updateJobs() {
    const box = document.getElementById("jobs"); if (!box) return;
    box.innerHTML = Object.entries(s.jobs).filter(([,j])=>j.state!=="completed").map(([kind,j])=>`<div class="job"><b>${kind==="registry"?"Проверка источников":"Финансовая оценка"}</b><p>${esc({running:"Получаем данные…",waiting_for_captcha:"ФНС запросила капчу",completed:"Запрос завершён",failed:"Не удалось завершить запрос",cancelled:"Запрос отменён"}[j.state]||j.state)}</p>${j.error?`<p class="error">${esc(j.error.message)}</p>`:""}${j.state==="waiting_for_captcha"?`<form id="captcha"><p>Введите символы с картинки ФНС. После ответа запрос продолжится.</p><img id="captcha-image" alt="Капча ФНС" ${s.imageUrl?`src="${s.imageUrl}"`:""}><input name="code" aria-label="Код с картинки" autocomplete="off" required maxlength="16"><button class="primary">Отправить код</button>${btn("Другая картинка","refresh-captcha","ghost")}</form>`:""}${isRunning(kind)?btn("Остановить запрос","cancel-job","ghost",`data-kind="${kind}"`):""}</div>`).join("") + pendingView();
    const j = Object.values(s.jobs).find(x=>x.captcha&&x.state==="waiting_for_captcha");
    if (j && s.imageKey !== j.captcha.id) loadImage(j).catch(showError);
  }
  function pendingView() {
    const r = s.pending?.result;
    if (!r) return "";
    if (!r.entities?.length) return `<div class="notice">${r.status==="not_found"?"Компания не найдена.":"Источники пока не подтвердили данные компании."} ${Object.values(r.sources||{}).map(v=>esc(v.error?.message||"")).filter(Boolean).join(" ")}</div>`;
    return `<form id="attach"><label>Регистрация компании<select name="ogrn">${r.entities.map(e=>`<option value="${esc(e.ogrn)}">${esc(e.name)} · ${esc(e.ogrn)}</option>`).join("")}</select></label><button class="secondary wide">Использовать данные реестра</button></form>`;
  }
  async function loadImage(j) {
    s.imageKey = j.captcha.id;
    const key=j.captcha.id;
    const r = await fetch(API+`/jobs/${j.job_id}/captchas/${key}/image`,{headers:{Authorization:"Bearer "+s.token}});
    if (!r.ok) {s.imageKey=null; throw new Error("Картинка устарела. Обновите капчу.");}
    const blob=await r.blob();
    if(s.imageKey!==key) return;
    if(s.imageUrl) URL.revokeObjectURL(s.imageUrl);
    s.imageUrl=URL.createObjectURL(blob);
    const el=document.getElementById("captcha-image");if(el)el.src=s.imageUrl;
  }
  function showError(e) {s.connectionError=!!e.network;s.needSync=!!e.network;if(!s.user){s.error=e.message;render();return;}s.error=e.message||String(e);const box=document.getElementById("message");if(box)box.textContent=s.error;else render();}
  async function run(fn) {
    if (s.busy) return; s.busy=true;s.error="";
    try {await fn();await ensureAssessment();} catch(e) {s.error=e.message;s.needSync=!!e.network;s.connectionError=!!e.network;if(e.status===409 && s.session) {try {await refresh();} catch(_) {}}}
    finally {s.busy=false;render();}
  }
  async function answer(value) {
    const q=activeQuestion();
    setSession(await post(sessionPath()+"/answers",{revision:s.session.revision,question_id:q.id,value}));
    s.edit=null;s.draft=undefined;
    if (q.id==="inn" && value) await startRegistry(value);
  }
  async function startRegistry(identifier) {
    if (isRunning("registry")) await api("/jobs/"+s.jobs.registry.job_id,{method:"DELETE"});
    s.pending=null;s.imageKey=null;
    if(s.imageUrl) URL.revokeObjectURL(s.imageUrl);
    s.imageUrl=null;
    s.jobs.registry=await post(sessionPath()+"/registry/jobs",{identifier,enrich:true,full:false});
  }
  async function attach(ogrn) {
    const e=s.pending.result.entities.find(x=>x.ogrn===ogrn);
    if (!s.session.data.company.inn && s.session.next_question?.id === "inn") {
      setSession(await post(sessionPath()+"/answers",{revision:s.session.revision,question_id:"inn",value:e.inn}));
    }
    if (s.session.data.company.inn!==e.inn) throw new Error("Укажите ИНН этой компании в соответствующем вопросе анкеты, затем присоедините данные.");
    setSession(await post(sessionPath()+"/registry",{revision:s.session.revision,job_id:s.pending.job_id,selected_ogrn:ogrn}));
    s.pending=null;
  }
  async function startAssessment() {
    s.jobs.scoring=await post(sessionPath()+"/scoring/jobs",{revision:s.session.revision});
    await refresh();
  }
  async function ensureAssessment() {
    const x=s.session;
    if (!x || x.state!=="ready" || x.scoring || x.data.company.inn?.length!==10 || isRunning("registry") || isRunning("scoring") || s.pending) return;
    if (x.scoring_job_id && s.jobs.scoring?.job_id===x.scoring_job_id) return;
    await startAssessment();
  }
  let polling=false;
  async function poll() {
    if (polling || !s.token || !s.session || s.busy) return;
    polling=true;const sid=s.session.session_id;
    try {
      if(s.needSync){
        const question=s.session.next_question?.id, draft=s.draft;
        await openSession(sid);
        if(s.session.next_question?.id===question)s.draft=draft;
        s.needSync=false;s.connectionError=false;s.error="";
        await ensureAssessment();render();
      }
      for (const [kind,old] of Object.entries(s.jobs)) {
        if (!["running","waiting_for_captcha"].includes(old.state)) continue;
        const next=await api("/jobs/"+old.job_id);
        if (s.session?.session_id!==sid) break;
        s.jobs[kind]=next;
        if(s.connectionError){s.connectionError=false;s.error="";const message=document.getElementById("message");if(message)message.textContent="";}
        if (next.state==="completed" && old.state!=="completed") {
          if(kind==="registry") {
            s.pending=next;
            if(next.result?.entities?.length===1 && s.session.data.company.inn===next.result.entities[0].inn) await attach(next.result.entities[0].ogrn);
          } else {
            await refresh();
            if(next.result?.session_attachment?.status==="stale_or_deleted_session") s.error="Анкета изменилась во время расчёта. Запустите оценку заново.";
          }
          await ensureAssessment();render();
        } else if (next.state!==old.state || next.captcha?.id!==old.captcha?.id) updateJobs();
      }
    } catch(e) {
      if(e.status===404) for(const j of Object.values(s.jobs)) if(["running","waiting_for_captcha"].includes(j.state)) {j.state="failed";j.error={message:"Задание больше не доступно. Запустите запрос заново."};}
      showError(e); if(!s.user) render(); else updateJobs();
    }
    finally {polling=false;}
  }
  root.addEventListener("input", e=>{if(e.target.id==="answer-value")s.draft=e.target.value;if(e.target.form?.id==="purchase"){s.groupDraft??={};s.groupDraft[e.target.name]=e.target.value;}});
  root.addEventListener("submit", e=>{e.preventDefault();const f=e.target;const values=new FormData(f);run(async()=>{
    if(f.id==="login") {const r=await post("/auth/login",{email:values.get("email"),password:values.get("password")});s.token=r.token;s.user=r.user;sessionStorage.setItem("credit-token",s.token);await loadList();if(s.items.length)await openSession(s.items[0].id);}
    if(f.id==="answer") {const q=activeQuestion();await answer(["number","integer"].includes(q.type)?Number(values.get("value")):values.get("value"));}
    if(f.id==="purchase") {
      const answers=s.session.question_group.map(q=>({question_id:q.id,value:values.get(q.id)===""?null:Number(values.get(q.id))}));
      setSession(await post(sessionPath()+"/answers/batch",{revision:s.session.revision,answers}));s.groupDraft={};
    }
    if(f.id==="attach") await attach(values.get("ogrn"));
    if(f.id==="captcha") {const j=Object.values(s.jobs).find(x=>x.state==="waiting_for_captcha");await post(`/jobs/${j.job_id}/captchas/${j.captcha.id}/answer`,{action:"answer",code:String(values.get("code")).trim()});j.state="running";delete j.captcha;}
  });});
  root.addEventListener("click", e=>{
    const b=e.target.closest("button");if(!b||b.disabled)return;
    if(b.dataset.answer!==undefined) {run(()=>answer(b.dataset.answer));return;}
    if(b.dataset.session) {run(()=>openSession(b.dataset.session));return;}
    if(b.dataset.edit!==undefined) {s.edit=s.session.answered_questions[Number(b.dataset.edit)];s.draft=s.edit.value;render();return;}
    if(!b.dataset.action)return;e.preventDefault();run(async()=>{
      switch(b.dataset.action) {
        case "new": s.groupDraft={};s.jobs={};s.pending=null;s.edit=null;s.draft=undefined;setSession(await post("/credit/sessions"));await loadList();break;
        case "logout": await post("/auth/logout");s.token=null;s.user=null;s.session=null;s.jobs={};sessionStorage.removeItem("credit-token");sessionStorage.removeItem("credit-session");break;
        case "unknown": await answer(null);break;
        case "cancel-edit": s.edit=null;s.draft=undefined;break;
        case "score": await startAssessment();break;
        case "refresh-registry": await startRegistry(s.session.data.company.inn);break;
        case "refresh-captcha": {const j=Object.values(s.jobs).find(x=>x.state==="waiting_for_captcha");await post(`/jobs/${j.job_id}/captchas/${j.captcha.id}/answer`,{action:"refresh"});j.state="running";delete j.captcha;break;}
        case "cancel-job": {const kind=b.dataset.kind;s.jobs[kind]=await api("/jobs/"+s.jobs[kind].job_id,{method:"DELETE"});break;}
        case "download": {const a=document.createElement("a");const url=URL.createObjectURL(new Blob([JSON.stringify(s.session,null,2)],{type:"application/json"}));a.href=url;a.download="recommendation-"+s.session.session_id+".json";a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);break;}
      }
    });
  });
  (async()=>{try {if(s.token) {s.user=await api("/auth/me");await loadList();const id=sessionStorage.getItem("credit-session");if(id&&s.items.some(x=>x.id===id))await openSession(id);await ensureAssessment();}}catch(e){s.error=e.message;}render();})();
  setInterval(poll,2000);
})();
