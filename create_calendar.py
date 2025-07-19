import calendar
import datetime
import csv
import sys
from collections import defaultdict
import json
import argparse
import re

try:
    from ics import Calendar, Event
    from tzlocal import get_localzone
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:
    pass  # Handled in main execution block where it's needed

# --- Configuration ---
MOM_COLOR = "#ffb6c1"
DAD_COLOR = "#add8e6"
DEFAULT_OUTPUT_HTML_FILE = "custody_calendar.html"
DEFAULT_CSS_FILE = "style.css"

CYCLE_START_DATE = None
DAY_NAMES = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
DAY_NAMES_LOWER = [d.lower() for d in DAY_NAMES]
DAY_MAP = {name: i for i, name in enumerate(DAY_NAMES)}
SLOTS_PER_DAY = 48


# --- Core Logic ---

def sanitize_filename(text: str) -> str:
    """Converts a string into a safe, usable filename."""
    if not text:
        return ""
    text = text.lower()
    text = re.sub(r'\s+', '_', text)
    text = re.sub(r'[^a-z0-9_-]', '', text)
    return text[:60]


def parse_schedule_from_csv(filepath: str) -> list:
    """Parses the schedule from a CSV file into a list of rules."""
    try:
        with open(filepath, mode='r', encoding='utf-8-sig') as infile:
            return list(csv.DictReader(infile))
    except FileNotFoundError:
        print(f"❌ Error: Schedule file not found: {filepath}", file=sys.stderr)
        return []


def time_to_slot(time_str: str) -> int:
    """Converts 'HH:MM' time string to a slot index (0-48)."""
    if not time_str: return None
    if time_str == "24:00": return SLOTS_PER_DAY
    hours, minutes = map(int, time_str.split(':'))
    return hours * 2 + minutes // 30


def slot_to_12h_time(slot: int) -> str:
    """Converts a slot index to a formatted 12-hour time string."""
    if slot >= SLOTS_PER_DAY: return ""
    h24 = slot // 2
    minutes = (slot % 2) * 30

    ampm = 'AM' if h24 < 12 else 'PM'
    h12 = h24 % 12
    if h12 == 0: h12 = 12

    if minutes == 0:
        return f"{h12}{ampm}"
    else:
        return f"{h12}:{minutes:02d}{ampm}"


def build_canonical_cycle(rules: list) -> (list, int):
    """Builds a canonical custody cycle from rules and returns the cycle and its length in weeks."""
    if not rules:
        return ([None] * (4 * 7 * SLOTS_PER_DAY), 4)

    try:
        num_weeks = max(int(rule["Week of Cycle"]) for rule in rules)
    except (ValueError, KeyError):
        print("⚠️ Warning: Could not find 'Week of Cycle' column or values are invalid. Defaulting to 4 weeks.",
              file=sys.stderr)
        num_weeks = 4

    total_slots = num_weeks * 7 * SLOTS_PER_DAY
    cycle_slots = [None] * total_slots

    for rule in rules:
        start_day_idx = DAY_MAP[rule["Start Day of Window"]]
        end_day_idx = DAY_MAP[rule["End Day of Window"]]
        start_slot_in_day = time_to_slot(rule["Start Time of Window"])
        end_slot_in_day = time_to_slot(rule["End Time of Window"])
        week = int(rule["Week of Cycle"])

        start_abs_slot = ((week - 1) * 7 + start_day_idx) * SLOTS_PER_DAY + start_slot_in_day
        end_abs_slot_base = ((week - 1) * 7 + end_day_idx) * SLOTS_PER_DAY + end_slot_in_day
        end_abs_slot = end_abs_slot_base + (
                7 * SLOTS_PER_DAY) if end_abs_slot_base <= start_abs_slot else end_abs_slot_base

        for i in range(start_abs_slot, end_abs_slot):
            cycle_slots[i % total_slots] = rule["Custodian"]

    return cycle_slots, num_weeks


def load_json_file(filepath: str, file_type: str) -> dict:
    """Loads and validates a JSON file."""
    if not filepath: return None
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"ⓘ Info: {file_type} file '{filepath}' not found. This is optional.")
        return None
    except json.JSONDecodeError:
        print(f"⚠️ Warning: Could not parse '{filepath}'. Make sure it is valid JSON.")
        return None


def build_daily_lookup(years: range, schedule_data: dict) -> list:
    """Builds a master lookup array for every day in the calendar range."""
    global CYCLE_START_DATE
    first_day = datetime.date(years.start, 1, 1)
    CYCLE_START_DATE = first_day - datetime.timedelta(days=first_day.isoweekday() % 7)

    daily_lookup = []
    start_date = datetime.date(years.start, 1, 1)
    end_date = datetime.date(years.stop - 1, 12, 31)

    schedule_map = schedule_data.get('map', {})
    school_weeks = schedule_map.get('school_weeks', [])
    summer_weeks = schedule_map.get('summer_weeks', [])

    current_date = start_date
    while current_date <= end_date:
        week_num = current_date.isocalendar().week
        day_of_week_lower = DAY_NAMES_LOWER[current_date.isoweekday() % 7]

        if week_num in school_weeks:
            active_cycle = schedule_data['school_cycle']
            active_cycle_duration_days = schedule_data['school_cycle_weeks'] * 7
            active_interaction = schedule_data.get('school_interaction')
        elif week_num in summer_weeks:
            active_cycle = schedule_data['summer_cycle']
            active_cycle_duration_days = schedule_data['summer_cycle_weeks'] * 7
            active_interaction = schedule_data.get('summer_interaction')
        else:
            active_cycle, active_cycle_duration_days, active_interaction = None, 0, None

        day_data = {"custody": [None] * SLOTS_PER_DAY, "interaction": None}

        if active_cycle and active_cycle_duration_days > 0:
            days_since_cycle_start = (current_date - CYCLE_START_DATE).days
            day_in_cycle = (
                                       days_since_cycle_start % active_cycle_duration_days + active_cycle_duration_days) % active_cycle_duration_days
            start_slot = day_in_cycle * SLOTS_PER_DAY
            day_data["custody"] = active_cycle[start_slot: start_slot + SLOTS_PER_DAY]

        if active_interaction and day_of_week_lower in active_interaction:
            day_data["interaction"] = active_interaction[day_of_week_lower]

        daily_lookup.append(day_data)
        current_date += datetime.timedelta(days=1)

    return daily_lookup


def build_markers_and_labels(years: range, schedule_data: dict, daily_lookup: list) -> (dict, dict):
    """Builds markers and labels for all schedule rules across the entire date range."""
    window_markers = defaultdict(lambda: defaultdict(list))
    end_time_labels = defaultdict(lambda: defaultdict(str))

    schedule_map = schedule_data.get('map', {})

    for schedule_type in ['school', 'summer']:
        rules = schedule_data.get(f'{schedule_type}_rules')
        cycle_weeks = schedule_data.get(f'{schedule_type}_cycle_weeks')
        valid_weeks = set(schedule_map.get(f'{schedule_type}_weeks', []))

        if not rules or not cycle_weeks or not valid_weeks:
            continue

        for rule in rules:
            try:
                week = int(rule["Week of Cycle"])
                window_num = rule["Window number"]
                start_day_idx = DAY_MAP[rule["Start Day of Window"]]
                end_day_idx = DAY_MAP[rule["End Day of Window"]]
            except (KeyError, ValueError) as e:
                print(f"⚠️ Warning: Skipping rule due to missing/invalid data: {rule}. Error: {e}", file=sys.stderr)
                continue

            start_abs_slot = ((week - 1) * 7 + start_day_idx) * SLOTS_PER_DAY + time_to_slot(
                rule["Start Time of Window"])
            end_abs_slot_base = ((week - 1) * 7 + end_day_idx) * SLOTS_PER_DAY + time_to_slot(
                rule["End Time of Window"])
            end_abs_slot = end_abs_slot_base + (
                    7 * SLOTS_PER_DAY) if end_abs_slot_base <= start_abs_slot else end_abs_slot_base

            day_of_start_in_cycle = start_abs_slot // SLOTS_PER_DAY
            day_of_end_in_cycle = end_abs_slot // SLOTS_PER_DAY

            for day_offset in range(len(daily_lookup)):
                current_date = datetime.date(years.start, 1, 1) + datetime.timedelta(days=day_offset)

                if current_date.isocalendar().week not in valid_weeks:
                    continue

                days_since_cycle_start = (current_date - CYCLE_START_DATE).days
                day_in_this_schedule_cycle = (days_since_cycle_start % (cycle_weeks * 7) + (cycle_weeks * 7)) % (
                        cycle_weeks * 7)

                if day_in_this_schedule_cycle == day_of_start_in_cycle:
                    marker_id = f"{schedule_type.capitalize()} {week}-{window_num}"
                    slot = start_abs_slot % SLOTS_PER_DAY
                    window_markers[current_date][slot].append(f"{marker_id} START")

                if day_in_this_schedule_cycle == day_of_end_in_cycle:
                    marker_id = f"{schedule_type.capitalize()} {week}-{window_num}"
                    slot = end_abs_slot % SLOTS_PER_DAY
                    window_markers[current_date][slot].append(f"{marker_id} END")
                    if slot != 0:
                        end_time_labels[current_date][slot] = slot_to_12h_time(slot)

    return window_markers, end_time_labels


# --- iCal Export Logic ---
def generate_ical_file(years: range, schedule_data: dict, output_filename: str, description: str, tz: datetime.tzinfo):
    """Generates a timezone-aware iCalendar (.ics) file from the schedule rules."""
    print(f"Generating iCalendar file using timezone: {tz}...")

    schedule_map = schedule_data.get('map', {})
    global CYCLE_START_DATE
    first_day = datetime.date(years.start, 1, 1)
    CYCLE_START_DATE = first_day - datetime.timedelta(days=first_day.isoweekday() % 7)

    raw_events = []
    for schedule_type in ['school', 'summer']:
        rules = schedule_data.get(f'{schedule_type}_rules')
        cycle_weeks = schedule_data.get(f'{schedule_type}_cycle_weeks')
        valid_iso_weeks = set(schedule_map.get(f'{schedule_type}_weeks', []))

        if not rules or not cycle_weeks or not valid_iso_weeks:
            continue

        for rule in rules:
            try:
                week_in_cycle = int(rule["Week of Cycle"])
                custodian = rule["Custodian"]
                start_day_idx = DAY_MAP[rule["Start Day of Window"]]
                start_h, start_m = map(int, rule["Start Time of Window"].split(':'))

                start_abs_slot = ((week_in_cycle - 1) * 7 + start_day_idx) * SLOTS_PER_DAY + time_to_slot(
                    rule["Start Time of Window"])
                end_day_idx = DAY_MAP[rule["End Day of Window"]]
                end_abs_slot_base = ((week_in_cycle - 1) * 7 + end_day_idx) * SLOTS_PER_DAY + time_to_slot(
                    rule["End Time of Window"])
                end_abs_slot = end_abs_slot_base + (
                            7 * SLOTS_PER_DAY) if end_abs_slot_base < start_abs_slot else end_abs_slot_base
                duration_in_minutes = (end_abs_slot - start_abs_slot) * 30

                first_start_day = CYCLE_START_DATE + datetime.timedelta(
                    days=(((week_in_cycle - 1) * 7) + start_day_idx))

                cycle_duration_delta = datetime.timedelta(weeks=cycle_weeks)
                current_start_day = first_start_day
                while current_start_day.year < years.stop:
                    if current_start_day.year >= years.start and current_start_day.isocalendar().week in valid_iso_weeks:
                        start_dt = datetime.datetime(current_start_day.year, current_start_day.month,
                                                     current_start_day.day, start_h, start_m, tzinfo=tz)
                        end_dt = start_dt + datetime.timedelta(minutes=duration_in_minutes)

                        event = Event()
                        event.name = f"{custodian}'s Custody"
                        if description: event.description = description
                        event.begin = start_dt
                        event.end = end_dt
                        raw_events.append(event)

                    current_start_day += cycle_duration_delta

            except (KeyError, ValueError) as e:
                print(f"⚠️ Warning: Skipping rule in .ics export due to invalid data: {rule}. Error: {e}",
                      file=sys.stderr)

    if not raw_events:
        print("⚠️ Warning: No events generated to export.")
        return

    print("Merging consecutive events...")
    raw_events.sort(key=lambda e: e.begin)

    merged_events = []
    if raw_events:
        current_event = raw_events[0]
        for next_event in raw_events[1:]:
            if next_event.name == current_event.name and next_event.begin == current_event.end:
                current_event.end = next_event.end
            else:
                merged_events.append(current_event)
                current_event = next_event
        merged_events.append(current_event)

    cal = Calendar(events=merged_events)
    try:
        with open(output_filename, 'w', encoding="utf-8") as f:
            f.writelines(cal.serialize_iter())
        print(f"✅ Success! Calendar data exported to '{output_filename}'")
    except IOError as e:
        print(f"❌ Error: Could not write to file '{output_filename}'. Reason: {e}", file=sys.stderr)


# --- File Generation ---

def get_default_css() -> str:
    """Returns the default CSS content as a string."""
    return f"""
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background-color: #f4f4f9; color: #333; margin: 0; padding: 20px; }}
        body.modal-open {{ overflow: hidden; }}
        .container {{ max-width: 1400px; margin: auto; }}
        .header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; flex-wrap: wrap; }}
        h1 {{ text-align: left; flex-grow: 1; margin-bottom: 0; }}
        .page-description {{ width: 100%; text-align: center; font-size: 1.2em; font-weight: bold; color: #555; margin-bottom: 20px; }}
        h2, h3 {{ text-align: center; color: #444; }}
        h2 {{ background-color: #e9ecef; padding: 15px; border-radius: 8px; margin-top: 40px; }}
        h3 {{ margin-block-start: 10px; margin-block-end: 10px; }}
        .stats, .interaction-stats {{ display: flex; justify-content: center; align-items: center; gap: 20px; font-weight: 500; margin-bottom: 10px;}}
        .interaction-stats {{ font-size: 0.9em; font-style: italic; color: #555; }}
        .legend {{ display: flex; justify-content: center; align-items: center; gap: 20px; font-weight: 500; margin: 0 auto; }}
        .legend-text {{ font-size: 1.1em; font-weight: bold; }}
        .legend-color {{ width: 20px; height: 20px; border-radius: 4px; border: 1px solid #ccc; margin-right: 8px;}}
        #settingsBtn {{ font-size: 1.5em; background: none; border: none; cursor: pointer; padding: 5px 10px; }}
        .calendar-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(400px, 1fr)); gap: 30px; }}
        .calendar-month {{ background-color: #fff; border-radius: 8px; padding: 15px; box-shadow: 0 4px 8px rgba(0,0,0,0.05); cursor: pointer; transition: transform 0.2s, box-shadow 0.2s; }}
        .calendar-month:hover {{ transform: translateY(-5px); box-shadow: 0 8px 16px rgba(0,0,0,0.1); }}
        .calendar-table {{ width: 100%; border-collapse: collapse; }}
        th, td {{ text-align: left; padding: 0; vertical-align: top; height: 80px; border: 1px solid #e0e0e0; position: relative; }}
        th {{ font-size: 0.8em; color: #666; padding-bottom: 5px; text-align: center; border: none; }}
        .day-number {{ position: absolute; top: 4px; left: 4px; font-size: 0.9em; font-weight: bold; z-index: 10; }}
        .custody-bar {{ display: flex; width: 100%; height: 100%; position: absolute; top: 0; left: 0; }}
        .mom-block {{ background-color: var(--mom-color); }}
        .dad-block {{ background-color: var(--dad-color); }}
        .noday {{ background-color: #f8f8f8; border-color: #f8f8f8; }}

        /* State-dependent styling */
        .end-time-label {{ position: absolute; top: 5px; transform: translateX(-50%); font-size: 10px; font-weight: 600; color: #333; background: rgba(255,255,255,0.8); padding: 1px 4px; border-radius: 3px; z-index: 15; white-space: nowrap; }}
        .debug-marker-wrapper {{ position: absolute; top: 0; bottom: 0; width: 1px; background-color: #d00; display: none; z-index: 20; }}
        .debug-marker {{ position: relative; top: 25px; transform: translateX(-50%); background: #d00; color: white; padding: 2px 5px; font-size: 10px; border-radius: 3px; white-space: nowrap; margin-bottom: 2px; }}
        body.debug-mode .debug-marker-wrapper {{ display: block; }}
        body.debug-mode .end-time-label {{ display: none; }}

        /* Continuous View Styling */
        .month-header-row {{ display: none; }}
        .month-header-cell {{ text-align: center; font-weight: bold; background-color: #f8f8f8; color: #555; border-bottom: 2px solid #ddd; }}
        body.continuous-view .month-header-cell {{ font-size: 1em; padding: 4px; line-height: 1.1; }}
        body.continuous-view th {{ padding-bottom: 3px; }}
        body.continuous-view td {{ height: 40px; }}
        body.continuous-view .calendar-grid {{ display: block; border: 1px solid #ccc; box-shadow: 0 4px 8px rgba(0,0,0,0.05); }}
        body.continuous-view .calendar-month {{ padding: 0; margin: 0; border-radius: 0; box-shadow: none; cursor: default; }}
        body.continuous-view .calendar-month:hover {{ transform: none; }}
        body.continuous-view .calendar-month h3, body.continuous-view .month-stats, body.continuous-view .interaction-stats {{ display: none; }}
        body.continuous-view .month-header-row {{ display: table-row; }}

        /* Modal Styles */
        .modal-overlay {{ position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.6); z-index: 100; display: none; align-items: center; justify-content: center; }}
        .modal-content {{ background: #fff; padding: 20px 30px; border-radius: 10px; max-width: 90%; position: relative; }}
        .modal-content.month-modal {{ width: 1200px; }}
        .modal-content.settings-modal {{ width: 320px; }}
        .modal-close {{ position: absolute; top: 10px; right: 20px; font-size: 30px; font-weight: bold; cursor: pointer; }}
        .modal-body .calendar-table td {{ height: 140px; }}
        .settings-modal .settings-item {{ display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 20px; }}
        .settings-modal .button-group {{ display: flex; gap: 10px; margin-top: 20px; }}
        .settings-modal .button-group button {{ flex-grow: 1; font-size: 1em; padding: 10px 15px; border-radius: 5px; cursor: pointer; }}
        #exportBtn {{ border: 1px solid #007bff; background-color: #007bff; color: white; }}
        #exportCalculationsBtn {{ border: 1px solid #17a2b8; background-color: #17a2b8; color: white; }}

        /* Toggle Switch */
        .toggle-switch {{ position: relative; display: inline-block; width: 50px; height: 24px; }}
        .toggle-switch input {{ opacity: 0; width: 0; height: 0; }}
        .slider {{ position: absolute; cursor: pointer; top: 0; left: 0; right: 0; bottom: 0; background-color: #ccc; border-radius: 24px; transition: .4s; }}
        .slider:before {{ position: absolute; content: ""; height: 16px; width: 16px; left: 4px; bottom: 4px; background-color: white; border-radius: 50%; transition: .4s; }}
        input:checked + .slider {{ background-color: #2196F3; }}
        input:checked + .slider:before {{ transform: translateX(26px); }}

        .pdf-header {{ display: none; }}
        /* Print-specific Styles */
        @media print {{
            body {{ background-color: #fff !important; padding-top: 50px; }}
            .page-description {{ display: block; position: fixed; top: 0; left: 0; right: 0; text-align: center; padding: 10px; font-size: 1.2em; font-weight: bold; border-bottom: 1px solid #ccc; background-color: #fff; z-index: 1000;}}
            .container {{ margin-top: 0; }}
            .header, .modal-overlay, .interaction-stats {{ display: none !important; }}
            .calendar-grid {{ display: grid !important; }}
            .calendar-month {{ box-shadow: none !important; border: 1px solid #ccc !important; page-break-inside: avoid; cursor: default; }}
            h2 {{ page-break-before: always; page-break-after: avoid; }}
            h1, h2, h3 {{ color: #000; }}
            .month-header-row, .debug-marker-wrapper {{ display: none !important; }}
            .end-time-label {{ display: block !important; }}
            .custody-block, .legend-color, .legend-text span {{ -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
        }}
    """


def write_default_css_file():
    """Writes the default CSS content to a file."""
    try:
        with open(DEFAULT_CSS_FILE, "w", encoding="utf-8") as f:
            f.write(get_default_css())
        print(f"✅ Success! Default stylesheet saved to '{DEFAULT_CSS_FILE}'")
    except IOError as e:
        print(f"❌ Error: Could not write stylesheet. Reason: {e}", file=sys.stderr)


def generate_html_calendar(years: range, daily_lookup: list, window_markers: dict, end_time_labels: dict, css_path=None,
                           description=None) -> str:
    """Generates the full HTML file content for the calendar."""

    daily_lookup_json = json.dumps(daily_lookup)
    description_json = json.dumps(description)

    title_text = f"Custody Calendar ({years.start}-{years.stop - 1})"
    if description:
        title_text += f" - {description}"

    if css_path:
        style_block = f'<link rel="stylesheet" href="{css_path}">'
    else:
        style_block = f'<style>{get_default_css()}</style>'

    html_start = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>{title_text}</title>
    <style> :root {{ --mom-color: {MOM_COLOR}; --dad-color: {DAD_COLOR}; }} </style>
    {style_block}
</head>
<body>
    <div class="container">
        <div class="page-description">{description or ''}</div>
        <div class="header">
            <h1>Child Custody Calendar</h1>
            <div class="legend">
                <div style="display: flex; align-items: center;"><div class="legend-color mom-block"></div><span class="legend-text" style="color: var(--mom-color);">Mom</span></div>
                <div style="display: flex; align-items: center;"><div class="legend-color dad-block"></div><span class="legend-text" style="color: var(--dad-color);">Dad</span></div>
            </div>
            <button id="settingsBtn" title="Settings">⚙️</button>
        </div>
    """

    html_body = ""
    cal = calendar.Calendar(firstweekday=6)

    for year in years:
        html_body += f"<div data-year-container='{year}'><h2>{year}</h2>"
        html_body += f"<div class='stats year-stats' data-year='{year}'></div>"
        html_body += f"<div class='interaction-stats' id='interaction-stats-year-{year}'></div>"
        html_body += f"<div class='calendar-grid'>"

        for month in range(1, 13):
            month_name = datetime.date(year, month, 1).strftime('%B')
            html_body += f"<div class='calendar-month'><div class='month-content'>"
            html_body += f"<h3>{month_name}</h3><div class='stats month-stats' data-year='{year}' data-month='{month}'></div>"
            html_body += f"<div class='interaction-stats' id='interaction-stats-month-{year}-{month}'></div>"
            html_body += f"<table class='calendar-table'><thead>"
            html_body += f"<tr class='month-header-row'><th colspan='7' class='month-header-cell'>{month_name} {year}</th></tr>"
            html_body += "<tr>"
            for day_name in DAY_NAMES:
                html_body += f"<th>{day_name[:3]}</th>"
            html_body += "</tr></thead><tbody>"

            for week in cal.monthdatescalendar(year, month):
                html_body += "<tr>"
                for day_date in week:
                    if day_date.month != month:
                        html_body += "<td class='noday'></td>"
                    else:
                        day_index = (day_date - datetime.date(years.start, 1, 1)).days
                        custody_slots = daily_lookup[day_index]["custody"] if day_index < len(daily_lookup) else [
                                                                                                                     None] * SLOTS_PER_DAY
                        html_body += f"<td><div class='day-number'>{day_date.day}</div>"
                        html_body += "<div class='custody-bar'>"
                        for i in range(SLOTS_PER_DAY):
                            custodian = custody_slots[i]
                            color_class = "mom-block" if custodian == "Mom" else "dad-block" if custodian == "Dad" else ""
                            html_body += f"<div class='custody-block {color_class}' style='width: {1 / SLOTS_PER_DAY * 100:.2f}%;'></div>"
                        html_body += "</div>"

                        day_labels = end_time_labels.get(day_date, {})
                        for slot, label_text in day_labels.items():
                            html_body += f"<div class='end-time-label' style='left: {(slot / SLOTS_PER_DAY) * 100:.2f}%;'>{label_text}</div>"

                        day_markers = window_markers.get(day_date, {})
                        for slot, markers in day_markers.items():
                            html_body += f"<div class='debug-marker-wrapper' style='left: {(slot / SLOTS_PER_DAY) * 100:.2f}%;'>"
                            for marker_text in markers:
                                html_body += f"<div class='debug-marker'>{marker_text}</div>"
                            html_body += "</div>"
                        html_body += "</td>"
                html_body += "</tr>"
            html_body += "</tbody></table></div></div>"
        html_body += "</div></div>"

    html_end = f"""
    </div>
    <div id="monthModal" class="modal-overlay"><div class="modal-content month-modal"><span class="modal-close">&times;</span><div class="modal-body"></div></div></div>
    <div id="settingsModal" class="modal-overlay"><div class="modal-content settings-modal"><span class="modal-close">&times;</span><h3>Settings</h3>
        <div class="settings-item"><span>Continuous View</span><label class="toggle-switch"><input type="checkbox" id="viewToggle"><span class="slider"></span></label></div>
        <div class="settings-item"><span>Show Window Markers</span><label class="toggle-switch"><input type="checkbox" id="debugToggle"><span class="slider"></span></label></div>
        <div class="button-group"><button id="exportBtn">Export Page</button><button id="exportCalculationsBtn">Export Calculations</button></div>
    </div></div>

    <script>
        const DAILY_LOOKUP = {daily_lookup_json};
        const CALENDAR_DESCRIPTION = {description_json};
        const CALENDAR_START_DATE = new Date("{years.start}", 0, 1);
        const SLOTS_PER_DAY = {SLOTS_PER_DAY};
        const MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

        document.addEventListener('DOMContentLoaded', () => {{
            const viewToggle = document.getElementById('viewToggle');
            const debugToggle = document.getElementById('debugToggle');
            const exportBtn = document.getElementById('exportBtn');
            const exportCalculationsBtn = document.getElementById('exportCalculationsBtn');
            const settingsBtn = document.getElementById('settingsBtn');
            const monthModal = document.getElementById('monthModal');
            const settingsModal = document.getElementById('settingsModal');

            function openModal(modal) {{ modal.style.display = 'flex'; document.body.classList.add('modal-open'); }}
            function closeModal(modal) {{ modal.style.display = 'none'; if (!document.querySelector('.modal-overlay[style*="display: flex"]')) document.body.classList.remove('modal-open'); }}

            viewToggle.addEventListener('change', () => document.body.classList.toggle('continuous-view', viewToggle.checked));
            debugToggle.addEventListener('change', () => document.body.classList.toggle('debug-mode', debugToggle.checked));
            exportBtn.addEventListener('click', () => window.print());
            settingsBtn.addEventListener('click', () => openModal(settingsModal));
            exportCalculationsBtn.addEventListener('click', exportCalculationsToCSV);

            document.querySelectorAll('.calendar-month').forEach(monthEl => {{
                monthEl.addEventListener('click', () => {{
                    if (document.body.classList.contains('continuous-view')) return;
                    const monthContent = monthEl.querySelector('.month-content').cloneNode(true);
                    monthModal.querySelector('.modal-body').innerHTML = '';
                    monthModal.querySelector('.modal-body').appendChild(monthContent);
                    openModal(monthModal);
                }});
            }});

            [monthModal, settingsModal].forEach(modal => {{
                modal.querySelector('.modal-close').addEventListener('click', () => closeModal(modal));
                modal.addEventListener('click', (e) => {{ if (e.target === modal) closeModal(modal); }});
            }});
            document.addEventListener('keydown', (e) => {{ if (e.key === "Escape") [monthModal, settingsModal].forEach(closeModal); }});

            function timeToSlot(timeStr) {{
                if (!timeStr || !/^[0-9]{{2}}:[0-9]{{2}}$/.test(timeStr)) return null;
                const [hours, minutes] = timeStr.split(':').map(Number);
                return hours * 2 + Math.floor(minutes / 30);
            }}

            function calculateStatsForPeriod(startIndex, endIndex) {{
                let momSlots = 0, dadSlots = 0, totalSlots = 0;
                let momInteraction = 0, dadInteraction = 0, totalInteraction = 0;

                for (let i = startIndex; i <= endIndex; i++) {{
                    const dayData = DAILY_LOOKUP[i];
                    if (!dayData) continue;
                    for (const custodian of dayData.custody) {{
                        if (custodian === 'Mom') momSlots++;
                        else if (custodian === 'Dad') dadSlots++;
                    }}
                    totalSlots += dayData.custody.length;
                    const window = dayData.interaction;
                    if (window) {{
                        const startSlot = timeToSlot(window.start);
                        const endSlot = timeToSlot(window.end);
                        if (startSlot !== null && endSlot !== null && startSlot < endSlot) {{
                            for (let slot = startSlot; slot < endSlot; slot++) {{
                                const custodian = dayData.custody[slot];
                                if (custodian === 'Mom') momInteraction++;
                                else if (custodian === 'Dad') dadInteraction++;
                            }}
                            totalInteraction += (endSlot - startSlot);
                        }}
                    }}
                }}
                return {{ momSlots, dadSlots, totalSlots, momInteraction, dadInteraction, totalInteraction }};
            }}

            function getDayIndex(date) {{
                const utcDate = Date.UTC(date.getFullYear(), date.getMonth(), date.getDate());
                const utcStartDate = Date.UTC(CALENDAR_START_DATE.getFullYear(), CALENDAR_START_DATE.getMonth(), CALENDAR_START_DATE.getDate());
                return Math.floor((utcDate - utcStartDate) / (24 * 60 * 60 * 1000));
            }}

            function runAllCalculations() {{
                document.querySelectorAll('[data-year-container]').forEach(yearEl => {{
                    const year = parseInt(yearEl.dataset.yearContainer);
                    const yearStartDate = new Date(year, 0, 1);
                    const yearEndDate = new Date(year, 11, 31);
                    const yearStartIndex = getDayIndex(yearStartDate);
                    const yearEndIndex = getDayIndex(yearEndDate);

                    const calculatedYearStats = calculateStatsForPeriod(yearStartIndex, yearEndIndex);
                    const yearStatsDiv = yearEl.querySelector('.year-stats');
                    if(calculatedYearStats.totalSlots > 0) {{
                        yearStatsDiv.innerHTML = `<div>Mom: ${{(calculatedYearStats.momSlots / calculatedYearStats.totalSlots * 100).toFixed(2)}}%</div><div>Dad: ${{(calculatedYearStats.dadSlots / calculatedYearStats.totalSlots * 100).toFixed(2)}}%</div>`;
                    }}

                    if (calculatedYearStats.totalInteraction > 0) {{
                        const resultDiv = yearEl.querySelector(`#interaction-stats-year-${{year}}`);
                        const momPercent = (calculatedYearStats.momInteraction / calculatedYearStats.totalInteraction) * 100;
                        const dadPercent = (calculatedYearStats.dadInteraction / calculatedYearStats.totalInteraction) * 100;
                        resultDiv.innerHTML = `Interaction Time: <div>Mom: <span style="color:var(--mom-color)">${{momPercent.toFixed(2)}}%</span></div> <div>Dad: <span style="color:var(--dad-color)">${{dadPercent.toFixed(2)}}%</span></div>`;
                    }}

                    for (let month = 0; month < 12; month++) {{
                        const monthStartDate = new Date(year, month, 1);
                        const monthEndDate = new Date(year, month + 1, 0);
                        const monthStartIndex = getDayIndex(monthStartDate);
                        const monthEndIndex = getDayIndex(monthEndDate);
                        const calculatedMonthStats = calculateStatsForPeriod(monthStartIndex, monthEndIndex);

                        const monthStatsDiv = yearEl.querySelector(`.month-stats[data-month='${{month+1}}']`);
                        if(monthStatsDiv && calculatedMonthStats.totalSlots > 0) {{
                            monthStatsDiv.innerHTML = `<div>Mom: ${{(calculatedMonthStats.momSlots / calculatedMonthStats.totalSlots * 100).toFixed(2)}}%</div><div>Dad: ${{(calculatedMonthStats.dadSlots / calculatedMonthStats.totalSlots * 100).toFixed(2)}}%</div>`;
                        }}

                        if(calculatedMonthStats.totalInteraction > 0) {{
                            const resultDiv = yearEl.querySelector(`#interaction-stats-month-${{year}}-${{month+1}}`);
                            const momPercent = (calculatedMonthStats.momInteraction / calculatedMonthStats.totalInteraction) * 100;
                            const dadPercent = (calculatedMonthStats.dadInteraction / calculatedMonthStats.totalInteraction) * 100;
                            if(resultDiv) resultDiv.innerHTML = `Interaction: <div>Mom: <span style="color:var(--mom-color)">${{momPercent.toFixed(2)}}%</span></div> <div>Dad: <span style="color:var(--dad-color)">${{dadPercent.toFixed(2)}}%</span></div>`;
                        }}
                    }}
                }});
            }}

            function exportCalculationsToCSV() {{
                const quote = (val) => `"${{String(val === null || val === undefined ? '' : val).replace(/"/g, '""')}}"`;
                let csvRows = [];

                if (CALENDAR_DESCRIPTION) {{
                    csvRows.push([quote(CALENDAR_DESCRIPTION)]);
                    csvRows.push([]);
                }}
                csvRows.push(["Custody Percentage Calculation Audit File"]);
                csvRows.push([quote('Generated On:'), quote(new Date().toLocaleDateString())]);
                csvRows.push([quote('Purpose:'), quote('This file breaks down the custody schedule to show how time percentages are calculated. All time is measured in 30-minute blocks (slots).')]);
                csvRows.push([]);

                const startYear = {years.start};
                const endYear = {years.stop - 1};
                const overallStats = calculateStatsForPeriod(0, DAILY_LOOKUP.length - 1);
                csvRows.push([`OVERALL SUMMARY (${{startYear}} - ${{endYear}})`]);
                csvRows.push(["Calculation Type", "Mom's Slots", "Dad's Slots", "Total Slots", "Mom's Percentage", "Dad's Percentage"]);

                if (overallStats.totalSlots > 0) {{
                    const totalMomPct = (overallStats.momSlots / overallStats.totalSlots * 100).toFixed(2) + '%';
                    const totalDadPct = (overallStats.dadSlots / overallStats.totalSlots * 100).toFixed(2) + '%';
                    csvRows.push(["Total Custody Time", overallStats.momSlots, overallStats.dadSlots, overallStats.totalSlots, totalMomPct, totalDadPct]);
                }}

                if(overallStats.totalInteraction > 0) {{
                    const intMomPct = (overallStats.momInteraction / overallStats.totalInteraction * 100).toFixed(2) + '%';
                    const intDadPct = (overallStats.dadInteraction / overallStats.totalInteraction * 100).toFixed(2) + '%';
                    csvRows.push(["Interaction Time", overallStats.momInteraction, overallStats.dadInteraction, overallStats.totalInteraction, intMomPct, intDadPct]);
                }}
                csvRows.push([]);
                csvRows.push([]);

                csvRows.push(["MONTHLY BREAKDOWN"]);
                let headers = ["Year", "Month", "Total Slots", "Mom's Slots", "Dad's Slots", "Mom %", "Dad %", "Interaction Mom Slots", "Interaction Dad Slots", "Interaction Total", "Interaction Mom %", "Interaction Dad %"];
                csvRows.push(headers);

                for (let year = startYear; year <= endYear; year++) {{
                    for (let month = 0; month < 12; month++) {{
                        const monthStartDate = new Date(year, month, 1);
                        const monthEndDate = new Date(year, month + 1, 0);
                        const monthStartIndex = getDayIndex(monthStartDate);
                        const monthEndIndex = getDayIndex(monthEndDate);
                        const stats = calculateStatsForPeriod(monthStartIndex, monthEndIndex);

                        const momPct = stats.totalSlots > 0 ? (stats.momSlots/stats.totalSlots*100).toFixed(2) + '%' : 'N/A';
                        const dadPct = stats.totalSlots > 0 ? (stats.dadSlots/stats.totalSlots*100).toFixed(2) + '%' : 'N/A';
                        const momIntPct = stats.totalInteraction > 0 ? (stats.momInteraction/stats.totalInteraction*100).toFixed(2) + '%' : 'N/A';
                        const dadIntPct = stats.totalInteraction > 0 ? (stats.dadInteraction/stats.totalInteraction*100).toFixed(2) + '%' : 'N/A';

                        let row = [year, MONTH_NAMES[month], stats.totalSlots, stats.momSlots, stats.dadSlots, 
                                   momPct, dadPct,
                                   stats.momInteraction, stats.dadInteraction, stats.totalInteraction,
                                   momIntPct, dadIntPct];
                        csvRows.push(row);
                    }}
                }}

                const csvContent = "data:text/csv;charset=utf-8," + csvRows.map(e => e.map(quote).join(",")).join("\\n");
                const encodedUri = encodeURI(csvContent);
                const link = document.createElement("a");
                link.setAttribute("href", encodedUri);
                link.setAttribute("download", "custody_calculation_audit.csv");
                document.body.appendChild(link);
                link.click();
                document.body.removeChild(link);
            }}

            runAllCalculations();
        }});
    </script>
    """
    return html_start + html_body + html_end


# --- Main Execution ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate a custody calendar from schedule definitions.",
        formatter_class=argparse.RawTextHelpFormatter
    )
    # Arguments for all modes
    parser.add_argument('map_file',
                        help="Path to the main schedule_map.json file.")
    parser.add_argument('school_schedule',
                        help="Path to the school schedule CSV file.")
    parser.add_argument('summer_schedule', nargs='?', default=None,
                        help="Path to the summer schedule CSV file (optional).")
    parser.add_argument('--description', help="A description for the calendar titles and headers.")

    # Mode-specific arguments
    html_group = parser.add_argument_group('HTML Output (Default Mode)')
    html_group.add_argument('--school_interaction', default=None,
                            help="Path to the school interaction JSON file.")
    html_group.add_argument('--summer_interaction', default=None,
                            help="Path to the summer interaction JSON file.")
    html_group.add_argument('--style-file', help="Path to an existing CSS file to use for styling.")

    action_group = parser.add_argument_group('Standalone Actions & iCal Export')
    action_group.add_argument('--write-style-defaults', action='store_true',
                              help=f"Write the default styles to {DEFAULT_CSS_FILE} and exit.")
    action_group.add_argument('--export-ical', metavar="FILENAME.ICS",
                              help="Export the schedule to an iCalendar (.ics) file and exit.")
    action_group.add_argument('--timezone',
                              help="Specify timezone for iCal export (e.g., 'America/New_York').\nDefaults to local system time.")

    args = parser.parse_args()

    if args.write_style_defaults:
        write_default_css_file()
        sys.exit(0)

    schedule_map = load_json_file(args.map_file, "Schedule Map")
    if not schedule_map or 'start_year' not in schedule_map or 'end_year' not in schedule_map:
        print(f"❌ Error: '{args.map_file}' must exist and contain 'start_year' and 'end_year' keys.",
              file=sys.stderr)
        sys.exit(1)

    start_year = schedule_map['start_year']
    end_year = schedule_map['end_year']
    years_range = range(start_year, end_year + 1)

    schedules = {
        'map': schedule_map,
        'school_rules': parse_schedule_from_csv(args.school_schedule),
        'summer_rules': parse_schedule_from_csv(args.summer_schedule),
        'school_interaction': load_json_file(args.school_interaction, "School Interaction"),
        'summer_interaction': load_json_file(args.summer_interaction, "Summer Interaction"),
    }

    if not schedules['school_rules']:
        print(f"❌ Error: Could not load '{args.school_schedule}'. This file is required.", file=sys.stderr)
        sys.exit(1)

    print("Building canonical cycles for each schedule type...")
    schedules['school_cycle'], schedules['school_cycle_weeks'] = build_canonical_cycle(schedules['school_rules'])
    schedules['summer_cycle'], schedules['summer_cycle_weeks'] = build_canonical_cycle(schedules['summer_rules'])

    # --- Mode selection: iCal Export or HTML Generation ---
    if args.export_ical:
        try:
            from ics import Calendar, Event
            from tzlocal import get_localzone
            from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        except ImportError:
            print("❌ Error: Required libraries for iCal export are missing.", file=sys.stderr)
            print("Please install them by running: pip install ics tzlocal", file=sys.stderr)
            sys.exit(1)

        tz = None
        if args.timezone:
            try:
                tz = ZoneInfo(args.timezone)
            except ZoneInfoNotFoundError:
                print(f"❌ Error: Timezone '{args.timezone}' not found.", file=sys.stderr)
                sys.exit(1)
        else:
            tz = get_localzone()
        generate_ical_file(years_range, schedules, args.export_ical, args.description, tz)
    else:
        # Full HTML generation path
        print("Building master daily lookup for all years...")
        daily_lookup_data = build_daily_lookup(years_range, schedules)

        print("Building window markers and time labels...")
        window_markers, end_time_labels = build_markers_and_labels(years_range, schedules, daily_lookup_data)

        output_filename = DEFAULT_OUTPUT_HTML_FILE
        if args.description:
            safe_desc = sanitize_filename(args.description)
            if safe_desc:
                output_filename = f"{safe_desc}_{DEFAULT_OUTPUT_HTML_FILE}"

        print(f"Generating HTML calendar for {start_year}-{end_year}...")
        html_content = generate_html_calendar(
            years=years_range,
            daily_lookup=daily_lookup_data,
            window_markers=window_markers,
            end_time_labels=end_time_labels,
            css_path=args.style_file,
            description=args.description
        )

        try:
            with open(output_filename, "w", encoding="utf-8") as f:
                f.write(html_content)
            print(f"✅ Success! Calendar saved to '{output_filename}'")
        except IOError as e:
            print(f"❌ Error: Could not write to file '{output_filename}'. Reason: {e}", file=sys.stderr)