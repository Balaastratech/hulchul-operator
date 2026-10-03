import sys
from playwright.sync_api import sync_playwright

urls = [
    ("smartrecruiters", "https://jobs.smartrecruiters.com/Expeditors/744000153279529-night-shift-monitoring-supervisor"),
    ("breezy", "https://acme-consulting.breezy.hr/p/123456"),
    ("lever_palantir", "https://jobs.lever.co/palantir/6ed76ce8-4156-4b60-b120-403538bd66cd/apply"),
    ("greenhouse_vercel", "https://job-boards.greenhouse.io/vercel/jobs/6136160004"),
    ("ashby_ashby", "https://jobs.ashbyhq.com/ashby/7458d4e9-da2e-47bd-98cb-adfda43d42b2/application"),
    ("workable_apna", "https://careers.apna.co/_/j/8161DF2AC9/apply"),
]

with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    pg = b.new_page()
    for name, u in urls:
        print(f"--- {name} ---")
        try:
            pg.goto(u, wait_until="domcontentloaded", timeout=20000)
            pg.wait_for_timeout(3000)
            print("URL:", pg.url)
            print("Title:", pg.title())
            btn = pg.query_selector('a:has-text("I\'m interested"), a:has-text("Apply"), button:has-text("Apply")')
            if btn:
                print("Found apply button:", btn.inner_text(), btn.get_attribute("href"))
            inputs = pg.evaluate("() => [...document.querySelectorAll('input, select, textarea')].map(e => e.name || e.id || e.placeholder)")
            print(f"Inputs count: {len(inputs)}")
        except Exception as e:
            print("ERR:", str(e)[:150])
    b.close()
