import asyncio
import random
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
from flask import Flask, jsonify, render_template_string, request

app = Flask(__name__)
executor = ThreadPoolExecutor(max_workers=1)

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
            <input type="text" id="baseUrl" placeholder="http://localhost:3000" />
        </div>
        <div class="row">
            <div class="form-group">
                <label>Concurrent Users</label>
                <input type="number" id="concurrency" value="10" min="1" />
            </div>
            <div class="form-group">
                <label>Duration (seconds)</label>
                <input type="number" id="duration" value="60" min="5" />
            </div>
        </div>
        <button id="runBtn" onclick="runTest()">Start Load Test</button>
        <button id="stopBtn" class="stop" onclick="stopTest()" style="display:none;">Stop</button>

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
    return render_template_string(HTML)


@app.route("/api/run", methods=["POST"])
def run_test():
    global test_state, stop_flag
    data = request.json
    stop_flag = False
    test_state = {
        "done": False, "success": 0, "errors": 0,
        "avg_ms": 0, "rps": 0, "p50_ms": 0, "p95_ms": 0, "max_ms": 0,
        "elapsed_s": 0, "duration_s": data["duration"],
    }
    executor.submit(run_load_test, data)
    return jsonify({"status": "started"})


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
    base_url = data["base_url"].rstrip("/")
    concurrency = data["concurrency"]
    duration = data["duration"]

    latencies = []
    start_time = time.time()

    async def user_journey(client, user_id):
        """Full RoboShop journey through the nginx reverse proxy."""
        token = None
        user_uuid = None
        cities = []

        while (time.time() - start_time) < duration and not stop_flag:
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

            # --- Register & login ---
            uname = f"loaduser_{user_id}_{random.randint(1000000000, 9999999999)}"
            email = f"{uname}@test.com"
            password = "LoadTest123!"
            await do_request(client, "POST", f"{base_url}/api/user/register",
                             json={"username": uname, "email": email, "password": password,
                                   "firstName": "Load", "lastName": "Test"},
                             latencies=latencies, start_time=start_time)
            login_resp = await do_request(client, "POST", f"{base_url}/api/user/login",
                                          json={"username": uname, "password": password},
                                          latencies=latencies, start_time=start_time)
            if login_resp is not None and login_resp.status_code == 200:
                try:
                    body = login_resp.json()
                    token = body.get("token")
                    user_obj = body.get("user") or {}
                    user_uuid = user_obj.get("id")
                except Exception:
                    pass

            headers = {"Authorization": f"Bearer {token}"} if token else {}

            # --- Profile (uses JWT) ---
            await do_request(client, "GET", f"{base_url}/api/user/profile",
                             headers=headers, latencies=latencies, start_time=start_time)

            # Skip remainder if login failed
            if not user_uuid:
                await asyncio.sleep(random.uniform(0.5, 1.5))
                continue

            # --- Shipping ---
            if not cities:
                cities_resp = await do_request(client, "GET", f"{base_url}/api/shipping/shipping/cities",
                                               latencies=latencies, start_time=start_time)
                if cities_resp is not None and cities_resp.status_code == 200:
                    try:
                        cities = cities_resp.json() or []
                    except Exception:
                        cities = []
            city_id = random.choice(cities).get("id") if cities else 1
            await do_request(client, "GET", f"{base_url}/api/shipping/shipping/calc?cityId={city_id}",
                             latencies=latencies, start_time=start_time)

            # --- Cart ---
            await do_request(client, "POST", f"{base_url}/api/cart/cart/{user_uuid}/add",
                             json={"productId": product_id, "quantity": 1},
                             latencies=latencies, start_time=start_time)
            await do_request(client, "GET", f"{base_url}/api/cart/cart/{user_uuid}",
                             latencies=latencies, start_time=start_time)
            await do_request(client, "PUT", f"{base_url}/api/cart/cart/{user_uuid}/update",
                             json={"productId": product_id, "quantity": 2},
                             latencies=latencies, start_time=start_time)

            # --- Checkout (publishes order event to RabbitMQ) ---
            await do_request(client, "POST", f"{base_url}/api/payment/payment/process",
                             json={"userId": user_uuid, "cityId": city_id},
                             latencies=latencies, start_time=start_time)

            # --- Orders (orders consumer is async; give it a moment) ---
            await asyncio.sleep(0.5)
            await do_request(client, "GET", f"{base_url}/api/orders/orders/user/{user_uuid}",
                             latencies=latencies, start_time=start_time)

            # --- Rate product ---
            rating_product = random.randint(1, 12)
            await do_request(client, "POST", f"{base_url}/api/ratings/ratings",
                             json={"productId": rating_product, "userId": user_uuid,
                                   "score": random.randint(1, 5), "review": "Load test review"},
                             latencies=latencies, start_time=start_time)
            await do_request(client, "GET", f"{base_url}/api/ratings/ratings/product/{rating_product}",
                             latencies=latencies, start_time=start_time)
            await do_request(client, "GET", f"{base_url}/api/ratings/ratings/product/{rating_product}/average",
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

            update_stats(latencies, start_time)
            return resp
        except Exception:
            elapsed_ms = round((time.time() - req_start) * 1000, 1)
            latencies.append(elapsed_ms)
            test_state["errors"] += 1
            update_stats(latencies, start_time)
            return None

    async def run():
        async with httpx.AsyncClient(timeout=30.0) as client:
            tasks = [user_journey(client, i) for i in range(concurrency)]
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
    app.run(host="0.0.0.0", port=5000)
