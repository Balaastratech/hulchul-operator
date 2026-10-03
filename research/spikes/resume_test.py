"""Phase A fills a real Lever form via LLM then 'crashes'; phase B reattaches via CDP, edits one field, reloads, replays with 0 LLM calls."""
import json, os, subprocess, sys, time, urllib.request
sys.path.insert(0, os.path.dirname(__file__)); import spike
from playwright.sync_api import sync_playwright
URL = "https://jobs.lever.co/palantir/6ed76ce8-4156-4b60-b120-403538bd66cd/apply"
LOG = os.path.join(spike.OUT, "action_log.json"); PORT = 9333

def key(f, n): return f'{f["label"]}|{f["type"]}|{f["group"]}|{n}'
def keyed(fields):
    seen, out = {}, {}
    for f in fields:
        k0 = f'{f["label"]}|{f["type"]}|{f["group"]}'; seen[k0] = seen.get(k0, -1) + 1; out[key(f, seen[k0])] = f
    return out

def phase_a():
    chrome = subprocess.Popen([r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe", f"--remote-debugging-port={PORT}", "--headless=new",
        f"--user-data-dir={os.path.expanduser('~/spike/profile')}", "--no-first-run", "about:blank"])
    time.sleep(4)
    with sync_playwright() as pw:
        b = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{PORT}"); page = b.contexts[0].pages[0]
        page.goto(URL, wait_until="networkidle"); page.wait_for_timeout(2000)
        fields = page.evaluate(spike.EXTRACT); byid = {f["id"]: f for f in fields}; k = {f["id"]: kk for kk, f in keyed(fields).items()}
        acts, _ = spike.llm(fields); log = []
        for a in acts:
            if a["id"] in byid and a["action"] in ("fill", "select", "check", "upload_resume"):
                try: spike.execute(page, a, byid); log.append({"key": k[a["id"]], "action": a["action"], "value": a.get("value", "")})
                except Exception as e: print("A fail", byid[a["id"]]["label"][:40], str(e)[:50])
        json.dump(log, open(LOG, "w"), indent=1); print(f"A: filled {len(log)} fields, LLM calls=1. Simulating operator crash (process exits, browser stays).")
    return chrome.pid

def read(page):  # key -> value
    return {kk: f["value"] for kk, f in keyed(page.evaluate(spike.EXTRACT)).items()}

def phase_b():
    log = json.load(open(LOG)); llm_calls = 0
    with sync_playwright() as pw:
        b = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{PORT}"); page = b.contexts[0].pages[0]
        vals = read(page); kept = sum(1 for e in log if e["action"] in ("fill",) and vals.get(e["key"], "") not in ("", None))
        print(f"B1 reattached after 'crash': {kept}/{sum(1 for e in log if e['action']=='fill')} text fields still filled, 0 re-fills, URL unchanged={page.url==URL}")
        # edit ONE field by stable key, no LLM
        email_key = next(e["key"] for e in log if "email" in e["key"].lower())
        byid = {f["id"]: f for f in page.evaluate(spike.EXTRACT)}; kk = {v: i for i, v in ((i, k_) for k_, f in keyed(list(byid.values())).items() for i in [f["id"]])}
        spike.execute(page, {"id": kk[email_key], "action": "fill", "value": "changed.email@example.com"}, byid)
        print("B2 edit one field:", read(page)[email_key])
        for e in log:
            if e["key"] == email_key: e["value"] = "changed.email@example.com"
        # hard reload = lose all page state -> replay from action log, zero LLM
        page.reload(wait_until="networkidle"); page.wait_for_timeout(2000)
        print("B3 after reload, text fields filled:", sum(1 for v in read(page).values() if v))
        t0 = time.time(); fields = page.evaluate(spike.EXTRACT); byid = {f["id"]: f for f in fields}; km = keyed(fields); ok = miss = 0
        for e in log:
            f = km.get(e["key"])
            if not f: miss += 1; continue
            try: spike.execute(page, {"id": f["id"], "action": e["action"], "value": e["value"]}, byid); ok += 1
            except Exception: miss += 1
        page.wait_for_timeout(800); after = read(page)
        good = sum(1 for e in log if e["action"] == "fill" and e["value"].lower() in after.get(e["key"], "").lower())
        print(f"B4 replay: {ok} actions replayed, {miss} missed, {good} text values verified, LLM calls={llm_calls}, {time.time()-t0:.1f}s; email={after[email_key]}")

def probe_blockers():
    from playwright.sync_api import sync_playwright
    tests = {"linkedin_job": "https://www.linkedin.com/jobs/view/4000000000", "workday_apply": "https://workday.wd5.myworkdayjobs.com/en-US/Workday"}
    JS = """() => ({pw: !!document.querySelector('input[type=password]'), signin: /sign in|log in|create account/i.test(document.body.innerText.slice(0,1500)),
      captchaVisible: [...document.querySelectorAll('iframe[src*=captcha],iframe[src*=turnstile],iframe[title*=challenge i]')].some(i=>{const r=i.getBoundingClientRect();return r.width>100&&r.height>100}),
      forms: document.querySelectorAll('input:not([type=hidden])').length, title: document.title.slice(0,50)})"""
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True); p = b.new_page()
        for n, u in tests.items():
            try: p.goto(u, wait_until="domcontentloaded", timeout=40000); p.wait_for_timeout(3000); print("BLOCKER", n, p.url[:60], json.dumps(p.evaluate(JS)))
            except Exception as e: print("BLOCKER", n, "ERR", str(e)[:100])
        b.close()

if __name__ == "__main__":
    if sys.argv[1] == "a": phase_a()
    elif sys.argv[1] == "b": phase_b()
    else: probe_blockers()
