'use strict';
const el = id => document.getElementById(id);
const palette = [
  ['Коралловый', '#ff655c'], ['Янтарный', '#ffbe57'], ['Мятный', '#57edb0'],
  ['Голубой', '#58bfff'], ['Фиолетовый', '#9a75ff'], ['Розовый', '#ff77bd']
];
let state, working = false, reading = false, revision = 0, tabChoice, editing;
function tab(color) {
  el('colors').hidden = !color; el('whites').hidden = color;
  el('colorTab').setAttribute('aria-pressed', String(color));
  el('whiteTab').setAttribute('aria-pressed', String(!color));
}
function busy(locked) {
  for (const id of ['power','controls','extra']) el(id).disabled = locked;
}
function colorValues(hex) {
  const [r,g,b] = hex.match(/\w\w/g).map(v=>parseInt(v,16)/255);
  const hi=Math.max(r,g,b), lo=Math.min(r,g,b), d=hi-lo;
  let hue=0;
  if(d) {
    if(hi===r) hue=(g-b)/d;
    else if(hi===g) hue=(b-r)/d+2;
    else hue=(r-g)/d+4;
  }
  return {hue:Math.round(((hue+6)%6)/6*254),sat:hi?Math.round(d/hi*254):0};
}
function paint(s) {
  state=s;
  const level=Math.max(1,Math.round(s.CurrentLevel*100/254));
  const white=s.ColorMode===2, kelvin=Math.round(1000000/s.ColorTemperatureMireds/50)*50;
  const hue=s.CurrentHue*360/254, sat=s.CurrentSaturation/254;
  const light=white ? (kelvin<3500?'#ffcb90':kelvin<4800?'#ffe5bd':kelvin<5700?'#fff4e2':'#e3edff')
                    : 'hsl('+hue+' '+(sat*100)+'% '+(100-50*sat)+'%)';
  el('hero').classList.remove('unknown'); el('hero').classList.toggle('off',!s.OnOff);
  el('hero').style.setProperty('--light',light);
  el('hero').style.setProperty('--level',String(.2+.8*level/100));
  el('reading').textContent=s.OnOff?'Включена':'Выключена';
  el('power').setAttribute('aria-pressed',String(s.OnOff));
  el('power').setAttribute('aria-label',s.OnOff?'Выключить свет':'Включить свет');
  if(editing!=='level'){el('level').value=level;el('levelValue').textContent=level+' %';}
  if(editing!=='kelvin'){el('kelvin').value=kelvin;el('kelvinValue').textContent=kelvin+' К';}
  tab(tabChoice??!white);
  for(const b of el('swatches').querySelectorAll('button')){
    const c=colorValues(b.dataset.hex);
    b.setAttribute('aria-pressed',String(!white && Math.abs(c.hue-s.CurrentHue)<2 && Math.abs(c.sat-s.CurrentSaturation)<2));
  }
  el('loop').textContent=s.ColorLoopActive?'Остановить':'Запустить';
  if(s.ColorLoopActive){el('period').value=s.ColorLoopTime;el('direction').value=s.ColorLoopDirection;}
  el('timer').textContent=s.OnTime?'Отменить':'Запустить';
  el('timerLeft').textContent=s.OnTime?'· '+Math.ceil(s.OnTime/600)+' мин':'';
  el('startup').value=String(s.StartUpOnOff);
}
async function api(path,options={}) {
  const response=await fetch(path,{cache:'no-store',...options});
  const data=await response.json();
  if(!response.ok) throw new Error(data.error||'Пульт недоступен.');
  return data;
}
function unavailable(error){
  el('status').textContent='Нет связи';el('msg').textContent=error.message;
  el('reading').textContent='Недоступна';el('hero').classList.add('unknown');busy(true);
}
async function refresh(){
  if(working||reading||document.hidden) return;
  const at=revision;reading=true;
  try{
    const s=await api('state');
    if(at!==revision) return;
    paint(s);busy(false);el('status').textContent='На связи';el('msg').textContent='';
  }catch(e){if(at===revision) unavailable(e);}
  finally{reading=false;}
}
async function send(action,values={}) {
  if(working||!state) return;
  revision++; working=true;busy(true);el('status').textContent='Отправка…';el('msg').textContent='';
  try{
    await api('command',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action,...values})});
    el('status').textContent='Проверяем…';
    paint(await api('state'));busy(false);el('status').textContent='На связи';
  }catch(e){unavailable(e);}
  finally{working=false;}
}
const fade=()=>Number(el('fade').value);
function color(hex){tabChoice=true;send('color',{...colorValues(hex),fade:fade()});}
for(const [name,hex] of palette) {
  const b=document.createElement('button');b.style.backgroundColor=hex;b.title=name;
  b.setAttribute('aria-label',name);b.dataset.hex=hex;b.onclick=()=>color(hex);
  el('swatches').insertBefore(b,el('swatches').lastElementChild);
}
el('custom').onchange=e=>color(e.target.value);
el('colorTab').onclick=()=>tab(tabChoice=true);
el('whiteTab').onclick=()=>tab(tabChoice=false);
for(const [id,unit] of [['level',' %'],['kelvin',' К']]){
  el(id).oninput=e=>{editing=id;el(id+'Value').textContent=e.target.value+unit;};
  el(id).onblur=()=>{editing=null;};
}
el('level').onchange=e=>{editing=null;send('brightness',{value:Number(e.target.value),fade:fade()});};
el('kelvin').onchange=e=>{editing=null;send('white',{kelvin:Number(e.target.value),fade:fade()});};
el('power').onclick=()=>send('power',{on:!state.OnOff});
function loop(active){send('loop',{active,seconds:Number(el('period').value),direction:Number(el('direction').value)});}
el('loop').onclick=()=>loop(state.ColorLoopActive?0:1);
for(const id of ['period','direction']) el(id).onchange=()=>{if(state.ColorLoopActive) loop(1);};
el('timer').onclick=()=>send('timer',{minutes:state.OnTime?0:Number(el('minutes').value)});
el('startup').onchange=e=>send('startup',{value:e.target.value});
el('identify').onclick=()=>send('identify');
document.addEventListener('visibilitychange',refresh);window.addEventListener('pageshow',refresh);
refresh();setInterval(refresh,8000);
