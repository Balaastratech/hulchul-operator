"""Driver for the S5/S6 spike. Secrets come from the .env (python-dotenv); never printed.

    python run_spike.py s5    --base https://<tunnel>   # Telegram delivery + prefetch (M1..M3)
    python run_spike.py s6    --base https://<tunnel>   # signed token + worker leg via the tunnel
    python run_spike.py phone --base https://<tunnel>   # M5, waits <= 10 min for a human POST

Only a redacted record (method name, HTTP status, ok, message_id) of Bot API calls is kept.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import statistics
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

import tokens

HERE = Path(__file__).resolve().parent
LOG_PATH = Path(os.environ.get("SPIKE_REQUEST_LOG", HERE / "requests.jsonl"))
WORKER_TOKEN_FILE = Path(os.environ.get("SPIKE_WORKER_TOKEN_FILE",
                                        Path(tempfile.gettempdir()) / "hulchul_spike_worker_token.txt"))
RUN = "spike-run"
MOBILE_RE = re.compile(r"Mobile|Android|iPhone|iPad", re.I)

logging.getLogger("httpx").setLevel(logging.WARNING)  # httpx INFO would log Bot API URLs (contain the token)
logging.getLogger("httpcore").setLevel(logging.WARNING)


def load_cfg():
    load_dotenv(os.environ.get("ENV_FILE", r"C:\Balaastra\hulchul-operator\.env"))
    cfg = {k: os.environ.get(k) for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "CP_SIGNING_KEY")}
    missing = [k for k, v in cfg.items() if not v]
    if missing:
        sys.exit(f"missing in .env: {', '.join(missing)}")
    return cfg


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def tg_call(cfg, method: str, payload: dict) -> dict:
    """Bot API call. Returns only redacted facts; exceptions are reduced to their type name."""
    url = f"https://api.telegram.org/bot{cfg['TELEGRAM_BOT_TOKEN']}/{method}"
    try:
        r = httpx.post(url, json=payload, timeout=15)
        body = r.json()
        res = {"method": method, "http_status": r.status_code, "ok": bool(body.get("ok"))}
        if body.get("ok") and "message_id" in (body.get("result") or {}):
            res["message_id"] = body["result"]["message_id"]
        if not body.get("ok"):
            res["error_code"] = body.get("error_code")
        return res
    except Exception as e:  # never str(e): httpx errors can embed the request URL
        return {"method": method, "http_status": None, "ok": False, "error_type": type(e).__name__}


def read_log() -> list[dict]:
    out = []
    if LOG_PATH.exists():
        for line in LOG_PATH.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def seconds_after(t0: datetime, ts: str) -> float:
    return round((datetime.fromisoformat(ts) - t0).total_seconds(), 2)


def hits_for(path: str, t0: datetime) -> list[dict]:
    rows = []
    for e in read_log():
        if e["path"] == path and datetime.fromisoformat(e["ts"]) >= t0:
            rows.append({"t_plus_s": seconds_after(t0, e["ts"]), "method": e["method"], "status": e["status"],
                         "user_agent": e["user_agent"], "accept": e.get("accept"), "ip_redacted": e["client_ip_redacted"],
                         "country": e.get("cf_country"), "outcome": e["outcome"]})
    return rows


def state(client: httpx.Client) -> dict:
    return client.get("/_spike/state").json()


def mk_links(cfg, base: str, job: str, ttl_action=300):
    key = cfg["CP_SIGNING_KEY"].encode()
    snap = tokens.snapshot_hash(RUN, job)
    view = tokens.mint_view(key, RUN, job, snap, ttl_action=ttl_action)
    return f"{base}/r/{RUN}/{job}?t={view}", f"/r/{RUN}/{job}", view


def save(name: str, data: dict):
    (HERE / name).write_text(json.dumps(data, indent=2), encoding="utf-8")


# ------------------------------------------------------------------------------ S5
def cmd_s5(cfg, base: str):
    res: dict = {"started_utc": utcnow().isoformat(), "base_host": base.split("//")[1], "messages": {}}
    with httpx.Client(base_url=base, follow_redirects=False, timeout=20) as c:
        t = time.perf_counter()
        r = c.get("/healthz")
        res["healthz_via_tunnel"] = {"status": r.status_code, "ms": round((time.perf_counter() - t) * 1000)}
        res["state_before"] = state(c)

        plan = [
            ("M1", "job-1", 90, {}, "link in text, preview ENABLED",
             lambda link: {"text": f"SPIKE S5 (test, nothing to approve)\n{link}"}),
            ("M2", "job-2", 60, {"disable_web_page_preview": True}, "link in text, disable_web_page_preview=true",
             lambda link: {"text": f"SPIKE S5 M2 (test, preview disabled, nothing to approve)\n{link}"}),
            ("M3", "job-3", 60, {}, "inline-keyboard url button only (no link in text)",
             lambda link: {"text": "SPIKE S5 M3 (test, button only, nothing to approve)",
                           "reply_markup": {"inline_keyboard": [[{"text": "Open review (SPIKE)", "url": link}]]}}),
        ]
        for name, job, window, extra, desc, build in plan:
            link, path, _ = mk_links(cfg, base, job)
            before = state(c)["state_changes"]
            t0 = utcnow()
            payload = {"chat_id": cfg["TELEGRAM_CHAT_ID"], **build(link), **extra}
            sent = tg_call(cfg, "sendMessage", payload)
            print(name, "sent", sent, flush=True)
            time.sleep(window)
            hits = hits_for(path, t0)
            after = state(c)["state_changes"]
            res["messages"][name] = {
                "variant": desc, "bot_api": sent, "path_observed": path, "window_s": window,
                "requests_to_path": hits, "request_count": len(hits),
                "state_changes_before": before, "state_changes_after": after,
            }
        res["state_after"] = state(c)

        # Step 6: the action token must live only inside the HTML form, never in a URL.
        link, path, view = mk_links(cfg, base, "job-1")
        page = c.get(link.replace(base, ""), headers={"User-Agent": "spike-verifier/1.0"})
        html_text = page.text
        action_tokens = re.findall(r"name='token' value='([^']+)'", html_text)
        urls_in_html = re.findall(r"(?:href|action|src)=['\"]([^'\"]*)['\"]", html_text)
        view_payload = tokens.verify(cfg["CP_SIGNING_KEY"].encode(), view)
        res["step6_action_token_placement"] = {
            "page_status": page.status_code,
            "action_tokens_in_hidden_input": len(action_tokens),
            "urls_in_page": urls_in_html,
            "action_token_in_any_url": any(a in u for a in action_tokens for u in urls_in_html),
            "view_token_typ": view_payload["typ"],
            "view_token_has_nonce": "nonce" in view_payload,
            "view_token_as_action_rejected_status": c.post(
                "/api/approve", json={"token": view, "run": RUN, "job": "job-1"}).status_code,
            "cache_control": page.headers.get("cache-control"),
            "referrer_policy": page.headers.get("referrer-policy"),
            "state_changes_after_own_get": state(c)["state_changes"],
        }
    res["telegram_delivery_to_phone"] = "received on phone (human-confirmed): PENDING - needs the user's confirmation"
    res["finished_utc"] = utcnow().isoformat()
    save("results_s5.json", res)
    print("saved results_s5.json")


# ------------------------------------------------------------------------------ S6
def scrape_action(c: httpx.Client, cfg, job: str) -> str:
    link, _, _ = mk_links(cfg, str(c.base_url).rstrip("/"), job)
    r = c.get(link.replace(str(c.base_url).rstrip("/"), ""), headers={"User-Agent": "spike-verifier/1.0"})
    m = re.search(r"name='token' value='([^']+)'", r.text)
    assert r.status_code == 200 and m, f"no form on {job} page"
    return m.group(1)


def post_form(c, token, run, job):
    r = c.post("/api/approve", data={"token": token, "run": run, "job": job})
    reason = re.search(r"reason: (\w+)", r.text)
    return {"status": r.status_code, "reason": reason.group(1) if reason else None}


def cmd_s6(cfg, base: str):
    key = cfg["CP_SIGNING_KEY"].encode()
    res: dict = {"started_utc": utcnow().isoformat(), "base_host": base.split("//")[1], "tunnel_checks": {}}
    with httpx.Client(base_url=base, follow_redirects=False, timeout=20) as c:
        s0 = state(c)["state_changes"]
        link, _, _ = mk_links(cfg, base, "job-1")
        t = time.perf_counter()
        page = c.get(link.replace(base, ""), headers={"User-Agent": "spike-verifier/1.0"})
        res["tunnel_checks"]["get_review_page"] = {"status": page.status_code, "has_form": "<form" in page.text,
                                                   "ms": round((time.perf_counter() - t) * 1000)}
        good = re.search(r"name='token' value='([^']+)'", page.text).group(1)
        res["tunnel_checks"]["post_valid"] = post_form(c, good, RUN, "job-1")
        res["tunnel_checks"]["post_replay"] = post_form(c, good, RUN, "job-1")
        res["tunnel_checks"]["second_fresh_token_same_snapshot"] = post_form(c, scrape_action(c, cfg, "job-1"), RUN, "job-1")

        tok2 = scrape_action(c, cfg, "job-2")
        payload_b64, sig = tok2.split(".")
        flipped = ("A" if sig[0] != "A" else "B") + sig[1:]
        res["tunnel_checks"]["post_tampered_signature"] = post_form(c, f"{payload_b64}.{flipped}", RUN, "job-2")
        res["tunnel_checks"]["post_expired"] = post_form(
            c, tokens.mint_action(key, RUN, "job-2", tokens.snapshot_hash(RUN, "job-2"), exp=int(time.time()) - 30), RUN, "job-2")
        res["tunnel_checks"]["post_token_for_other_job"] = post_form(c, scrape_action(c, cfg, "job-2"), RUN, "job-1")
        res["tunnel_checks"]["post_stale_snapshot"] = post_form(
            c, tokens.mint_action(key, RUN, "job-2", tokens.snapshot_hash(RUN, "job-2", "v0")), RUN, "job-2")
        res["tunnel_checks"]["post_view_token_as_action"] = post_form(
            c, tokens.mint_view(key, RUN, "job-2", tokens.snapshot_hash(RUN, "job-2")), RUN, "job-2")
        s1 = state(c)["state_changes"]
        res["state_changes_before"], res["state_changes_after"], res["state_changes_delta"] = s0, s1, s1 - s0

        # Worker leg
        wtok = WORKER_TOKEN_FILE.read_text(encoding="ascii").strip()
        auth = {"Authorization": f"Bearer {wtok}"}
        lat, sizes, statuses, redirects = [], [], [], []
        for _ in range(10):
            t = time.perf_counter()
            r = c.get("/worker/commands", headers=auth)
            lat.append((time.perf_counter() - t) * 1000)
            sizes.append(len(r.content))
            statuses.append(r.status_code)
            redirects.append(r.is_redirect)
        body = r.json()
        hl = []
        for _ in range(10):
            t = time.perf_counter()
            c.get("/healthz")
            hl.append((time.perf_counter() - t) * 1000)
        pct = lambda xs, q: round(sorted(xs)[min(len(xs) - 1, int(round(q * (len(xs) - 1))))])  # noqa: E731
        res["worker"] = {
            "poll_statuses": statuses, "any_redirect": any(redirects), "response_bytes": sizes[0],
            "shape_ok": isinstance(body.get("commands"), list),
            "commands_latency_ms": {"median": round(statistics.median(lat)), "p95": pct(lat, 0.95), "min": round(min(lat)), "max": round(max(lat))},
            "healthz_latency_ms": {"median": round(statistics.median(hl)), "p95": pct(hl, 0.95)},
            "no_authorization_status": c.get("/worker/commands").status_code,
            "wrong_token_status": c.get("/worker/commands", headers={"Authorization": "Bearer wrong"}).status_code,
            "ack_status": c.post("/worker/ack", json={"command_id": "cmd-spike-1"}, headers=auth).status_code,
            "heartbeat_status": c.post("/worker/heartbeat", json={"run_id": RUN, "status": "running"}, headers=auth).status_code,
            "query_strings_used": False,
        }
        res["state_after"] = state(c)
    res["finished_utc"] = utcnow().isoformat()
    save("results_s6.json", res)
    print("saved results_s6.json")


def cmd_phone(cfg, base: str, wait_s: int = 600):
    link, path, _ = mk_links(cfg, base, "job-5", ttl_action=600)
    t0 = utcnow()
    msg = ("SPIKE S6 - harmless test, nothing is submitted. Open the link on your phone, press the big "
           f"APPROVE (SPIKE) button once, then press it again.\n{link}")
    sent = tg_call(cfg, "sendMessage", {"chat_id": cfg["TELEGRAM_CHAT_ID"], "text": msg})
    print("M5 sent", sent, flush=True)
    res = {"sent_utc": t0.isoformat(), "bot_api": sent, "wait_s": wait_s}
    first_post_at = None
    deadline = time.time() + wait_s
    while time.time() < deadline:
        time.sleep(3)
        posts = [h for h in hits_for("/api/approve", t0) if h["method"] == "POST"]
        if posts and first_post_at is None:
            first_post_at = time.time()
        if len(posts) >= 2 or (first_post_at and time.time() - first_post_at > 180):
            break
    posts = [h for h in hits_for("/api/approve", t0) if h["method"] == "POST"]
    pages = hits_for(path, t0)
    is_phone = lambda h: bool(MOBILE_RE.search(h["user_agent"] or "")) and not (h["ip_redacted"] or "127.").startswith("127.")  # noqa: E731
    phone_posts = [p for p in posts if is_phone(p)]
    res.update({"page_requests": pages, "approve_posts": posts, "mobile_nonlocal_posts": len(phone_posts),
                "phone_test_performed": len(phone_posts) >= 1})
    if not phone_posts:
        res["verdict"] = "PHONE TEST: NOT PERFORMED within 10 min - needs user"
    with httpx.Client(base_url=base, follow_redirects=False, timeout=20) as c:
        res["state_after"] = state(c)
    res["finished_utc"] = utcnow().isoformat()
    save("results_phone.json", res)
    print("saved results_phone.json")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["s5", "s6", "phone"])
    ap.add_argument("--base", required=True)
    a = ap.parse_args()
    cfg = load_cfg()
    base = a.base.rstrip("/")
    {"s5": cmd_s5, "s6": cmd_s6, "phone": cmd_phone}[a.cmd](cfg, base)
