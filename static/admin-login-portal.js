'use strict';
const loginForm=document.querySelector('#admin-login-form');
if(loginForm)loginForm.addEventListener('submit',async event=>{
  event.preventDefault();const button=loginForm.querySelector('[type="submit"]');button.disabled=true;const error=loginForm.querySelector('.form-error');error.textContent='';
  try{const form=new FormData(loginForm);await Parking.api('/api/admin/login',{body:{username:form.get('username'),password:form.get('password')}});location.assign('/admin');}
  catch(e){error.textContent=e.message;}finally{button.disabled=false;}
});
