from curl_cffi import requests
import json, datetime, random, re, time, os

# --- Configuration & Global Session ---
ICS_FILE, MAP_FILE = "economic_and_earnings_events.ics", "instruments_mapping.json"
session = requests.Session(impersonate="chrome")

# --- Helper Functions ---
def fetch(url, api=True, auth=None):
    """Handles all web requests with Cloudflare bypass headers & retries."""
    headers = {
        "accept": "*/*" if api else "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "sec-fetch-dest": "empty" if api else "document",
        "sec-fetch-mode": "cors" if api else "navigate",
        "sec-fetch-site": "same-site" if api else "none"
    }
    if api: headers.update({"origin": "https://www.investing.com", "referer": "https://www.investing.com/"})
    else: headers.update({"sec-fetch-user": "?1", "upgrade-insecure-requests": "1"})
    if auth: headers["authorization"] = auth

    for _ in range(4):
        try:
            if (res := session.get(url, headers=headers, timeout=15)).status_code == 200: return res
        except: pass
        time.sleep(random.uniform(2.5, 4.5))
    return None

def fmt_num(v):
    try: return f"{float(v)/1e9:.2f}B" if float(v)>=1e9 else f"{float(v)/1e6:.2f}M" if float(v)>=1e6 else f"{float(v):.2f}"
    except: return str(v) if v else "N/A"

def get_utc(d_str, h, m):
    """Converts US Eastern Time to UTC, auto-calculating US DST changes."""
    dt = datetime.datetime.strptime(d_str, "%Y-%m-%d").replace(hour=h, minute=m)
    y = dt.year
    sun2_mar = datetime.datetime(y, 3, 1) + datetime.timedelta(days=(13 - datetime.datetime(y, 3, 1).weekday()) % 7)
    sun1_nov = datetime.datetime(y, 11, 1) + datetime.timedelta(days=(6 - datetime.datetime(y, 11, 1).weekday()) % 7)
    return dt - datetime.timedelta(hours=-4 if sun2_mar.date() <= dt.date() < sun1_nov.date() else -5)

def build_vevent(uid, title, start, end, desc):
    """Creates the VEVENT string without alarms/reminders."""
    return f"BEGIN:VEVENT\nUID:{uid}\nSUMMARY:{title}\n{start}\n{end}\nDESCRIPTION:{desc}\nEND:VEVENT"

# --- 1. Load Local Files ---
events = {}
if os.path.exists(ICS_FILE):
    for b in open(ICS_FILE, encoding="utf-8").read().split("BEGIN:VEVENT\n")[1:]:
        events[re.search(r"UID:(.+)", b).group(1).strip()] = "BEGIN:VEVENT\n" + b.strip()
mapping = json.load(open(MAP_FILE, encoding="utf-8")) if os.path.exists(MAP_FILE) else {}

# --- 2. Setup Timeframes & Authenticate ---
now = datetime.datetime.now()
t_start = (now - datetime.timedelta(days=2)).strftime("%Y-%m-%dT00%%3A00%%3A00.000")
t_end = (now + datetime.timedelta(days=30)).strftime("%Y-%m-%dT23%%3A59%%3A59.999")

print("Scraping homepage token...")
home = fetch("https://www.investing.com/", api=False)
token = re.search(r'(eyJhbGciOiJIUzI1NiIs[\w-]+\.[\w-]+\.[\w-]+)', home.text).group(1) if home else None

# --- 3. Process Economic Events ---
print("Fetching Economic Events...")
if res_eco := fetch(f"https://endpoints.investing.com/pd-instruments/v1/calendars/economic/events/occurrences?domain_id=1&limit=200&start_date={t_start}%2B08%3A00&end_date={t_end}%2B08%3A00&country_ids=5,35&importance=high"):
    d = res_eco.json()
    lkp = {e["event_id"]: e for e in d.get("events", [])}
    for o in d.get("occurrences", []):
        if not (t := o.get("occurrence_time")): continue
        dt = datetime.datetime.fromisoformat(t.replace('Z', '+00:00')).astimezone(datetime.timezone.utc)
        info = lkp.get(o.get("event_id"), {})
        name = info.get("event_translated") or info.get("short_name") or "Economic Event"
        act, fcst, prev, unit = o.get("actual","N/A"), o.get("forecast","N/A"), o.get("previous","N/A"), o.get("unit","")
        
        d_start, d_end = f"DTSTART:{dt.strftime('%Y%m%dT%H%M%SZ')}", f"DTEND:{(dt + datetime.timedelta(minutes=30)).strftime('%Y%m%dT%H%M%SZ')}"
        desc = f"Currency: {info.get('currency', 'N/A')}\\nActual: {act}{unit if act!='N/A' else ''}\\nForecast: {fcst}{unit if fcst!='N/A' else ''}\\nPrevious: {prev}{unit if prev!='N/A' else ''}"
        uid = f"eco-{re.sub(r'[^a-zA-Z0-9]', '', name)}-{dt.strftime('%Y%m%d')}"
        events[uid] = build_vevent(uid, name, d_start, d_end, desc)

# --- 4. Process Earnings Events ---
print("Fetching Earnings Events...")
if token and (res_earn := fetch(f"https://endpoints.investing.com/earnings/v1/instruments/earnings?start_date={t_start}Z&end_date={t_end}Z&country_ids=5&importance=high&limit=200&deduplicate=true", auth=f"Bearer {token}")):
    earns = res_earn.json().get("earnings", [])
    
    # Auto-Heal Local JSON cache
    if missing := [str(e["instrument_id"]) for e in earns if str(e["instrument_id"]) not in mapping]:
        print(f"Fetching {len(missing)} unknown companies for local cache...")
        for i in range(0, len(missing), 15):
            q = "&".join([f"instrument_ids={x}" for x in missing[i:i+15]])
            if r := fetch(f"https://endpoints.investing.com/pd-instruments/v1/instruments?domain_id=1&{q}"):
                for item in r.json(): mapping[str(item["id"])] = {"symbol": item.get("symbol"), "name": item.get("long_name") or item.get("short_name")}
            time.sleep(random.uniform(1.5, 3.0))
        json.dump(mapping, open(MAP_FILE, "w", encoding="utf-8"), indent=4)
    
    for e in earns:
        if not (d := e.get("date")): continue
        iid = str(e.get("instrument_id"))
        sym = mapping.get(iid, {}).get("symbol") or f"ID-{iid}"
        name = mapping.get(iid, {}).get("name") or sym
        phase = e.get("market_phase", "")
        
        if phase in ("PRE_MARKET", "AFTER_HOURS"):
            dt = get_utc(d, 8 if phase=="PRE_MARKET" else 16, 0 if phase=="PRE_MARKET" else 15)
            d_start, d_end = f"DTSTART:{dt.strftime('%Y%m%dT%H%M%SZ')}", f"DTEND:{(dt + datetime.timedelta(hours=1)).strftime('%Y%m%dT%H%M%SZ')}"
        else:
            d_obj = datetime.datetime.strptime(d, "%Y-%m-%d")
            d_start, d_end = f"DTSTART;VALUE=DATE:{d_obj.strftime('%Y%m%d')}", f"DTEND;VALUE=DATE:{(d_obj + datetime.timedelta(days=1)).strftime('%Y%m%d')}"

        desc = f"EPS Actual: {e.get('eps_actual','N/A')}\\nEPS Forecast: {e.get('eps_forecast','N/A')}\\nRevenue Actual: {fmt_num(e.get('revenue_actual'))}\\nRevenue Forecast: {fmt_num(e.get('revenue_forecast'))}"
        uid = f"earn-{sym}-{d.replace('-', '')}"
        events[uid] = build_vevent(uid, f"[Earning] {name}", d_start, d_end, desc)

# --- 5. Save ICS ---
with open(ICS_FILE, "w", encoding="utf-8") as f:
    f.write("BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//Economic & Earnings Calendar//EN\nCALSCALE:GREGORIAN\nMETHOD:PUBLISH\n" + 
            "\n".join(events.values()) + "\nEND:VCALENDAR")

print(f"✅ Master process complete! Saved {len(events)} events to {ICS_FILE}")
