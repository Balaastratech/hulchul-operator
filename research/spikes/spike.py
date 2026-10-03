"""Feasibility spike: can an LLM + Playwright fill UNSEEN real ATS forms and verify? Never submits."""
import json, subprocess, sys, time, urllib.request, os
from playwright.sync_api import sync_playwright

PROJECT = "ai-negotiation-copilot"
MODEL = os.environ.get("SPIKE_MODEL", "gemini-3-flash-preview")
OUT = os.path.expanduser("~/spike/out"); os.makedirs(OUT, exist_ok=True)

PROFILE = {
  "full_name": "Aarav Mehta", "first_name": "Aarav", "last_name": "Mehta",
  "email": "aarav.mehta.test@example.com", "phone": "+91 98765 43210",
  "city": "Ahmedabad", "country": "India", "location": "Ahmedabad, India",
  "linkedin": "https://www.linkedin.com/in/aarav-mehta-test", "github": "https://github.com/aarav-test",
  "portfolio": "https://aarav-test.dev", "current_company": "Acme Labs", "current_title": "Software Engineer",
  "years_experience": 3, "skills": ["TypeScript", "React", "Node.js", "Python", "PostgreSQL"],
  "summary": "Full-stack engineer with 3 years building web apps and LLM-powered automations.",
  "work_authorization": "Authorized to work in India; would require visa sponsorship elsewhere",
  "notice_period": "30 days", "willing_to_relocate": False,
}
RULES = ["Never invent facts missing from PROFILE: use ask_user.",
         "Salary expectations, gender/race/veteran/disability (EEO) and legal attestations: ask_user.",
         "Essay questions: write 2-3 honest sentences from PROFILE only; set generated=true.",
         "Use upload_resume for resume/CV file inputs; skip cover-letter/other uploads.",
         "Never choose any submit/apply button; you only fill fields."]

EXTRACT = """() => {
 window.__n = window.__n || 0;
 const txt = s => (s||'').replace(/\\s+/g,' ').trim();
 const lab = e => {
   let t='';
   if (e.labels && e.labels.length) t=[...e.labels].map(l=>l.innerText).join(' ');
   if(!t && e.getAttribute('aria-labelledby')) t=e.getAttribute('aria-labelledby').split(' ').map(i=>(document.getElementById(i)||{}).innerText||'').join(' ');
   if(!t) t=e.getAttribute('aria-label')||'';
   if(!t){let p=e.parentElement,d=0; while(p&&d<5&&!t){const l=p.querySelector('label,legend'); if(l&&l.innerText.trim()) t=l.innerText; p=p.parentElement; d++;}}
   if(!t) t=e.placeholder||e.name||'';
   return txt(t).slice(0,200);
 };
 const grp = e => { const g=e.closest('fieldset,[role=group],[role=radiogroup]'); if(!g) return '';
   const l=g.querySelector('legend,[id]>label,label'); return txt((g.getAttribute('aria-label')||(l&&l.innerText)||'')).slice(0,200); };
 const els=[...document.querySelectorAll('input:not([type=hidden]):not([type=submit]):not([type=button]), textarea, select')];
 const out=[];
 for (const e of els){
   const isFile=e.type==='file'; const r=e.getBoundingClientRect(); const s=getComputedStyle(e);
   const shown=(r.width>0&&r.height>0&&s.visibility!=='hidden')||isFile||e.type==='checkbox'||e.type==='radio';
   if(!shown) continue;
   if(e.closest('[aria-hidden=true]') && !isFile) continue;
   if(!e.dataset.opid){ e.dataset.opid=String(++window.__n); }
   const combo = e.getAttribute('role')==='combobox' || e.getAttribute('aria-haspopup')==='listbox' || e.getAttribute('aria-autocomplete')==='list';
   let value = e.tagName==='SELECT' ? txt([...e.selectedOptions].map(o=>o.text).join(',')) :
               (e.type==='checkbox'||e.type==='radio') ? String(e.checked) : e.value;
   if (combo && !value){ const c=e.closest('div[class*=select],div[class*=Select],div[class*=control]')||e.parentElement.parentElement; value=txt(c?c.innerText:'').slice(0,80); }
   out.push({id:+e.dataset.opid, tag:e.tagName.toLowerCase(), type:e.type||'', combo, label:lab(e), group:grp(e),
     required: e.required||e.getAttribute('aria-required')==='true'||/\\*/.test(lab(e)),
     options: e.tagName==='SELECT'?[...e.options].map(o=>txt(o.text)).slice(0,40):undefined, value:(value||'').slice(0,120)});
 }
 return out; }"""

def token():
    return subprocess.check_output("gcloud auth print-access-token", shell=True, text=True).strip()

def llm(fields):
    prompt = ("You fill a job application form for a candidate.\nRULES:\n- " + "\n- ".join(RULES) +
      "\nPROFILE:\n" + json.dumps(PROFILE) + "\nFIELDS (id,label,group,type,required,options,current value):\n" + json.dumps(fields) +
      '\nReturn JSON {"actions":[{"id":int,"action":"fill|select|check|upload_resume|ask_user|skip","value":str,"question":str,"generated":bool}]}.'
      " For radio/checkbox, use action check on the option id you choose. Skip fields already correctly filled.")
    url = f"https://aiplatform.googleapis.com/v1/projects/{PROJECT}/locations/global/publishers/google/models/{MODEL}:generateContent"
    body = {"contents":[{"role":"user","parts":[{"text":prompt}]}], "generationConfig":{"responseMimeType":"application/json","temperature":0}}
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, json.dumps(body).encode(), {"Authorization":"Bearer "+token(),"Content-Type":"application/json"})
            r = json.load(urllib.request.urlopen(req, timeout=120))
            return json.loads(r["candidates"][0]["content"]["parts"][0]["text"])["actions"], r.get("usageMetadata",{})
        except Exception as e:
            print("   llm retry", str(e)[:120]); time.sleep(3)
    return [], {}

def execute(page, a, byid):
    f = byid[a["id"]]; loc = page.locator(f'[data-opid="{a["id"]}"]').first
    act, val = a["action"], str(a.get("value",""))
    if act == "upload_resume":
        loc.set_input_files(os.path.join(OUT, "resume.pdf")); return
    if act == "check":
        loc.check(force=True, timeout=4000); return
    if f["tag"] == "select":
        try: loc.select_option(label=val, timeout=3000)
        except Exception:
            opt = next((o for o in f["options"] if val.lower() in o.lower() or o.lower() in val.lower()), None)
            loc.select_option(label=opt, timeout=3000)
        return
    if f["combo"]:
        loc.click(timeout=4000); loc.fill(val, timeout=4000); page.wait_for_timeout(700)
        opt = page.locator('[role=option]').filter(has_text=val).first
        if opt.count(): opt.click(timeout=3000)
        else: page.keyboard.press("Enter")
        return
    loc.fill(val, timeout=4000)

def verify(f_after, a):
    cur = f_after.get(a["id"])
    if cur is None: return False
    if a["action"] == "check": return cur["value"] == "true"
    if a["action"] == "upload_resume": return True  # checked separately via file name
    v = str(a.get("value","")).lower()
    return v and (v in cur["value"].lower() or cur["value"].lower() in v)

def run(name, url, pw):
    print(f"\n===== {name} {url}")
    b = pw.chromium.launch(channel="chrome", headless=True); ctx = b.new_context(viewport={"width":1280,"height":900}); page = ctx.new_page()
    page.goto(url, wait_until="networkidle", timeout=60000); page.wait_for_timeout(2500)
    stats = dict(site=name, rounds=0, llm_calls=0, tokens=0, planned=0, exec_ok=0, exec_fail=[], verified=0, unverified=[], ask_user=[], skipped=0, secs=0)
    t0 = time.time(); seen = set()
    for rnd in range(3):
        fields = [f for f in page.evaluate(EXTRACT) if f["id"] not in seen]
        if not fields: break
        stats["rounds"] += 1; byid = {f["id"]: f for f in fields}
        acts, usage = llm(fields); stats["llm_calls"] += 1; stats["tokens"] += usage.get("totalTokenCount", 0)
        done = []
        for a in acts:
            if a.get("id") not in byid: continue
            seen.add(a["id"])
            if a["action"] == "ask_user": stats["ask_user"].append(f'{byid[a["id"]]["label"][:60]} -> {a.get("question","")[:60]}'); continue
            if a["action"] == "skip": stats["skipped"] += 1; continue
            stats["planned"] += 1
            try: execute(page, a, byid); stats["exec_ok"] += 1; done.append(a)
            except Exception as e: stats["exec_fail"].append(f'{byid[a["id"]]["label"][:50]} [{a["action"]}] {str(e)[:70]}')
        page.wait_for_timeout(1200)
        after = {f["id"]: f for f in page.evaluate(EXTRACT)}
        for a in done:
            if verify(after, a): stats["verified"] += 1
            else: stats["unverified"].append(f'{byid[a["id"]]["label"][:50]} wanted={str(a.get("value"))[:30]} got={after.get(a["id"],{}).get("value","?")[:30]}')
        seen |= set(f["id"] for f in fields)
    stats["secs"] = round(time.time() - t0, 1)
    page.screenshot(path=os.path.join(OUT, f"{name}.png"), full_page=True)
    print(json.dumps(stats, indent=1)); b.close(); return stats

if __name__ == "__main__":
    URLS = {"greenhouse":"https://job-boards.greenhouse.io/vercel/jobs/6136160004",
            "lever":"https://jobs.lever.co/palantir/6ed76ce8-4156-4b60-b120-403538bd66cd/apply",
            "ashby":"https://jobs.ashbyhq.com/ashby/7458d4e9-da2e-47bd-98cb-adfda43d42b2/application"}
    with sync_playwright() as pw:
        pg = pw.chromium.launch(channel="chrome", headless=True).new_page()
        pg.set_content("<h1>Aarav Mehta</h1><p>Software Engineer. TypeScript, React, Node, Python. Acme Labs 2023-2026.</p>"); pg.pdf(path=os.path.join(OUT, "resume.pdf"))
        res = [run(n, u, pw) for n, u in URLS.items() if len(sys.argv) < 2 or n in sys.argv[1:]]
    json.dump(res, open(os.path.join(OUT, "results.json"), "w"), indent=1)
