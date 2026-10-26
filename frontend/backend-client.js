(function (global) {
  "use strict";

  var configuredBase = String(global.ALFA_API_BASE_URL || "").trim();
  var base = configuredBase.replace(/\/+$/, "");

  function url(path) {
    if (/^https?:\/\//i.test(path)) return path;
    return base + (path.charAt(0) === "/" ? path : "/" + path);
  }

  async function request(path, options) {
    var init = Object.assign({ credentials: "omit", cache: "no-store" }, options || {});
    init.headers = Object.assign({ Accept: "application/json" }, init.headers || {});
    var response;
    try {
      response = await fetch(url(path), init);
    } catch (error) {
      throw new Error("Не удалось связаться с API. Проверьте, что сервер запущен.");
    }
    var body = null;
    if (response.status !== 204) {
      try { body = await response.json(); }
      catch (error) { body = null; }
    }
    if (!response.ok) {
      var message = body && body.error && (typeof body.error === "string" ? body.error : body.error.message);
      var failure = new Error(message || "API вернул ошибку HTTP " + response.status + ".");
      failure.status = response.status;
      failure.data = body;
      throw failure;
    }
    return body;
  }

  function json(method, value) {
    return { method: method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(value) };
  }

  function sessionPath(id, suffix) {
    return "/api/v1/credit/sessions/" + encodeURIComponent(id) + (suffix || "");
  }

  global.AlfaBackend = Object.freeze({
    baseUrl: base,
    url: url,
    request: request,
    health: function () { return request("/health"); },
    createSession: function () { return request("/api/v1/credit/sessions", { method: "POST" }); },
    getSession: function (id) { return request(sessionPath(id)); },
    getResult: function (id) { return request(sessionPath(id, "/result")); },
    deleteSession: function (id, revision) {
      return request(sessionPath(id) + "?revision=" + encodeURIComponent(revision), { method: "DELETE" });
    },
    answer: function (id, revision, questionId, value) {
      return request(sessionPath(id, "/answers"), json("POST", { revision: revision, question_id: questionId, value: value }));
    },
    startRegistry: function (identifier) {
      return request("/api/v1/jobs", json("POST", { identifier: identifier, enrich: true, max_pages: 3 }));
    },
    job: function (jobId) { return request("/api/v1/jobs/" + encodeURIComponent(jobId)); },
    cancelJob: function (jobId) { return request("/api/v1/jobs/" + encodeURIComponent(jobId), { method: "DELETE" }); },
    company: function (identifier, options) {
      var params = new URLSearchParams();
      options = options || {};
      params.set("full", options.full ? "true" : "false");
      params.set("enrich", options.enrich === false ? "false" : "true");
      if (options.maxPages) params.set("max_pages", String(options.maxPages));
      return request("/api/v1/companies/" + encodeURIComponent(identifier) + "?" + params.toString());
    },
    scoringModel: function () { return request("/api/v1/scoring/model"); },
    startStandaloneScoring: function (identifier, options) {
      options = options || {};
      var body = { identifier: identifier };
      if (options.year) body.year = Number(options.year);
      if (options.asOf) body.as_of = options.asOf;
      if (options.registryJobId) body.registry_job_id = options.registryJobId;
      return request("/api/v1/scoring/jobs", json("POST", body));
    },
    batchCaptchas: function () { return request("/api/v1/batch/captchas"); },
    answerBatchCaptcha: function (answerUrl, action, code) {
      var body = { action: action };
      if (code) body.code = code;
      return request(answerUrl, json("POST", body));
    },
    attachRegistry: function (id, revision, jobId, selectedOgrn) {
      var body = { revision: revision, job_id: jobId };
      if (selectedOgrn) body.selected_ogrn = selectedOgrn;
      return request(sessionPath(id, "/registry"), json("POST", body));
    },
    answerCaptcha: function (answerUrl, action, code) {
      var body = { action: action };
      if (code) body.code = code;
      return request(answerUrl, json("POST", body));
    },
    startScoring: function (id, revision) {
      return request(sessionPath(id, "/scoring/jobs"), json("POST", { revision: revision }));
    }
  });
})(window);
