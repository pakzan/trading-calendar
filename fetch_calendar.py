from curl_cffi import requests
import json
import datetime
import re
import time
import os

# ==========================================
# HELPER FUNCTIONS
# ==========================================

def format_large_number(value):
    """Formats large numbers into Billions (B) or Millions (M)."""
    if value is None or value == "N/A": return "N/A"
    try:
        v = float(value)
        if v >= 1e9: return f"{v/1e9:.2f}B"
        if v >= 1e6: return f"{v/1e6:.2f}M"
        return f"{v:.2f}"
    except:
        return str(value)

def get_utc_from_ny(date_str, hour, minute):
    """Converts a specific US Eastern Time to UTC, accounting for US DST."""
    dt = datetime.datetime.strptime(date_str, "%Y-%m-%d").replace(hour=hour, minute=minute)
    year = dt.year
    
    # DST math
    mar_1 = datetime.datetime(year, 3, 1)
    mar_2nd_sun = mar_1 + datetime.timedelta(days=(6 - mar_1.weekday() + 7) % 7 + 7)
    nov_1 = datetime.datetime(year, 11, 1)
    nov_1st_sun = nov_1 + datetime.timedelta(days=(6 - nov_1.weekday()) % 7)
    
    offset = -4 if (mar_2nd_sun.date() <= dt.date() < nov_1st_sun.date()) else -5
    return dt - datetime.timedelta(hours=offset)

def fetch_with_retry(url, headers=None, retries=3):
    """Centralized request handler with retry logic and deep diagnostic logging."""
    # Base headers that mimic a real browser request to help bypass 403s
    default_headers = {
        "accept": "*/*",
        "accept-language": "en-US,en;q=0.9",
        "origin": "https://www.investing.com",
        "referer": "https://www.investing.com/"
    }
    if headers:
        default_headers.update(headers)

    for attempt in range(retries):
        try:
            res = requests.get(url, impersonate="chrome", headers=default_headers)
            if res.status_code == 200:
                return res
            
            print(f"⚠️ Attempt {attempt+1} Failed! HTTP Status: {res.status_code} for URL: {url.split('?')[0]}")
            print(f"🔍 SERVER HEADERS: {dict(res.headers)}")
            print(f"📄 ERROR SNIPPET:\n{res.text[:1000]}\n")
            time.sleep(2)
        except Exception as e:
            print(f"⚠️ Attempt {attempt+1} Network Exception: {e}")
            time.sleep(2)
            
    print("❌ Request completely failed after 3 attempts.")
    return None

def get_anonymous_token():
    """Scrapes the session JWT Token from the homepage."""
    print("Scraping fresh Auth Token from Investing.com...")
    res = fetch_with_retry("https://www.investing.com/")
    if res:
        match = re.search(r'(eyJhbGciOiJIUzI1NiIs[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+)', res.text)
        if match:
            print("✅ Successfully generated new session token!")
            return match.group(1)
        print("❌ Connected successfully, but no token found in HTML. Did they serve a Captcha?")
    return None

def build_vevent(uid, title, dtstart_line, dtend_line, description, alarm_name):
    """Creates the standard iCalendar event string."""
    return "\n".join([
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"SUMMARY:{title}",
        dtstart_line,
        dtend_line,
        f"DESCRIPTION:{description}",
        "BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:Reminder: {alarm_name} in 1 week", "TRIGGER:-P1W", "END:VALARM",
        "BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:Reminder: {alarm_name} in 2 days", "TRIGGER:-P2D", "END:VALARM",
        "END:VEVENT"
    ])

# ==========================================
# PART 1: LOAD EXISTING EVENTS
# ==========================================
FILENAME = "economic_and_earnings_events.ics"
existing_events = {}

if os.path.exists(FILENAME):
    print(f"📂 Reading existing '{FILENAME}' to prevent deletion of old events...")
    with open(FILENAME, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()
        
    in_event, current_uid = False, None
    current_event_lines = []
    
    for line in lines:
        if line.strip() == "BEGIN:VEVENT":
            in_event, current_uid, current_event_lines = True, None, []
            
        if in_event:
            current_event_lines.append(line)
            if line.startswith("UID:"): current_uid = line.split("UID:")[1].strip()
                
        if line.strip() == "END:VEVENT" and in_event:
            if current_uid: existing_events[current_uid] = "\n".join(current_event_lines)
            in_event = False
    print(f"✅ Loaded {len(existing_events)} historical events.")
else:
    print(f"📂 No existing '{FILENAME}' found. Starting fresh.")


# ==========================================
# PART 2: FETCH NEW DATA
# ==========================================
now = datetime.datetime.now()
future = now + datetime.timedelta(days=30) 

tz_offset = "%2B08%3A00" # URL encoded "+08:00"
start_date_eco = now.strftime("%Y-%m-%dT00%%3A00%%3A00.000") + tz_offset
end_date_eco = future.strftime("%Y-%m-%dT23%%3A59%%3A59.999") + tz_offset
start_date_earn = now.strftime("%Y-%m-%dT00%%3A00%%3A00.000Z")
end_date_earn = future.strftime("%Y-%m-%dT23%%3A59%%3A59.999Z")

new_events = {}
print(f"Fetching new data from {now.strftime('%Y-%m-%d')} to {future.strftime('%Y-%m-%d')}...")

# --- FETCH ECONOMIC EVENTS ---
print("\n--- Fetching Economic Events ---")
url_eco = (
    "https://endpoints.investing.com/pd-instruments/v1/calendars/economic/events/occurrences"
    f"?domain_id=1&limit=200&start_date={start_date_eco}&end_date={end_date_eco}"
    "&country_ids=5,35&importance=high"
)
res_eco = fetch_with_retry(url_eco)
if res_eco:
    data_eco = res_eco.json()
    events_lookup = {e["event_id"]: e for e in data_eco.get("events", [])}

    for occ in data_eco.get("occurrences", []):
        event_info = events_lookup.get(occ.get("event_id"), {})
        name = event_info.get("event_translated") or event_info.get("short_name") or "Economic Event"
        
        time_str = occ.get("occurrence_time")
        if not time_str: continue
        
        try:
            dt_utc = datetime.datetime.fromisoformat(time_str.replace('Z', '+00:00')).astimezone(datetime.timezone.utc)
        except: continue

        dtstart_line = f"DTSTART:{dt_utc.strftime('%Y%m%dT%H%M%SZ')}"
        dtend_line = f"DTEND:{(dt_utc + datetime.timedelta(minutes=30)).strftime('%Y%m%dT%H%M%SZ')}"

        fcst, prev, unit = occ.get("forecast", "N/A"), occ.get("previous", "N/A"), occ.get("unit", "")
        if fcst != "N/A": fcst = f"{fcst}{unit}"
        if prev != "N/A": prev = f"{prev}{unit}"
        
        description = f"Currency: {event_info.get('currency', 'N/A')}\\nForecast: {fcst}\\nPrevious: {prev}"
        
        uid = f"eco-{re.sub(r'[^a-zA-Z0-9]', '', name)}-{dt_utc.strftime('%Y%m%d')}@investing.com"
        new_events[uid] = build_vevent(uid, name, dtstart_line, dtend_line, description, name)
    print(f"✅ Parsed Economic Events.")

# --- FETCH EARNINGS EVENTS ---
print("\n--- Fetching Earnings Events ---")
token = get_anonymous_token()

if token:
    url_earn = (
        "https://endpoints.investing.com/earnings/v1/instruments/earnings"
        f"?start_date={start_date_earn}&end_date={end_date_earn}"
        "&country_ids=5&importance=high&limit=200&deduplicate=true"
    )
    res_earn = fetch_with_retry(url_earn, headers={"authorization": f"Bearer {token}"})
    
    if res_earn:
        earnings_data = res_earn.json().get("earnings", [])
        if earnings_data:
            instrument_ids = list(set([str(e["instrument_id"]) for e in earnings_data]))
            ids_query = "&".join([f"instrument_ids={i}" for i in instrument_ids])
            
            print("Fetching Company Tickers for Earnings...")
            url_inst = f"https://endpoints.investing.com/pd-instruments/v1/instruments?domain_id=1&{ids_query}"
            res_inst = fetch_with_retry(url_inst)
            
            instruments_lookup = {i["id"]: i for i in res_inst.json()} if res_inst else {}
            
            for earn in earnings_data:
                inst_id = earn.get("instrument_id")
                symbol = instruments_lookup.get(inst_id, {}).get("symbol", f"ID-{inst_id}")
                date_str = earn.get("date")
                
                if not date_str: continue
                
                phase = earn.get("market_phase", "")
                if phase == "PRE_MARKET":
                    dt_utc = get_utc_from_ny(date_str, 8, 0)
                    dtstart_line = f"DTSTART:{dt_utc.strftime('%Y%m%dT%H%M%SZ')}"
                    dtend_line = f"DTEND:{(dt_utc + datetime.timedelta(hours=1)).strftime('%Y%m%dT%H%M%SZ')}"
                elif phase == "AFTER_HOURS":
                    dt_utc = get_utc_from_ny(date_str, 16, 15)
                    dtstart_line = f"DTSTART:{dt_utc.strftime('%Y%m%dT%H%M%SZ')}"
                    dtend_line = f"DTEND:{(dt_utc + datetime.timedelta(hours=1)).strftime('%Y%m%dT%H%M%SZ')}"
                else:
                    date_obj = datetime.datetime.strptime(date_str, "%Y-%m-%d")
                    dtstart_line = f"DTSTART;VALUE=DATE:{date_obj.strftime('%Y%m%d')}"
                    dtend_line = f"DTEND;VALUE=DATE:{(date_obj + datetime.timedelta(days=1)).strftime('%Y%m%d')}"
                
                fcst = earn.get("eps_forecast", "N/A")
                rev = format_large_number(earn.get("revenue_forecast", "N/A"))
                description = f"EPS Forecast: {fcst}\\nRevenue Forecast: {rev}"
                
                uid = f"earn-{symbol}-{date_str.replace('-', '')}@investing.com"
                new_events[uid] = build_vevent(uid, f"[Earning] {symbol}", dtstart_line, dtend_line, description, f"{symbol} Earnings")
        print(f"✅ Parsed Earnings Events.")
else:
    print("⚠️ Skipping Earnings. No token available.")


# ==========================================
# PART 3: SMART MERGE AND SAVE
# ==========================================
merged_events = existing_events.copy()
merged_events.update(new_events)

final_ics = [
    "BEGIN:VCALENDAR",
    "VERSION:2.0",
    "PRODID:-//Python Economic & Earnings Calendar Fetcher//EN",
    "CALSCALE:GREGORIAN",
    "METHOD:PUBLISH"
]
final_ics.extend(merged_events.values())
final_ics.append("END:VCALENDAR")

with open(FILENAME, "w", encoding="utf-8") as f:
    f.write("\n".join(final_ics))

print(f"\n✅ Successfully saved {len(merged_events)} total merged events to '{FILENAME}'!")
