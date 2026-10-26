(function () {
  "use strict";

  var STORE_KEY = "alfa-credit-demo-v1";
  var DEFAULT_PROFILE = { email: "client@demo.alfa.ru", name: "Алексей Смирнов", role: "client" };
  var PRODUCTS = {
    single: { title: "Кредит одной суммой", rate: 22, minAmount: 300000, maxAmount: 30000000, minTerm: 3, maxTerm: 60, note: "Вся сумма сразу на развитие бизнеса", alt: "line" },
    secured: { title: "Кредит с залогом", rate: 19, minAmount: 500000, maxAmount: 150000000, minTerm: 12, maxTerm: 120, note: "Увеличенный лимит под залог имущества", alt: "single" },
    line: { title: "Возобновляемая кредитная линия", rate: 23, minAmount: 300000, maxAmount: 30000000, minTerm: 6, maxTerm: 18, note: "Получайте деньги частями, когда понадобится", alt: "single" },
    overdraft: { title: "Овердрафт", rate: 24, minAmount: 300000, maxAmount: 20000000, minTerm: 2, maxTerm: 12, note: "Короткий запас средств до поступления выручки", alt: "line" }
  };
  var DEMO_COMPANIES = {
    "9900000017": {
      tin: "9900000017", name: "ООО «Север Логистик»", legalType: "ООО", regDate: "14.03.2021", ageMonths: 67, region: "Москва",
      activity: "Грузовые перевозки и логистика", director: "Алексей Смирнов", hasReport: true, annualRevenue: 24000000, monthlyRevenue: 2000000,
      modelScore: 0.9, creditHistory: "clear", currentPayments: 0, taxDebt: 0, courtCases: false, bailiffDebt: 0, liquidating: false
    },
    "990000000299": {
      tin: "990000000299", name: "ИП «Зерно»", legalType: "ИП", regDate: "02.06.2024", ageMonths: 28, region: "Санкт-Петербург",
      activity: "Кофейни и общественное питание", director: "Алексей Смирнов", hasReport: false, annualRevenue: null, monthlyRevenue: 450000,
      modelScore: null, creditHistory: "clear", currentPayments: 0, taxDebt: 0, courtCases: false, bailiffDebt: 0, liquidating: false, requiresDocs: true
    },
    "9900000031": {
      tin: "9900000031", name: "ООО «Мастерская Ремонт»", legalType: "ООО", regDate: "18.09.2024", ageMonths: 25, region: "Казань",
      activity: "Ремонт оборудования и помещений", director: "Алексей Смирнов", hasReport: true, annualRevenue: 14400000, monthlyRevenue: 1200000,
      modelScore: 0.3, creditHistory: "closed", currentPayments: 0, taxDebt: 150000, courtCases: true, bailiffDebt: 0, liquidating: false
    },
    "990000000436": {
      tin: "990000000436", name: "ИП «Цветы у дома»", legalType: "ИП", regDate: "12.02.2026", ageMonths: 8, region: "Екатеринбург",
      activity: "Розничная торговля цветами", director: "Алексей Смирнов", hasReport: false, annualRevenue: null, monthlyRevenue: 700000,
      modelScore: null, creditHistory: "clear", currentPayments: 0, taxDebt: 0, courtCases: false, bailiffDebt: 0, liquidating: false, requiresDocs: true
    },
    "9900000056": {
      tin: "9900000056", name: "ООО «Вектор Строй»", legalType: "ООО", regDate: "30.07.2019", ageMonths: 87, region: "Новосибирск",
      activity: "Строительство нежилых зданий", director: "Алексей Смирнов", hasReport: true, annualRevenue: 38000000, monthlyRevenue: 3166667,
      modelScore: 0.72, creditHistory: "clear", currentPayments: 0, taxDebt: 0, courtCases: false, bailiffDebt: 0, liquidating: true
    },
    "9900000063": {
      tin: "9900000063", name: "ООО «Быстрый старт»", legalType: "ООО", regDate: "09.10.2025", ageMonths: 12, region: "Самара",
      activity: "Оптовая торговля хозяйственными товарами", director: "Алексей Смирнов", hasReport: true, annualRevenue: 3600000, monthlyRevenue: 300000,
      modelScore: 0.1, creditHistory: "current", currentPayments: 90000, taxDebt: 0, courtCases: true, bailiffDebt: 0, liquidating: false
    }
  };

  function freshDb() {
    return { session: null, view: "dashboard", currentAppId: null, staffAppId: null, apps: [] };
  }
  function readDb() {
    try {
      var saved = JSON.parse(localStorage.getItem(STORE_KEY) || "null");
      if (saved && Array.isArray(saved.apps)) return Object.assign(freshDb(), saved);
    } catch (error) {}
    return freshDb();
  }
  var db = readDb();
  var appRoot = document.getElementById("app");
  var liveRegion = document.getElementById("live-region");
  var toastTimer = null;
  var modalMarkup = "";

  if (!db.session || (db.session.role !== "client" && db.session.role !== "staff")) {
    db.session = Object.assign({}, DEFAULT_PROFILE);
  }

  function saveDb() {
    try { localStorage.setItem(STORE_KEY, JSON.stringify(db)); } catch (error) {}
  }
  function uid() { return "req-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 7); }
  function isoNow() { return new Date().toISOString(); }
  function copy(value) { return JSON.parse(JSON.stringify(value)); }
  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (char) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char];
    });
  }
  function money(value) {
    return new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 0 }).format(Math.round(Number(value) || 0)) + " ₽";
  }
  function compactMoney(value) {
    var amount = Number(value) || 0;
    if (amount >= 1000000) {
      var millions = amount / 1000000;
      return (Number.isInteger(millions) ? String(millions) : millions.toFixed(1).replace(".", ",")) + " млн ₽";
    }
    return money(amount);
  }
  function dateTime(value) {
    if (!value) return "";
    try { return new Date(value).toLocaleString("ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }); }
    catch (error) { return ""; }
  }
  function dateOnly(value) {
    if (!value) return "";
    try { return new Date(value).toLocaleDateString("ru-RU", { day: "numeric", month: "short", year: "numeric" }); }
    catch (error) { return ""; }
  }
  function showToast(message) {
    var old = document.querySelector(".toast");
    if (old) old.remove();
    var item = document.createElement("div");
    item.className = "toast";
    item.setAttribute("role", "status");
    item.textContent = message;
    document.body.appendChild(item);
    if (liveRegion) liveRegion.textContent = message;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { item.remove(); }, 3200);
  }
  function currentApp() {
    return db.apps.find(function (app) { return app.id === db.currentAppId; }) || null;
  }
  function displayId(app) {
    if (app && app.displayId) return app.displayId;
    var value = app && app.createdAt ? Date.parse(app.createdAt) : Date.now();
    return String(value).slice(-6);
  }
  function draftForSession() {
    if (!db.session) return null;
    return db.apps.find(function (app) { return app.ownerId === db.session.email && app.status === "draft" && app.backendSessionId; }) ||
      db.apps.find(function (app) { return app.ownerId === db.session.email && app.status === "draft"; }) || null;
  }
  function appsForSession() {
    if (!db.session) return [];
    return db.apps.filter(function (app) { return app.ownerId === db.session.email; }).sort(function (a, b) {
      return new Date(b.createdAt) - new Date(a.createdAt);
    });
  }
  function currentDraft() {
    var app = currentApp();
    return app && app.status === "draft" ? app : draftForSession();
  }
  function defaultForm() {
    return {
      purpose: "", otherGoal: "", moneyUse: "", amount: 3000000, term: 24, collateral: "",
      tin: "", applicantName: "", applicantRole: "owner", powerOfAttorney: false,
      monthlyRevenue: 0, revenueOverride: false, currentPayments: 0,
      consentBki: false, consentPrivacy: false
    };
  }
  function startDraft() {
    var existing = draftForSession();
    if (existing) {
      db.currentAppId = existing.id;
      db.view = "wizard";
      saveDb();
      render();
      return existing;
    }
    var created = {
      id: uid(), displayId: String(Math.floor(100000 + Math.random() * 900000)), ownerId: db.session.email, ownerName: db.session.name,
      createdAt: isoNow(), updatedAt: isoNow(), status: "draft", wizardStage: "product", wizardIndex: 0,
      form: defaultForm(), company: null, applicant: null, product: null, decision: null,
      history: [{ from: null, to: "draft", at: isoNow(), actor: "Клиент", label: "Черновик создан" }]
    };
    db.apps.push(created);
    db.currentAppId = created.id;
    db.view = "wizard";
    saveDb();
    render();
    return created;
  }
  var backendBusy = false;
  function backendDraftForSession() {
    return appsForSession().find(function (app) { return app.backendSessionId && app.status === "draft"; }) || null;
  }
  function syncBackendDraft(app, session) {
    app.backendView = session;
    app.updatedAt = session.updated_at || isoNow();
    var data = session.data || {};
    var financing = data.financing || {};
    app.form = app.form || defaultForm();
    if (financing.amount_rub != null) app.form.amount = financing.amount_rub;
    if (financing.term_months != null) app.form.term = financing.term_months;
    if (financing.goal) app.form.purpose = financing.goal;
    if (data.company && data.company.inn) app.form.tin = data.company.inn;
    var entities = data.company && data.company.entities || [];
    var selected = entities.find(function (entity) { return entity.ogrn === (data.company && data.company.selected_ogrn); }) || entities[0];
    if (selected) app.company = { name: selected.name || "Компания", tin: selected.inn || app.form.tin, legalType: selected.entity_type || "Организация" };
    saveDb();
  }
  async function startBackendDraft() {
    if (backendBusy) return;
    var existing = backendDraftForSession() || appsForSession().find(function (app) { return app.status === "draft" && app.wizardStage === "backend"; });
    if (existing) {
      db.currentAppId = existing.id; db.view = "wizard"; saveDb(); render();
      if (existing.backendSessionId) return loadBackendSession(existing);
      backendBusy = true; existing.backendLoading = true; existing.backendError = ""; saveDb(); render();
      try {
        var restored = await window.AlfaBackend.createSession();
        existing.backendSessionId = restored.session_id; existing.backendLoading = false; syncBackendDraft(existing, restored); render();
      } catch (error) { existing.backendLoading = false; existing.backendError = error.message; saveDb(); render(); }
      finally { backendBusy = false; }
      return;
    }
    backendBusy = true;
    var created = {
      id: uid(), displayId: String(Math.floor(100000 + Math.random() * 900000)), ownerId: db.session.email, ownerName: db.session.name,
      createdAt: isoNow(), updatedAt: isoNow(), status: "draft", wizardStage: "backend", wizardIndex: 0,
      form: defaultForm(), company: null, applicant: null, product: null, decision: null,
      backendLoading: true, backendError: "", backendJobId: null, backendJob: null,
      history: [{ from: null, to: "draft", at: isoNow(), actor: "Клиент", label: "Анкета создана" }]
    };
    db.apps.push(created); db.currentAppId = created.id; db.view = "wizard"; saveDb(); render();
    try {
      var session = await window.AlfaBackend.createSession();
      created.backendSessionId = session.session_id;
      created.backendLoading = false;
      syncBackendDraft(created, session);
      render();
    } catch (error) {
      created.backendLoading = false; created.backendError = error.message; saveDb(); render();
    } finally { backendBusy = false; }
  }
  async function loadBackendSession(app) {
    if (!app || !app.backendSessionId || app.backendLoading) return;
    app.backendLoading = true; app.backendError = ""; saveDb(); render();
    try {
      syncBackendDraft(app, await window.AlfaBackend.getSession(app.backendSessionId));
      app.backendLoading = false; render();
      if (app.backendJobId) pollBackendJob(app, app.backendJobId);
    } catch (error) {
      app.backendLoading = false; app.backendError = error.message; saveDb(); render();
    }
  }
  function backendCurrent() {
    var app = currentApp();
    return app && app.backendSessionId ? app : null;
  }
  function currentBackendQuestion(app) { return app && app.backendView && app.backendView.next_question; }
  function refreshBackendSession(app) {
    if (!app || !app.backendSessionId || app.backendLoading) return;
    app.backendLoading = true; app.backendError = ""; saveDb(); render();
    window.AlfaBackend.getSession(app.backendSessionId).then(function (session) {
      syncBackendDraft(app, session); app.backendLoading = false; render();
    }).catch(function (error) {
      app.backendLoading = false; app.backendError = error.message; saveDb(); render();
    });
  }
  async function answerBackendQuestion(value, questionId) {
    var app = backendCurrent();
    if (!app) return;
    var question = currentBackendQuestion(app);
    var targetQuestion = questionId && app.backendQuestions && app.backendQuestions.find(function (item) { return item.id === questionId; });
    targetQuestion = targetQuestion || (question && (!questionId || question.id === questionId) ? question : null);
    if (!app || !targetQuestion || app.backendLoading) return;
    app.backendLoading = true; app.backendError = ""; saveDb(); render();
    try {
      var session = await window.AlfaBackend.answer(app.backendSessionId, app.backendView.revision, questionId || question.id, value);
      app.backendQuestions = app.backendQuestions || [];
      var existingIndex = app.backendQuestions.findIndex(function (item) { return item.id === targetQuestion.id; });
      if (existingIndex < 0) app.backendQuestions.push(targetQuestion);
      else app.backendQuestions = app.backendQuestions.slice(0, existingIndex + 1);
      app.backendAnswers = (app.backendAnswers || []).filter(function (item) { return app.backendQuestions.some(function (saved) { return saved.id === item.id; }); });
      var previous = app.backendAnswers.findIndex(function (item) { return item.id === targetQuestion.id; });
      var answerEntry = { id: targetQuestion.id, value: value };
      if (previous < 0) app.backendAnswers.push(answerEntry); else app.backendAnswers[previous] = answerEntry;
      app.backendEditQuestionId = null;
      syncBackendDraft(app, session); app.backendLoading = false; render();
    } catch (error) {
      app.backendLoading = false; app.backendError = error.message; saveDb(); render();
    }
  }
  function pollBackendJob(app, jobId) {
    if (!app || !jobId) return;
    window.AlfaBackend.job(jobId).then(function (job) {
      if (app.backendJobId !== jobId) return;
      app.backendJob = job; app.backendError = ""; saveDb();
      if (job.state === "running" || job.state === "waiting_for_captcha") {
        if (db.currentAppId === app.id && db.view === "wizard") render();
        setTimeout(function () { pollBackendJob(app, jobId); }, 1800);
      } else if (job.state === "completed" && app.backendJobKind === "registry") {
        app.backendJobReady = true;
        if (db.currentAppId === app.id && db.view === "wizard") render();
      } else if (job.state === "completed" && app.backendJobKind === "scoring") {
        refreshBackendSession(app);
        if (db.currentAppId === app.id && db.view === "wizard") render();
      } else {
        app.backendJobId = null; app.backendJobReady = false;
        app.backendError = job.error && job.error.message || (job.state === "cancelled" ? "Проверка отменена." : "Не удалось завершить проверку.");
        if (db.currentAppId === app.id && db.view === "wizard") render();
      }
    }).catch(function (error) {
      if (app.backendJobId !== jobId) return;
      app.backendError = error.message; saveDb();
      if (db.currentAppId === app.id && db.view === "wizard") render();
    });
  }
  async function startBackendRegistry() {
    var app = backendCurrent(), inn = app && app.backendView && app.backendView.data.company.inn;
    if (!app || !inn || app.backendLoading) return;
    app.backendLoading = true; app.backendError = ""; saveDb(); render();
    try {
      var job = await window.AlfaBackend.startRegistry(inn);
      app.backendJobId = job.job_id; app.backendJobKind = "registry"; app.backendJobReady = false;
      app.backendLoading = false; app.backendJob = job; saveDb(); render(); pollBackendJob(app, job.job_id);
    } catch (error) { app.backendLoading = false; app.backendError = error.message; saveDb(); render(); }
  }
  async function attachBackendRegistry(selectedOgrn) {
    var app = backendCurrent();
    if (!app || !app.backendView || !app.backendJob || app.backendLoading) return;
    app.backendLoading = true; app.backendError = ""; saveDb(); render();
    try {
      var session = await window.AlfaBackend.attachRegistry(app.backendSessionId, app.backendView.revision, app.backendJob.job_id, selectedOgrn || undefined);
      app.backendJobId = null; app.backendJob = null; app.backendJobKind = null; app.backendJobReady = false;
      syncBackendDraft(app, session); app.backendLoading = false; render();
    } catch (error) { app.backendLoading = false; app.backendError = error.message; saveDb(); render(); }
  }
  async function answerBackendCaptcha(action) {
    var app = backendCurrent(), captcha = app && app.backendJob && app.backendJob.captcha;
    if (!app || !captcha || app.backendLoading) return;
    var code = "";
    if (action === "answer") {
      var input = document.getElementById("backend-captcha-code");
      code = input ? input.value.trim() : "";
      if (!/^\d{6}$/.test(code)) { showToast("Введите 6 цифр с картинки."); return; }
    }
    app.backendLoading = true; saveDb(); render();
    try {
      await window.AlfaBackend.answerCaptcha(captcha.answer_url, action, code);
      app.backendLoading = false; app.backendJob.captcha = null; saveDb(); render(); pollBackendJob(app, app.backendJobId);
    } catch (error) { app.backendLoading = false; app.backendError = error.message; saveDb(); render(); }
  }
  async function startBackendScoring() {
    var app = backendCurrent();
    if (!app || !app.backendView || app.backendLoading) return;
    app.backendLoading = true; app.backendError = ""; saveDb(); render();
    try {
      var job = await window.AlfaBackend.startScoring(app.backendSessionId, app.backendView.revision);
      app.backendJobId = job.job_id; app.backendJobKind = "scoring"; app.backendJobReady = false;
      app.backendLoading = false; app.backendJob = job; saveDb(); render(); pollBackendJob(app, job.job_id);
    } catch (error) { app.backendLoading = false; app.backendError = error.message; saveDb(); render(); }
  }
  function updateDraft(mutator) {
    var draft = currentDraft();
    if (!draft) return null;
    mutator(draft);
    draft.updatedAt = isoNow();
    saveDb();
    return draft;
  }
  function setStatus(app, status, actor, label) {
    var oldStatus = app.status;
    app.status = status;
    app.updatedAt = isoNow();
    app.history = app.history || [];
    app.history.push({ from: oldStatus, to: status, at: isoNow(), actor: actor || "Система", label: label || STATUS_LABEL[status] || status });
    saveDb();
  }
  var STATUS_LABEL = {
    draft: "Черновик", submitted: "Подана", checking: "Проверяем данные", needs_docs: "Нужны документы",
    enrichment: "Получаем сведения", scoring: "Оцениваем заявку", manual_review: "Ручная проверка",
    approved: "Предварительный вариант найден", counteroffer: "Есть другой вариант", declined: "Вариант не подобран",
    accepted: "Условия выбраны", rejected_by_client: "Отклонено клиентом", signed: "Условия подтверждены", issued: "Расчёт завершён"
  };
  function statusBadge(status) {
    var kind = "badge-neutral";
    if (["checking", "needs_docs", "enrichment", "scoring", "manual_review"].indexOf(status) >= 0) kind = "badge-progress";
    if (["approved", "accepted", "signed", "issued"].indexOf(status) >= 0) kind = "badge-success";
    if (["declined", "rejected_by_client"].indexOf(status) >= 0) kind = "badge-danger";
    if (status === "counteroffer") kind = "badge-blue";
    return '<span class="badge ' + kind + '">' + esc(STATUS_LABEL[status] || status) + "</span>";
  }
  function isPending(status) {
    return ["submitted", "checking", "needs_docs", "enrichment", "scoring", "manual_review"].indexOf(status) >= 0;
  }
  function isOffer(status) { return status === "approved" || status === "counteroffer"; }

  function monthPayment(amount, annualRate, months) {
    var principal = Math.max(0, Number(amount) || 0);
    var term = Math.max(1, Number(months) || 1);
    var rate = (Number(annualRate) || 0) / 1200;
    if (!rate) return principal / term;
    return principal * rate / (1 - Math.pow(1 + rate, -term));
  }
  function productKey(form) {
    if (form.moneyUse === "bridge" || form.purpose === "gap") return "overdraft";
    if (form.moneyUse === "parts") return "line";
    if (Number(form.amount) > 30000000 || ["property", "vehicle"].indexOf(form.collateral) >= 0) return "secured";
    return "single";
  }
  function getProduct(app) {
    var form = app.form || defaultForm();
    return PRODUCTS[productKey(form)];
  }
  function selectedQuote(app) {
    var form = app.form || defaultForm();
    var product = getProduct(app);
    var amount = Math.max(product.minAmount, Math.min(product.maxAmount, Number(form.amount) || product.minAmount));
    var months = Math.max(product.minTerm, Math.min(product.maxTerm, Number(form.term) || product.minTerm));
    return { product: product, amount: amount, months: months, payment: monthPayment(amount, product.rate, months) };
  }
  function stopReason(company) {
    if (!company) return "";
    if (company.liquidating) return "Компания находится в стадии ликвидации.";
    if (company.bankrupt) return "В отношении компании начата процедура банкротства.";
    if (Number(company.ageMonths) < 3) return "Бизнесу меньше трёх месяцев.";
    if (Number(company.bailiffDebt) > 1000000) return "Задолженность у приставов превышает 1 млн ₽.";
    return "";
  }
  function scoreApplication(app) {
    var company = app.company || {};
    var form = app.form || {};
    var quote = selectedQuote(app);
    var revenue = Math.max(0, Number(form.monthlyRevenue) || Number(company.monthlyRevenue) || 0);
    var currentPayments = Math.max(0, Number(form.currentPayments) || Number(company.currentPayments) || 0);
    var loadRatio = revenue > 0 ? (currentPayments + quote.payment) / revenue : 1;
    var factors = [];
    var total = 0;
    var modelUsed = company.hasReport && Number.isFinite(Number(company.modelScore));
    if (modelUsed) {
      var model = Number(company.modelScore);
      var financial = model >= 0.78 ? 40 : 40 * Math.max(0, model) / 0.78;
      factors.push({ name: "Финансовая устойчивость", value: financial, max: 40, note: "Расчётный индекс, скор " + model.toFixed(2).replace(".", ",") });
      total += financial;
    }
    var credit = company.creditHistory === "current" ? 0 : company.creditHistory === "closed" ? 10 : 20;
    factors.push({ name: "Платёжная дисциплина", value: credit, max: 20, note: company.creditHistory === "current" ? "Есть текущие просрочки" : company.creditHistory === "closed" ? "Были закрытые просрочки" : "Просрочек нет" });
    total += credit;
    var load = loadRatio < 0.3 ? 15 : loadRatio <= 0.5 ? 8 : 0;
    factors.push({ name: "Платёжная нагрузка", value: load, max: 15, note: Math.round(loadRatio * 100) + "% выручки" });
    total += load;
    var months = Number(company.ageMonths) || 0;
    var age = months >= 36 ? 10 : months >= 12 ? 7 : months >= 6 ? 4 : months >= 3 ? 1 : 0;
    factors.push({ name: "Возраст бизнеса", value: age, max: 10, note: months >= 12 ? Math.floor(months / 12) + " лет" : months + " мес." });
    total += age;
    var tax = Number(company.taxDebt) > 100000 ? 0 : Number(company.taxDebt) > 0 ? 5 : 10;
    factors.push({ name: "Налоговый долг", value: tax, max: 10, note: Number(company.taxDebt) > 0 ? money(company.taxDebt) : "Нет" });
    total += tax;
    var legal = company.courtCases || Number(company.bailiffDebt) > 0 ? 0 : 5;
    factors.push({ name: "Суды и приставы", value: legal, max: 5, note: legal ? "Ограничений нет" : "Требует дополнительной проверки" });
    total += legal;
    if (!modelUsed) total = total * 100 / 60;
    return { total: Math.max(0, Math.min(100, total)), factors: factors, modelUsed: modelUsed, ratio: loadRatio, payment: quote.payment, revenue: revenue, currentPayments: currentPayments };
  }
  function calculateDecision(app) {
    var score = scoreApplication(app);
    var quote = selectedQuote(app);
    if (score.total < 35) return { kind: "declined", score: score, reason: "Итоговый балл пока ниже порога для предложения. Можно подать заявку снова, когда финансовая нагрузка станет меньше." };
    if (score.total < 50) return { kind: "manual_review", score: score, reason: "Нужна дополнительная проверка специалистом." };
    var allowedRatio = score.total >= 70 ? 0.3 : 0.2;
    var limit = score.revenue * allowedRatio;
    if (score.payment <= limit) {
      return { kind: "approved", score: score, amount: quote.amount, months: quote.months, rate: quote.product.rate, payment: quote.payment, reason: "Платёж укладывается в комфортную долю от выручки." };
    }
    var months = Math.min(Number(app.form.term) || quote.months, quote.product.maxTerm);
    var amount = Math.min(quote.amount, quote.product.maxAmount);
    var proposal = null;
    for (var candidate = amount; candidate >= quote.product.minAmount; candidate -= 100000) {
      var payment = monthPayment(candidate, quote.product.rate, months);
      if (payment <= limit) {
        proposal = { amount: candidate, months: months, rate: quote.product.rate, payment: payment };
        break;
      }
    }
    if (proposal && proposal.amount < quote.amount) {
      return { kind: "counteroffer", score: score, amount: proposal.amount, months: proposal.months, rate: proposal.rate, payment: proposal.payment, reason: "При меньшей сумме платёж будет комфортнее для выручки компании." };
    }
    return { kind: "declined", score: score, reason: "При текущей выручке платёж получился бы слишком высоким. Можно подать заявку повторно после роста поступлений." };
  }
  function due(app, delay) { app.nextAt = Date.now() + delay; }
  function consentLabel(kind) { return kind === "bki" ? "Запрос кредитной истории, 218-ФЗ" : "Обработка персональных данных, 152-ФЗ"; }
  function newCompany(tin) {
    return {
      tin: tin, name: "ООО «Профиль компании»", legalType: tin.length === 12 ? "ИП" : "ООО",
      regDate: "01.08.2020", ageMonths: 74, region: "Москва", activity: "Оптовая торговля",
      director: "Алексей Смирнов", hasReport: false, annualRevenue: null, monthlyRevenue: 650000,
      modelScore: null, creditHistory: "clear", currentPayments: 0, taxDebt: 0, courtCases: false, bailiffDebt: 0,
      liquidating: false, requiresDocs: true, syntheticFallback: true
    };
  }
  function validInn(value) {
    var tin = String(value || "").replace(/\D/g, "");
    if (DEMO_COMPANIES[tin]) return true;
    var d = tin.split("").map(Number);
    if (d.length === 10) {
      var c10 = [2, 4, 10, 3, 5, 9, 4, 6, 8];
      var s10 = c10.reduce(function (sum, weight, i) { return sum + weight * d[i]; }, 0);
      return (s10 % 11 % 10) === d[9];
    }
    if (d.length === 12) {
      var c11 = [7, 2, 4, 10, 3, 5, 9, 4, 6, 8];
      var c12 = [3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8];
      var s11 = c11.reduce(function (sum, weight, i) { return sum + weight * d[i]; }, 0);
      var s12 = c12.reduce(function (sum, weight, i) { return sum + weight * d[i]; }, 0);
      return (s11 % 11 % 10) === d[10] && (s12 % 11 % 10) === d[11];
    }
    return false;
  }
  function companyForInn(tin) {
    return copy(DEMO_COMPANIES[tin] || newCompany(tin));
  }
  function icon(name) {
    var paths = {
      home: '<path d="m3 10 9-7 9 7v10a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z"/>',
      list: '<path d="M8 6h12M8 12h12M8 18h12"/><path d="M3.5 6h.01M3.5 12h.01M3.5 18h.01"/>',
      file: '<path d="M6 3h8l5 5v13H6a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2Z"/><path d="M14 3v6h6M8 13h8M8 17h8"/>',
      help: '<circle cx="12" cy="12" r="9"/><path d="M9.6 9a2.5 2.5 0 1 1 4.4 1.6c-.9.8-2 1.2-2 2.9M12 17h.01"/>',
      bank: '<path d="m3 9 9-6 9 6M5 10v8M10 10v8M14 10v8M19 10v8M3 21h18M4 18h16"/>',
      clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
      user: '<circle cx="12" cy="8" r="3.5"/><path d="M5 20c.7-3.4 3-5 7-5s6.3 1.6 7 5"/>',
      plus: '<path d="M12 5v14M5 12h14"/>',
      arrow: '<path d="M5 12h14M13 6l6 6-6 6"/>',
      check: '<path d="m5 12 4 4L19 6"/>',
      bag: '<path d="M4 7h16v14H4z"/><path d="M9 7V5a3 3 0 0 1 6 0v2M4 11h16"/>',
      search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4 4"/>',
      chevron: '<path d="m6 9 6 6 6-6"/>'
    };
    return '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + (paths[name] || paths.file) + "</svg>";
  }
  function formatRole(role) { return role === "staff" ? "Сотрудник банка" : "Клиент"; }
  function applicantRelation(value) {
    return ({ owner: "Собственник или директор", finance: "Бухгалтер или финдиректор", employee: "Другой сотрудник" })[value] || "Собственник или директор";
  }
  function termText(value) { return Number(value) + " " + (Number(value) % 10 === 1 && Number(value) % 100 !== 11 ? "мес." : "мес."); }
  function navButton(label, view, iconName, active, mobile) {
    return '<button class="nav-item ' + (active ? "active" : "") + '" type="button" data-action="nav" data-view="' + esc(view) + '" aria-label="' + esc(label) + '">' +
      icon(iconName) + '<span>' + esc(label) + "</span></button>";
  }
  function stageTitle(stage) {
    return ({ product: "Подбор продукта", result: "Подбор продукта", company: "Компания", applicant: "Заявитель и выручка", consent: "Подтверждения" })[stage] || "Заявка";
  }
  function stageNumber(stage) { return ({ product: 1, result: 1, company: 2, applicant: 3, consent: 4 })[stage] || 1; }
  function helpModal(title, body) {
    modalMarkup = '<div class="modal-backdrop" data-action="close-modal"><div class="modal" role="dialog" aria-modal="true" aria-labelledby="modal-title">' +
      '<div class="modal-head"><h2 id="modal-title">' + esc(title) + '</h2><button class="modal-close" type="button" data-action="close-modal" aria-label="Закрыть">×</button></div>' +
      '<div class="modal-body">' + body + '</div><div class="modal-actions"><button class="btn btn-secondary" type="button" data-action="close-modal">Понятно</button></div></div></div>';
    render();
  }
  function modalClose() { modalMarkup = ""; render(); }

  function visibleApps() {
    return db.session && db.session.role === "staff" ? db.apps.slice().sort(function (a, b) { return new Date(b.createdAt) - new Date(a.createdAt); }) : appsForSession();
  }
  function appCompanyName(app) { return app.company ? app.company.name : "Новая заявка"; }
  function appAmount(app) {
    if (app.backendView && app.backendView.data && app.backendView.data.financing.amount_rub != null) return app.backendView.data.financing.amount_rub;
    if (app.decision && app.decision.amount) return app.decision.amount;
    return app.form ? app.form.amount : 0;
  }
  function appProductName(app) {
    if (app.backendSessionId) return "Анкета в работе";
    if (app.decision && app.decision.productTitle) return app.decision.productTitle;
    return (getProduct(app) || PRODUCTS.single).title;
  }
  function appDate(app) { return dateOnly(app.createdAt); }
  function openApp(app) {
    db.currentAppId = app.id;
    if (app.status === "draft") db.view = "wizard";
    else if (app.status === "accepted" || app.status === "signed") db.view = "contract";
    else db.view = "tracker";
    saveDb();
    render();
    if (app.backendSessionId) loadBackendSession(app);
  }
  function appTable(apps, compact) {
    if (!apps.length) return '<div class="empty-state"><div class="empty-icon">▤</div><h3>Заявок пока нет</h3><p>Подберите кредит — мы сохраним черновик и покажем его здесь.</p><button class="btn btn-primary btn-small" type="button" data-action="start-new">Подобрать кредит</button></div>';
    return '<div class="table-wrap"><table><thead><tr><th>Заявка</th><th>Цель</th><th>Сумма</th><th>Статус</th><th></th></tr></thead><tbody>' +
      apps.map(function (app) {
        var goal = app.form && app.form.purpose ? purposeLabel(app.form.purpose) : "Подбор продукта";
        return '<tr class="row-click" data-action="open-app" data-id="' + esc(app.id) + '" tabindex="0"><td><div class="table-title">№ ' + esc(displayId(app)) + "</div><div class=\"table-sub\">" + esc(appDate(app)) + "</div></td>" +
          '<td>' + esc(compact ? appProductName(app) : goal) + "</td><td>" + esc(compactMoney(appAmount(app))) + "</td><td>" + statusBadge(app.status) + '</td><td><button class="text-button" type="button" data-action="open-app" data-id="' + esc(app.id) + '">Открыть →</button></td></tr>';
      }).join("") + "</tbody></table></div>";
  }
  function purposeLabel(value) {
    return ({
      stock: "Закупка товара", gap: "Разрыв до выручки", equipment: "Оборудование и ремонт",
      transport: "Транспорт и техника", premises: "Помещение", other: "Другая цель",
      investment: "Развитие бизнеса", property: "Покупка помещения", vehicle: "Транспорт или спецтехника",
      startup: "Запуск бизнеса", refinance: "Рефинансирование", contract: "Контракт или гарантия"
    })[value] || "Подбор продукта";
  }
  function dashboardCallout() {
    var draft = draftForSession();
    var docs = appsForSession().find(function (app) { return app.status === "needs_docs"; });
    var offer = appsForSession().find(function (app) { return isOffer(app.status); });
    var manual = appsForSession().find(function (app) { return app.status === "manual_review"; });
    if (docs) {
      return '<section class="dashboard-callout"><div class="callout-copy"><p class="eyebrow">Нужно действие</p><h2>Добавьте выписку по счёту</h2><p>Чтобы продолжить проверку заявки № ' + esc(displayId(docs)) + ", загрузите PDF-выписку.</p><button class=\"btn btn-primary btn-small\" type=\"button\" data-action=\"open-app\" data-id=\"" + esc(docs.id) + '">Загрузить выписку <span class="icon-arrow">→</span></button></div><div class="callout-art"><div class="art-circle"></div><div class="art-card"><b>Альфа-Банк</b><span>ДЛЯ БИЗНЕСА</span></div><div class="art-coin">✓</div></div></section>';
    }
    if (offer) {
      return '<section class="dashboard-callout"><div class="callout-copy"><p class="eyebrow">Предварительный подбор</p><h2>' + (offer.status === "approved" ? "Подходящий вариант найден" : "Есть вариант с другими условиями") + '</h2><p>' + esc(appCompanyName(offer)) + " · " + esc(compactMoney(appAmount(offer))) + " · посмотрите условия и выберите, что делать дальше.</p><button class=\"btn btn-primary btn-small\" type=\"button\" data-action=\"open-app\" data-id=\"" + esc(offer.id) + '">Посмотреть условия <span class="icon-arrow">→</span></button></div><div class="callout-art"><div class="art-circle"></div><div class="art-card"><b>Альфа-Банк</b><span>ВАРИАНТ</span></div><div class="art-coin">✓</div></div></section>';
    }
    if (manual) {
      return '<section class="dashboard-callout"><div class="callout-copy"><p class="eyebrow">Заявка проверяется</p><h2>Заявка проходит дополнительную проверку</h2><p>Решение по заявке № ' + esc(displayId(manual)) + " появится здесь. Можно вернуться позже.</p><button class=\"btn btn-secondary btn-small\" type=\"button\" data-action=\"open-app\" data-id=\"" + esc(manual.id) + '">Открыть трекер</button></div><div class="callout-art"><div class="art-circle"></div><div class="art-card"><b>Альфа-Банк</b><span>ПРОВЕРКА</span></div></div></section>';
    }
    if (draft) {
      return '<section class="dashboard-callout"><div class="callout-copy"><p class="eyebrow">Черновик сохранён</p><h2>Продолжите оформление анкеты</h2><p>' + esc(draft.company ? draft.company.name : "Ответы сохранены") + ' · шаг «' + esc(stageTitle(draft.wizardStage)) + '».</p><button class="btn btn-primary btn-small" type="button" data-action="resume-draft">Продолжить <span class="icon-arrow">→</span></button></div><div class="callout-art"><div class="art-circle"></div><div class="art-card"><b>Альфа-Банк</b><span>ЧЕРНОВИК</span></div></div></section>';
    }
    return '<section class="dashboard-callout"><div class="callout-copy"><p class="eyebrow">Финансирование для бизнеса</p><h2>Подберите вариант под свою задачу</h2><p>Ответьте на несколько вопросов — мы покажем предварительные варианты и подскажем, что уточнить.</p><button class="btn btn-primary btn-small" type="button" data-action="start-new">Начать анкету <span class="icon-arrow">→</span></button></div><div class="callout-art" aria-hidden="true"><div class="art-circle"></div><div class="art-card"><b>Альфа-Банк</b><span>ДЛЯ БИЗНЕСА</span></div><div class="art-coin">А</div></div></section>';
  }
  function dashboardStats(apps) {
    var active = apps.filter(function (app) { return isPending(app.status) || app.status === "draft"; }).length;
    var amount = apps.reduce(function (sum, app) { return sum + (Number(appAmount(app)) || 0); }, 0);
    var docs = apps.filter(function (app) { return app.status === "needs_docs"; }).length;
    return '<div class="stats-grid"><section class="panel stat-card"><div class="stat-label">Активные заявки</div><div class="stat-value">' + active + '</div><div class="stat-foot">включая черновики</div></section>' +
      '<section class="panel stat-card"><div class="stat-label">Запрошенная сумма</div><div class="stat-value">' + esc(compactMoney(amount)) + '</div><div class="stat-foot">по всем заявкам</div></section>' +
      '<section class="panel stat-card"><div class="stat-label">Нужно загрузить</div><div class="stat-value">' + docs + '</div><div class="stat-foot">документов</div></section></div>';
  }
  function renderDashboard() {
    var apps = appsForSession();
    return '<div class="page-heading"><div><p class="eyebrow">Добрый день, ' + esc((db.session.name || "Клиент").split(" ")[0]) + '</p><h1>Кредитный кабинет</h1><p class="page-subtitle">' + esc(apps[0] && apps[0].company ? apps[0].company.name : "Управляйте заявками и следите за решением онлайн") + '</p></div>' +
      '<div class="heading-actions"><button class="btn btn-primary" type="button" data-action="' + (draftForSession() ? "resume-draft" : "start-new") + '">' + (draftForSession() ? "Продолжить" : "Новая заявка") + "</button></div></div>" +
      dashboardCallout() + dashboardStats(apps) +
      '<section class="panel panel-pad"><div class="section-head"><div><h2>Мои заявки</h2><p>Заявки и черновики в одном месте</p></div><button class="text-button" type="button" data-action="nav" data-view="applications">Все заявки →</button></div>' +
      appTable(apps.slice(0, 5), false) + '</section><p class="disclaimer">Предварительный подбор — не решение банка. Итоговые условия определяются после проверки заявки.</p>';
  }
  function renderApplications() {
    var apps = visibleApps();
    return '<div class="page-heading"><div><p class="eyebrow">' + (db.session.role === "staff" ? "Рабочее место" : "Кредитный кабинет") + '</p><h1>' + (db.session.role === "staff" ? "Все заявки" : "Мои заявки") + '</h1><p class="page-subtitle">Текущий статус и сумма каждой заявки</p></div>' +
      (db.session.role === "client" ? '<div class="heading-actions"><button class="btn btn-primary" type="button" data-action="' + (draftForSession() ? "resume-draft" : "start-new") + '">' + (draftForSession() ? "Продолжить черновик" : "Подобрать кредит") + "</button></div>" : "") + "</div>" +
      '<section class="panel panel-pad"><div class="section-head"><div><h2>Список заявок</h2><p>' + apps.length + (apps.length === 1 ? " запись" : " записей") + "</p></div></div>" + appTable(apps, true) + '</section>' +
      '<p class="disclaimer">История статусов и подтверждений доступна в профиле.</p>';
  }
  function renderDocuments() {
    var apps = visibleApps().filter(function (app) { return app.status === "needs_docs" || app.documentName; });
    var rows = apps.length ? '<div class="table-wrap"><table><thead><tr><th>Заявка</th><th>Компания</th><th>Документ</th><th>Статус</th><th></th></tr></thead><tbody>' +
      apps.map(function (app) {
        return '<tr><td><div class="table-title">№ ' + esc(displayId(app)) + '</div><div class="table-sub">' + esc(dateOnly(app.createdAt)) + '</div></td><td>' + esc(appCompanyName(app)) + '</td><td>' +
          (app.documentName ? esc(app.documentName) : "Выписка по счёту") + "</td><td>" + statusBadge(app.documentName ? app.status : "needs_docs") +
          '</td><td><button class="text-button" type="button" data-action="open-app" data-id="' + esc(app.id) + '">' + (app.documentName ? "Открыть заявку" : "Загрузить →") + "</button></td></tr>";
      }).join("") + "</tbody></table></div>" :
      '<div class="empty-state"><div class="empty-icon">' + icon("file") + '</div><h3>Документы не запрашивались</h3><p>Если для заявки понадобится выписка, запрос появится здесь.</p><button class="btn btn-secondary btn-small" type="button" data-action="nav" data-view="applications">К заявкам</button></div>';
    return '<div class="page-heading"><div><p class="eyebrow">Кредитный кабинет</p><h1>Документы</h1><p class="page-subtitle">Запрошенные выписки по заявкам</p></div></div>' +
      '<section class="panel panel-pad"><div class="section-head"><div><h2>Документы по заявкам</h2><p>Выбранные документы по заявкам</p></div></div>' + rows + '</section><p class="disclaimer">Для оценки выберите PDF-выписку. В расчёте учитывается указанная выручка, содержимое файла не анализируется.</p>';
  }
  function renderShell(content, active) {
    var staff = db.session.role === "staff";
    var side = staff
      ? navButton("Очередь на проверку", "employee", "bank", active === "employee") + navButton("Все заявки", "applications", "list", active === "applications") + navButton("Документы", "documents", "file", active === "documents") + navButton("Подсказки", "help", "help", false)
      : navButton("Обзор", "dashboard", "home", active === "dashboard") + navButton("Заявки", "applications", "list", active === "applications") + navButton("Документы", "documents", "file", active === "documents") + navButton("Помощь", "help", "help", false);
    var mobileViews = staff
      ? navButton("Очередь", "employee", "bank", active === "employee", true) + navButton("Заявки", "applications", "list", active === "applications", true) + navButton("Документы", "documents", "file", active === "documents", true) + navButton("Помощь", "help", "help", false, true)
      : navButton("Обзор", "dashboard", "home", active === "dashboard", true) + navButton("Заявки", "applications", "list", active === "applications", true) + navButton("Новая", "new", "plus", active === "wizard", true) + navButton("Документы", "documents", "file", active === "documents", true);
    var title = staff ? "Мария Волкова" : "Алексей Смирнов";
    return '<header class="topbar"><a class="brand" href="#" data-action="nav" data-view="dashboard"><span class="brand-mark">А</span> Альфа-Банк</a>' +
      '<div class="brand-context-nav" aria-label="Направление банка"><span>Частным лицам</span><span class="active" aria-current="page">Малому бизнесу и ИП</span><span>Среднему и крупному бизнесу</span></div>' +
      '<div class="topbar-right"><div class="topbar-links"><span>Кредитование для бизнеса</span><button class="topbar-link" type="button" data-action="help">' + icon("help") + ' <span style="vertical-align:2px">Помощь</span></button></div>' +
      '<div class="user-menu" aria-label="Профиль пользователя"><span class="avatar">' + (staff ? "МВ" : "АС") + '</span><span class="user-caption">' + title + '<small>' + formatRole(db.session.role) + '</small></span></div></div></header>' +
      '<div class="app-shell ' + (active === "dashboard" ? "dashboard-shell" : "") + '"><aside class="sidebar"><div class="side-caption">' + (staff ? "Сотрудник банка" : "Кредитный кабинет") + '</div><nav class="side-nav">' + side + "</nav>" +
      '<div class="side-spacer"></div><div class="side-help"><strong>Нужна помощь?</strong><p>Подскажем, как пройти заявку, и ответим на ваши вопросы.</p><button class="text-button" type="button" data-action="help">Как это работает →</button></div>' +

      '<main class="main ' + (active === "dashboard" ? "dashboard-main" : "") + '">' + content + '<p class="footer-note">Предварительный подбор — не решение банка.</p></main></div>' +
      '<nav class="mobile-nav ' + (staff ? "staff" : "") + (active === "dashboard" ? " dashboard-nav" : "") + '" aria-label="Основная навигация">' + mobileViews + "</nav>";
  }
  function render() {
    if (!appRoot) return;
    var view = db.view || (db.session.role === "staff" ? "employee" : "dashboard");
    var content = "";
    var active = view;
    if (view === "wizard") {
      var draft = currentDraft();
      if (!draft) { db.view = "dashboard"; saveDb(); content = renderDashboard(); active = "dashboard"; }
      else content = renderWizard(draft);
    } else if (view === "applications") {
      content = renderApplications();
    } else if (view === "documents") {
      content = renderDocuments();
    } else if (view === "tracker") {
      var tracked = currentApp();
      if (!tracked) { db.view = "applications"; saveDb(); content = renderApplications(); active = "applications"; }
      else content = renderTracker(tracked);
    } else if (view === "contract") {
      var contractApp = currentApp();
      if (!contractApp) { db.view = "applications"; saveDb(); content = renderApplications(); active = "applications"; }
      else content = renderContract(contractApp);
    } else if (view === "employee" && db.session.role === "staff") {
      content = renderEmployee();
    } else if (view === "dashboard") {
      content = db.session.role === "staff" ? renderEmployee() : renderDashboard();
      active = db.session.role === "staff" ? "employee" : "dashboard";
    } else {
      db.view = "dashboard";
      content = db.session.role === "staff" ? renderEmployee() : renderDashboard();
      active = db.session.role === "staff" ? "employee" : "dashboard";
    }
    appRoot.innerHTML = renderShell(content, active) + modalMarkup;
  }

  var wizardError = "";
  function wizardTop(d) {
    var stage = d.wizardStage || "product";
    var number = stageNumber(stage);
    var labels = ["Продукт", "Компания", "Вы и выручка", "Согласия"];
    return '<div class="wizard-top"><div class="wizard-topline"><span>Шаг ' + number + " из 4 · " + esc(stageTitle(stage)) + '</span><span>Черновик сохраняется автоматически</span></div>' +
      '<div class="progress-track"><div class="progress-fill" style="width:' + (number * 25) + '%"></div></div><div class="wizard-stages">' +
      labels.map(function (label, index) {
        var cls = index + 1 < number ? "done" : index + 1 === number ? "active" : "";
        return '<span class="stage-label ' + cls + '">' + esc(label) + "</span>";
      }).join("") + "</div></div>";
  }
  function quoteCard(d) {
    var quote = selectedQuote(d);
    var form = d.form || {};
    var goal = form.purpose ? purposeLabel(form.purpose) : "Сумма, срок и цель заявки";
    return '<aside class="panel quote-card"><div class="quote-head"><h3>Ваш кредит</h3><span class="help-dot" title="Предварительный расчёт по параметрам анкеты" aria-label="Предварительная оценка">?</span></div>' +
      '<div class="quote-amount" data-quote="amount">' + esc(compactMoney(quote.amount)) + "</div><div class=\"quote-product\" data-quote=\"product\">" + esc(quote.product.title) + "</div>" +
      '<div class="quote-rows"><div class="quote-row"><span>Цель</span><b>' + esc(goal) + "</b></div><div class=\"quote-row\"><span>Срок</span><b data-quote=\"term\">" + esc(termText(quote.months)) + "</b></div>" +
      '<div class="quote-row"><span>Ставка</span><b data-quote="rate">' + quote.product.rate + "% годовых</b></div></div>" +
      '<div class="quote-payment"><small>Примерный платёж в месяц</small><b data-quote="payment">' + esc(money(quote.payment)) + "</b></div>" +
      '<p class="quote-note">Расчёт аннуитетный и предварительный. Итоговые условия определяются после проверки заявки.</p></aside>';
  }
  function wizardLayout(d, body) {
    var stage = d.wizardStage || "product";
    var saved = dateTime(d.updatedAt);
    return '<div class="page-heading"><div><p class="eyebrow">Заявка № ' + esc(displayId(d)) + '</p><h1>' + esc(stage === "product" || stage === "result" ? "Подберём финансирование" : stageTitle(stage)) + '</h1><p class="page-subtitle">Пара коротких шагов — и заявка готова к проверке</p></div><div class="heading-actions"><span class="status-label">Сохранено ' + esc(saved) + '</span></div></div>' +
      '<div class="wizard-layout"><section class="panel wizard-main">' + wizardTop(d) + '<div class="wizard-body">' + body + "</div></section>" + quoteCard(d) + "</div>";
  }
  function optionCard(label, value, group, mark, sub, selected) {
    return '<button class="option-card ' + (selected ? "selected" : "") + '" type="button" data-action="choice" data-group="' + esc(group) + '" data-value="' + esc(value) + '">' +
      '<span class="option-mark">' + esc(mark || "•") + '</span><span class="option-copy"><b>' + esc(label) + (sub ? '<small>' + esc(sub) + "</small>" : "") +
      "</b></span><span class=\"choice-radio\" aria-hidden=\"true\"></span></button>";
  }
  function questionIntro(kicker, title, help) {
    return '<p class="question-kicker">' + esc(kicker) + '</p><h2>' + esc(title) + '</h2><p class="question-help">' + esc(help) + "</p>";
  }
  function renderProductQuestion(d) {
    var form = d.form;
    var index = Number(d.wizardIndex) || 0;
    if (index === 0) {
      var purposes = [
        ["stock", "Закупка товара или сырья", "Чтобы пополнить склад и запасы", "Т"],
        ["gap", "Закрыть разрыв до выручки", "Ненадолго, пока не придёт оплата", "↗"],
        ["equipment", "Оборудование или ремонт", "Обновить технику или помещение", "О"],
        ["transport", "Транспорт или спецтехника", "Купить или обновить транспорт", "Т"],
        ["premises", "Помещение", "Купить, обустроить или расширить", "П"]
      ];
      var out = questionIntro("Вопрос 1 из 5", "На что нужны деньги?", "Выберите задачу, а мы предложим подходящий формат финансирования.");
      out += '<div class="option-grid">' + purposes.map(function (item) {
        return optionCard(item[1], item[0], "purpose", item[3], item[2], form.purpose === item[0]);
      }).join("") + optionCard("Другое", "other", "purpose", "…", "Опишите цель своими словами", form.purpose === "other") + "</div>";
      if (form.purpose === "other") {
        out += '<div class="field" style="margin-top:14px"><label for="other-goal">Коротко опишите цель</label><textarea class="textarea" id="other-goal" data-field="otherGoal" placeholder="Например: хочу купить кофемашину">' + esc(form.otherGoal) + '</textarea><span class="field-hint">После описания выберите подходящую задачу, чтобы продолжить подбор.</span></div>';
        out += '<div class="section-head"><h3 style="font-size:11px;margin:0">Выберите наиболее близкую задачу</h3></div><div class="option-grid">' +
          purposes.filter(function (item) { return item[0] !== "gap"; }).map(function (item) {
            return optionCard(item[1], item[0], "purpose-category", item[3], "", false);
          }).join("") + "</div>";
      }
      return out;
    }
    if (index === 1) {
      return questionIntro("Вопрос 2 из 5", "Как вам нужны деньги?", "Можно получить всю сумму сразу или использовать её по мере необходимости.") +
        '<div class="option-grid">' +
        optionCard("Вся сумма сразу", "all", "moneyUse", "1×", "На одну крупную покупку или развитие", form.moneyUse === "all") +
        optionCard("Частями, когда понадобятся", "parts", "moneyUse", "↗", "Лимит восстанавливается после погашения", form.moneyUse === "parts") +
        optionCard("На несколько дней", "bridge", "moneyUse", "↻", "До поступления оплаты от покупателя", form.moneyUse === "bridge") + "</div>" +
        '<div class="form-actions"><span class="field-hint">Можно вернуться к предыдущему ответу</span><button class="btn btn-secondary" type="button" data-action="wizard-back">Назад</button></div>';
    }
    if (index === 2) {
      var amount = Number(form.amount) || 3000000;
      return questionIntro("Вопрос 3 из 5", "Сколько нужно?", "Минимальная сумма — 300 тыс. ₽. Если понадобится больше 30 млн ₽, мы учтём возможность залога.") +
        '<div class="field-inline"><div class="field"><label for="amount-number">Сумма кредита</label><div class="input-wrap"><input class="input" id="amount-number" type="number" min="300000" max="150000000" step="100000" data-field="amount" value="' + amount + '" /><span class="input-suffix">₽</span></div></div><div class="field"><span class="field-label">От 300 тыс. ₽</span><span class="field-hint">До 150 млн ₽ с залогом</span></div></div>' +
        '<input class="range" type="range" min="300000" max="150000000" step="100000" value="' + amount + '" data-field="amount" aria-label="Сумма кредита" /><div class="range-scale"><span>300 тыс. ₽</span><span>150 млн ₽</span></div>' +
        '<div class="quick-values">' + [1000000, 3000000, 5000000, 15000000, 35000000].map(function (value) {
          return '<button class="quick-chip ' + (amount === value ? "active" : "") + '" type="button" data-action="quick-value" data-field="amount" data-value="' + value + '">' + esc(compactMoney(value)) + "</button>";
        }).join("") + '</div><div class="form-actions"><button class="btn btn-secondary" type="button" data-action="wizard-back">Назад</button><button class="btn btn-primary" type="button" data-action="wizard-next">Продолжить</button></div>';
    }
    if (index === 3) {
      var product = PRODUCTS[productKey(form)];
      var months = Math.max(product.minTerm, Math.min(product.maxTerm, Number(form.term) || 24));
      if (Number(form.term) !== months) { form.term = months; d.updatedAt = isoNow(); saveDb(); }
      var maxTerm = product.maxTerm;
      return questionIntro("Вопрос 4 из 5", "На какой срок?", "Укажите максимальный срок, который вам подходит. Если платёж окажется высоким, рассмотрим этот предел.") +
        '<div class="field-inline"><div class="field"><label for="term-number">Срок кредита</label><div class="input-wrap"><input class="input" id="term-number" type="number" min="' + product.minTerm + '" max="' + maxTerm + '" step="1" data-field="term" value="' + months + '" /><span class="input-suffix">мес.</span></div></div><div class="field"><span class="field-label">До ' + maxTerm + " месяцев</span><span class=\"field-hint\">" + esc(product.title) + "</span></div></div>" +
        '<input class="range" type="range" min="' + product.minTerm + '" max="' + maxTerm + '" step="1" value="' + months + '" data-field="term" aria-label="Срок кредита" /><div class="range-scale"><span>' + product.minTerm + " мес.</span><span>" + maxTerm + " мес.</span></div>" +
        '<div class="quick-values">' + [12, 18, 24, 36, 60].filter(function (value) { return value >= product.minTerm && value <= maxTerm; }).map(function (value) {
          return '<button class="quick-chip ' + (months === value ? "active" : "") + '" type="button" data-action="quick-value" data-field="term" data-value="' + value + '">' + value + " мес.</button>";
        }).join("") + '</div><div class="form-actions"><button class="btn btn-secondary" type="button" data-action="wizard-back">Назад</button><button class="btn btn-primary" type="button" data-action="wizard-next">Продолжить</button></div>';
    }
    var collateralOptions = [
      ["none", "Нет", "Подберём без залога", "—"],
      ["property", "Недвижимость", "Помещение или другая недвижимость", "Н"],
      ["vehicle", "Транспорт или техника", "Автомобиль или спецтехника", "Т"],
      ["unknown", "Не знаю", "Можно решить это позже", "?" ]
    ];
    return questionIntro("Вопрос 5 из 5", "Есть имущество, которое можно оставить в залог?", "Ответ поможет понять, какой лимит может подойти. Это ни к чему не обязывает.") +
      '<div class="option-grid">' + collateralOptions.map(function (item) {
        return optionCard(item[1], item[0], "collateral", item[3], item[2], form.collateral === item[0]);
      }).join("") + "</div>" +
      '<div class="form-actions"><button class="btn btn-secondary" type="button" data-action="wizard-back">Назад</button><span class="field-hint">Ответ можно изменить в любой момент</span></div>';
  }
  function renderProductResult(d) {
    var product = PRODUCTS[productKey(d.form)];
    var alternative = PRODUCTS[product.alt] || PRODUCTS.single;
    var quote = selectedQuote(d);
    return questionIntro("Подбор готов", "Вот что может подойти", "По вашим ответам мы подобрали вариант и добавили альтернативу для сравнения.") +
      '<div class="result-product recommended"><div class="result-titleline"><div><span class="badge badge-success">Подходит по ответам</span><h3 style="margin-top:8px">' + esc(product.title) + '</h3></div><div class="rate-large">' + product.rate + "%</div></div><p>" + esc(product.note) + ". " + productWhy(d.form, productKey(d.form)) + '</p><div class="result-terms"><span>Сумма <b>' + esc(compactMoney(quote.amount)) + '</b></span><span>Срок <b>' + esc(termText(quote.months)) + '</b></span><span>Платёж <b>' + esc(money(quote.payment)) + "</b></span></div></div>" +
      '<div class="result-product"><div class="result-titleline"><div><span class="badge badge-neutral">Альтернатива</span><h3 style="margin-top:8px">' + esc(alternative.title) + '</h3></div><div class="rate-large">' + alternative.rate + "%</div></div><p>" + esc(alternative.note) + '. Можно сравнить условия после подбора.</p><div class="result-terms"><span>Сумма <b>' + esc(compactMoney(quote.amount)) + '</b></span><span>Ставка <b>' + alternative.rate + "%</b></span></div></div>" +
      '<div class="inline-info">Предварительный расчёт по указанным параметрам. Итоговые условия определяются после проверки заявки.</div>' +
      '<div class="form-actions"><button class="btn btn-secondary" type="button" data-action="wizard-back">Изменить ответы</button><button class="btn btn-primary" type="button" data-action="apply-product">Оформить заявку <span class="icon-arrow">→</span></button></div>';
  }
  function productWhy(form, key) {
    if (key === "overdraft") return "Короткий срок поможет закрыть разрыв до поступления выручки.";
    if (key === "line") return "Деньги можно получать частями по мере необходимости.";
    if (key === "secured") return "Сумма или выбранное имущество подходят для варианта с залогом.";
    return form.purpose === "equipment" || form.purpose === "transport" ? "Подходит для крупной покупки или обновления оборудования." : "Вся сумма будет доступна сразу для вашей задачи.";
  }
  function renderCompanyStage(d) {
    var company = d.company;
    var blocked = company && stopReason(company);
    var out = questionIntro("Шаг 2 из 4", "Проверим компанию", "Введите ИНН компании или выберите профиль из списка примеров для кейса.");
    if (!company || (company.syntheticFallback && company.tin !== d.form.tin)) {
      out += '<form id="company-search"><div class="field"><label for="company-inn">ИНН компании</label><div class="field-inline" style="grid-template-columns:minmax(0,1fr) auto"><input class="input" id="company-inn" name="inn" data-field="tin" inputmode="numeric" autocomplete="off" maxlength="12" placeholder="10 или 12 цифр" value="' + esc(d.form.tin) + '" required /><button class="btn btn-dark" type="submit">' + icon("search") + " Найти</button></div>" +
        '<span class="field-hint">Чтобы пройти сценарий, выберите один из профилей компаний ниже.</span></div></form>';
      out += '<div class="field"><span class="field-label">Примеры компаний</span><div class="demo-inn-list">' +
        Object.keys(DEMO_COMPANIES).map(function (tin) { return '<button class="demo-inn" type="button" data-action="demo-inn" data-value="' + tin + '">' + esc(tin) + " · " + esc(DEMO_COMPANIES[tin].name.replace(/[«»"]/g, "")) + "</button>"; }).join("") + "</div></div>";
      if (wizardError) out += '<div class="inline-info danger" role="alert">' + esc(wizardError) + "</div>";
      return out + '<div class="form-actions"><button class="btn btn-secondary" type="button" data-action="wizard-back">Назад к продукту</button></div>';
    }
    if (blocked) {
      out += '<div class="inline-info danger"><b>' + esc(blocked) + '</b> По этому профилю нельзя продолжить оформление заявки.</div>' +
        '<div class="company-card">' + companyCard(company) + "</div>" +
        '<div class="form-actions"><button class="btn btn-secondary" type="button" data-action="change-company">Выбрать другую компанию</button></div>';
      return out;
    }
    out += '<div class="company-card">' + companyCard(company) + "</div>";
    if (company.syntheticFallback) out += '<div class="inline-info">Для этого ИНН нет профиля в сценарии. Выберите компанию из списка примеров.</div>';
    if (company.requiresDocs) out += '<div class="inline-info">В профиле нет сведений о выручке. После отправки заявки можно будет добавить выписку.</div>';
    out += '<div class="inline-info success">Проверьте сведения. Если всё верно, продолжим к данным заявителя.</div>' +
      '<div class="form-actions"><button class="btn btn-secondary" type="button" data-action="change-company">Изменить ИНН</button><button class="btn btn-primary" type="button" data-action="confirm-company">Всё верно, продолжить</button></div>';
    return out;
  }
  function companyCard(company) {
    return '<div class="company-heading"><div><strong>' + esc(company.name) + '</strong><small>' + esc(company.legalType) + " · ИНН " + esc(company.tin) + '</small></div><span class="badge badge-success">Профиль из кейса</span></div>' +
      '<div class="company-grid"><div class="data-pair"><small>Дата регистрации</small><b>' + esc(company.regDate) + '</b></div><div class="data-pair"><small>Регион</small><b>' + esc(company.region) + '</b></div>' +
      '<div class="data-pair"><small>Вид деятельности</small><b>' + esc(company.activity) + '</b></div><div class="data-pair"><small>Руководитель</small><b>' + esc(company.director) + '</b></div>' +
      '<div class="data-pair"><small>Срок работы</small><b>' + Number(company.ageMonths) + " мес.</b></div><div class=\"data-pair\"><small>Статус</small><b>" + (company.liquidating ? "В ликвидации" : "Действует") + "</b></div></div>";
  }
  function renderApplicantStage(d) {
    var company = d.company;
    var form = d.form;
    var revenue = Number(form.monthlyRevenue) || Number(company.monthlyRevenue) || 0;
    var out = questionIntro("Шаг 3 из 4", "Кто подаёт заявку?", "Укажите сведения заявителя для предварительной оценки заявки.");
    out += '<div class="field"><label for="applicant-name">ФИО заявителя</label><input class="input" id="applicant-name" type="text" data-field="applicantName" value="' + esc(form.applicantName || company.director) + '" placeholder="Имя Фамилия" /></div>';
    out += '<div class="field"><span class="field-label">Кем вы приходитесь компании?</span><div class="option-grid" style="margin-top:7px">' +
      optionCard("Собственник или директор", "owner", "applicantRole", "С", "", form.applicantRole === "owner") +
      optionCard("Бухгалтер или финдиректор", "finance", "applicantRole", "Ф", "", form.applicantRole === "finance") +
      optionCard("Другой сотрудник", "employee", "applicantRole", "Д", "", form.applicantRole === "employee") + "</div></div>";
    if (form.applicantRole !== "owner") {
      out += '<div class="checkbox-row"><input type="checkbox" id="power-of-attorney" data-field="powerOfAttorney" ' + (form.powerOfAttorney ? "checked" : "") + ' /><label for="power-of-attorney"><strong>У меня есть доверенность</strong><br />Она подтверждает право действовать от имени компании.</label></div>';
    }
    if (company.hasReport && !form.revenueOverride) {
      out += '<div class="company-card"><div class="company-heading"><div><strong>Выручка за 2025 год</strong><small>Сведения о выручке в профиле компании</small></div><span class="badge badge-neutral">Проверьте</span></div>' +
        '<div style="font-size:18px;font-weight:700;letter-spacing:-.03em">' + esc(money(company.annualRevenue)) + '</div><p class="field-hint" style="margin:4px 0 0">Это ' + esc(money(company.monthlyRevenue)) + " в среднем за месяц.</p></div>" +
        '<button class="text-button" type="button" data-action="edit-revenue">Указать другую выручку</button>';
      form.monthlyRevenue = Number(form.monthlyRevenue) || Number(company.monthlyRevenue);
    } else {
      out += '<div class="field" style="margin-top:15px"><label for="monthly-revenue">' + (company.hasReport ? "Средняя выручка в месяц" : "Средняя выручка в месяц") + '</label><div class="input-wrap"><input class="input" id="monthly-revenue" type="number" min="0" step="10000" data-field="monthlyRevenue" value="' + revenue + '" placeholder="Например, 450000" /><span class="input-suffix">₽</span></div>' +
        '<span class="field-hint">' + (company.hasReport ? "Значение скоринга можно уточнить." : "Укажите примерную среднемесячную выручку.") + "</span></div>";
    }
    out += '<div class="field" style="margin-top:15px"><label for="current-payments">Сколько в месяц платите по другим кредитам?</label><div class="input-wrap"><input class="input" id="current-payments" type="number" min="0" step="1000" data-field="currentPayments" value="' + (Number(form.currentPayments) || 0) + '" /><span class="input-suffix">₽</span></div><span class="field-hint">Если других кредитов нет — оставьте 0 ₽.</span></div>';
    out += '<div class="form-actions"><button class="btn btn-secondary" type="button" data-action="wizard-back">Назад</button><button class="btn btn-primary" type="button" data-action="to-consent">Продолжить</button></div>';
    return out;
  }
  function renderConsentStage(d) {
    var quote = selectedQuote(d);
    var company = d.company;
    var applicant = d.form.applicantName || company.director;
    var out = questionIntro("Шаг 4 из 4", "Проверьте данные", "Проверьте данные и подтвердите ознакомление с предварительными условиями.");
    out += '<form id="consent-form"><div class="checkbox-row"><input type="checkbox" id="consent-bki" data-field="consentBki" ' + (d.form.consentBki ? "checked" : "") + ' required /><label for="consent-bki"><strong>Подтверждение для предварительной оценки</strong><br />Подтверждаю, что сведения анкеты указаны верно. <button class="text-button" type="button" data-action="legal-info" data-kind="bki">Подробнее</button></label></div>' +
      '<div class="checkbox-row"><input type="checkbox" id="consent-privacy" data-field="consentPrivacy" ' + (d.form.consentPrivacy ? "checked" : "") + ' required /><label for="consent-privacy"><strong>Ознакомление с условиями подбора</strong><br />Понимаю, что результат носит предварительный характер. <button class="text-button" type="button" data-action="legal-info" data-kind="privacy">Подробнее</button></label></div>' +
      '<div class="inline-info">Проверка в бюро кредитных историй не запускается. Для подачи реальной заявки потребуется отдельное согласие.</div>' +
      '<div class="summary-list"><div class="summary-line"><span>Продукт</span><b>' + esc(quote.product.title) + '</b></div><div class="summary-line"><span>Сумма</span><b>' + esc(money(quote.amount)) + '</b></div><div class="summary-line"><span>Срок</span><b>' + esc(termText(quote.months)) + '</b></div>' +
      '<div class="summary-line"><span>Компания</span><b>' + esc(company.name) + '</b></div><div class="summary-line"><span>Заявитель</span><b>' + esc(applicant) + '</b></div><div class="summary-line"><span>Выручка в месяц</span><b>' + esc(money(d.form.monthlyRevenue)) + '</b></div></div>' +
      '<div class="form-actions"><button class="btn btn-secondary" type="button" data-action="wizard-back">Назад</button><button class="btn btn-primary" type="submit">Отправить заявку <span class="icon-arrow">→</span></button></div></form>';
    return out;
  }
  function renderWizard(d) {
    if (d.backendSessionId || d.wizardStage === "backend") return renderBackendWizard(d);
    var stage = d.wizardStage || "product";
    var body = "";
    if (stage === "product") body = renderProductQuestion(d);
    else if (stage === "result") body = renderProductResult(d);
    else if (stage === "company") body = renderCompanyStage(d);
    else if (stage === "company-blocked") body = renderCompanyStage(d);
    else if (stage === "applicant") body = renderApplicantStage(d);
    else if (stage === "consent") body = renderConsentStage(d);
    return wizardLayout(d, body);
  }

  function backendValue(value) {
    if (value == null || value === "") return "—";
    if (typeof value === "number") return money(value);
    if (value === "yes") return "Да";
    if (value === "no") return "Нет";
    if (value === "unknown") return "Не знаю";
    return String(value);
  }
  function backendQuestion(app, question) {
    var previousAnswer = (app.backendAnswers || []).find(function (item) { return item.id === question.id; });
    var editing = app.backendEditQuestionId === question.id;
    var out = questionIntro(editing ? "Изменить ответ" : "Анкета", question.text,
      "Ответы сохраняются в заявке. Если точного ответа нет, выберите «Не знаю».");
    if (question.type === "choice") {
      out += '<div class="option-grid">' + (question.options || []).map(function (option) {
        return optionCard(option.label, option.value, "backend", "•", "", previousAnswer && previousAnswer.value === option.value).replace('data-action="choice" data-group="backend"', 'data-action="backend-answer" data-question="' + esc(question.id) + '"');
      }).join("") + "</div>";
    } else {
      var type = question.type === "date" ? "date" : question.type === "text" ? "text" : "number";
      out += '<form id="backend-answer-form" data-question="' + esc(question.id) + '"><div class="field"><label for="backend-answer">Ваш ответ</label>';
      if (type === "text") out += '<textarea class="textarea" id="backend-answer" name="answer" maxlength="' + Number(question.max_length || 2000) + '" required>' + esc(previousAnswer && previousAnswer.value) + '</textarea>';
      else out += '<input class="input" id="backend-answer" name="answer" type="' + type + '" ' + (type === "number" ? 'step="any" ' + (question.minimum != null ? 'min="' + Number(question.minimum) + '" ' : "") + (question.maximum != null ? 'max="' + Number(question.maximum) + '"' : "") : "") + (previousAnswer && previousAnswer.value != null ? ' value="' + esc(previousAnswer.value) + '"' : "") + ' required />';
      out += '</div><button class="btn btn-primary" type="submit">Сохранить ответ <span class="icon-arrow">→</span></button></form>';
    }
    if (question.allow_unknown) out += '<button class="btn btn-quiet" type="button" data-action="backend-unknown" data-question="' + esc(question.id) + '">Не знаю / пропустить</button>';
    var info = app.backendView.data && app.backendView.data.company;
    if (info && info.inn) out += '<div class="inline-info">ИНН в анкете: ' + esc(info.inn) + '. После заполнения можно отдельно запросить общедоступные сведения о регистрации.</div>';
    return out;
  }
  function backendAnswerText(question, value) {
    if (value == null || value === "unknown") return "Не знаю";
    var option = (question.options || []).find(function (item) { return item.value === value; });
    return option ? option.label : backendValue(value);
  }
  function backendHistory(app) {
    var answers = app.backendAnswers || [], current = currentBackendQuestion(app);
    if (!answers.length) return "";
    return '<details class="backend-history"><summary>Ваши ответы (' + answers.length + ')</summary><div class="backend-history-list">' + answers.map(function (answer) {
      var question = (app.backendQuestions || []).find(function (item) { return item.id === answer.id; });
      if (!question) return "";
      return '<div class="backend-history-item"><div><b>' + esc(question.text) + '</b><small>' + esc(backendAnswerText(question, answer.value)) + '</small></div><button class="text-button" type="button" data-action="backend-edit-answer" data-question="' + esc(answer.id) + '">Изменить</button></div>';
    }).join("") + '</div></details>';
  }
  function backendRecommendation(app) {
    var view = app.backendView || {}, recommendation = view.recommendation || {};
    var names = { R01: "Есть предварительный кандидат", R02: "Нужно уточнить условия", R03: "Варианты исключены правилами" };
    var candidates = recommendation.candidates || [];
    var out = questionIntro("Анкета заполнена · " + (recommendation.status || ""), names[recommendation.status] || "Предварительный подбор завершён",
      "Это результат формальных правил анкеты, а не решение банка и не обещание выдачи кредита.");
    out += '<div class="inline-info danger"><b>Подбор носит предварительный характер. Итоговое решение и условия определяются банком после проверки.</div>';
    if (candidates.length) out += '<div class="backend-candidates">' + candidates.map(function (candidate) {
      return '<article class="result-product"><div class="result-titleline"><div><span class="badge ' + (candidate.status === "candidate" ? "badge-success" : "badge-neutral") + '">' + esc(candidate.status === "candidate" ? "Предварительный кандидат" : candidate.status === "excluded" ? "Исключён" : candidate.status === "separate" ? "Отдельная программа" : "Нужно уточнить") + '</span><h3 style="margin-top:8px">' + esc(candidate.name) + '</h3></div></div><p>' + esc(candidate.reason) + '</p>' + ((candidate.checks || []).length ? '<ul>' + candidate.checks.map(function (check) { return '<li>' + esc(check) + '</li>'; }).join("") + '</ul>' : "") + '</article>';
    }).join("") + '</div>';
    var issues = (recommendation.issues || []).concat(recommendation.notes || []);
    if (issues.length) out += '<section class="panel panel-pad backend-list"><h3>Что требует уточнения</h3><ul>' + issues.map(function (issue) { return '<li>' + esc(issue) + '</li>'; }).join("") + '</ul></section>';
    var company = view.data && view.data.company || {};
    out += backendRegistryPanel(app, company);
    var scoring = view.scoring;
    if (scoring) {
      var prediction = scoring.prediction;
      out += '<section class="panel panel-pad backend-list"><h3>Индекс финансовой устойчивости</h3><p>' + esc(scoring.status === "ready" && prediction ? prediction.meaning : (scoring.error && scoring.error.message) || "Недостаточно данных для оценки.") + '</p>' + (prediction ? '<div class="summary-line"><span>Вероятность финансовой устойчивости</span><b>' + Math.round(prediction.probability_financial_stability * 100) + '%</b></div>' : "") + '<p class="disclaimer">Показатель носит справочный характер и не является решением банка.</p></section>';
    } else if (company.inn && company.entities && company.entities.some(function (entity) { return entity.entity_type === "legal"; })) {
      out += '<div class="backend-secondary-action"><p>Дополнительно оцените финансовую устойчивость компании по доступной отчётности.</p><button class="btn btn-secondary" type="button" data-action="backend-score">Оценить фин. профиль</button></div>';
    }
    out += backendHistory(app) + '<div class="form-actions"><button class="btn btn-secondary" type="button" data-action="backend-result-refresh">Обновить итог</button><button class="btn btn-secondary" type="button" data-action="start-new">Новая анкета</button><button class="btn btn-danger" type="button" data-action="backend-delete-session">Удалить анкету</button></div>';
    return out;
  }
  function backendRegistryPanel(app, company) {
    var out = '<section class="panel panel-pad backend-list"><h3>Публичные сведения о компании</h3>';
    if (!company.inn) return out + '<p>ИНН в анкете не указан, поэтому запросить сведения пока нельзя.</p></section>';
    if (app.backendJobKind === "registry" && app.backendJob && app.backendJob.captcha) {
      var captcha = app.backendJob.captcha;
      out += '<p>Источник просит подтвердить запрос кодом с картинки. Введите код, показанный источником.</p><img class="backend-captcha" src="' + esc(window.AlfaBackend.url(captcha.image_url)) + '" alt="Код проверки источника" /><form id="backend-captcha-form"><label class="field-label" for="backend-captcha-code">6 цифр</label><input class="input" id="backend-captcha-code" inputmode="numeric" autocomplete="one-time-code" maxlength="6" required /><div class="form-actions"><button class="btn btn-primary" type="submit">Отправить код</button><button class="btn btn-secondary" type="button" data-action="backend-captcha-refresh">Новая картинка</button><button class="btn btn-quiet" type="button" data-action="backend-captcha-skip">Пропустить</button></div></form><button class="btn btn-danger btn-small" type="button" data-action="backend-cancel-job" ' + (app.backendLoading ? "disabled" : "") + '>Отменить сбор</button>';
    } else if (app.backendJobKind === "registry" && app.backendJob && (app.backendJob.state === "running" || app.backendJob.state === "waiting_for_captcha")) {
      out += '<p>Собираем сведения из доступных реестров. Источники могут отвечать больше минуты.</p><div class="inline-info">Состояние: ' + esc(app.backendJob.state === "waiting_for_captcha" ? "ожидаем код проверки" : "выполняется") + '</div><button class="btn btn-danger btn-small" type="button" data-action="backend-cancel-job" ' + (app.backendLoading ? "disabled" : "") + '>Отменить сбор</button>';
    } else if (app.backendJobReady && app.backendJob && app.backendJob.result) {
      var entities = app.backendJob.result.entities || [];
      if (entities.length > 1) {
        out += '<p>Найдено несколько записей. Выберите регистрацию, к которой относится заявка:</p><div class="backend-entities">' + entities.map(function (entity) {
          return '<button class="backend-entity" type="button" data-action="backend-attach-registry" data-ogrn="' + esc(entity.ogrn || "") + '"><b>' + esc(entity.name || "Компания / ИП") + '</b><small>ИНН ' + esc(entity.inn || company.inn) + ' · ОГРН ' + esc(entity.ogrn || "не указан") + '</small></button>';
        }).join("") + '</div>';
      } else out += '<p>' + esc(entities[0] && entities[0].name || "Запрос завершён") + '</p><button class="btn btn-primary" type="button" data-action="backend-attach-registry">Добавить к анкете</button>';
      if (app.backendJob.result.issues && app.backendJob.result.issues.length) out += '<ul>' + app.backendJob.result.issues.map(function (issue) { return '<li>' + esc(typeof issue === "string" ? issue : issue.message || JSON.stringify(issue)) + '</li>'; }).join("") + '</ul>';
    } else if (company.lookup_status === "ok" || company.lookup_status === "partial") {
      out += '<p>Статус источников: ' + esc(company.lookup_status === "ok" ? "получены сведения" : "получены частичные сведения") + ' · записей: ' + (company.entities || []).length + '</p><button class="btn btn-secondary" type="button" data-action="backend-refresh">Обновить анкету</button>';
    } else {
      out += '<p>Можно проверить открытые карточки ЕГРЮЛ/ЕГРИП и связанные общедоступные источники. Запрос может занять больше минуты и потребовать ручной код.</p><button class="btn btn-secondary" type="button" data-action="backend-check-registry">Проверить по реестрам</button>';
    }
    return out + '</section>';
  }
  function renderBackendWizard(app) {
    var nextQuestion = currentBackendQuestion(app);
    if (nextQuestion) {
      app.backendQuestions = app.backendQuestions || [];
      if (!app.backendQuestions.some(function (item) { return item.id === nextQuestion.id; })) app.backendQuestions.push(nextQuestion);
    }
    var body = "";
    var editingQuestion = app.backendEditQuestionId && (app.backendQuestions || []).find(function (item) { return item.id === app.backendEditQuestionId; });
    if (app.backendLoading) body = '<div class="empty-state"><div class="empty-icon">…</div><h3>Подготавливаем анкету</h3><p>Подождите, загружаем анкету.</p></div>';
    else if (app.backendError && !app.backendView) body = '<div class="inline-info danger" role="alert">' + esc(app.backendError) + '</div><button class="btn btn-secondary" type="button" data-action="backend-refresh">Повторить</button>';
    else if (editingQuestion) body = backendQuestion(app, editingQuestion) + backendHistory(app);
    else if (app.backendJobKind === "scoring" && app.backendJob && (app.backendJob.state === "running" || app.backendJob.state === "waiting_for_captcha")) body = questionIntro("Обработка запроса", "Оцениваем финансовый профиль", "Оцениваем финансовый профиль. Результат носит предварительный характер и не заменяет решение банка.") + '<button class="btn btn-danger btn-small" type="button" data-action="backend-cancel-job" ' + (app.backendLoading ? "disabled" : "") + '>Отменить расчёт</button>' + (app.backendJob.captcha ? '<img class="backend-captcha" src="' + esc(window.AlfaBackend.url(app.backendJob.captcha.image_url)) + '" alt="Код проверки источника" /><form id="backend-captcha-form"><label class="field-label" for="backend-captcha-code">6 цифр</label><input class="input" id="backend-captcha-code" inputmode="numeric" autocomplete="one-time-code" maxlength="6" required /><div class="form-actions"><button class="btn btn-primary" type="submit">Отправить код</button><button class="btn btn-secondary" type="button" data-action="backend-captcha-refresh">Новая картинка</button><button class="btn btn-quiet" type="button" data-action="backend-captcha-skip">Пропустить</button></div></form>' : "");
    else if (nextQuestion) body = backendQuestion(app, app.backendEditQuestionId && app.backendQuestions.find(function (item) { return item.id === app.backendEditQuestionId; }) || nextQuestion) + backendHistory(app);
    else if (app.backendView && app.backendView.state === "ready") body = backendRecommendation(app);
    else body = '<div class="inline-info">Анкета загружается. Если это сообщение не исчезло, обновите её.</div><button class="btn btn-secondary" type="button" data-action="backend-refresh">Обновить</button>';
    if (app.backendError && app.backendView && !app.backendLoading) body = '<div class="inline-info danger" role="alert">' + esc(app.backendError) + ' <button class="text-button" type="button" data-action="backend-refresh">Обновить данные</button></div>' + body;
    var amount = app.backendView && app.backendView.data && app.backendView.data.financing.amount_rub;
    var company = app.backendView && app.backendView.data && app.backendView.data.company;
    var selectedName = app.company && app.company.name || "Не подтверждена";
    var deleteControl = app.backendView && app.backendView.state !== "ready" ? '<button class="btn btn-danger btn-small" type="button" data-action="backend-delete-session" ' + (app.backendLoading ? "disabled" : "") + '>Удалить анкету</button>' : "";
    var side = '<aside class="panel backend-summary"><p class="question-kicker">Ваша заявка</p><h3>Сводка анкеты</h3><div class="summary-line"><span>Компания</span><b>' + esc(selectedName) + '</b></div>' +
      '<div class="summary-line"><span>ИНН</span><b>' + esc(company && company.inn || "—") + '</b></div><div class="summary-line"><span>Сумма</span><b>' + esc(amount == null ? "Не указана" : money(amount)) + '</b></div>' +
      '<div class="summary-line"><span>Сохранено</span><b>' + esc(dateTime(app.backendView && app.backendView.updated_at || app.updatedAt)) + '</b></div>' + deleteControl + '</aside>';
    var title = app.backendView && app.backendView.state === "ready" ? "Предварительный подбор" : "Анкета финансирования";
    return '<div class="page-heading"><div><p class="eyebrow">Заявка № ' + esc(displayId(app)) + '</p><h1>' + title + '</h1><p class="page-subtitle">Ответьте на вопросы — это поможет подобрать подходящие условия</p></div><div class="heading-actions"></div></div><div class="wizard-layout backend-wizard-layout"><section class="panel wizard-main"><div class="wizard-body">' + body + (app.backendError && !app.backendLoading && !body.includes("role=\"alert\"") ? '<div class="inline-info danger" role="alert">' + esc(app.backendError) + '</div>' : "") + '</div></section>' + side + '</div>';
  }

  function timelineIndex(status) {
    var index = {
      draft: -1, submitted: 0, checking: 1, needs_docs: 2, enrichment: 2, scoring: 3,
      manual_review: 4, approved: 4, counteroffer: 4, declined: 4, accepted: 4,
      rejected_by_client: 4, signed: 4, issued: 4
    };
    return index[status] == null ? 0 : index[status];
  }
  function timelineTime(app, statuses) {
    var event = (app.history || []).slice().reverse().find(function (item) { return statuses.indexOf(item.to) >= 0; });
    return event ? dateTime(event.at) : "";
  }
  function timeline(app) {
    var items = [
      { title: "Заявка отправлена", sub: "Черновик стал заявкой", statuses: ["submitted"] },
      { title: "Проверяем данные", sub: "Смотрим, всё ли заполнено корректно", statuses: ["checking"] },
      { title: "Получаем сведения о компании", sub: app.status === "needs_docs" ? "Ждём выписку по счёту" : "Проверяем сведения о компании", statuses: ["enrichment", "needs_docs"] },
      { title: "Оцениваем заявку", sub: "Рассчитываем предварительную оценку", statuses: ["scoring"] },
      { title: "Результат подбора", sub: app.status === "manual_review" ? "Заявка проходит дополнительную проверку" : "Результат появится здесь", statuses: ["manual_review", "approved", "counteroffer", "declined"] }
    ];
    var at = timelineIndex(app.status);
    return '<div class="timeline">' + items.map(function (item, i) {
      var done = i < at || (i === 4 && ["approved", "counteroffer", "declined", "accepted", "rejected_by_client", "signed", "issued"].indexOf(app.status) >= 0);
      var current = i === at && !done;
      return '<div class="timeline-item ' + (done ? "done" : current ? "current" : "") + '"><span class="timeline-mark">' + (done ? "✓" : "") + '</span><div class="timeline-copy"><b>' + esc(item.title) + "</b><small>" + esc(item.sub) + "</small></div><span class=\"timeline-time\">" + esc(timelineTime(app, item.statuses)) + "</span></div>";
    }).join("") + "</div>";
  }
  function decisionSummary(app) {
    var decision = app.decision || {};
    if (app.status === "needs_docs") {
      return '<section class="panel action-card"><p class="question-kicker">Что нужно от вас</p><h3>Загрузите выписку по счёту</h3><p>Чтобы продолжить оценку, укажите выручку компании и при необходимости добавьте выписку.</p>' +
        (app.documentName ? '<div class="inline-info success">Файл выбран: ' + esc(app.documentName) + "</div>" : "") +
        '<form id="upload-form"><label class="field-label" for="document-file">Выписка в формате PDF</label><input class="file-input" id="document-file" name="file" type="file" accept=".pdf,application/pdf" required />' +
        '<button class="btn btn-primary btn-full" type="submit">Добавить файл</button></form><p class="disclaimer">Содержание PDF не анализируется. Для предварительного расчёта используется указанная выручка.</p></section>';
    }
    if (app.status === "manual_review") {
      return '<section class="panel action-card"><p class="question-kicker">Решение на проверке</p><h3>Заявка направлена на дополнительную проверку</h3><p>Результат проверки появится в кабинете.</p>' +
        (db.session.role === "staff" ? '<button class="btn btn-dark btn-full" type="button" data-action="nav" data-view="employee">Открыть рабочее место</button>' : "") +
        '<p class="disclaimer">Баллы скоринга: ' + Number(scoreApplication(app).total).toFixed(1).replace(".", ",") + " из 100.</p></section>";
    }
    if (app.status === "approved" || app.status === "counteroffer") {
      var isCounter = app.status === "counteroffer";
      return '<section class="panel action-card"><p class="question-kicker">Предварительный подбор</p><h3>' + (isCounter ? "Есть вариант с другой суммой" : "Подходящий вариант найден") + '</h3><div class="decision-banner ' + (isCounter ? "counteroffer" : "approved") + '"><p>' +
        (isCounter ? "Вы просили " + esc(money(app.form.amount)) + " на " + esc(termText(app.form.term)) + ". Можем предложить:" : "Предварительные условия по заявке:") +
        '</p><div class="summary-line"><span>Сумма</span><b>' + esc(money(decision.amount)) + '</b></div><div class="summary-line"><span>Срок</span><b>' + esc(termText(decision.months)) + '</b></div>' +
        '<div class="summary-line"><span>Ставка</span><b>' + decision.rate + "% годовых</b></div><div class=\"summary-line\"><span>Платёж в месяц</span><b>" + esc(money(decision.payment)) + "</b></div></div>" +
        '<p style="margin-top:12px">' + esc(decision.reason || "Сформирован предварительный вариант по данным анкеты.") + "</p>" +
        '<div class="decision-actions"><button class="btn btn-primary" type="button" data-action="accept-offer">Выбрать условия</button>' +
        (isCounter ? '<button class="btn btn-secondary" type="button" data-action="edit-offer">Изменить параметры</button>' : "") +
        '<button class="btn btn-quiet" type="button" data-action="reject-offer">Отказаться</button></div><p class="disclaimer">Это предварительный расчёт, а не предложение банка. Итоговые условия определяются после проверки.</p></section>';
    }
    if (app.status === "declined") {
      return '<section class="panel action-card"><p class="question-kicker">Предварительный подбор</p><h3>Пока не можем предложить кредит</h3><div class="decision-banner declined"><p>' + esc(decision.reason || "Заявка не прошла предварительную оценку.") + '</p></div><p>Можно пересмотреть сумму или снизить текущую нагрузку и подать новую заявку позже.</p><button class="btn btn-secondary btn-full" type="button" data-action="nav" data-view="dashboard">Вернуться в кабинет</button></section>';
    }
    if (["accepted", "signed", "issued"].indexOf(app.status) >= 0) {
      return '<section class="panel action-card"><p class="question-kicker">Следующий шаг</p><h3>' + esc(STATUS_LABEL[app.status]) + '</h3><p>' +
        (app.status === "accepted" ? "Ознакомьтесь с предварительными условиями." : app.status === "signed" ? "Подтверждение сохранено. Заявка переходит к завершающему этапу." : "Этапы рассмотрения заявки завершены.") + '</p><button class="btn btn-primary btn-full" type="button" data-action="open-contract">' + (app.status === "accepted" ? "Посмотреть условия" : "Посмотреть условия") + "</button></section>";
    }
    return '<section class="panel action-card"><p class="question-kicker">Что нужно от вас</p><h3>Проверка идёт</h3><p>Следующий этап появится здесь автоматически. Обновлять страницу не нужно.</p><div class="inline-info">Следующий этап появится здесь автоматически.</div></section>';
  }
  function scoreCard(app) {
    var score = scoreApplication(app);
    return '<section class="panel action-card"><div class="section-head"><h3>Оценка заявки</h3><span class="help-dot" title="Предварительная оценка по данным заявки">?</span></div>' +
      '<div class="score-total"><span>Итоговый балл</span><b>' + Number(score.total).toFixed(1).replace(".", ",") + '</b></div><div class="score-bar"><span style="width:' + Math.max(0, Math.min(100, score.total)) + '%"></span></div>' +
      '<div class="score-list">' + score.factors.map(function (factor) {
        return '<div class="score-factor"><span>' + esc(factor.name) + "<br /><small>" + esc(factor.note) + '</small></span><b>' + Number(factor.value).toFixed(1).replace(".", ",") + " / " + factor.max + "</b></div>";
      }).join("") + "</div>" + (!score.modelUsed ? '<p class="disclaimer">Отчётности нет: оставшиеся факторы пересчитаны пропорционально до 100 баллов.</p>' : "") + "</section>";
  }
  function renderTracker(app) {
    var statusText = app.status === "needs_docs" ? "Нужна выписка" : STATUS_LABEL[app.status] || "Заявка";
    var quote = selectedQuote(app);
    return '<div class="page-heading"><div><p class="eyebrow">Заявка № ' + esc(displayId(app)) + " · " + esc(appCompanyName(app)) + '</p><h1>Статус заявки</h1><p class="page-subtitle">' + esc(appProductName(app)) + " · " + esc(money(appAmount(app))) + '</p></div><div class="heading-actions">' + statusBadge(app.status) + "</div></div>" +
      '<div class="status-layout"><section class="panel status-card"><div class="status-overview"><div><span class="app-id">Подана ' + esc(dateTime(app.submittedAt || app.createdAt)) + '</span><h2>' + esc(statusText) + '</h2><p>Статус заявки обновляется автоматически.</p></div><button class="btn btn-outline btn-small" type="button" data-action="nav" data-view="applications">Все заявки</button></div>' +
      timeline(app) + (app.status === "manual_review" ? '<div class="inline-info">Заявка проходит дополнительную проверку. Результат появится в кабинете.</div>' : "") +
      '<details style="margin-top:12px"><summary class="text-button">История статусов</summary><div class="summary-list">' + (app.history || []).slice().reverse().map(function (event) {
        return '<div class="summary-line"><span>' + esc(dateTime(event.at)) + " · " + esc(event.actor) + '</span><b>' + esc(event.label || STATUS_LABEL[event.to] || event.to) + "</b></div>";
      }).join("") + "</div></details></section>" +
      '<div class="status-side">' + decisionSummary(app) + (app.decision && app.decision.score ? scoreCard(app) : "") +
      '<section class="panel action-card" style="margin-top:13px"><h3>Заявка</h3><div class="summary-line"><span>Сумма</span><b>' + esc(money(app.form.amount)) + '</b></div><div class="summary-line"><span>Срок</span><b>' + esc(termText(app.form.term)) + '</b></div><div class="summary-line"><span>Примерный платёж</span><b>' + esc(money(quote.payment)) + '</b></div><div class="summary-line"><span>Компания</span><b>' + esc(appCompanyName(app)) + "</b></div></section></div></div>";
  }
  function contractStatus(app) {
    if (app.status === "issued") return '<div class="signature-state"><div class="signature-check">✓</div><h2>Этап рассмотрения завершён</h2><p class="page-subtitle">Предварительные условия: ' + esc(money(app.decision.amount)) + " · " + esc(dateTime(app.issuedAt || isoNow())) + '</p><p class="disclaimer">Кредитный договор не оформлен, перечисления средств не было.</p><button class="btn btn-secondary" type="button" data-action="nav" data-view="applications">Вернуться к заявкам</button></div>';
    if (app.status === "signed") return '<div class="signature-state"><div class="signature-check">✓</div><h2>Условия подтверждены</h2><p class="page-subtitle">Заявка перешла к завершающему этапу рассмотрения.</p><p class="disclaimer">Подтверждение ознакомления не является подписанием договора.</p></div>';
    return "";
  }
  function renderContract(app) {
    if (app.status === "issued" || app.status === "signed") {
      return '<div class="page-heading"><div><p class="eyebrow">Заявка № ' + esc(displayId(app)) + '</p><h1>Предварительный расчёт</h1><p class="page-subtitle">Предварительные условия по заявке</p></div></div><section class="panel panel-pad">' + contractStatus(app) + "</section>";
    }
    var decision = app.decision || {};
    return '<div class="page-heading"><div><p class="eyebrow">Заявка № ' + esc(displayId(app)) + '</p><h1>Проверьте условия</h1><p class="page-subtitle">Предварительный расчёт. Он не является офертой или кредитным договором.</p></div><div class="heading-actions">' + statusBadge(app.status) + "</div></div>" +
      '<section class="panel panel-pad"><div class="contract-paper"><div class="contract-brand">Альфа-Банк · для бизнеса</div><p style="text-align:center;color:#888">ПРЕДВАРИТЕЛЬНЫЕ УСЛОВИЯ</p><h2>Условия финансирования</h2><p class="contract-sub">Предпросмотр по заявке № ' + esc(displayId(app)) + '</p>' +
      '<p>Этот расчёт не является кредитным договором. Подтверждение ознакомления не создаёт обязательств.</p>' +
      '<div class="contract-grid"><div class="contract-term"><small>Заёмщик</small><b>' + esc(appCompanyName(app)) + '</b></div><div class="contract-term"><small>Продукт</small><b>' + esc(appProductName(app)) + '</b></div>' +
      '<div class="contract-term"><small>Сумма</small><b>' + esc(money(decision.amount)) + '</b></div><div class="contract-term"><small>Срок</small><b>' + esc(termText(decision.months)) + '</b></div>' +
      '<div class="contract-term"><small>Ставка (предварительно)</small><b>' + decision.rate + "% годовых</b></div><div class=\"contract-term\"><small>Примерный платёж</small><b>" + esc(money(decision.payment)) + "</b></div></div>" +
      '<p>Предварительные условия будут сохранены в заявке. После подтверждения ознакомления статус обновится в кабинете.</p></div>' +
      '<div class="checkbox-row" style="max-width:720px;margin:0 auto 14px"><input type="checkbox" id="contract-check" /><label for="contract-check"><strong>Я ознакомлен с предварительными условиями</strong><br />Подтверждение ознакомления не является подписанием договора.</label></div>' +
      '<div class="heading-actions" style="justify-content:center"><button class="btn btn-secondary" type="button" data-action="nav" data-view="tracker">Назад к заявке</button><button class="btn btn-primary" type="button" data-action="sign-contract">Подтвердить ознакомление</button></div></section>';
  }
  function renderEmployee() {
    var queue = db.apps.filter(function (app) { return app.status === "manual_review"; }).sort(function (a, b) { return new Date(a.createdAt) - new Date(b.createdAt); });
    var selected = queue.find(function (app) { return app.id === db.staffAppId; }) || queue[0] || null;
    if (selected && db.staffAppId !== selected.id) { db.staffAppId = selected.id; saveDb(); }
    var queueHtml = queue.length ? '<div class="queue-list">' + queue.map(function (app) {
      return '<button class="queue-item ' + (selected && app.id === selected.id ? "active" : "") + '" type="button" data-action="staff-select" data-id="' + esc(app.id) + '">' +
        statusBadge(app.status) + "<b>" + esc(appCompanyName(app)) + "</b><small>" + esc(money(app.form.amount)) + " · " + esc(dateTime(app.createdAt)) + "</small></button>";
    }).join("") + "</div>" : '<div class="empty-state"><div class="empty-icon">✓</div><h3>Очередь пуста</h3><p>Заявки со статусом «Ручная проверка» появятся здесь.</p></div>';
    var detail = "";
    if (selected) {
      var score = scoreApplication(selected);
      detail = '<section class="panel staff-detail"><div class="section-head"><div><p class="eyebrow">Заявка № ' + esc(displayId(selected)) + '</p><h2>' + esc(appCompanyName(selected)) + '</h2></div><button class="btn btn-outline btn-small" type="button" data-action="open-app" data-id="' + esc(selected.id) + '">Открыть трекер</button></div>' +
        '<div class="company-card">' + companyCard(selected.company) + "</div>" +
        '<div class="summary-list"><div class="summary-line"><span>Заявитель</span><b>' + esc(selected.form.applicantName || selected.company.director) + '</b></div><div class="summary-line"><span>Роль</span><b>' + esc(applicantRelation(selected.form.applicantRole)) + '</b></div>' +
        '<div class="summary-line"><span>Продукт</span><b>' + esc(appProductName(selected)) + '</b></div><div class="summary-line"><span>Просит</span><b>' + esc(money(selected.form.amount)) + " на " + esc(termText(selected.form.term)) + '</b></div>' +
        '<div class="summary-line"><span>Выручка в месяц</span><b>' + esc(money(selected.form.monthlyRevenue)) + '</b></div><div class="summary-line"><span>Подтверждения</span><b>' + ((selected.consentLog || []).length) + " записи</b></div></div>" +
        '<div style="margin-top:17px"><div class="section-head"><h3>Разбивка скоринга</h3><span class="badge badge-blue">' + Number(score.total).toFixed(1).replace(".", ",") + " / 100</span></div>" + scoreCard(selected).replace('<section class="panel action-card">', '<div class="score-review">').replace("</section>", "</div>") + "</div>" +
        '<form class="staff-form" id="staff-decision-form"><h3>Решение специалиста</h3><div class="staff-form-grid"><div class="field"><label for="staff-amount">Сумма предложения</label><input class="input" id="staff-amount" type="number" min="300000" step="100000" value="' + Math.min(Number(selected.form.amount) || 1000000, 2000000) + '" /></div>' +
        '<div class="field"><label for="staff-term">Срок, месяцев</label><input class="input" id="staff-term" type="number" min="3" max="' + getProduct(selected).maxTerm + '" value="' + Number(selected.form.term || 24) + '" /></div></div>' +
        '<div class="field"><label for="staff-reason">Причина, если нужен отказ</label><input class="input" id="staff-reason" type="text" placeholder="Например: высокая нагрузка на выручку" /></div>' +
        '<div class="staff-action-row"><button class="btn btn-primary btn-small" type="button" data-action="staff-approve">Одобрить</button><button class="btn btn-secondary btn-small" type="button" data-action="staff-counter">Контрпредложение</button><button class="btn btn-danger btn-small" type="button" data-action="staff-decline">Отказать</button></div></form></section>';
    } else {
      detail = '<section class="panel staff-detail"><div class="empty-state"><div class="empty-icon">☷</div><h3>Выберите заявку</h3><p>Данные заявки и предварительная оценка появятся в этой панели.</p></div></section>';
    }
    return '<div class="page-heading"><div><p class="eyebrow">Рабочее место сотрудника</p><h1>Ручная проверка</h1><p class="page-subtitle">Заявки, направленные на дополнительную проверку</p></div><div class="heading-actions"><span class="badge ' + (queue.length ? "badge-progress" : "badge-neutral") + '">' + queue.length + " в очереди</span></div></div>" +
      '<div class="staff-layout"><section class="panel panel-pad"><div class="section-head"><div><h2>Очередь</h2><p>Выберите заявку для проверки</p></div></div>' + queueHtml + "</section>" + detail + "</div>";
  }

  function persistField(field, value) {
    var draft = currentDraft();
    if (!draft || !draft.form) return;
    if (field === "amount" || field === "term" || field === "monthlyRevenue" || field === "currentPayments") {
      var number = Number(value);
      if (Number.isFinite(number)) draft.form[field] = number;
    } else if (field === "powerOfAttorney" || field === "consentBki" || field === "consentPrivacy" || field === "revenueOverride") {
      draft.form[field] = Boolean(value);
    } else {
      draft.form[field] = value;
    }
    draft.updatedAt = isoNow();
    saveDb();
  }
  function refreshQuote() {
    var draft = currentDraft();
    if (!draft) return;
    var quote = selectedQuote(draft);
    var values = {
      amount: compactMoney(quote.amount),
      product: quote.product.title,
      term: termText(quote.months),
      rate: quote.product.rate + "% годовых",
      payment: money(quote.payment)
    };
    Object.keys(values).forEach(function (key) {
      var node = document.querySelector('[data-quote="' + key + '"]');
      if (node) node.textContent = values[key];
    });
  }
  function findCompany(draft, value) {
    var tin = String(value || draft.form.tin || "").replace(/\D/g, "");
    draft.form.tin = tin;
    wizardError = "";
    if (!validInn(tin)) {
      wizardError = "Проверьте ИНН: введите 10 или 12 цифр с корректной контрольной суммой.";
      saveDb();
      render();
      return;
    }
    draft.company = companyForInn(tin);
    draft.form.monthlyRevenue = Number(draft.company.monthlyRevenue) || 0;
    draft.form.currentPayments = Number(draft.company.currentPayments) || 0;
    draft.form.applicantName = draft.company.director || "";
    draft.wizardStage = "company";
    draft.updatedAt = isoNow();
    saveDb();
    render();
  }
  function submitApplication(draft) {
    var form = draft.form;
    if (!draft.company || stopReason(draft.company)) {
      wizardError = stopReason(draft.company) || "Сначала найдите компанию.";
      render();
      return;
    }
    if (!form.consentBki || !form.consentPrivacy) {
      showToast("Подтвердите сведения, чтобы продолжить.");
      return;
    }
    var stamp = isoNow();
    var quote = selectedQuote(draft);
    draft.applicant = {
      name: form.applicantName || draft.company.director,
      relation: form.applicantRole,
      powerOfAttorney: Boolean(form.powerOfAttorney),
      monthlyRevenue: Number(form.monthlyRevenue) || 0,
      currentPayments: Number(form.currentPayments) || 0
    };
    draft.product = { key: productKey(form), title: quote.product.title, rate: quote.product.rate };
    draft.consentLog = [
      { type: "bki", text: consentLabel("bki"), agreed: true, user: db.session.email, at: stamp },
      { type: "privacy", text: consentLabel("privacy"), agreed: true, user: db.session.email, at: stamp }
    ];
    draft.submittedAt = stamp;
    draft.decision = null;
    draft.documentName = "";
    due(draft, 2600);
    setStatus(draft, "submitted", "Клиент", "Заявка отправлена");
    db.currentAppId = draft.id;
    db.view = "tracker";
    saveDb();
    render();
    showToast("Заявка отправлена. Покажем, как меняется статус.");
  }
  function timelineTransition(app) {
    var blocked = stopReason(app.company);
    if (app.status === "submitted") {
      setStatus(app, "checking", "Система", "Проверка данных");
      due(app, 2700);
      return true;
    }
    if (app.status === "checking") {
      if (blocked) {
        app.decision = { kind: "declined", score: scoreApplication(app), reason: blocked + " Это не позволяет продолжить оформление заявки." };
        app.decision.productTitle = appProductName(app);
        setStatus(app, "declined", "Система", "Отказ по стоп-фактору");
      } else if (app.company && app.company.requiresDocs && !app.documentName) {
        setStatus(app, "needs_docs", "Система", "Запрошена выписка по счёту");
        app.nextAt = null;
      } else {
        setStatus(app, "enrichment", "Система", "Получение сведений о компании");
        due(app, 2700);
      }
      return true;
    }
    if (app.status === "enrichment") {
      setStatus(app, "scoring", "Система", "Оценка заявки");
      due(app, 2700);
      return true;
    }
    if (app.status === "scoring") {
      var decision = calculateDecision(app);
      decision.productTitle = appProductName(app);
      app.decision = decision;
      if (decision.kind === "manual_review") {
        setStatus(app, "manual_review", "Система", "Направлена на ручную проверку");
      } else {
        setStatus(app, decision.kind, "Система", decision.kind === "counteroffer" ? "Подготовлен альтернативный вариант" : decision.kind === "approved" ? "Предварительный вариант сформирован" : "Вариант не подобран");
      }
      return true;
    }
    if (app.status === "signed" && app.issueAt && Date.now() >= app.issueAt) {
      app.issuedAt = isoNow();
      setStatus(app, "issued", "Система", "Завершающий этап рассмотрения");
      return true;
    }
    return false;
  }
  function tick() {
    var changed = false;
    var now = Date.now();
    db.apps.forEach(function (app) {
      if (app.nextAt && now >= app.nextAt) {
        app.nextAt = null;
        if (timelineTransition(app)) changed = true;
      }
    });
    if (changed) {
      saveDb();
      if (db.session) render();
    }
  }
  function renderHistory(app) {
    return '<details><summary class="text-button">Показать историю подтверждений и статусов</summary><div class="summary-list">' +
      (app.consentLog || []).map(function (event) {
        return '<div class="summary-line"><span>' + esc(event.text) + '</span><b>' + esc(dateTime(event.at)) + " · " + esc(event.user) + "</b></div>";
      }).join("") + (app.history || []).map(function (event) {
        return '<div class="summary-line"><span>' + esc(event.label || STATUS_LABEL[event.to] || event.to) + '</span><b>' + esc(dateTime(event.at)) + " · " + esc(event.actor) + "</b></div>";
      }).join("") + "</div></details>";
  }
  function signContract(app) {
    var checkbox = document.getElementById("contract-check");
    if (!checkbox || !checkbox.checked) {
      showToast("Подтвердите, что ознакомились с предварительными условиями.");
      return;
    }
    app.signedAt = isoNow();
    app.issueAt = Date.now() + 2100;
    setStatus(app, "signed", "Клиент", "Ознакомление с условиями подтверждено");
    db.view = "contract";
    saveDb();
    render();
    showToast("Условия подтверждены. Статус заявки обновлён.");
  }
  function employeeDecision(kind) {
    var app = db.apps.find(function (item) { return item.id === db.staffAppId && item.status === "manual_review"; });
    if (!app) { showToast("В очереди больше нет этой заявки."); render(); return; }
    var amount = Number(document.getElementById("staff-amount") && document.getElementById("staff-amount").value);
    var months = Number(document.getElementById("staff-term") && document.getElementById("staff-term").value);
    var reason = String(document.getElementById("staff-reason") && document.getElementById("staff-reason").value || "").trim();
    if (kind !== "declined" && (!amount || amount < 300000 || !months)) {
      showToast("Укажите сумму от 300 тыс. ₽ и срок.");
      return;
    }
    if (kind === "declined" && !reason) reason = "После ручной проверки заявку пока нельзя одобрить.";
    var product = getProduct(app);
    if (kind !== "declined") {
      app.decision = {
        kind: kind, score: scoreApplication(app), amount: amount,
        months: months, rate: product.rate, payment: monthPayment(amount, product.rate, months),
        reason: kind === "counteroffer" ? "Специалист предложил изменить сумму или срок, чтобы платёж соответствовал выручке." : "Решение принято специалистом банка.",
        productTitle: product.title, actor: db.session.name
      };
      setStatus(app, kind, db.session.name, kind === "counteroffer" ? "Контрпредложение сотрудника" : "Вариант сформирован сотрудником");
    } else {
      app.decision = { kind: kind, score: scoreApplication(app), reason: reason, productTitle: product.title, actor: db.session.name };
      setStatus(app, "declined", db.session.name, "Отказ после ручной проверки");
    }
    db.staffAppId = null;
    saveDb();
    render();
    showToast(kind === "declined" ? "Решение по заявке сохранено." : "Предложение появилось в кабинете клиента.");
  }
  function editOffer(app) {
    if (!app || !app.decision) return;
    setStatus(app, "rejected_by_client", "Клиент", "Клиент выбрал изменить параметры");
    var clone = copy(app);
    clone.id = uid();
    clone.displayId = String(Math.floor(100000 + Math.random() * 900000));
    clone.createdAt = isoNow();
    clone.updatedAt = isoNow();
    clone.status = "draft";
    clone.submittedAt = null;
    clone.decision = null;
    clone.consentLog = [];
    clone.documentName = "";
    clone.nextAt = null;
    clone.wizardStage = "product";
    clone.wizardIndex = 2;
    clone.form.amount = Number(app.decision.amount) || clone.form.amount;
    clone.form.term = Number(app.decision.months) || clone.form.term;
    clone.form.consentBki = false;
    clone.form.consentPrivacy = false;
    clone.history = [{ from: null, to: "draft", at: isoNow(), actor: "Клиент", label: "Создан черновик с изменёнными параметрами" }];
    db.apps.push(clone);
    db.currentAppId = clone.id;
    db.view = "wizard";
    saveDb();
    render();
  }
  function onClick(event) {
    var target = event.target && event.target.closest ? event.target.closest("[data-action]") : null;
    if (!target) return;
    var action = target.getAttribute("data-action");
    var value = target.getAttribute("data-value");
    var field = target.getAttribute("data-field");
    var draft = currentDraft();
    if (action === "close-modal") {
      if (event.target.classList.contains("modal-backdrop") || target.classList.contains("modal-close")) modalClose();
      return;
    }
    if (action === "nav") {
      var view = target.getAttribute("data-view");
      if (view === "help") { helpModal("Как устроен кабинет", '<p>Ответьте на вопросы анкеты, чтобы получить предварительный подбор и список следующих шагов.</p><p>Данные из открытых реестров и расчёт по анкете помогают оценить профиль компании, но не заменяют проверку банка и не являются решением о выдаче кредита.</p>'); return; }
      if (view === "new") { startBackendDraft(); return; }
      if (view === "dashboard" && db.session.role === "staff") view = "employee";
      db.view = view; saveDb(); render();
    } else if (action === "help") {
      helpModal("Как работает предварительный подбор", '<p>Предварительный подбор рассчитывается по ответам анкеты. При необходимости можно проверить общедоступные сведения компании.</p><p>Подбор не является решением банка. Для оформления кредита потребуется полноценная проверка и отдельное согласие на запрос кредитной истории.</p>');
    } else if (action === "start-new") {
      startBackendDraft();
    } else if (action === "resume-draft") {
      var resume = backendDraftForSession() || draftForSession();
      if (resume) { db.currentAppId = resume.id; db.view = "wizard"; saveDb(); render(); if (resume.backendSessionId) loadBackendSession(resume); else if (resume.wizardStage === "backend") startBackendDraft(); }
      else startBackendDraft();
    } else if (action === "open-app") {
      var app = db.apps.find(function (item) { return item.id === target.getAttribute("data-id"); });
      if (app) openApp(app);
    } else if (action === "choice") {
      if (!draft) return;
      var group = target.getAttribute("data-group");
      if (group === "purpose") {
        draft.form.purpose = value;
        if (value === "other") {
          draft.wizardIndex = 0;
        } else if (value === "gap") {
          draft.form.moneyUse = "bridge";
          draft.wizardIndex = 2;
        } else {
          draft.wizardIndex = 1;
        }
      } else if (group === "purpose-category") {
        if (!String(draft.form.otherGoal || "").trim()) {
          wizardError = "";
        }
        draft.form.purpose = value;
        draft.wizardIndex = 1;
      } else if (group === "moneyUse") {
        draft.form.moneyUse = value;
        draft.wizardIndex = 2;
      } else if (group === "collateral") {
        draft.form.collateral = value;
        draft.wizardIndex = 5;
        draft.wizardStage = "result";
      } else if (group === "applicantRole") {
        draft.form.applicantRole = value;
      }
      draft.updatedAt = isoNow(); saveDb(); render();
    } else if (action === "backend-answer") {
      answerBackendQuestion(value, target.getAttribute("data-question"));
    } else if (action === "backend-unknown") {
      answerBackendQuestion(null, target.getAttribute("data-question"));
    } else if (action === "backend-edit-answer") {
      var editable = backendCurrent();
      if (editable) { editable.backendEditQuestionId = target.getAttribute("data-question"); editable.backendError = ""; saveDb(); render(); }
    } else if (action === "backend-refresh") {
      var backendApp = backendCurrent();
      if (backendApp) refreshBackendSession(backendApp); else startBackendDraft();
    } else if (action === "backend-check-registry") {
      startBackendRegistry();
    } else if (action === "backend-cancel-job") {
      cancelBackendJob();
    } else if (action === "backend-result-refresh") {
      refreshBackendResult();
    } else if (action === "backend-delete-session") {
      deleteBackendSession();
    } else if (action === "backend-attach-registry") {
      attachBackendRegistry(target.getAttribute("data-ogrn"));
    } else if (action === "backend-captcha-refresh") {
      answerBackendCaptcha("refresh");
    } else if (action === "backend-captcha-skip") {
      answerBackendCaptcha("skip");
    } else if (action === "backend-score") {
      startBackendScoring();
    } else if (action === "wizard-back") {
      if (!draft) return;
      if (draft.wizardStage === "product") {
        if (draft.wizardIndex === 2 && draft.form.purpose === "gap") draft.wizardIndex = 0;
        else draft.wizardIndex = Math.max(0, Number(draft.wizardIndex) - 1);
      } else if (draft.wizardStage === "result") {
        draft.wizardStage = "product"; draft.wizardIndex = 4;
      } else if (draft.wizardStage === "company") {
        draft.wizardStage = "result";
      } else if (draft.wizardStage === "applicant") {
        draft.wizardStage = "company";
      } else if (draft.wizardStage === "consent") {
        draft.wizardStage = "applicant";
      }
      saveDb(); render();
    } else if (action === "wizard-next") {
      if (!draft) return;
      if (draft.wizardIndex === 2 && Number(draft.form.amount) < 300000) { showToast("Укажите сумму от 300 тыс. ₽."); return; }
      if (draft.wizardIndex === 3) {
        var productForTerm = PRODUCTS[productKey(draft.form)];
        if (Number(draft.form.term) < productForTerm.minTerm || Number(draft.form.term) > productForTerm.maxTerm) {
          showToast("Срок для этого продукта — от " + productForTerm.minTerm + " до " + productForTerm.maxTerm + " месяцев."); return;
        }
      }
      draft.wizardIndex = Math.min(4, Number(draft.wizardIndex) + 1);
      saveDb(); render();
    } else if (action === "quick-value") {
      if (!draft) return;
      var number = Number(value);
      if (field === "amount") draft.form.amount = number;
      if (field === "term") draft.form.term = number;
      saveDb(); render();
    } else if (action === "apply-product") {
      if (!draft) return;
      draft.wizardStage = "company"; draft.form.tin = draft.form.tin || "";
      wizardError = ""; saveDb(); render();
    } else if (action === "demo-inn") {
      if (!draft) return;
      draft.form.tin = value; findCompany(draft, value);
    } else if (action === "change-company") {
      if (!draft) return;
      draft.company = null; draft.wizardStage = "company"; wizardError = ""; saveDb(); render();
    } else if (action === "confirm-company") {
      if (!draft || !draft.company || stopReason(draft.company)) { showToast("Нельзя продолжить с этими данными компании."); return; }
      draft.wizardStage = "applicant"; draft.form.applicantName = draft.form.applicantName || draft.company.director;
      draft.form.monthlyRevenue = Number(draft.form.monthlyRevenue) || Number(draft.company.monthlyRevenue) || 0;
      saveDb(); render();
    } else if (action === "edit-revenue") {
      if (!draft) return;
      draft.form.revenueOverride = true; saveDb(); render();
    } else if (action === "to-consent") {
      if (!draft) return;
      if (!String(draft.form.applicantName || "").trim()) { showToast("Укажите ФИО заявителя."); return; }
      if (draft.form.applicantRole !== "owner" && !draft.form.powerOfAttorney) { showToast("Отметьте, что у вас есть доверенность."); return; }
      if (Number(draft.form.monthlyRevenue) <= 0) { showToast("Укажите среднюю выручку в месяц."); return; }
      draft.wizardStage = "consent"; saveDb(); render();
    } else if (action === "legal-info") {
      var kind = target.getAttribute("data-kind");
      if (kind === "bki") helpModal("Проверка кредитной истории", "<p>Подтверждение в кабинете не отправляет запрос в бюро кредитных историй. Для реальной заявки потребуется отдельное согласие с указанием получателя сведений.</p>");
      else helpModal("Обработка данных", "<p>Для подачи заявки в банк потребуется отдельное согласие на обработку персональных данных. Подтверждение в кабинете используется только для предварительной оценки.</p>");
    } else if (action === "accept-offer") {
      var accepted = currentApp();
      if (accepted && isOffer(accepted.status)) {
        setStatus(accepted, "accepted", "Клиент", "Предложение принято");
        accepted.acceptedAt = isoNow(); db.view = "contract"; saveDb(); render();
      }
    } else if (action === "reject-offer") {
      var rejected = currentApp();
      if (rejected && isOffer(rejected.status)) {
        setStatus(rejected, "rejected_by_client", "Клиент", "Клиент отказался от предложения");
        db.view = "dashboard"; saveDb(); render(); showToast("Предложение отклонено.");
      }
    } else if (action === "edit-offer") {
      editOffer(currentApp());
    } else if (action === "open-contract") {
      db.view = "contract"; saveDb(); render();
    } else if (action === "sign-contract") {
      var toSign = currentApp();
      if (toSign) signContract(toSign);
    } else if (action === "staff-select") {
      db.staffAppId = target.getAttribute("data-id"); saveDb(); render();
    } else if (action === "staff-approve") {
      employeeDecision("approved");
    } else if (action === "staff-counter") {
      employeeDecision("counteroffer");
    } else if (action === "staff-decline") {
      employeeDecision("declined");
    }
  }
  function onInput(event) {
    var target = event.target;
    if (!target || !target.getAttribute) return;
    var field = target.getAttribute("data-field");
    if (!field) return;
    var value = target.type === "checkbox" ? target.checked : target.value;
    if (field === "amount" || field === "term" || field === "monthlyRevenue" || field === "currentPayments") value = Number(value);
    persistField(field, value);
    if (field === "amount" || field === "term") {
      document.querySelectorAll('[data-field="' + field + '"]').forEach(function (node) {
        if (node !== target && node.type !== "checkbox") node.value = target.value;
      });
      refreshQuote();
    }
  }
  function onChange(event) {
    var target = event.target;
    if (!target) return;
    var field = target.getAttribute && target.getAttribute("data-field");
    if (field) {
      var value = target.type === "checkbox" ? target.checked : target.value;
      if (["amount", "term", "monthlyRevenue", "currentPayments"].indexOf(field) >= 0) value = Number(value);
      persistField(field, value);
    }
    if (target.id === "document-file" && target.files && target.files[0]) {
      var file = target.files[0];
      if (!/\.pdf$/i.test(file.name) && file.type !== "application/pdf") {
        target.value = "";
        showToast("Выберите файл PDF.");
      }
    }
  }
  function onSubmit(event) {
    var form = event.target;
    if (!form || !form.id) return;
    if (form.id === "company-search") {
      event.preventDefault();
      var draft = currentDraft();
      if (draft) findCompany(draft, new FormData(form).get("inn"));
    } else if (form.id === "consent-form") {
      event.preventDefault();
      var activeDraft = currentDraft();
      if (activeDraft) submitApplication(activeDraft);
    } else if (form.id === "upload-form") {
      event.preventDefault();
      var app = currentApp();
      var file = form.querySelector('input[type="file"]') && form.querySelector('input[type="file"]').files[0];
      if (!app || app.status !== "needs_docs") return;
      if (!file || (!/\.pdf$/i.test(file.name) && file.type !== "application/pdf")) {
        showToast("Выберите PDF-выписку, чтобы продолжить."); return;
      }
      app.documentName = file.name;
      setStatus(app, "enrichment", "Клиент", "Выписка добавлена");
      due(app, 2600); saveDb(); render(); showToast("Выписка добавлена. Продолжаем проверку.");
    } else if (form.id === "backend-answer-form") {
      event.preventDefault();
      var backendApp = backendCurrent();
      var questionId = form.getAttribute("data-question");
      var question = currentBackendQuestion(backendApp) || (backendApp && backendApp.backendQuestions || []).find(function (item) { return item.id === questionId; });
      var raw = new FormData(form).get("answer");
      if (question && (question.type === "number" || question.type === "integer")) {
        var number = Number(raw);
        if (!Number.isFinite(number)) { showToast("Введите число."); return; }
        if (question.type === "integer" && !Number.isInteger(number)) { showToast("Введите целое число."); return; }
        answerBackendQuestion(number, questionId);
      } else answerBackendQuestion(String(raw || "").trim(), questionId);
    } else if (form.id === "backend-captcha-form") {
      event.preventDefault(); answerBackendCaptcha("answer");
    }
  }
  appRoot.addEventListener("click", onClick);
  appRoot.addEventListener("input", onInput);
  appRoot.addEventListener("change", onChange);
  appRoot.addEventListener("submit", onSubmit);
  appRoot.addEventListener("keydown", function (event) {
    if ((event.key === "Enter" || event.key === " ") && event.target && event.target.matches("tr.row-click")) {
      event.preventDefault();
      var id = event.target.getAttribute("data-id");
      var app = db.apps.find(function (item) { return item.id === id; });
      if (app) openApp(app);
    }
  });
  setInterval(tick, 900);
  saveDb();
  render();
})();
