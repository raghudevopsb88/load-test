import asyncio
import os
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import httpx
from flask import Flask, jsonify, render_template_string, request

app = Flask(__name__)
executor = ThreadPoolExecutor(max_workers=1)
DEFAULT_BASE_URL = os.getenv("BASE_URL", "").strip()
DEFAULT_CONCURRENCY = os.getenv("CONCURRENCY", "").strip()
DEFAULT_DURATION = os.getenv("DURATION", "").strip()
DEFAULT_PORT = os.getenv("PORT", "").strip()
ERROR_BODY_MAX = int(os.getenv("ERROR_BODY_MAX", "500"))


def log_request_error(method, url, status_code=None, body=None, exc=None):
    """Print failed request details to stdout for docker logs."""
    parts = [f"LOAD_TEST_ERROR method={method} url={url}"]
    if status_code is not None:
        parts.append(f"status={status_code}")
    if body:
        snippet = body.replace("\n", " ").strip()
        if len(snippet) > ERROR_BODY_MAX:
            snippet = snippet[:ERROR_BODY_MAX] + "..."
        parts.append(f"body={snippet}")
    if exc is not None:
        parts.append(f"error={type(exc).__name__}: {exc}")
    print(" ".join(parts), file=sys.stdout, flush=True)


def response_body_snippet(resp):
    try:
        return resp.text
    except Exception:
        return "<unable to read response body>"


def parse_user_id(user_obj):
    uid = (user_obj or {}).get("id")
    if uid is None:
        return None
    if isinstance(uid, dict):
        return uid.get("$oid") or str(uid)
    return str(uid)


def make_http_client():
    return httpx.AsyncClient(
        timeout=httpx.Timeout(60.0, connect=15.0, pool=120.0),
        limits=httpx.Limits(max_connections=25, max_keepalive_connections=15),
    )

HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>RoboShop Load Tester</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Segoe UI', sans-serif; background: #1a1a2e; color: #e0e0e0; min-height: 100vh; padding: 40px; }
        .container { max-width: 800px; margin: 0 auto; }
        h1 { color: #00d4ff; margin-bottom: 30px; font-size: 28px; }
        .form-group { margin-bottom: 20px; }
        label { display: block; margin-bottom: 6px; color: #aaa; font-size: 14px; }
        input { width: 100%; padding: 12px; background: #16213e; border: 1px solid #333; border-radius: 6px; color: #fff; font-size: 16px; }
        input:focus { outline: none; border-color: #00d4ff; }
        .row { display: flex; gap: 20px; }
        .row .form-group { flex: 1; }
        button { padding: 14px 32px; background: #00d4ff; color: #1a1a2e; border: none; border-radius: 6px; font-size: 16px; font-weight: bold; cursor: pointer; margin-right: 10px; }
        button:hover { background: #00b8d9; }
        button:disabled { background: #555; color: #888; cursor: not-allowed; }
        button.stop { background: #ff4757; color: #fff; }
        button.stop:hover { background: #ff3344; }
        .results { margin-top: 30px; display: none; }
        .results.show { display: block; }
        .progress { background: #16213e; border-radius: 6px; padding: 20px; margin-bottom: 20px; }
        .progress-bar { height: 8px; background: #333; border-radius: 4px; overflow: hidden; margin-top: 10px; }
        .progress-fill { height: 100%; background: #00d4ff; transition: width 0.3s; width: 0%; }
        .stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 15px; margin-top: 20px; }
        .stat-card { background: #16213e; border-radius: 6px; padding: 20px; text-align: center; }
        .stat-value { font-size: 28px; font-weight: bold; color: #00d4ff; }
        .stat-label { font-size: 12px; color: #888; margin-top: 4px; }
        .stat-card.error .stat-value { color: #ff4757; }
        .stat-card.success .stat-value { color: #2ed573; }
        .status-text { font-size: 14px; color: #aaa; }
        .info { background: #16213e; border-radius: 6px; padding: 15px; margin-top: 20px; font-size: 13px; color: #888; }
        .info strong { color: #00d4ff; }
    </style>
</head>
<body>
    <div class="container">
        <h1>RoboShop Load Tester</h1>
        <div class="form-group">
            <label>Application Base URL (nginx / frontend)</label>
            <input type="text" id="baseUrl" placeholder="http://roboshop-frontend-dev.raghudevopsb88.online" value="{{ default_base_url }}" />
        </div>
        <div class="row">
            <div class="form-group">
                <label>Concurrent Users</label>
                <input type="number" id="concurrency" value="{{ default_concurrency }}" min="1" />
            </div>
            <div class="form-group">
                <label>Duration (seconds)</label>
                <input type="number" id="duration" value="{{ default_duration }}" min="5" />
            </div>
        </div>
        <button id="runBtn" onclick="runTest()">Start Load Test</button>
        <button id="stopBtn" class="stop" onclick="stopTest()" style="display:none;">Stop</button>

        <div class="info">
            <strong>Base URL:</strong> Use the RoboShop frontend / nginx entry point only (not individual microservice URLs). Example: <code>http://roboshop-frontend-dev.raghudevopsb88.online</code>. Required env vars: <code>BASE_URL</code>, <code>CONCURRENCY</code>, <code>DURATION</code>, <code>PORT</code>.
        </div>

        <div class="info">
            <strong>Full Journey Test:</strong> Each virtual user will Browse Catalogue &rarr; Register &rarr; Login &rarr; Profile &rarr; Shipping &rarr; Add to Cart &rarr; Checkout &rarr; Orders &rarr; Rate Product (repeating until duration ends). All traffic flows through the nginx reverse proxy at the base URL.
        </div>

        <div class="results" id="results">
            <div class="progress">
                <div class="status-text" id="statusText">Running...</div>
                <div class="progress-bar"><div class="progress-fill" id="progressFill"></div></div>
            </div>
            <div class="stats">
                <div class="stat-card success"><div class="stat-value" id="successCount">0</div><div class="stat-label">Success</div></div>
                <div class="stat-card error"><div class="stat-value" id="errorCount">0</div><div class="stat-label">Failures</div></div>
                <div class="stat-card"><div class="stat-value" id="totalRequests">0</div><div class="stat-label">Total Requests</div></div>
                <div class="stat-card"><div class="stat-value" id="rps">0</div><div class="stat-label">Requests/sec</div></div>
            </div>
            <div class="stats" style="margin-top: 15px;">
                <div class="stat-card"><div class="stat-value" id="avgTime">0</div><div class="stat-label">Avg (ms)</div></div>
                <div class="stat-card"><div class="stat-value" id="p50Time">0</div><div class="stat-label">P50 (ms)</div></div>
                <div class="stat-card"><div class="stat-value" id="p95Time">0</div><div class="stat-label">P95 (ms)</div></div>
                <div class="stat-card"><div class="stat-value" id="maxTime">0</div><div class="stat-label">Max (ms)</div></div>
            </div>
        </div>
    </div>
    <script>
        let pollInterval;

        async function runTest() {
            const baseUrl = document.getElementById('baseUrl').value.trim();
            const concurrency = parseInt(document.getElementById('concurrency').value);
            const duration = parseInt(document.getElementById('duration').value);

            if (!baseUrl) { alert('Enter the application base URL'); return; }

            document.getElementById('runBtn').disabled = true;
            document.getElementById('stopBtn').style.display = 'inline-block';
            document.getElementById('results').classList.add('show');
            document.getElementById('statusText').textContent = 'Running...';
            document.getElementById('progressFill').style.width = '0%';
            ['successCount','errorCount','totalRequests','rps','avgTime','p50Time','p95Time','maxTime'].forEach(id => document.getElementById(id).textContent = '0');

            await fetch('/api/run', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ base_url: baseUrl, concurrency, duration }) });

            pollInterval = setInterval(pollStatus, 500);
        }

        async function stopTest() {
            await fetch('/api/stop', { method: 'POST' });
        }

        async function pollStatus() {
            const res = await fetch('/api/status');
            const data = await res.json();

            const elapsed = data.elapsed_s || 0;
            const total = data.duration_s || 1;
            const pct = Math.min(100, Math.round((elapsed / total) * 100));
            document.getElementById('progressFill').style.width = pct + '%';
            document.getElementById('successCount').textContent = data.success;
            document.getElementById('errorCount').textContent = data.errors;
            document.getElementById('totalRequests').textContent = data.success + data.errors;
            document.getElementById('rps').textContent = data.rps;
            document.getElementById('avgTime').textContent = data.avg_ms;
            document.getElementById('p50Time').textContent = data.p50_ms;
            document.getElementById('p95Time').textContent = data.p95_ms;
            document.getElementById('maxTime').textContent = data.max_ms;

            if (data.done) {
                clearInterval(pollInterval);
                document.getElementById('statusText').textContent = `Done — ${data.success + data.errors} requests in ${data.elapsed_s}s`;
                document.getElementById('runBtn').disabled = false;
                document.getElementById('stopBtn').style.display = 'none';
            } else {
                document.getElementById('statusText').textContent = `Running... ${elapsed}s / ${total}s — ${data.success + data.errors} requests`;
            }
        }
    </script>
</body>
</html>
"""

test_state = {
    "done": True, "success": 0, "errors": 0,
    "avg_ms": 0, "rps": 0, "p50_ms": 0, "p95_ms": 0, "max_ms": 0,
    "elapsed_s": 0, "duration_s": 0,
}
stop_flag = False


@app.route("/")
def index():
    return render_template_string(
        HTML,
        default_base_url=DEFAULT_BASE_URL,
        default_concurrency=DEFAULT_CONCURRENCY,
        default_duration=DEFAULT_DURATION,
    )


def normalize_base_url(url):
    """Accept full URLs or hostnames and return scheme://host[:port]."""
    raw = (url or "").strip()
    if not raw:
        raise ValueError("base_url is required")

    if "://" not in raw:
        raw = f"http://{raw}"

    parsed = urlparse(raw)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"Invalid base URL: {url}")

    if parsed.scheme not in ("http", "https"):
        raise ValueError("base URL must use http or https")

    return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")


@app.route("/api/run", methods=["POST"])
def run_test():
    global test_state, stop_flag
    data = request.json or {}
    try:
        base_url = normalize_base_url(data.get("base_url"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    stop_flag = False
    test_state = {
        "done": False, "success": 0, "errors": 0,
        "avg_ms": 0, "rps": 0, "p50_ms": 0, "p95_ms": 0, "max_ms": 0,
        "elapsed_s": 0, "duration_s": data.get("duration", 60),
    }
    executor.submit(run_load_test, {
        "base_url": base_url,
        "concurrency": data.get("concurrency", 10),
        "duration": data.get("duration", 60),
    })
    return jsonify({"status": "started", "base_url": base_url})


@app.route("/api/stop", methods=["POST"])
def stop_test():
    global stop_flag
    stop_flag = True
    return jsonify({"status": "stopping"})


@app.route("/api/status")
def status():
    return jsonify(test_state)


def run_load_test(data):
    global test_state, stop_flag
    base_url = normalize_base_url(data["base_url"])
    concurrency = data["concurrency"]
    duration = data["duration"]

    latencies = []
    start_time = time.time()
    auth_semaphore = asyncio.Semaphore(max(2, min(concurrency, 8)))

    async def user_journey(user_id):
        """Full RoboShop journey through the nginx reverse proxy."""
        await asyncio.sleep(user_id * 0.25)

        async with make_http_client() as client:
            token = None
            user_uuid = None
            cities = []

            while (time.time() - start_time) < duration and not stop_flag:
                token = None
                user_uuid = None

                # --- Browse catalogue ---
                await do_request(client, "GET", f"{base_url}/api/catalogue/products",
                                 latencies=latencies, start_time=start_time)
                cat_resp = await do_request(client, "GET", f"{base_url}/api/catalogue/categories",
                                            latencies=latencies, start_time=start_time)
                category = None
                if cat_resp is not None and cat_resp.status_code == 200:
                    try:
                        cats = cat_resp.json()
                        if cats:
                            category = random.choice(cats)
                    except Exception:
                        pass
                if category:
                    await do_request(client, "GET",
                                     f"{base_url}/api/catalogue/products?category={category}",
                                     latencies=latencies, start_time=start_time)
                await do_request(client, "GET", f"{base_url}/api/catalogue/products/search?q=robot",
                                 latencies=latencies, start_time=start_time)

                product_id = random.randint(1, 12)
                await do_request(client, "GET", f"{base_url}/api/catalogue/products/{product_id}",
                                 latencies=latencies, start_time=start_time)

                # --- Register & login (throttled — bcrypt is CPU-heavy on user service) ---
                uname = f"loaduser_{user_id}_{random.randint(1000000000, 9999999999)}"
                email = f"{uname}@test.com"
                password = "LoadTest123!"
                async with auth_semaphore:
                    reg_resp = await do_request(client, "POST", f"{base_url}/api/user/register",
                                     json={"username": uname, "email": email, "password": password,
                                           "firstName": "Load", "lastName": "Test"},
                                     latencies=latencies, start_time=start_time)
                    if reg_resp is None or reg_resp.status_code >= 300:
                        await asyncio.sleep(random.uniform(0.5, 1.5))
                        continue

                    await asyncio.sleep(0.2)
                    login_resp = await do_request(client, "POST", f"{base_url}/api/user/login",
                                                  json={"username": uname, "password": password},
                                                  latencies=latencies, start_time=start_time)
                if login_resp is not None and login_resp.status_code == 200:
                    try:
                        body = login_resp.json()
                        token = body.get("token")
                        user_uuid = parse_user_id(body.get("user") or {})
                    except Exception:
                        pass

                if not token or not user_uuid:
                    await asyncio.sleep(random.uniform(0.5, 1.5))
                    continue

                headers = {"Authorization": f"Bearer {token}"}

                # --- Profile (uses JWT) ---
                await do_request(client, "GET", f"{base_url}/api/user/profile",
                                 headers=headers, latencies=latencies, start_time=start_time)

                # --- Shipping ---
                if not cities:
                    cities_resp = await do_request(client, "GET", f"{base_url}/api/shipping/cities",
                                                   latencies=latencies, start_time=start_time)
                    if cities_resp is not None and cities_resp.status_code == 200:
                        try:
                            cities = cities_resp.json() or []
                        except Exception:
                            cities = []
                city_id = random.choice(cities).get("id") if cities else 1
                await do_request(client, "GET", f"{base_url}/api/shipping/calc?cityId={city_id}",
                                 latencies=latencies, start_time=start_time)

                # --- Cart (payment needs non-empty cart + user validate upstream) ---
                add_resp = await do_request(client, "POST", f"{base_url}/api/cart/{user_uuid}/add",
                                            json={"productId": product_id, "quantity": 1},
                                            latencies=latencies, start_time=start_time)
                if add_resp is None or add_resp.status_code >= 300:
                    await asyncio.sleep(random.uniform(0.5, 1.5))
                    continue

                cart_resp = await do_request(client, "GET", f"{base_url}/api/cart/{user_uuid}",
                                             latencies=latencies, start_time=start_time)
                cart_ok = False
                if cart_resp is not None and cart_resp.status_code == 200:
                    try:
                        cart_ok = bool((cart_resp.json() or {}).get("items"))
                    except Exception:
                        cart_ok = False
                if not cart_ok:
                    await asyncio.sleep(random.uniform(0.5, 1.5))
                    continue

                await do_request(client, "PUT", f"{base_url}/api/cart/{user_uuid}/update",
                                 json={"productId": product_id, "quantity": 2},
                                 latencies=latencies, start_time=start_time)

                # --- Checkout (payment calls user validate + cart; retry on transient 503) ---
                await asyncio.sleep(0.15)
                pay_payload = {"userId": user_uuid, "cityId": city_id}
                pay_resp = await do_request(client, "POST", f"{base_url}/api/payment/process",
                                            json=pay_payload,
                                            latencies=latencies, start_time=start_time)
                if pay_resp is not None and pay_resp.status_code == 503:
                    await asyncio.sleep(0.5)
                    await do_request(client, "POST", f"{base_url}/api/payment/process",
                                     json=pay_payload,
                                     latencies=latencies, start_time=start_time)

                # --- Orders (orders consumer is async; give it a moment) ---
                await asyncio.sleep(0.5)
                await do_request(client, "GET", f"{base_url}/api/orders/user/{user_uuid}",
                                 latencies=latencies, start_time=start_time)

                # --- Rate product ---
                rating_product = random.randint(1, 12)
                await do_request(client, "POST", f"{base_url}/api/ratings",
                                 json={"productId": rating_product, "userId": user_uuid,
                                       "score": random.randint(1, 5), "review": "Load test review"},
                                 latencies=latencies, start_time=start_time)
                await do_request(client, "GET", f"{base_url}/api/ratings/product/{rating_product}",
                                 latencies=latencies, start_time=start_time)
                await do_request(client, "GET", f"{base_url}/api/ratings/product/{rating_product}/average",
                                 latencies=latencies, start_time=start_time)

                await asyncio.sleep(random.uniform(0.5, 1.5))

    async def do_request(client, method, url, json=None, headers=None, latencies=None, start_time=None):
        req_start = time.time()
        try:
            resp = await client.request(method, url, json=json, headers=headers)
            elapsed_ms = round((time.time() - req_start) * 1000, 1)
            latencies.append(elapsed_ms)

            if 200 <= resp.status_code < 300:
                test_state["success"] += 1
            else:
                test_state["errors"] += 1
                log_request_error(
                    method,
                    url,
                    status_code=resp.status_code,
                    body=response_body_snippet(resp),
                )

            update_stats(latencies, start_time)
            return resp
        except Exception as exc:
            elapsed_ms = round((time.time() - req_start) * 1000, 1)
            latencies.append(elapsed_ms)
            test_state["errors"] += 1
            log_request_error(method, url, exc=exc)
            update_stats(latencies, start_time)
            return None

    async def run():
        tasks = [user_journey(i) for i in range(concurrency)]
        await asyncio.gather(*tasks)

    asyncio.run(run())

    test_state["done"] = True
    test_state["elapsed_s"] = round(time.time() - start_time, 1)
    update_stats(latencies, start_time)


def update_stats(latencies, start_time):
    if not latencies:
        return
    sorted_lat = sorted(latencies)
    n = len(sorted_lat)
    elapsed = time.time() - start_time

    test_state["elapsed_s"] = round(elapsed, 1)
    test_state["avg_ms"] = round(sum(sorted_lat) / n, 1)
    test_state["max_ms"] = round(sorted_lat[-1], 1)
    test_state["p50_ms"] = round(sorted_lat[int(n * 0.5)], 1)
    test_state["p95_ms"] = round(sorted_lat[min(int(n * 0.95), n - 1)], 1)
    test_state["rps"] = round(n / elapsed, 1) if elapsed > 0 else 0


if __name__ == "__main__":
    if not DEFAULT_PORT:
        raise SystemExit("PORT is required")
    app.run(host="0.0.0.0", port=int(DEFAULT_PORT))
