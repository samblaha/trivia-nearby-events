"""Public invitation landing page. No member roster, email, or RSVP data is exposed."""

INVITE_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer"><meta name="robots" content="noindex">
<meta name="theme-color" content="#7047eb">
<title>You're invited · Trivia Nearby</title>
<style>
:root{color-scheme:light dark;--bg:#f6f4fb;--card:#fff;--text:#221b35;--muted:#685f78;--accent:#7047eb}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:17px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
main{max-width:540px;margin:7vh auto;padding:24px}.brand{font-weight:750;color:var(--accent)}
.icon{margin-top:44px;font-size:44px}h1{font-size:clamp(32px,7vw,46px);line-height:1.1;letter-spacing:-1.5px;margin:18px 0}
p{color:var(--muted)}.card{background:var(--card);border-radius:24px;padding:24px;margin:28px 0}
h2{font-size:23px;margin:0 0 8px;overflow-wrap:anywhere}.detail{white-space:pre-line;margin:0}
a.primary,button{font:inherit;font-weight:650;border:0;border-radius:14px;padding:16px 22px;text-align:center;cursor:pointer;width:100%;display:block}
a.primary{background:var(--accent);color:#fff;text-decoration:none}button{background:transparent;color:var(--accent);margin:8px 0}
.note{font-size:14px}.hidden{display:none!important}#status{min-height:24px;font-size:14px}
@media(prefers-color-scheme:dark){:root{--bg:#17131f;--card:#272030;--text:#f7f4ff;--muted:#bdb4cc;--accent:#aa8bff}a.primary{color:#17131f}}
</style>
</head><body><main>
<div class="brand">Trivia Nearby</div>
<div class="icon" aria-hidden="true">✦</div>
<h1>There’s a seat<br>for you.</h1>
<p>Good trivia is better together.</p>
<section class="card" aria-live="polite"><h2 id="title">Opening invitation…</h2><p class="detail" id="detail"></p></section>
<a class="primary hidden" id="open" href="#">Open in Trivia Nearby</a>
<button class="hidden" id="copy" type="button">Copy invitation link</button>
<p id="status" role="status"></p>
<p class="note" id="help">You’ll need Trivia Nearby installed to join. If you don’t have the app yet, ask the person who invited you for access.</p>
<p class="note">Opening an invitation never joins a team or sends a reply automatically.</p>
</main><script>
(() => {
  const title = document.getElementById('title'), detail = document.getElementById('detail');
  const open = document.getElementById('open'), copy = document.getElementById('copy'), status = document.getElementById('status');
  const params = new URLSearchParams(location.search);
  const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  function fail() { title.textContent='This invitation isn’t available'; detail.textContent='Ask your teammate for a fresh invitation link.'; }
  function ready(url) {
    open.href = url; open.classList.remove('hidden'); copy.classList.remove('hidden');
    copy.onclick = async () => {
      try { await navigator.clipboard.writeText(location.href); status.textContent='Invitation copied. Paste it in Profile → Teams in the app.'; }
      catch { status.textContent='Copy the address from your browser, then open Profile → Teams in the app.'; }
    };
  }
  if (location.pathname === '/invite/team') {
    const token=params.get('token'), event=params.get('event');
    if (!uuid.test(token||'') || params.getAll('token').length!==1 || params.getAll('event').length>1 || (event && !uuid.test(event))) return fail();
    const query=new URLSearchParams({token}); if(event) query.set('event',event);
    title.textContent='You’re invited to a trivia team';
    detail.textContent='Meet your teammates, plan a night, and let everyone know you’re in.';
    ready('trivianearby://team?'+query);
    fetch('https://jtzjfyjgzkdldbcewrdp.supabase.co/rest/v1/rpc/team_api', {
      method:'POST', headers:{'Content-Type':'application/json','apikey':'sb_publishable_EPwtwPsPoOQqT75QUc6QSA_brapMkI3'},
      body:JSON.stringify({action:'preview',args:{token}}), signal:AbortSignal.timeout(8000)
    }).then(async response => {
      if(!response.ok) {
        if(response.status===403 || response.status===400 || response.status===401) { fail(); open.classList.add('hidden'); copy.classList.add('hidden'); }
        return;
      }
      const data=await response.json();
      if(typeof data.name==='string') title.textContent=data.name;
      if(Number.isInteger(data.member_count)) detail.textContent=data.member_count+' '+(data.member_count===1?'player':'players')+' at the table.\nJoin in the app to see shared nights and RSVP.';
    }).catch(() => { status.textContent='The preview couldn’t load. You can still try opening this invitation in the app.'; });
  } else {
    try {
      const p=params.get('p');
      if(!p || p.length>16384 || params.getAll('p').length!==1) return fail();
      const encoded=p.replace(/-/g,'+').replace(/_/g,'/');
      const payload=JSON.parse(new TextDecoder().decode(Uint8Array.from(atob(encoded), c=>c.charCodeAt(0))));
      if(!['id','t','v','a','s','tz'].every(key=>typeof payload[key]==='string') || payload.t.length>240 || payload.v.length>240 || payload.a.length>2048) return fail();
      const date=new Date(payload.s); if(!Number.isFinite(date.getTime())) return fail();
      const when=new Intl.DateTimeFormat(undefined,{dateStyle:'full',timeStyle:'short',timeZone:payload.tz}).format(date);
      title.textContent=payload.t;
      detail.textContent=when+'\n'+payload.v+'\n'+payload.a;
      document.getElementById('help').textContent='No app? You can still use the event details above and reply directly to the person who invited you.';
      ready('trivianearby://invite?'+new URLSearchParams({p}));
    } catch { fail(); }
  }
})();
</script></body></html>"""
