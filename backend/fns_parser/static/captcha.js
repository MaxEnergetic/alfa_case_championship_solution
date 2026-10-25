"use strict";
const $ = (id) => document.getElementById(id);
let jobId = null, captcha = null, resultData = null, timer = null, polling = false;
const states = {running:"Собираем данные",waiting_for_captcha:"Нужна ручная проверка",completed:"Сбор завершён",failed:"Не удалось завершить сбор",cancelled:"Задание отменено"};
const statuses = {ok:"Получено",partial:"Частично",error:"Ошибка источника",manual_required:"Нужна ручная проверка",not_found:"Записей не найдено",not_applicable:"Не применяется"};
const names = {egrul:"ЕГРЮЛ / ЕГРИП",transparent_business:"Прозрачный бизнес",girbo:"ГИР БО",arbitration:"Арбитражные дела",fssp:"ФССП",fedresurs:"Федресурс",bankruptcy:"ЕФРСБ",pledges:"Реестр залогов ФНП"};
const official = {egrul:"https://egrul.nalog.ru/",transparent_business:"https://pb.nalog.ru/",girbo:"https://bo.nalog.gov.ru/",arbitration:"https://kad.arbitr.ru/",fssp:"https://fssp.gov.ru/iss/ip",fedresurs:"https://fedresurs.ru/",bankruptcy:"https://bankrot.fedresurs.ru/",pledges:"https://www.reestr-zalogov.ru/search/index"};
function error(message=""){$("error").textContent=message;$("error").hidden=!message;}
async function api(url,options={}){const r=await fetch(url,{cache:"no-store",...options});const data=await r.json();if(!r.ok)throw new Error(data.error?.message||`Ошибка HTTP ${r.status}`);return data;}
function jsonOptions(method,data){return {method,headers:{"Content-Type":"application/json"},body:JSON.stringify(data)};}
function answerBusy(busy){for(const id of ["answer-button","refresh-button","skip-button"]){$(id).disabled=busy;}}
function showResult(data){
  resultData=data;$("result").hidden=false;
  $("result-status").textContent=data.status==="partial"?"Часть источников недоступна. Их ошибки не означают отсутствие записей.":data.status==="ok"?"Проверки завершены.":"Проверьте статусы источников ниже.";
  $("json-result").textContent=JSON.stringify(data,null,2);$("source-list").replaceChildren();
  for(const [name,entry] of Object.entries(data.sources||{})){
    const row=document.createElement("div");row.className="source-row";
    const title=document.createElement("div");title.className="source-title";
    const label=document.createElement("strong");label.textContent=names[name]||name;
    const badge=document.createElement("span");badge.className="badge";if(["ok","partial","manual_required"].includes(entry.status))badge.classList.add(entry.status);badge.textContent=statuses[entry.status]||entry.status;title.append(label,badge);row.append(title);
    if(entry.error){const p=document.createElement("p");p.textContent=entry.error.message;row.append(p);}
    if(entry.status!=="ok"&&entry.status!=="not_applicable"&&official[name]){const link=document.createElement("a");link.href=official[name];link.target="_blank";link.rel="noopener noreferrer";link.textContent="Проверить на сайте источника ↗";row.append(link);}
    $("source-list").append(row);
  }
}
function render(job){
  const terminal=["completed","failed","cancelled"].includes(job.state);
  $("job").hidden=false;$("job-state").textContent=states[job.state]||job.state;
  $("job-detail").textContent=`Запрос: ${job.identifier}. `+(terminal?"":job.captcha?"Ожидаем ваш ответ.":"Источники могут отвечать больше минуты. Страницу можно оставить открытой.");
  $("cancel-button").hidden=terminal;$("start-button").disabled=!terminal;
  $("challenge").hidden=!job.captcha;
  if(job.captcha&&job.captcha.id!==captcha?.id){
    captcha=job.captcha;$("captcha-image").src=captcha.image_url;$("code").value="";answerBusy(false);
    const fns=["egrul","transparent_business"].includes(captcha.source);
    $("code").pattern=fns?"[0-9]{6}":"[A-Za-z0-9]+";$("code").maxLength=fns?6:16;$("code").inputMode=fns?"numeric":"text";
    $("captcha-description").textContent=`${names[captcha.source] || captcha.source} · ИНН / ОГРН ${captcha.inn} · запрос картинки ${captcha.attempt} из 5`;
    $("captcha-expiry").textContent=`Ожидаем ответ до ${new Date(captcha.expires_at).toLocaleTimeString("ru-RU")}. Если картинка устарела, запросите её заново.`;$("code").focus();
  }else if(!job.captcha){captcha=null;$("captcha-image").removeAttribute("src");}
  if(job.result)showResult(job.result);
  if(job.error)error(job.error.message);
  if(terminal){clearTimeout(timer);timer=null;}
  return terminal;
}
async function poll(){
  if(!jobId||polling)return;polling=true;let again=true;
  try{const job=await api(`/api/v1/jobs/${encodeURIComponent(jobId)}`);error();again=!render(job);}
  catch(e){error(e.message);again=false;$("start-button").disabled=false;}
  finally{polling=false;if(again)timer=setTimeout(poll,1500);}
}
$("start-form").addEventListener("submit",async(event)=>{
  event.preventDefault();error();clearTimeout(timer);$("start-button").disabled=true;
  try{const job=await api("/api/v1/jobs",jsonOptions("POST",{identifier:$("identifier").value.trim(),full:$("full").checked}));jobId=job.job_id;captcha=null;resultData=null;$("result").hidden=true;history.replaceState(null,"",`#job=${encodeURIComponent(jobId)}`);render(job);poll();}
  catch(e){error(e.message);$("start-button").disabled=false;}
});
async function answer(action){
  if(!captcha)return;error();answerBusy(true);
  try{const body={action};if(action==="answer")body.code=$("code").value.trim();await api(captcha.answer_url,jsonOptions("POST",body));$("challenge").hidden=true;$("code").value="";clearTimeout(timer);poll();}
  catch(e){error(e.message);answerBusy(false);}
}
$("answer-form").addEventListener("submit",event=>{event.preventDefault();answer("answer");});
$("refresh-button").addEventListener("click",()=>answer("refresh"));
$("skip-button").addEventListener("click",()=>answer("skip"));
$("cancel-button").addEventListener("click",async()=>{if(!jobId)return;try{render(await api(`/api/v1/jobs/${encodeURIComponent(jobId)}`,{method:"DELETE"}));}catch(e){error(e.message);}});
$("download-button").addEventListener("click",()=>{if(!resultData)return;const u=URL.createObjectURL(new Blob([JSON.stringify(resultData,null,2)],{type:"application/json"}));const a=document.createElement("a");a.href=u;a.download=`company-${resultData.query||"result"}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(u),1000);});
const saved=new URLSearchParams(location.hash.slice(1)).get("job");if(saved&&/^[A-Za-z0-9_-]{32}$/.test(saved)){jobId=saved;$("start-button").disabled=true;poll();}
