const assert = require('node:assert/strict');
const fs = require('node:fs');
const test = require('node:test');
const vm = require('node:vm');
for (const [mode, role, destination] of [['login','user','/my-plots'], ['login','admin','/fra'], ['register','user','/my-plots'], ['staff','reviewer','/fra']]) {
  test(`${mode} signs in with stored ${role} role`, async () => {
    const elements = new Map();
    class Element { constructor() { this.value='';this.listeners={};this.dataset={};this.textContent='Sign in'; } addEventListener(t,fn){this.listeners[t]=fn;} setAttribute(){} focus(){} }
    const element=(id)=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
    const modes=['login','register','staff'].map((value)=>{const el=new Element();el.dataset.mode=value;return el;});
    element('username').value='alice';element('password').value='long password';element('displayName').value='Alice';element('accessCode').value='1234';
    let location=null,request=null;
    vm.runInNewContext(fs.readFileSync('app/static/login/app.js','utf8'),{document:{getElementById:element,querySelectorAll:()=>modes},fetch:async(url,options)=>{request={url,body:JSON.parse(options.body)};return{ok:true,json:async()=>({role,redirect:mode==='staff'?undefined:destination})};},window:{location:{assign:(url)=>location=url}}});
    modes.find((el)=>el.dataset.mode===mode).listeners.click();
    await element('loginForm').listeners.submit({preventDefault(){}});
    assert.equal(location,destination);assert.equal(request.url,`/api/auth/${mode==='staff'?'demo-login':mode}`);assert.equal(request.body.role,undefined);
  });
}
