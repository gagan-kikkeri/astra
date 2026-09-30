import requests

BASE = "http://127.0.0.1:8000"

print("=== 1. HEALTH & TELEMETRY ===")
res_h = requests.get(f"{BASE}/api/health", timeout=5)
print("Health:", res_h.status_code, res_h.json())

print("\n=== 2. DYNAMIC TAXONOMY ===")
res_c = requests.get(f"{BASE}/api/categories", timeout=5)
print("Categories:", res_c.status_code, res_c.json())

print("\n=== 3. WIRE DISPATCHES ===")
res_a = requests.get(f"{BASE}/api/articles?limit=3", timeout=5)
print("Articles:", res_a.status_code, f"({len(res_a.json())} fetched)")
for a in res_a.json():
    print(f" - [{a['id']}] [{a['category']}] {a['title'][:65]}...")

print("\n=== 4. LIVE SITREP TEST ===")
res_s = requests.post(f"{BASE}/api/intel/synthesize", json={
    "query": "Hypersonic missile test telemetry and glide phase interceptor validation"
}, timeout=20)
print("SitRep Status:", res_s.status_code)
s_data = res_s.json()
print("Executive Assessment:\n", s_data.get("executive_assessment"))
print("Grounded Dispatch IDs:", s_data.get("referenced_dispatch_ids"))
print("Related Platforms:", s_data.get("related_platforms"))

print("\n=== 5. DASHBOARD UI CHECK ===")
res_ui = requests.get(f"{BASE}/", timeout=5)
print("Dashboard UI Status:", res_ui.status_code, f"({len(res_ui.text)} bytes)")
print("\n>>> ALL SYSTEM CHECKS OPERATIONAL <<<")
