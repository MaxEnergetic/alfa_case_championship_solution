"use strict";
(() => {
  const root = document.getElementById("app");
  const API = "/api/v1";
  const s = {token: sessionStorage.getItem("credit-token"), user: null, session: null,
    items: [], jobs: {}, pending: null, edit: null, draft: undefined, error: "", busy: false, imageUrl: null, imageKey: null,
    view: sessionStorage.getItem("credit-view") || "session"};
  const esc = v => String(v ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const money = v => v == null ? "Нет данных" : new Intl.NumberFormat("ru-RU", {maximumFractionDigits:0}).format(v) + " ₽";
  const date = v => v ? new Date(v).toLocaleString("ru-RU") : "Нет данных";
  const status = {ok:"Получено",partial:"Частично",error:"Источник недоступен",not_found:"Не найдено",not_requested:"Не запрашивалось",manual_required:"Нужна ручная проверка",not_applicable:"Не применимо"};
  const btn = (label, action, cls="primary", extra="") => `<button type="button" class="${cls}" data-action="${action}" ${extra}>${label}</button>`;
  const title = x => x.data.company.entities.find(e => e.ogrn === x.data.company.selected_ogrn)?.name || x.data.company.inn || "Новый подбор";
  const activeQuestion = () => s.edit?.question || s.session?.next_question;
  function icon(name) {
    const paths = {
      home:'<path d="m3 10 9-7 9 7v10a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z"/>',
      list:'<path d="M8 6h12M8 12h12M8 18h12M3.5 6h.01M3.5 12h.01M3.5 18h.01"/>',
      plus:'<path d="M12 5v14M5 12h14"/>',
      file:'<path d="M6 3h8l5 5v13H6a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2Z"/><path d="M14 3v6h6M8 13h8M8 17h8"/>',
      bank:'<path d="m3 9 9-6 9 6M5 10v8M10 10v8M14 10v8M19 10v8M3 21h18M4 18h16"/>',
      check:'<path d="m5 12 4 4L19 6"/>',
      arrow:'<path d="M5 12h14M13 6l6 6-6 6"/>'
    };
    return `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name] || paths.file}</svg>`;
  }
  const brand = () => '<span class="brand-mark" aria-hidden="true">А</span> Альфа-Банк';
  const contextNav = () => '<div class="brand-context-nav" aria-label="Направление банка"><span>Частным лицам</span><span class="active" aria-current="page">Малому бизнесу и ИП</span><span>Среднему и крупному бизнесу</span></div>';
  function changeView(view) {s.view=view;s.scrollPending=true;sessionStorage.setItem("credit-view",view);}
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
  async function openSession(id, activate=true) {
    if(activate) changeView("session");
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
    root.innerHTML = `<div class="login-screen"><header class="login-topbar"><a class="brand" href="/" aria-label="Альфа-Банк — кредитный кабинет">${brand()}</a>${contextNav()}<span class="cabinet-label">Кредитный кабинет</span></header><main class="login-layout"><section class="login-art-panel"><div class="login-art-copy"><p class="eyebrow">Кредит для бизнеса</p><h1>Когда бизнесу нужен следующий шаг</h1><p>Расскажите о задаче бизнеса — подберём финансирование с учётом сведений компании и финансового профиля.</p></div><div class="login-visual" aria-hidden="true"></div><div class="login-caption">Потребность · Данные компании · Рекомендации</div></section><section class="login-side"><div class="login-box"><p class="eyebrow">Личный кабинет</p><h2>Добро пожаловать</h2><p>Войдите, чтобы начать подбор или вернуться к сохранённым ответам.</p><form id="login"><div class="field"><label for="login-email">Электронная почта</label><input class="input" id="login-email" name="email" type="email" autocomplete="username" required></div><div class="field"><label for="login-password">Пароль</label><input class="input" id="login-password" name="password" type="password" autocomplete="current-password" required></div><p class="error" role="alert">${esc(s.error)}</p><button class="btn btn-primary btn-full" ${s.busy?"disabled":""}>Войти <span aria-hidden="true">→</span></button></form><div class="login-hint">Ваши ответы и результаты сохраняются в кабинете.</div><p class="footer-note">Учётная запись задаётся при запуске сервиса.</p></div></section></main></div>`;
  }
  function render() {
    if (!s.user) {loginPage(); return;}
    const home=s.view==="home" || !s.session;
    root.innerHTML = `<header class="topbar"><button class="brand brand-button" data-action="home" aria-label="Обзор кабинета">${brand()}</button>${contextNav()}<div class="topbar-right"><span class="cabinet-label">Кредитный кабинет</span><div class="user-menu"><span class="avatar" aria-hidden="true">${esc(s.user.email.slice(0,1).toUpperCase())}</span><span class="user-caption">${esc(s.user.email)}<small>Личный кабинет</small></span></div>${btn("Выйти","logout","topbar-link")}</div></header><div class="app-shell ${home?"dashboard-shell":""}"><aside class="sidebar"><div class="side-caption">Кредитный кабинет</div><nav class="side-nav" aria-label="Основная навигация"><button class="nav-item ${home?"active":""}" data-action="home">${icon("home")}Обзор</button><button class="nav-item ${!home?"active":""}" data-action="${s.session?"resume":"new"}">${icon("list")}${s.session?"Текущий подбор":"Подбор финансирования"}</button><button class="nav-item" data-action="new">${icon("plus")}Новый подбор</button></nav><div class="side-caption saved-caption">Сохранённые подборы · ${s.items.length}</div><nav class="saved-sessions" aria-label="Сохранённые подборы">${s.items.map(x=>`<button class="session-link ${!home&&x.id===s.session?.session_id?"active":""}" data-session="${esc(x.id)}"><strong>${x.id===s.session?.session_id?esc(title(s.session)):"Подбор финансирования"}</strong><span>${esc(date(x.created_at))}</span></button>`).join("")}</nav><div class="side-spacer"></div><div class="side-help"><strong>Ответы под рукой</strong><p>Вернитесь к любому подбору, чтобы продолжить анкету или посмотреть результат.</p></div><div class="side-footer"><b>Данные открытых источников</b>ФНС и финансовая отчётность</div></aside><main class="main ${home?"dashboard-main":""}"><div id="message" class="error" role="alert">${esc(s.error)}</div>${home?dashboardView():`<div class="page-heading"><div><p class="eyebrow">Подбор финансирования</p><h1>${esc(title(s.session))}</h1><p class="page-subtitle">Анкета, сведения компании и подходящие продукты — в одном месте</p></div><div class="heading-actions">${btn(icon("file")+"Скачать JSON","download","btn btn-outline")}</div></div>${sessionView()}`}<p class="footer-note">Рекомендация помогает выбрать продукт. Решение о выдаче принимает банк.</p></main></div><nav class="mobile-nav ${home?"dashboard-nav":""}" aria-label="Мобильная навигация"><button class="nav-item ${home?"active":""}" data-action="home">${icon("home")}Обзор</button><button class="nav-item ${!home?"active":""}" data-action="${s.session?"resume":"new"}">${icon("list")}Подбор</button><button class="nav-item" data-action="new">${icon("plus")}Новый</button></nav>`;
    updateJobs();
    if(s.scrollPending) {if(typeof window!=="undefined")window.scrollTo({top:0,behavior:"instant"});s.scrollPending=false;}
  }
  function dashboardView() {
    const x=s.session, ready=!!x?.assessment;
    return `<div class="page-heading"><div><p class="eyebrow">Мой бизнес</p><h1>Кредитный кабинет</h1><p class="page-subtitle">Подберите финансирование для следующего шага</p></div><div class="heading-actions">${btn("Новый подбор","new","btn btn-primary")}</div></div><section class="dashboard-callout"><div class="callout-copy"><p class="eyebrow">Финансирование для бизнеса</p><h2>${ready?"Ваши рекомендации готовы":x?"Продолжим с того же места":"Подберите кредит для своей задачи"}</h2><p>${ready?"Посмотрите подходящие продукты и оценку финансового профиля компании.":x?"Ваши ответы сохранены. Завершите анкету, чтобы получить подходящие варианты.":"Ответьте на вопросы о бизнесе. Сведения компании загрузим из открытых источников."}</p>${btn((ready?"Посмотреть результат":x?"Продолжить подбор":"Подобрать финансирование")+' <span aria-hidden="true">→</span>',x?"resume":"new","btn btn-primary")}</div><div class="callout-art" aria-hidden="true"><div class="art-circle"></div><div class="art-card"><b>Альфа-Банк</b><span>ДЛЯ БИЗНЕСА</span></div><div class="art-coin">А</div></div></section><div class="stats-grid"><div class="panel stat-card"><div class="stat-label">Сохранённые подборы</div><div class="stat-value">${s.items.length}</div><div class="stat-foot">В вашем кабинете</div></div><div class="panel stat-card"><div class="stat-label">Ответы в текущем подборе</div><div class="stat-value">${x?.answered_questions?.length??"—"}</div><div class="stat-foot">Можно вернуться и исправить</div></div><div class="panel stat-card"><div class="stat-label">Подобранные продукты</div><div class="stat-value">${x?.assessment?.products.length??"—"}</div><div class="stat-foot">${ready?"Для текущей анкеты":"После завершения анкеты"}</div></div></div><section class="panel panel-pad"><div class="section-head"><div><h2>Мои подборы</h2><p>Сохранённые анкеты и результаты</p></div></div>${s.items.length?`<div class="table-wrap"><table><thead><tr><th>Подбор</th><th>Создан</th><th></th></tr></thead><tbody>${s.items.map(item=>`<tr><td><div class="table-title">${item.id===x?.session_id?esc(title(x)):"Подбор финансирования"}</div>${item.id===x?.session_id?'<div class="table-sub">Текущий подбор</div>':""}</td><td>${esc(date(item.created_at))}</td><td><button class="btn btn-secondary btn-small" data-session="${esc(item.id)}" aria-label="Открыть подбор от ${esc(date(item.created_at))}">Открыть <span aria-hidden="true">→</span></button></td></tr>`).join("")}</tbody></table></div>`:`<div class="empty-state"><div class="empty-icon">${icon("file")}</div><h3>Здесь появятся ваши подборы</h3><p>Начните с задачи бизнеса — анкета подстроится под ваши ответы.</p>${btn("Начать подбор","new","btn btn-primary")}</div>`}</section>`;
  }
  function sessionView() {
    const x = s.session, inn=x.data.company.inn;
    return `<div class="wizard-layout"><section class="main-column">${questionView()}${x.assessment?recommendationView():""}${historyView()}</section><aside class="facts"><section class="panel quote-card"><div class="quote-head"><h3>Данные компании</h3>${icon("bank")}</div>${inn?btn("Обновить сведения","refresh-registry","secondary wide",isRunning("registry")?"disabled":""):`<p class="quote-empty">Укажите ИНН в анкете — сведения загрузятся автоматически.</p>`}<div id="jobs"></div>${companyView()}</section></aside></div>`;
  }
  function questionPanel(content, group=false) {
    return `<section class="panel wizard-main"><div class="wizard-top"><div class="wizard-topline"><span>${s.edit?"Редактирование анкеты":"Подбор финансирования"}</span><span>Сохранено ответов: ${s.session.answered_questions?.length||0}</span></div><div class="wizard-stages cabinet-stages"><span class="stage-label active">Анкета</span><span class="stage-label ${s.session.data.company.checked_at?"done":""}">Данные компании</span><span class="stage-label">Рекомендации</span></div></div><div class="wizard-body question-step ${group?"purchase-step":""}">${content}</div></section>`;
  }
  function questionView() {
    const q = activeQuestion();
    if (!q) return "";
    if (!s.edit && s.session.question_group?.length>1) {
      return questionPanel(`<p class="question-kicker">Бюджет покупки</p><h2>Стоимость и сумма кредита</h2><form id="purchase"><div class="purchase-fields">${s.session.question_group.map((f,i)=>`<label>${i===0?"Стоимость покупки, ₽":"Из неё в кредит, ₽"}<input name="${f.id}" type="number" min="1" max="${f.maximum}" step="any" value="${esc(s.groupDraft?.[f.id]??"")}" placeholder="Если пока неизвестно, оставьте пустым"></label>`).join("")}</div><div class="form-actions form-actions-end"><button class="primary">Продолжить <span aria-hidden="true">→</span></button></div></form>`,true);
    }
    const value = s.draft !== undefined ? s.draft : s.edit?.value;
    let control;
    if (q.type === "choice") control = `<div class="option-grid">${q.options.map(o=>`<button class="option-card ${o.value===value?"selected":""}" data-answer="${esc(o.value)}"><span class="option-mark" aria-hidden="true">${icon(o.value===value?"check":"arrow")}</span><span class="option-copy"><b>${esc(o.label)}</b></span><span class="choice-radio" aria-hidden="true"></span></button>`).join("")}</div>`;
    else control = `<form id="answer"><label>${q.type === "date" ? "Выберите дату" : "Ваш ответ"}<input id="answer-value" name="value" type="${q.type === "date" ? "date" : ["number","integer"].includes(q.type)?"number":"text"}" ${q.minimum!=null?`min="${q.minimum}"`:""} ${q.maximum!=null?`max="${q.maximum}"`:""} ${q.max_length?`maxlength="${q.max_length}"`:""} step="${q.type === "integer"?"1":"any"}" value="${esc(value ?? "")}" required></label><button class="primary" ${s.busy?"disabled":""}>${s.edit?"Сохранить исправление":"Продолжить"}</button>${q.allow_unknown?btn("Не знаю","unknown","ghost"):""}</form>`;
    return questionPanel(`<p class="question-kicker">${s.edit?"Исправление ответа":"Анкета"}</p><h2>${esc(q.text)}</h2>${s.edit?`<p class="notice">Изменение ответа перестроит следующие вопросы и сбросит предыдущую финансовую оценку.</p>`:""}${control}${s.edit?btn("Отмена","cancel-edit","ghost"):""}`);
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
        await openSession(sid,false);
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
    if(f.id==="login") {const r=await post("/auth/login",{email:values.get("email"),password:values.get("password")});s.token=r.token;s.user=r.user;sessionStorage.setItem("credit-token",s.token);await loadList();if(s.items.length)await openSession(s.items[0].id);changeView("home");}
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
        case "home": changeView("home");break;
        case "resume": changeView("session");break;
        case "new": s.groupDraft={};s.jobs={};s.pending=null;s.edit=null;s.draft=undefined;setSession(await post("/credit/sessions"));changeView("session");await loadList();break;
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
  (async()=>{try {if(s.token) {s.user=await api("/auth/me");await loadList();const id=sessionStorage.getItem("credit-session");if(id&&s.items.some(x=>x.id===id))await openSession(id,false);await ensureAssessment();}}catch(e){s.error=e.message;}render();})();
  setInterval(poll,2000);
})();
