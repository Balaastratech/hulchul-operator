import sys, json
from playwright.sync_api import sync_playwright
urls = {
 "greenhouse":"https://job-boards.greenhouse.io/vercel/jobs/6136160004",
 "lever":"https://jobs.lever.co/palantir/6ed76ce8-4156-4b60-b120-403538bd66cd/apply",
 "ashby":"https://jobs.ashbyhq.com/ashby/7458d4e9-da2e-47bd-98cb-adfda43d42b2/application",
}
JS = """() => {
 const f=[...document.querySelectorAll('input,textarea,select')].filter(e=>e.type!=='hidden');
 return {
  fields:f.length,
  files:f.filter(e=>e.type==='file').length,
  selects:f.filter(e=>e.tagName==='SELECT').length,
  checks:f.filter(e=>['checkbox','radio'].includes(e.type)).length,
  labels:f.slice(0,12).map(e=>(e.labels&&e.labels[0]?e.labels[0].innerText:(e.getAttribute('aria-label')||e.name||e.placeholder||e.type)).trim().slice(0,50)),
  recaptcha: !!document.querySelector('iframe[src*="recaptcha"],.g-recaptcha,[data-sitekey],iframe[src*="hcaptcha"],iframe[src*="turnstile"]'),
  loginWall: /sign in|log in/i.test(document.body.innerText.slice(0,600)) && f.length<3,
  iframes:[...document.querySelectorAll('iframe')].map(i=>i.src.slice(0,60)),
  submit:[...document.querySelectorAll('button,input[type=submit]')].map(b=>(b.innerText||b.value||'').trim()).filter(Boolean).slice(-4)
 }}"""
with sync_playwright() as p:
    try:
        b = p.chromium.launch(channel="chrome", headless=True)
    except Exception as e:
        print("LAUNCH FAIL", str(e)[:300]); sys.exit()
    for name,u in urls.items():
        pg = b.new_page()
        try:
            pg.goto(u, wait_until="networkidle", timeout=45000)
            pg.wait_for_timeout(2500)
            print(name, pg.title()[:50], json.dumps(pg.evaluate(JS)))
        except Exception as e:
            print(name,"ERR",str(e)[:200])
        pg.close()
    b.close()
