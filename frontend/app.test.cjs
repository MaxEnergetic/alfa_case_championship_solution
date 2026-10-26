const assert = require('node:assert/strict');
const {test} = require('node:test');
const {readFileSync} = require('node:fs');
const vm = require('node:vm');
const code=readFileSync(require('node:path').join(__dirname,'app.js'),'utf8');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function session(q={id:'knowledge',question_id:'Q01',text:'Первый вопрос',type:'choice',options:[{value:'no',label:'Помогите выбрать'}]}) {
 return {session_id:'s1',revision:0,state:'collecting',next_question:q,question_group:[],answered_questions:[],data:{company:{inn:null,selected_ogrn:null,entities:[],sources:{},lookup_status:'not_requested'}},conflicts:[],recommendation:null,assessment:null,scoring:null};
}
async function harness(initial, onRequest=()=>undefined) {
 let current=initial;
 const events={},storage=new Map([['credit-token','test-token'],['credit-session','s1']]);
 const root={innerHTML:'',addEventListener:(name,fn)=>events[name]=fn};
 const calls=[];let interval;
 const context={document:{getElementById:id=>id==='app'?root:null},sessionStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},
  fetch:async(url,opts={})=>{
   calls.push({url,opts});
   const custom=await onRequest(url,opts,()=>current,x=>current=x);
   let value=custom;
   if(custom===undefined){
    if(url.endsWith('/auth/me'))value={email:'test@local.test'};
    else if(url==='/api/v1/credit/sessions')value=[{id:'s1',created_at:'2026-10-26T10:00:00Z'}];
    else if(url.endsWith('/s1/jobs'))value=[];
    else if(url.endsWith('/s1'))value=current;
    else throw Error('Unexpected request '+url);
   }
   return {status:200,ok:true,json:async()=>JSON.parse(JSON.stringify(value))};
  },setInterval:fn=>interval=fn,setTimeout,clearTimeout,URL,Blob,Intl,Date,
  FormData:class{constructor(form){this.form=form;}get(k){return this.form.values[k];}}
 };
 vm.runInNewContext(code,context);
 await tick();await tick();
 return {root,calls,poll:async()=>{await interval();await tick();},click:async data=>{events.click({target:{closest:()=>({dataset:data,disabled:false})},preventDefault(){}});await tick();await tick();},submit:async(id,values)=>{events.submit({target:{id,values},preventDefault(){}});await tick();await tick();}};
}
test('lost answer response recovers server state without sending answer twice',async()=>{
 const h=await harness(session(),async(url,opts,get,set)=>{
  if(url.endsWith('/answers')){
   set({...get(),revision:1,next_question:{id:'goal',question_id:'Q03',text:'Следующий вопрос',type:'choice',options:[]}});
   throw new TypeError('Failed to fetch');
  }
 });
 await h.click({answer:'no'});
 assert.match(h.root.innerHTML,/Связь с сервером прервалась/);
 await h.poll();
 assert.match(h.root.innerHTML,/Следующий вопрос/);
 assert.doesNotMatch(h.root.innerHTML,/Первый вопрос|Failed to fetch|Связь с сервером прервалась/);
 assert.equal(h.calls.filter(x=>x.url.endsWith('/answers')).length,1);
});
test('INN has one input in the questionnaire and no separate lookup form',async()=>{
 const h=await harness(session({id:'inn',question_id:'Q13',text:'Укажите ИНН',type:'text',options:[],allow_unknown:true}));
 assert.equal((h.root.innerHTML.match(/<input /g)||[]).length,1);
 assert.doesNotMatch(h.root.innerHTML,/id="lookup"|name="identifier"/);
});
test('purchase form sends both amounts in one atomic request',async()=>{
 const q={id:'asset_cost',text:'Стоимость',type:'number',minimum:1,maximum:1e15,options:[]};
 const initial={...session(q),question_group:[q,{...q,id:'amount'}]};
 const h=await harness(initial,async(url,opts,get,set)=>{
  if(url.endsWith('/answers/batch')){
   const body=JSON.parse(opts.body);
   assert.deepEqual(body.answers,[{question_id:'asset_cost',value:5000000},{question_id:'amount',value:3000000}]);
   const next={...get(),revision:1,question_group:[],next_question:{id:'registered',text:'Форма бизнеса',type:'choice',options:[]}};
   set(next);return next;
  }
 });
 assert.match(h.root.innerHTML,/Стоимость покупки, ₽/);
 assert.match(h.root.innerHTML,/Из неё в кредит, ₽/);
 await h.submit('purchase',{asset_cost:'5000000',amount:'3000000'});
 assert.match(h.root.innerHTML,/Форма бизнеса/);
 assert.equal(h.calls.filter(x=>x.opts.method==='POST').length,1);
});
test('ready questionnaire automatically starts one assessment and shows one result',async()=>{
 const assessment={title:'Подобрали варианты',financial_assessment:{status:'not_requested',probability_financial_stability:null},approval_probability:{message:'Нет модели одобрения'},clarifications:[],products:[{name:'Бизнес-ипотека',reason:'Покупка помещения',checks:[],readiness:'candidate'}]};
 const x={...session(null),state:'ready',assessment};x.data.company.inn='5505223431';
 const h=await harness(x,async(url,opts,get,set)=>{
  if(url.endsWith('/scoring/jobs')){
   const value={...get(),revision:1,scoring_job_id:'j1'};set(value);
   return {job_id:'j1',state:'running'};
  }
  if(url.endsWith('/jobs/j1')){
   const scoring={report_year:2025,financials_rub:{revenue:100},source:{name:'test fixture'},prediction:{probability_financial_stability:.83}};
   set({...get(),revision:2,scoring,assessment:{...assessment,financial_assessment:{status:'ready',probability_financial_stability:.83,report_year:2025}}});
   return {job_id:'j1',state:'completed',result:scoring};
  }
 });
 await h.poll();await h.poll();
 assert.equal(h.calls.filter(x=>x.url.endsWith('/scoring/jobs')).length,1);
 assert.match(h.root.innerHTML,/83.0%/);
 assert.match(h.root.innerHTML,/Бизнес-ипотека/);
 assert.match(h.root.innerHTML,/Вероятность одобрения/);
 assert.match(h.root.innerHTML,/Не рассчитана/);
 assert.doesNotMatch(h.root.innerHTML,/Q29|Q30|Q31|scoreView|идентификатор/);
});
