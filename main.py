import json
import os
from pprint import pprint
import sys
from io import StringIO
from datetime import datetime
import logging
from logging import debug
import csv
import requests
from skyfield.api import EarthSatellite, load, wgs84


def sync_satcat_csv():
    def pull_and_save_csv():
        res = requests.get("https://celestrak.org/pub/satcat.csv")
        res.raise_for_status()
        with open("satcat.csv", "w") as file:
            file.write(res.text)

    if not os.path.exists("satcat.csv"):
        pull_and_save_csv()
    else:
        # Sync the latest satcat csv from Celestrak if file is older than 1 day
        current_time = datetime.now()
        last_modified = datetime.fromtimestamp(os.path.getmtime("satcat.csv"))
        print(current_time)
        print(last_modified)
        diff = current_time - last_modified
        if diff.days > 0 or int(os.environ.get("FORCE_SYNC_SATCAT")) == 1:
            pull_and_save_csv()


def find_satcat_entry_by_id(satcatId):
    with open("satcat.csv", "r") as file:
        reader = csv.DictReader(file)
        for row in reader:
            if row["NORAD_CAT_ID"] == str(satcatId):
                return row
        print("No entry found for ID: {satcatId}")
        print("The SATCAT data is valid as of 14/03/2026")
    return None


def get_celestrak_data_by_satcat_id(satcatId: int, format="TLE"):
    """
    Allowed formats are:
    - TLE or 3LE: Three-line element sets including 24-character satellite name on Line 0.
    - 2LE: Two-line element sets (no satellite name on Line 0).
    - XML: CCSDS OMM XML format including all mandatory elements.
    - KVN: CCSDS OMM KVN format including all mandatory elements.
    - JSON: OMM keywords for all GP elements in JSON format.
    - JSON-PRETTY: OMM keywords for all GP elements in JSON pretty-print format.
    - CSV: OMM keywords for all GP elements in CSV format.
    """
    url = f"https://celestrak.org/NORAD/elements/gp.php?CATNR={satcatId}&FORMAT={format.upper()}"
    response = requests.get(url)
    return response.text


def create_sat_entity_from_omm_csv(ts, csv_string):
    f = StringIO(csv_string)
    data = csv.DictReader(f)
    sat = [EarthSatellite.from_omm(ts, fields) for fields in data][0]
    return sat


def postcode_to_latlon(postcode):
    res = requests.get(f"https://api.postcodes.io/postcodes/{postcode}")
    res.raise_for_status()
    data = res.json()
    return data["result"]["latitude"], data["result"]["longitude"]


def reverse_geocode(lat, lon):
    print("Reverse geocoding")
    api_key = os.environ.get("GEOAPIFY_KEY", "")
    if len(str(api_key)) == 0:
        raise Exception("Cannot reverse geocode without a Geoapify API key")

    url = f"https://api.geoapify.com/v1/geocode/reverse?lat={lat}&lon={lon}&apiKey={api_key}"
    res = requests.get(url)
    res.raise_for_status()
    dict = json.loads(res.text)
    pprint(dict)


def lat_lon_at_ts(sat: EarthSatellite, ts, offset=0):
    geocentric_post = sat.at(ts.now() + offset)
    lat, lon = wgs84.latlon_of(geocentric_post)
    return (lat.degrees, lon.degrees)


def find_visible_passes(sat: EarthSatellite, location, eph, ts, hours_ahead=6):
    t0 = ts.now()
    t1 = ts.now() + (hours_ahead / 24)

    # Find rise/culmination/set events above 20°
    t, events = sat.find_events(location, t0, t1, altitude_degrees=20)
    # events: 0 = rise, 1 = culmination (max elevation), 2 = set

    event_labels = ["rise", "peak", "set"]
    passes = []
    current_pass = {}

    for ti, event in zip(t, events):
        name = event_labels[event]
        difference = sat - location
        topocentric = difference.at(ti)
        alt, az, _ = topocentric.altaz()

        sat_sunlit = sat.at(ti).is_sunlit(eph)
        observer_dark = not location.at(ti).is_sunlit(eph)
        visible = sat_sunlit and observer_dark

        current_pass[name] = {
            "time": ti.utc_iso(),
            "alt": round(alt.degrees, 1),
            "az": round(az.degrees, 1),
            "visible": visible,
        }

        if name == "set":
            passes.append(current_pass)
            current_pass = {}

    return passes


def main():
    # Setup
    satcat_id = int(sys.argv[1])
    user_lat = float(sys.argv[2])
    user_lon = float(sys.argv[3])
    log_level = sys.argv[4] if len(sys.argv) > 4 else "INFO"
    logging.basicConfig(level=log_level.upper())
    print(f"Running with satcat ID: {satcat_id}")
    sync_satcat_csv()
    ephemeris = load("de421.bsp")
    ts = load.timescale()

    # Get satcat entry
    satcat_entry = find_satcat_entry_by_id(satcat_id)

    # Show some basic information about the satellite
    if satcat_entry:
        print("Satcat entry found")
        debug(satcat_entry)
        satellite_name = satcat_entry["OBJECT_NAME"]
        satellite_launch_date = satcat_entry["LAUNCH_DATE"]
        satellite_decay_date = satcat_entry["DECAY_DATE"]
        print(f"Name: {satellite_name}")
        print(f"Launch Date: {satellite_launch_date}")
        if satellite_decay_date:
            print(
                f"Satellite will decay/decayed out of orbit on {satellite_decay_date}"
            )

    # Init the satellite
    omm_csv = get_celestrak_data_by_satcat_id(satcat_id, "CSV")
    sat = create_sat_entity_from_omm_csv(ts, omm_csv)

    passes = find_visible_passes(
        sat, wgs84.latlon(user_lat, user_lon), ephemeris, ts, 6
    )

    pprint(passes)


if __name__ == "__main__":
    main()
