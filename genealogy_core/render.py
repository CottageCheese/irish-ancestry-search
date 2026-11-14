"""Pure HTML rendering. Every function returns a string; callers handle I/O.

Card-based kid-friendly layout: per-record cards grouped by confidence tier,
emoji icons per record type, big confirm/reject buttons, learning tooltips.
"""
import html
import json
import re

from .confirmations import record_key
from .models import Record, confidence_tier
from .nicknames import NICKNAMES


RECORD_ICONS: dict[str, str] = {
    "census": "📜",
    "civil birth": "👶",
    "civil marriage": "💍",
    "civil death": "🕯️",
    "church baptism": "⛪",
    "church marriage": "💒",
    "church burial": "⚰️",
    "griffith": "🏡",
    "manual": "🔗",
}

RECORD_FACTS: dict[str, str] = {
    "census": "A count of everyone sleeping in a house on one night. Ireland did a census in 1901, 1911, and 1926.",
    "civil birth": "Official birth record. Ireland started keeping these in 1864.",
    "civil marriage": "Government marriage register. From 1845 for non-Catholic, 1864 for all.",
    "civil death": "Government death register, from 1864.",
    "church baptism": "Church record of a baptism — often older than government records.",
    "church marriage": "Church record of a marriage ceremony.",
    "church burial": "Church record of a burial. Can predate civil records by centuries.",
    "griffith": "A list of who rented land in Ireland in the 1850s-60s.",
    "manual": "A link you'll need to open and read yourself.",
}

TIER_STARS: dict[str, str] = {
    "Likely": "★★★",
    "Possible": "★★",
    "Weak": "★",
    "Manual": "🔍",
}

TIER_BLURB: dict[str, str] = {
    "Likely": "Looks like a strong match.",
    "Possible": "Could be them — worth a look.",
    "Weak": "Shaky match. Check carefully.",
    "Manual": "Needs a human eye.",
}

TIER_ORDER = ["Likely", "Possible", "Weak", "Manual"]


BREAKDOWN_LABELS: dict[str, str] = {
    "base": "Starting point",
    "forename": "Forename matches",
    "surname_primary": "Surname matches",
    "surname_soundex": "Surname sounds like",
    "surname_variant": "Surname variant matches",
    "county": "County matches",
    "townland": "Townland matches",
    "townland_variant": "Townland variant matches",
    "townland_soundex": "Townland sounds like",
    "year_window": "Year in expected window",
    "sparse": "Sparse row (no place or year)",
    "kin": "Family name in record",
    "feedback": "Feedback from confirmed records",
}


def _breakdown_label(key: str) -> str:
    return BREAKDOWN_LABELS.get(key, key.replace("_", " ").capitalize())


_BASE_CSS = (
    "body{font-family:system-ui,-apple-system,'Segoe UI',sans-serif;"
    "max-width:1000px;margin:1.5em auto;padding:0 1em;color:#222;background:#fafaf7}"
    "h1{border-bottom:3px solid #8b6f47;padding-bottom:.3em}"
    ".person-hero{display:flex;gap:1em;align-items:center;margin-top:2em;"
    "padding:1em 1.2em;background:linear-gradient(135deg,#fff6d8,#ffe8c7);"
    "border-radius:12px;border-left:4px solid #8b6f47}"
    ".avatar{width:56px;height:56px;border-radius:50%;background:#8b6f47;color:#fff;"
    "font-weight:700;font-size:1.3em;display:flex;align-items:center;justify-content:center;"
    "flex-shrink:0}"
    ".hero-body{flex:1}"
    ".hero-name{font-size:1.4em;font-weight:700;margin:0}"
    ".hero-sub{color:#555;font-size:.95em;margin:.2em 0 0}"
    ".progress{margin-top:.5em}"
    ".progress-bar{height:8px;background:#eee;border-radius:4px;overflow:hidden}"
    ".progress-fill{height:100%;background:linear-gradient(90deg,#2a7d2a,#5cb85c);width:0%;transition:width .4s}"
    ".progress-text{font-size:.85em;color:#555;margin-top:.2em}"
    ".tier-section{margin:1.2em 0}"
    ".tier-head{font-weight:700;font-size:1.05em;margin:0 0 .5em;display:flex;align-items:center;gap:.4em}"
    ".tier-head .stars{color:#f3a800;letter-spacing:2px}"
    ".tier-blurb{color:#666;font-size:.9em;margin:0 0 .5em}"
    ".rec-card{background:#fff;border-radius:8px;border-left:5px solid #ccc;"
    "padding:.8em 1em;margin:.5em 0;box-shadow:0 1px 3px rgba(0,0,0,.08);"
    "transition:transform .12s,box-shadow .12s}"
    ".rec-card:hover{transform:translateY(-1px);box-shadow:0 3px 8px rgba(0,0,0,.12)}"
    ".rec-card.tier-Likely{border-left-color:#2a7d2a}"
    ".rec-card.tier-Possible{border-left-color:#b48a1a}"
    ".rec-card.tier-Weak{border-left-color:#888}"
    ".rec-card.tier-Manual{border-left-color:#bbb}"
    ".rec-head{display:flex;align-items:center;gap:.5em;margin-bottom:.3em}"
    ".rec-icon{font-size:1.4em;cursor:help}"
    ".rec-title{font-weight:600;flex:1}"
    ".rec-stars{color:#f3a800;letter-spacing:2px;font-size:1.05em}"
    ".rec-sentence{margin:.3em 0 .5em;line-height:1.4}"
    ".rec-meta{display:flex;flex-wrap:wrap;gap:.3em;margin-bottom:.5em}"
    ".chip{display:inline-block;padding:2px 8px;border-radius:12px;font-size:.85em;"
    "background:#eef;color:#335}"
    ".chip-place{background:#eaf4ea;color:#2a5}"
    ".chip-year{background:#faf0e0;color:#8a5a1a}"
    ".chip-new{background:#fde8f0;color:#a2366a}"
    ".chip-kin{background:#fef6d8;color:#8a6a00}"
    ".chip-variant{background:#eef;color:#447}"
    ".rec-actions{display:flex;gap:.5em;align-items:center;flex-wrap:wrap}"
    ".btn-open{display:inline-block;padding:.35em .8em;background:#0b5fa4;color:#fff;"
    "border-radius:6px;text-decoration:none;font-weight:600;font-size:.9em}"
    ".btn-open:hover{background:#094680;text-decoration:none}"
    ".conf-btns{display:flex;gap:.3em;margin-left:auto}"
    ".conf-btns button{cursor:pointer;border:1px solid #bbb;background:#fff;"
    "padding:.3em .7em;border-radius:6px;font-size:.9em;font-weight:600}"
    ".conf-btns button:hover{background:#f4f4f4}"
    ".conf-btns button.on-confirm{background:#2a7d2a;color:#fff;border-color:#2a7d2a}"
    ".conf-btns button.on-reject{background:#b43232;color:#fff;border-color:#b43232}"
    ".tier-new{display:inline-block;padding:1px 6px;border-radius:3px;background:#c33;"
    "color:#fff;font-size:.8em;font-weight:bold}"
    ".tier{display:inline-block;padding:1px 6px;border-radius:3px;font-size:.85em;font-weight:bold;color:#fff}"
    ".tier-Likely{background:#2a7d2a}"
    ".tier-Possible{background:#b48a1a}"
    ".tier-Weak{background:#888}"
    ".tier-Manual{background:#bbb;color:#333}"
    "img.thumb{max-width:60px;max-height:60px;border:1px solid #888;border-radius:4px}"
    "details{margin:.4em 0}"
    "details summary{cursor:pointer;color:#555;font-style:italic}"
    ".empty{color:#777;font-style:italic;padding:1em;background:#fff;border-radius:8px}"
    ".controls{margin:1em 0;display:flex;gap:.6em;align-items:center;flex-wrap:wrap}"
    "input.filter{padding:.5em .8em;font-size:1em;width:280px;border:1px solid #ccc;border-radius:6px}"
    "button.dark-toggle{padding:.4em .9em;cursor:pointer;border:1px solid #888;background:#fff;border-radius:6px}"
    "ul.toc{list-style:none;padding:0}"
    "ul.toc li{margin:.4em 0;padding:.5em .8em;background:#fff;border-radius:6px;border-left:4px solid #8b6f47}"
    "ul.toc a{font-weight:600;font-size:1.05em}"
    "a{color:#0b5fa4;text-decoration:none}a:hover{text-decoration:underline}"
    ".btn-reject-rest{cursor:pointer;border:1px solid #b43232;background:#fff;color:#b43232;"
    "padding:.3em .8em;border-radius:6px;font-size:.85em;font-weight:600;margin-left:auto}"
    ".btn-reject-rest:hover{background:#b43232;color:#fff}"
    ".variant{font-size:.8em;color:#666;font-style:italic;margin-top:.15em}"
    ".rec-why{margin:.3em 0}"
    ".rec-why summary{font-size:.85em;color:#555;cursor:pointer;display:inline-block;"
    "padding:2px 8px;border-radius:12px;background:#eef;list-style:none}"
    ".rec-why summary::-webkit-details-marker{display:none}"
    ".rec-why summary:hover{background:#dde}"
    ".rec-why ul.why-list{margin:.4em 0 .2em;padding-left:1.1em;font-size:.88em;color:#444}"
    ".rec-why li{margin:.1em 0}"
    ".rec-why .why-pos{color:#2a7d2a;font-weight:600}"
    ".rec-why .why-neg{color:#b43232;font-weight:600}"
    ".rec-household{font-size:.85em;color:#444;margin:.3em 0;padding:.3em .6em;"
    "background:#f5f0e8;border-radius:6px;border-left:3px solid #c7a46b}"
    ".name-search{background:linear-gradient(135deg,#f0f7ff,#e8f4e8);border-radius:12px;"
    "padding:1em 1.2em;margin:1em 0;border:2px solid #8b6f47}"
    ".name-search-title{font-size:1.1em;font-weight:700;margin-bottom:.7em;color:#4a3520}"
    ".name-search-row{display:flex;gap:.8em;align-items:flex-end;flex-wrap:wrap}"
    ".name-input-wrap{display:flex;flex-direction:column;gap:.3em}"
    ".name-label{font-size:.9em;font-weight:600;color:#555}"
    ".name-input{padding:.5em .8em;font-size:1.1em;width:160px;border:2px solid #8b6f47;"
    "border-radius:8px;outline:none;background:#fff}"
    ".name-input:focus{border-color:#0b5fa4;box-shadow:0 0 0 3px rgba(11,95,164,.15)}"
    ".btn-ns-clear{padding:.45em .9em;cursor:pointer;border:1px solid #888;background:#fff;"
    "border-radius:6px;font-size:.9em;align-self:flex-end}"
    ".btn-ns-clear:hover{background:#f4f4f4}"
    ".ns-variants{align-items:center;gap:.5em;flex-wrap:wrap;margin-top:.6em;"
    "padding:.5em .8em;background:rgba(255,255,255,.7);border-radius:8px}"
    ".ns-variants-label{font-size:.9em;color:#555;font-weight:600;white-space:nowrap}"
    ".ns-chip{display:inline-block;padding:3px 10px;border-radius:12px;font-size:.85em;"
    "background:#e0eeff;color:#0b5fa4;border:1px solid #b0d0ff;margin:1px}"
    ".ns-toggle-label{font-size:.9em;cursor:pointer;margin-left:.3em;user-select:none}"
    ".ns-toggle-label input{cursor:pointer;margin-right:.3em}"
)

_DARK_CSS = (
    "body.dark{background:#1a1a1a;color:#ddd}"
    "body.dark h1{border-bottom-color:#c7a46b}"
    "body.dark .person-hero{background:linear-gradient(135deg,#2a241c,#3a2e20);border-left-color:#c7a46b}"
    "body.dark .hero-sub,body.dark .progress-text,body.dark .tier-blurb{color:#aaa}"
    "body.dark .rec-card{background:#242424}"
    "body.dark .chip{background:#333;color:#ccc}"
    "body.dark .chip-place{background:#1e3b1e;color:#9c9}"
    "body.dark .chip-year{background:#3b2e1e;color:#e5c58a}"
    "body.dark .chip-new{background:#3b1e2e;color:#f0a0c0}"
    "body.dark .chip-kin{background:#4a3f10;color:#e0cc6a}"
    "body.dark .chip-variant{background:#2a2a3a;color:#aab}"
    "body.dark .conf-btns button{background:#333;color:#ddd;border-color:#555}"
    "body.dark .conf-btns button:hover{background:#444}"
    "body.dark input.filter{background:#222;color:#eee;border-color:#555}"
    "body.dark button.dark-toggle{background:#333;color:#eee;border-color:#666}"
    "body.dark ul.toc li{background:#242424}"
    "body.dark details summary,body.dark .empty{color:#aaa}"
    "body.dark .rec-why summary{background:#2a2a3a;color:#ccd}"
    "body.dark .rec-why summary:hover{background:#3a3a4a}"
    "body.dark .rec-why ul.why-list{color:#bbb}"
    "body.dark .rec-why .why-pos{color:#8fd48f}"
    "body.dark .rec-why .why-neg{color:#e78a8a}"
    "body.dark .rec-household{background:#2a2416;color:#c8b98a;border-left-color:#8b6f47}"
    "body.dark a{color:#6bb6ff}"
    "body.dark .name-search{background:linear-gradient(135deg,#1a2030,#1a2a1a);border-color:#8b6f47}"
    "body.dark .name-search-title{color:#d4b896}"
    "body.dark .name-label{color:#aaa}"
    "body.dark .name-input{background:#222;color:#eee;border-color:#666}"
    "body.dark .name-input:focus{border-color:#6bb6ff}"
    "body.dark .btn-ns-clear{background:#333;color:#eee;border-color:#555}"
    "body.dark .ns-variants{background:rgba(255,255,255,.05)}"
    "body.dark .ns-variants-label{color:#aaa}"
    "body.dark .ns-chip{background:#1a2a3a;color:#6bb6ff;border-color:#2a4a6a}"
    "body.dark .ns-toggle-label{color:#ccc}"
    ".rec-card.confirmed{background:#d7efd0!important}"
    ".rec-card.rejected{opacity:.35}"
    "body.dark .rec-card.confirmed{background:#1e4a24!important}"
    "body.dark .rec-card.rejected{opacity:.3}"
    "body.hide-rejected .rec-card.rejected{display:none}"
    "@keyframes starPop{0%{transform:scale(.6);opacity:0}100%{transform:scale(1);opacity:1}}"
    ".rec-stars{display:inline-block;animation:starPop .3s ease-out}"
    "@keyframes confettiFly{"
    "0%{transform:translate(0,0) rotate(0);opacity:1}"
    "100%{transform:translate(var(--tx),var(--ty)) rotate(360deg);opacity:0}}"
    ".confetti{position:absolute;pointer-events:none;font-size:1.2em;animation:confettiFly .8s ease-out forwards}"
    ".rec-actions{position:relative}"
    ".tour-overlay{position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:1000;"
    "display:flex;align-items:center;justify-content:center;padding:1em}"
    ".tour-box{background:#fff;border-radius:12px;max-width:520px;padding:1.5em 1.8em;"
    "box-shadow:0 10px 40px rgba(0,0,0,.3);line-height:1.5}"
    "body.dark .tour-box{background:#2a2a2a;color:#eee}"
    ".tour-box h3{margin:0 0 .5em;font-size:1.4em}"
    ".tour-box ul{padding-left:1.2em;margin:.5em 0}"
    ".tour-box li{margin:.4em 0}"
    ".tour-box .tour-btn{display:inline-block;margin-top:1em;padding:.5em 1.2em;"
    "background:#2a7d2a;color:#fff;border:0;border-radius:6px;cursor:pointer;font-weight:600}"
    ".tour-box .tour-btn:hover{background:#1e5c1e}"
    ".help-btn{position:fixed;bottom:1em;right:1em;width:40px;height:40px;"
    "border-radius:50%;background:#8b6f47;color:#fff;border:0;font-size:1.2em;"
    "cursor:pointer;box-shadow:0 2px 6px rgba(0,0,0,.2);z-index:999}"
    ".help-btn:hover{background:#6d5637}"
)

_HTML_SCRIPT = (
    "<script>"
    "(function(){try{if(localStorage.getItem('dark')==='1')document.body.classList.add('dark');}catch(e){}"
    "try{if(localStorage.getItem('hide_rejected')==='1')document.body.classList.add('hide-rejected');}catch(e){}"
    "var btn=document.getElementById('dm');if(btn)btn.onclick=function(){"
    "document.body.classList.toggle('dark');"
    "try{localStorage.setItem('dark',document.body.classList.contains('dark')?'1':'0');}catch(e){}"
    "};"
    "var hr=document.getElementById('hr');if(hr){"
    "hr.checked=document.body.classList.contains('hide-rejected');"
    "hr.onchange=function(){document.body.classList.toggle('hide-rejected',hr.checked);"
    "try{localStorage.setItem('hide_rejected',hr.checked?'1':'0');}catch(e){}};}"
    "var inp=document.getElementById('filter');if(inp)inp.oninput=function(){"
    "var q=this.value.toLowerCase();"
    "document.querySelectorAll('.rec-card').forEach(function(c){"
    "c.style.display=c.innerText.toLowerCase().indexOf(q)>=0?'':'none';});"
    "};"
    "function updateProgress(personEl){"
    "if(!personEl)return;"
    "var cards=personEl.querySelectorAll('.rec-card');"
    "var confirmed=personEl.querySelectorAll('.rec-card.confirmed').length;"
    "var bar=personEl.querySelector('.progress-fill');"
    "var txt=personEl.querySelector('.progress-text');"
    "if(bar&&cards.length)bar.style.width=(100*confirmed/cards.length)+'%';"
    "if(txt)txt.textContent='\\u2713 '+confirmed+' of '+cards.length+' confirmed';}"
    "function burstConfetti(anchor){"
    "var emojis=['\\u2728','\\ud83c\\udf89','\\u2b50','\\ud83c\\udf8a'];"
    "for(var i=0;i<6;i++){"
    "var s=document.createElement('span');"
    "s.className='confetti';s.textContent=emojis[i%emojis.length];"
    "var ang=Math.random()*2*Math.PI;var dist=40+Math.random()*40;"
    "s.style.setProperty('--tx',Math.cos(ang)*dist+'px');"
    "s.style.setProperty('--ty',Math.sin(ang)*dist+'px');"
    "s.style.right='10px';s.style.top='0';"
    "anchor.appendChild(s);setTimeout(function(n){n.remove();},800,s);}}"
    "function applyCard(card,status){"
    "card.classList.remove('confirmed','rejected');"
    "if(status)card.classList.add(status);"
    "card.querySelectorAll('.conf-btns button').forEach(function(b){"
    "b.classList.toggle('on-confirm',status==='confirmed'&&b.dataset.action==='confirmed');"
    "b.classList.toggle('on-reject',status==='rejected'&&b.dataset.action==='rejected');});"
    "updateProgress(card.closest('.person-block'));}"
    "document.querySelectorAll('.conf-btns button').forEach(function(b){"
    "b.addEventListener('click',function(){"
    "var card=b.closest('.rec-card'),key=card.dataset.key,person=card.dataset.person;"
    "var cur=card.classList.contains(b.dataset.action)?'':b.dataset.action;"
    "fetch('/confirm',{method:'POST',headers:{'Content-Type':'application/json'},"
    "body:JSON.stringify({key:key,status:cur,person:person})})"
    ".then(function(r){if(r.ok){applyCard(card,cur);"
    "if(cur==='confirmed'){var a=b.closest('.rec-actions');if(a)burstConfetti(a);}}"
    "else r.text().then(function(t){alert('Save failed: '+t);});})"
    ".catch(function(e){alert('Save failed (offline?): '+e);});});});"
    "document.querySelectorAll('.person-block').forEach(updateProgress);"
    "function showTour(){"
    "var ov=document.getElementById('tour');if(ov)ov.style.display='flex';}"
    "function hideTour(){"
    "var ov=document.getElementById('tour');if(ov)ov.style.display='none';"
    "try{localStorage.setItem('tour_seen','1');}catch(e){}}"
    "var tourBtn=document.getElementById('tour-close');"
    "if(tourBtn)tourBtn.onclick=hideTour;"
    "var helpBtn=document.getElementById('help-btn');"
    "if(helpBtn)helpBtn.onclick=showTour;"
    "try{if(!localStorage.getItem('tour_seen'))showTour();}catch(e){}"
    "document.querySelectorAll('.btn-reject-rest').forEach(function(btn){"
    "btn.addEventListener('click',function(){"
    "var person=btn.dataset.person;"
    "var block=btn.closest('.person-block');"
    "if(!block)return;"
    "var cards=block.querySelectorAll('.rec-card');"
    "var pending=[];"
    "cards.forEach(function(card){"
    "if(!card.classList.contains('tier-Likely')&&!card.classList.contains('confirmed')){"
    "pending.push(card);}});"
    "if(!pending.length)return;"
    "if(!confirm('Reject '+pending.length+' non-Likely record(s) for '+person+'?'))return;"
    "pending.forEach(function(card){"
    "var key=card.dataset.key,p=card.dataset.person;"
    "fetch('/confirm',{method:'POST',headers:{'Content-Type':'application/json'},"
    "body:JSON.stringify({key:key,status:'rejected',person:p})})"
    ".then(function(r){if(r.ok)applyCard(card,'rejected');"
    "else r.text().then(function(t){alert('Save failed: '+t);});})"
    ".catch(function(e){alert('Save failed (offline?): '+e);});});});});"
    "})();"
    "</script>"
)


def _name_search_script() -> str:
    nk_json = json.dumps(NICKNAMES)
    return (
        "<script>(function(){var NK=" + nk_json + ";"
        "function expandFore(name){"
        "var key=name.toLowerCase().trim();"
        "if(!key)return[];"
        "var out=[name],seen={};"
        "seen[key]=1;"
        "(NK[key]||[]).forEach(function(v){if(!seen[v]){out.push(v);seen[v]=1;}});"
        "Object.keys(NK).forEach(function(canon){"
        "if(NK[canon].indexOf(key)>=0&&!seen[canon]){"
        "out.push(canon);seen[canon]=1;"
        "NK[canon].forEach(function(v){if(!seen[v]){out.push(v);seen[v]=1;}});"
        "}});"
        "return out;}"
        "function filterBlocks(){"
        "var fore=(document.getElementById('ns-fore').value||'').trim();"
        "var sur=(document.getElementById('ns-sur').value||'').trim().toLowerCase();"
        "var useVar=document.getElementById('ns-use-variants').checked;"
        "var foreTerms=fore?(useVar?expandFore(fore):[fore]).map(function(t){return t.toLowerCase();}):null;"
        "document.querySelectorAll('.person-block').forEach(function(b){"
        "if(!fore&&!sur){b.style.display='';return;}"
        "var pn=(b.dataset.person||'').toLowerCase();"
        "var sp=pn.indexOf(' ');"
        "var pf=sp>=0?pn.slice(0,sp):pn;"
        "var ps=sp>=0?pn.slice(sp+1):'';"
        "var fm=!foreTerms||foreTerms.indexOf(pf)>=0;"
        "var sm=!sur||ps.indexOf(sur)>=0;"
        "b.style.display=(fm&&sm)?'':'none';});}"
        "function updateChips(){"
        "var fore=(document.getElementById('ns-fore').value||'').trim();"
        "var vd=document.getElementById('ns-variants');"
        "var ce=document.getElementById('ns-chips');"
        "if(!fore){vd.style.display='none';return;}"
        "var variants=expandFore(fore).slice(1);"
        "if(!variants.length){vd.style.display='none';return;}"
        "ce.innerHTML=variants.map(function(v){"
        "return '<span class=\"ns-chip\">'+v+'</span>';}).join('');"
        "vd.style.display='flex';}"
        "var nf=document.getElementById('ns-fore');"
        "var ns2=document.getElementById('ns-sur');"
        "var nc=document.getElementById('ns-clear');"
        "var nu=document.getElementById('ns-use-variants');"
        "if(nf)nf.oninput=function(){updateChips();filterBlocks();};"
        "if(ns2)ns2.oninput=function(){filterBlocks();};"
        "if(nc)nc.onclick=function(){"
        "nf.value='';ns2.value='';"
        "document.getElementById('ns-variants').style.display='none';"
        "document.querySelectorAll('.person-block').forEach(function(b){b.style.display='';});};"
        "if(nu)nu.onchange=function(){filterBlocks();};"
        "})();</script>"
    )


_TOUR_HTML = (
    "<div id='tour' class='tour-overlay' style='display:none'>"
    "<div class='tour-box'>"
    "<h3>🌳 Welcome!</h3>"
    "<p>Here's how to use this page:</p>"
    "<ul>"
    "<li><strong>⭐ Stars</strong> show how likely each record matches. ★★★ = very likely.</li>"
    "<li><strong>🔎 Open the record</strong> takes you to the original source.</li>"
    "<li><strong>👍 That's them</strong> saves a confirmation. <strong>👎 Not them</strong> rejects it.</li>"
    "<li>The progress bar at the top of each person fills as you confirm records.</li>"
    "<li>Use the <strong>filter box</strong> to find a name, place or year fast.</li>"
    "<li>Hover the emoji on each card for a fact about that record type.</li>"
    "</ul>"
    "<button type='button' id='tour-close' class='tour-btn'>Got it — let's explore 🚀</button>"
    "</div></div>"
    "<button type='button' id='help-btn' title='Show the welcome tour again'>?</button>"
)


def _esc(s: str) -> str:
    return html.escape(s or "")


def _record_icon_key(record_type: str) -> str:
    rt = (record_type or "").lower()
    if "census" in rt:
        return "census"
    if "civil birth" in rt or ("birth" in rt and "baptism" not in rt):
        return "civil birth"
    if "civil marriage" in rt:
        return "civil marriage"
    if "church marriage" in rt:
        return "church marriage"
    if "civil death" in rt:
        return "civil death"
    if "baptism" in rt:
        return "church baptism"
    if "burial" in rt:
        return "church burial"
    if "griff" in rt or "land" in rt:
        return "griffith"
    if "manual" in rt:
        return "manual"
    return ""


def _record_icon(record_type: str) -> str:
    return RECORD_ICONS.get(_record_icon_key(record_type), "📄")


def _record_fact(record_type: str) -> str:
    return RECORD_FACTS.get(_record_icon_key(record_type), "A genealogy record.")


def _narrate(r: Record) -> str:
    """Natural sentence describing one record."""
    name = (r.name_found or "").strip() or "Someone"
    rtype = (r.record_type or "").strip()
    place = (r.place or "").strip()
    year = (r.age_or_year or "").strip()
    bits = [f"<strong>{_esc(name)}</strong>"]
    if rtype:
        bits.append(f"in a {_esc(rtype.lower())} record")
    if year:
        bits.append(f"({_esc(year)})")
    if place:
        bits.append(f"at <strong>{_esc(place)}</strong>")
    return " ".join(bits) + "."


def _variant_tag(r: Record) -> str:
    used = (r.name_used or "").strip()
    primary = (r.person_searched or "").strip()
    if not used or used.lower() == primary.lower():
        return ""
    return f"<span class='chip chip-variant' title='We searched using this name variant'>🔀 tried: {_esc(used)}</span>"


def _html_head(title: str) -> list[str]:
    return [
        "<!doctype html><html><head><meta charset='utf-8'>",
        f"<title>{_esc(title)}</title>",
        "<style>",
        _BASE_CSS,
        _DARK_CSS,
        "</style></head><body>",
    ]


def _html_controls() -> str:
    return (
        "<div class='name-search'>"
        "<div class='name-search-title'>🔍 Who do you want to find?</div>"
        "<div class='name-search-row'>"
        "<div class='name-input-wrap'>"
        "<label class='name-label' for='ns-fore'>👤 First name</label>"
        "<input class='name-input' id='ns-fore' type='text' placeholder='e.g. William' autocomplete='off'>"
        "</div>"
        "<div class='name-input-wrap'>"
        "<label class='name-label' for='ns-sur'>👪 Last name</label>"
        "<input class='name-input' id='ns-sur' type='text' placeholder='e.g. Furey' autocomplete='off'>"
        "</div>"
        "<button class='btn-ns-clear' id='ns-clear' type='button'>✖ Show everyone</button>"
        "</div>"
        "<div class='ns-variants' id='ns-variants' style='display:none'>"
        "<span class='ns-variants-label'>Also known as:</span>"
        "<span id='ns-chips'></span>"
        "<label class='ns-toggle-label'>"
        "<input type='checkbox' id='ns-use-variants'>"
        " Look for all these names too \u2728"
        "</label>"
        "</div>"
        "</div>"
        "<div class='controls'>"
        "<input class='filter' id='filter' placeholder='Find a name, place or year...' type='search'>"
        "<button class='dark-toggle' id='dm' type='button'>\U0001f319 Toggle dark mode</button>"
        "<label style='margin-left:.4em'>"
        "<input type='checkbox' id='hr'> Hide rejected</label>"
        "</div>"
    )


def slugify(name: str) -> str:
    """Filesystem-safe slug used for per-person HTML filenames."""
    s = (name or "unknown").lower().strip()
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s or "unknown"


def summarise_person(recs: list[Record]) -> str:
    """One-line hit-count summary across record types for a single person."""
    counts: dict[str, int] = {}
    manual = 0
    for r in recs:
        if "manual" in r.record_type.lower():
            manual += 1
            continue
        if r.record_type.startswith("Census"):
            counts["census hits"] = counts.get("census hits", 0) + 1
        elif "Civil Birth" in r.record_type:
            counts["civil births"] = counts.get("civil births", 0) + 1
        elif "Civil Marriage" in r.record_type:
            counts["civil marriages"] = counts.get("civil marriages", 0) + 1
        elif "Civil Death" in r.record_type:
            counts["civil deaths"] = counts.get("civil deaths", 0) + 1
        elif "Church" in r.record_type:
            counts["church records"] = counts.get("church records", 0) + 1
        else:
            counts["other"] = counts.get("other", 0) + 1
    parts = [f"{v} {k}" for k, v in counts.items()]
    if manual:
        parts.append(f"{manual} manual link(s)")
    return ", ".join(parts) if parts else "no records"


def _tier_counts(recs: list[Record]) -> dict[str, int]:
    out = {t: 0 for t in TIER_ORDER}
    for r in recs:
        out[confidence_tier(r.score)] += 1
    return out


def _avatar_initials(name: str) -> str:
    parts = [p for p in (name or "").split() if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def _hero_sentence(name: str, tiers: dict[str, int], total: int) -> str:
    if total == 0:
        return "We didn't find anything yet."
    bits = [f"We found <strong>{total}</strong> clue{'s' if total != 1 else ''}."]
    tags = []
    if tiers["Likely"]:
        tags.append(f"{tiers['Likely']} very likely")
    if tiers["Possible"]:
        tags.append(f"{tiers['Possible']} possible")
    if tiers["Weak"]:
        tags.append(f"{tiers['Weak']} weak")
    if tiers["Manual"]:
        tags.append(f"{tiers['Manual']} to check by hand")
    if tags:
        bits.append(" &middot; ".join(tags) + ".")
    return " ".join(bits)


def _render_household(r: Record) -> str:
    """Compact chip listing co-residents when household data is present."""
    names = r.household
    if not names:
        return ""
    MAX_SHOW = 4
    shown = names[:MAX_SHOW]
    extra = len(names) - MAX_SHOW
    text = ", ".join(_esc(n) for n in shown)
    if extra > 0:
        text += f" <em>(+{extra} more)</em>"
    return f"<div class='rec-household'>🏠 Lived with: {text}</div>"


def _render_why(r: Record) -> str:
    """Expandable chip listing each score component and its signed delta.
    Empty when the record has no breakdown (manual rows, legacy cached data)."""
    bd = r.score_breakdown or {}
    if not bd:
        return ""
    items: list[str] = []
    for key, delta in bd.items():
        if delta == 0:
            continue
        cls = "why-pos" if delta > 0 else "why-neg"
        sign = f"+{delta}" if delta > 0 else str(delta)
        label = _esc(_breakdown_label(key))
        items.append(f"<li>{label} <span class='{cls}'>{sign}</span></li>")
    if not items:
        return ""
    return (
        "<details class='rec-why'>"
        f"<summary title='Which signals added up to the score'>🧮 Why this score? ({r.score})</summary>"
        f"<ul class='why-list'>{''.join(items)}</ul>"
        "</details>"
    )


def _render_card(r: Record) -> str:
    tier = confidence_tier(r.score)
    icon = _record_icon(r.record_type)
    fact = _record_fact(r.record_type)
    stars = TIER_STARS[tier]
    key = record_key(r)

    chips: list[str] = []
    if r.place:
        chips.append(f"<span class='chip chip-place'>📍 {_esc(r.place)}</span>")
    if r.age_or_year:
        chips.append(f"<span class='chip chip-year'>📅 {_esc(r.age_or_year)}</span>")
    if r.is_new:
        chips.append("<span class='chip chip-new' title='Not in the previous run'>✨ NEW</span>")
    if r.kin_hits:
        chips.append(f"<span class='chip chip-kin' title='Related family names seen in the same record'>👨‍👩‍👧 family: {_esc(r.kin_hits)}</span>")
    vt = _variant_tag(r)
    if vt:
        chips.append(vt)

    thumb = ""
    if r.thumbnail:
        thumb = (f"<a href='{_esc(r.thumbnail)}' target='_blank'>"
                 f"<img class='thumb' src='{_esc(r.thumbnail)}' alt=''></a>")

    status_cls = f" {r.status}" if r.status in ("confirmed", "rejected") else ""
    c_on = " on-confirm" if r.status == "confirmed" else ""
    x_on = " on-reject" if r.status == "rejected" else ""

    title_bits = []
    if r.record_type:
        title_bits.append(_esc(r.record_type))
    if r.site:
        title_bits.append(_esc(r.site))
    title = " &middot; ".join(title_bits) or "Record"

    notes_html = ""
    if r.user_notes:
        notes_html = f"<div class='rec-notes'><em>{_esc(r.user_notes)}</em></div>"

    other = ""
    if r.other_details:
        other = f"<div class='rec-other' style='color:#555;font-size:.9em;margin:.3em 0'>{_esc(r.other_details)}</div>"

    return (
        f"<article class='rec-card tier-{tier}{status_cls}' data-key='{key}' data-person='{_esc(r.person_searched)}'>"
        f"<div class='rec-head'>"
        f"<span class='rec-icon' title='{_esc(fact)}'>{icon}</span>"
        f"<span class='rec-title'>{title}</span>"
        f"<span class='rec-stars' title='{tier} match'>{stars}</span>"
        f"</div>"
        f"<p class='rec-sentence'>{_narrate(r)}</p>"
        f"<div class='rec-meta'>{''.join(chips)}</div>"
        f"{_render_why(r)}"
        f"{_render_household(r)}"
        f"{thumb}"
        f"{other}"
        f"{notes_html}"
        f"<div class='rec-actions'>"
        f"<a class='btn-open' href='{_esc(r.link)}' target='_blank'>🔎 Open the record</a>"
        f"<div class='conf-btns'>"
        f"<button type='button' data-action='confirmed' class='{c_on.strip()}' title='That\\u2019s them'>👍 That\u2019s them</button>"
        f"<button type='button' data-action='rejected' class='{x_on.strip()}' title='Not them'>👎 Not them</button>"
        f"</div></div>"
        f"</article>"
    )


def _tier_section(tier: str, recs: list[Record]) -> str:
    if not recs:
        return ""
    head = (f"<div class='tier-head'>"
            f"<span class='stars'>{TIER_STARS[tier]}</span>"
            f"<span class='tier tier-{tier}'>{tier}</span>"
            f"<span>({len(recs)})</span>"
            f"</div>"
            f"<p class='tier-blurb'>{TIER_BLURB[tier]}</p>")
    cards = "".join(_render_card(r) for r in recs)
    return f"<section class='tier-section tier-{tier}-section'>{head}{cards}</section>"


def render_person_table(recs: list[Record], collapse_weak: bool = True) -> str:
    """Render a single person's records as cards grouped by tier."""
    if not recs:
        return "<p class='empty'>No records.</p>"

    by_tier: dict[str, list[Record]] = {t: [] for t in TIER_ORDER}
    for r in recs:
        by_tier[confidence_tier(r.score)].append(r)

    parts: list[str] = []
    for tier in ("Likely", "Possible"):
        parts.append(_tier_section(tier, by_tier[tier]))

    weak = by_tier["Weak"]
    if weak:
        if collapse_weak:
            parts.append(
                f"<details><summary>Show {len(weak)} weak matches (weak-tier record)</summary>"
                f"{_tier_section('Weak', weak)}</details>"
            )
        else:
            parts.append(_tier_section("Weak", weak))

    parts.append(_tier_section("Manual", by_tier["Manual"]))
    return "".join(parts)


def _person_hero(name: str, recs: list[Record]) -> str:
    tiers = _tier_counts(recs)
    total = len(recs)
    confirmed = sum(1 for r in recs if r.status == "confirmed")
    pct = (100 * confirmed / total) if total else 0
    initials = _avatar_initials(name)
    sentence = _hero_sentence(name, tiers, total)
    return (
        f"<div class='person-hero'>"
        f"<div class='avatar'>{_esc(initials)}</div>"
        f"<div class='hero-body'>"
        f"<h2 class='hero-name'>{_esc(name)}</h2>"
        f"<p class='hero-sub'>{sentence}</p>"
        f"<div class='progress'>"
        f"<div class='progress-bar'><div class='progress-fill' style='width:{pct:.0f}%'></div></div>"
        f"<div class='progress-text'>✓ {confirmed} of {total} confirmed</div>"
        f"</div>"
        f"<div style='display:flex;justify-content:flex-end;margin-top:.5em'>"
        f"<button type='button' class='btn-reject-rest' data-person='{_esc(name)}'"
        f" title='Reject all non-Likely records that have not been confirmed'>👎 Reject rest</button>"
        f"</div>"
        f"</div></div>"
    )


def render_results_html(records: list[Record], stamp: str,
                        collapse_weak: bool = True) -> str:
    """Render the single-page results view: all people inline."""
    grouped: dict[str, list[Record]] = {}
    for r in records:
        grouped.setdefault(r.person_searched, []).append(r)

    parts = _html_head("Irish genealogy search results")
    parts.append("<h1>🌳 Irish genealogy search results</h1>")
    parts.append(f"<p>Generated {_esc(stamp)}.</p>")
    parts.append(_html_controls())

    if not grouped:
        parts.append("<p class='empty'>No records found.</p>")

    for person, recs in grouped.items():
        parts.append(f"<div class='person-block' data-person='{_esc(person)}'>")
        parts.append(_person_hero(person, recs))
        parts.append(render_person_table(recs, collapse_weak=collapse_weak))
        parts.append("</div>")

    parts.append(_TOUR_HTML)
    parts.append(_HTML_SCRIPT)
    parts.append(_name_search_script())
    parts.append("</body></html>")
    return "\n".join(parts)


def render_person_page(name: str, recs: list[Record], stamp: str,
                       back_href: str, collapse_weak: bool = True) -> str:
    """Render one person's page for the per-person output layout."""
    parts = _html_head(f"{name} - genealogy results")
    parts.append(f"<p><a href='{_esc(back_href)}'>&larr; back to index</a></p>")
    parts.append(f"<p>Generated {_esc(stamp)}.</p>")
    parts.append(_html_controls())
    parts.append(f"<div class='person-block' data-person='{_esc(name)}'>")
    parts.append(_person_hero(name, recs))
    parts.append(render_person_table(recs, collapse_weak=collapse_weak))
    parts.append("</div>")
    parts.append(_TOUR_HTML)
    parts.append(_HTML_SCRIPT)
    parts.append(_name_search_script())
    parts.append("</body></html>")
    return "\n".join(parts)


def render_index(records: list[Record], rel_map: dict[str, str], stamp: str) -> str:
    """Render the TOC index that links out to per-person pages."""
    grouped: dict[str, list[Record]] = {}
    for r in records:
        grouped.setdefault(r.person_searched, []).append(r)
    parts = _html_head("Irish genealogy search results")
    parts.append("<h1>🌳 Irish genealogy search results</h1>")
    parts.append(f"<p>Generated {_esc(stamp)}. Click a name for their records.</p>")
    parts.append(_html_controls())
    parts.append("<ul class='toc'>")
    for name, recs in grouped.items():
        tiers = _tier_counts(recs)
        badges = " ".join(
            f"<span class='tier tier-{k}'>{k}:{v}</span>"
            for k, v in tiers.items() if v
        )
        href = rel_map.get(name, "#")
        initials = _avatar_initials(name)
        parts.append(
            f"<li>"
            f"<span class='avatar' style='display:inline-flex;width:32px;height:32px;font-size:.9em;vertical-align:middle;margin-right:.5em'>{_esc(initials)}</span>"
            f"<a href='{_esc(href)}'>{_esc(name)}</a> &mdash; {len(recs)} record(s) {badges}"
            f"</li>"
        )
    parts.append("</ul>")
    parts.append(_TOUR_HTML)
    parts.append(_HTML_SCRIPT)
    parts.append("</body></html>")
    return "\n".join(parts)
