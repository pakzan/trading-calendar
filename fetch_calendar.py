from curl_cffi import requests
import datetime
import re

def make_uid(*parts):
    """Create a stable UID for an ICS event."""
    raw = "|".join(str(p) for p in parts)
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "-", raw).strip("-")
    return f"{safe}@economic-calendar.local"

def format_large_number(value):
    """Helper to turn 113900000000 into 113.90B"""
    if value is None or value == "N/A": return "N/A"
    try:
        v = float(value)
        if v >= 1e9: return f"{v/1e9:.2f}B"
        if v >= 1e6: return f"{v/1e6:.2f}M"
        return f"{v:.2f}"
    except:
        return str(value)

def get_utc_from_ny(date_str, hour, minute):
    """Converts a specific US Eastern Time to UTC, automatically handling US Daylight Saving Time."""
    dt = datetime.datetime.strptime(date_str, "%Y-%m-%d")
    dt = dt.replace(hour=hour, minute=minute)
    
    year = dt.year
    mar_1 = datetime.datetime(year, 3, 1)
    mar_2nd_sun = mar_1 + datetime.timedelta(days=(6 - mar_1.weekday() + 7) % 7 + 7)
    
    nov_1 = datetime.datetime(year, 11, 1)
    nov_1st_sun = nov_1 + datetime.timedelta(days=(6 - nov_1.weekday()) % 7)
    
    if mar_2nd_sun.date() <= dt.date() < nov_1st_sun.date():
        offset = -4  # EDT is UTC-4
    else:
        offset = -5  # EST is UTC-5
        
    utc_dt = dt - datetime.timedelta(hours=offset)
    return utc_dt

def get_anonymous_token():
    """Visits the homepage and scrapes a fresh guest JWT Token from the source code."""
    print("Scraping fresh Auth Token from Investing.com...")
    try:
        res = requests.get("https://www.investing.com/", impersonate="chrome")
        if res.status_code == 200:
            # Regex to find standard JWT token starting with HS256 header (eyJhbGciOiJIUzI1NiIs...)
            match = re.search(r'(eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+)', res.text)
            if match:
                print("✅ Successfully generated new session token!")
                return match.group(1)
            else:
                print("❌ Failed to find token in page source.")
                return None
    except Exception as e:
        print(f"❌ Error fetching token: {e}")
        return None

# 1. Calculate Dates
now = datetime.datetime.now()
future = now + datetime.timedelta(days=30) 

tz_offset = "%2B08%3A00" # URL encoded "+08:00" for economic events
start_date_eco = now.strftime("%Y-%m-%dT00%%3A00%%3A00.000") + tz_offset
end_date_eco = future.strftime("%Y-%m-%dT23%%3A59%%3A59.999") + tz_offset

start_date_earn = now.strftime("%Y-%m-%dT00%%3A00%%3A00.000Z")
end_date_earn = future.strftime("%Y-%m-%dT23%%3A59%%3A59.999Z")

print(f"Fetching data from {now.strftime('%Y-%m-%d')} to {future.strftime('%Y-%m-%d')}...")

# --- START CALENDAR ICS SETUP ---
ics_lines = [
    "BEGIN:VCALENDAR",
    "VERSION:2.0",
    "PRODID:-//Python Economic & Earnings Calendar Fetcher//EN",
    "CALSCALE:GREGORIAN",
    "METHOD:PUBLISH"
]
event_count = 0

# ==========================================
# PART 1: FETCH ECONOMIC EVENTS
# ==========================================
url_eco = (
    "https://endpoints.investing.com/pd-instruments/v1/calendars/economic/events/occurrences"
    f"?domain_id=1&limit=200&start_date={start_date_eco}&end_date={end_date_eco}"
    "&country_ids=5,35&importance=high"
)

try:
    print("Fetching Economic Events...")
    res_eco = requests.get(url_eco, impersonate="chrome")
    if res_eco.status_code == 200:
        data_eco = res_eco.json()
        occurrences = data_eco.get("occurrences", [])
        events_lookup = {e["event_id"]: e for e in data_eco.get("events", [])}

        for occ in occurrences:
            event_id = occ.get("event_id")
            event_info = events_lookup.get(event_id, {})
            name = event_info.get("event_translated") or event_info.get("short_name") or "Economic Event"
            currency = event_info.get("currency", "N/A")
            
            time_str = occ.get("occurrence_time")
            if not time_str: continue
            
            try:
                dt_obj = datetime.datetime.fromisoformat(time_str.replace('Z', '+00:00'))
            except: continue

            dt_utc = dt_obj.astimezone(datetime.timezone.utc)
            dt_start_str = dt_utc.strftime("%Y%m%dT%H%M%SZ")
            dt_end_str = (dt_utc + datetime.timedelta(minutes=30)).strftime("%Y%m%dT%H%M%SZ")

            forecast = occ.get("forecast", "N/A")
            previous = occ.get("previous", "N/A")
            unit = occ.get("unit", "")
            
            if forecast != "N/A": forecast = f"{forecast}{unit}"
            if previous != "N/A": previous = f"{previous}{unit}"
            
            description = f"Currency: {currency}\\nForecast: {forecast}\\nPrevious: {previous}"

            event_uid = make_uid(
                "economic",
                event_id,
                dt_start_str
            )
            ics_lines.extend([
                "BEGIN:VEVENT",
                f"UID:{event_uid}",
                f"SUMMARY:{name}",
                f"DTSTART:{dt_start_str}",
                f"DTEND:{dt_end_str}",
                f"DESCRIPTION:{description}",
                "BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:Reminder: {name} in 1 week", "TRIGGER:-P1W", "END:VALARM",
                "BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:Reminder: {name} in 2 days", "TRIGGER:-P2D", "END:VALARM",
                "END:VEVENT"
            ])
            event_count += 1
except Exception as e:
    print(f"Error fetching economic events: {e}")


# ==========================================
# PART 2: FETCH EARNINGS & INSTRUMENTS
# ==========================================
url_earn = (
    "https://endpoints.investing.com/earnings/v1/instruments/earnings"
    f"?start_date={start_date_earn}&end_date={end_date_earn}"
    "&country_ids=5&importance=high&limit=200&deduplicate=true"
)

# ⭐️ CALL THE NEW SCRAPER FUNCTION TO GET THE TOKEN ⭐️
fresh_token = get_anonymous_token()

if fresh_token:
    earn_headers = {
        "authorization": f"Bearer {fresh_token}",
        "accept": "*/*"
    }

    try:
        print("Fetching Earnings Events...")
        res_earn = requests.get(url_earn, impersonate="chrome", headers=earn_headers)
        
        if res_earn.status_code == 200:
            earnings_data = res_earn.json().get("earnings", [])
            
            if earnings_data:
                instrument_ids = list(set([str(e["instrument_id"]) for e in earnings_data]))
                ids_query = "&".join([f"instrument_ids={i}" for i in instrument_ids])
                
                print("Fetching Company Tickers...")
                url_inst = f"https://endpoints.investing.com/pd-instruments/v1/instruments?domain_id=1&{ids_query}"
                res_inst = requests.get(url_inst, impersonate="chrome")
                
                instruments_lookup = {}
                if res_inst.status_code == 200:
                    for inst in res_inst.json():
                        instruments_lookup[inst["id"]] = inst
                
                for earn in earnings_data:
                    inst_id = earn.get("instrument_id")
                    inst_details = instruments_lookup.get(inst_id, {})
                    
                    symbol = inst_details.get("symbol", f"ID:{inst_id}")
                    
                    phase_raw = earn.get("market_phase", "")
                    date_str = earn.get("date")
                    if not date_str: continue
                    
                    if phase_raw == "PRE_MARKET":
                        dt_utc = get_utc_from_ny(date_str, 8, 0)
                        dtstart_line = f"DTSTART:{dt_utc.strftime('%Y%m%dT%H%M%SZ')}"
                        dtend_line = f"DTEND:{(dt_utc + datetime.timedelta(hours=1)).strftime('%Y%m%dT%H%M%SZ')}"
                    elif phase_raw == "AFTER_HOURS":
                        dt_utc = get_utc_from_ny(date_str, 16, 15)
                        dtstart_line = f"DTSTART:{dt_utc.strftime('%Y%m%dT%H%M%SZ')}"
                        dtend_line = f"DTEND:{(dt_utc + datetime.timedelta(hours=1)).strftime('%Y%m%dT%H%M%SZ')}"
                    else:
                        date_obj = datetime.datetime.strptime(date_str, "%Y-%m-%d")
                        dtstart_line = f"DTSTART;VALUE=DATE:{date_obj.strftime('%Y%m%d')}"
                        dtend_line = f"DTEND;VALUE=DATE:{(date_obj + datetime.timedelta(days=1)).strftime('%Y%m%d')}"
                    
                    eps_forecast = earn.get("eps_forecast", "N/A")
                    rev_forecast = format_large_number(earn.get("revenue_forecast", "N/A"))
                    
                    description = f"EPS Forecast: {eps_forecast}\\nRevenue Forecast: {rev_forecast}"
                    event_title = f"[Earning] {symbol}"
                    event_uid = make_uid(
                        "earnings",
                        inst_id,
                        date_str
                    )
                    ics_lines.extend([
                        "BEGIN:VEVENT",
                        f"UID:{event_uid}",
                        f"SUMMARY:{event_title}",
                        dtstart_line,
                        dtend_line,
                        f"DESCRIPTION:{description}",
                        "BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:Reminder: {symbol} Earnings in 1 week", "TRIGGER:-P1W", "END:VALARM",
                        "BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:Reminder: {symbol} Earnings in 2 days", "TRIGGER:-P2D", "END:VALARM",
                        "END:VEVENT"
                    ])
                    event_count += 1
        else:
            print(f"Failed to fetch earnings. HTTP Status: {res_earn.status_code}")
            print(res_earn.text)
    except Exception as e:
        print(f"Error fetching earnings: {e}")
else:
    print("Skipping Earnings because token generation failed.")

# ==========================================
# PART 3: SAVE CALENDAR
# ==========================================
ics_lines.append("END:VCALENDAR")
filename = "economic_and_earnings_events.ics"
with open(filename, "w", encoding="utf-8") as f:
    f.write("\n".join(ics_lines))

print(f"✅ Successfully saved {event_count} combined events with notifications to '{filename}'!")
