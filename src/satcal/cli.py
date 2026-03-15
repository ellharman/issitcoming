import argparse
import csv
import json
import logging
import os
import sys
from datetime import datetime
from io import StringIO

import requests
from skyfield.api import EarthSatellite, load, wgs84

logger = logging.getLogger(__name__)


def _cache_dir() -> str:
    """
    Return the base cache directory for satcal, creating it if necessary.
    """
    # Follow XDG-style cache convention, e.g. ~/.cache/satcal
    base_cache = os.environ.get(
        "SATCAL_CACHE_DIR",
        os.environ.get(
            "XDG_CACHE_HOME", os.path.join(os.path.expanduser("~"), ".cache")
        ),
    )
    cache_dir = os.path.join(base_cache, "satcal", "satcat")
    os.makedirs(cache_dir, exist_ok=True)
    return cache_dir


def sync_satcat_csv() -> None:
    satcat_path = os.path.join(_cache_dir(), "satcat.csv")

    def pull_and_save_csv() -> None:
        res = requests.get("https://celestrak.org/pub/satcat.csv")
        res.raise_for_status()
        with open(satcat_path, "w") as file:
            file.write(res.text)

    if not os.path.exists(satcat_path):
        logger.info("Downloading SATCAT catalog from Celestrak…")
        pull_and_save_csv()
    else:
        # Sync the latest satcat csv from Celestrak if file is older than 1 day
        current_time = datetime.now()
        last_modified = datetime.fromtimestamp(os.path.getmtime(satcat_path))
        diff = current_time - last_modified
        force_sync = int(os.environ.get("FORCE_SYNC_SATCAT", 0)) == 1
        if force_sync:
            logger.warning(
                "FORCE_SYNC_SATCAT=1 is set; repeatedly forcing SATCAT downloads "
                "can thrash the Celestrak API and may result in HTTP 403 responses."
            )
        if diff.days > 0 or force_sync:
            logger.info("Refreshing SATCAT catalog from Celestrak…")
            pull_and_save_csv()


def find_satcat_entry_by_id(satcat_id: int) -> dict | None:
    satcat_path = os.path.join(_cache_dir(), "satcat.csv")
    with open(satcat_path, "r") as file:
        reader = csv.DictReader(file)
        for row in reader:
            if row.get("NORAD_CAT_ID") == str(satcat_id):
                return row
        logger.warning("No SATCAT entry found for NORAD catalog ID %s", satcat_id)
    return None


def get_celestrak_data_by_satcat_id(satcat_id: int, format: str = "TLE") -> str:
    """
    Allowed formats are:
    - TLE or 3LE: Three-line element sets including 24-character satellite name on Line 0.
    - 2LE: Two-line element sets (no satellite name on Line 0).
    - XML: CCSDS OMM XML format including all mandatory elements.
    - KVN: CCSDS OMM KVN format including all mandatory elements.
    - JSON: OMM keywords for all GP elements in JSON format.
    - JSON-PRETTY: OMM keywords for all GP elements in JSON pretty-debug format.
    - CSV: OMM keywords for all GP elements in CSV format.
    """
    # Cache under the user's cache directory, with backwards-compatible fallback.
    base_cache = os.environ.get(
        "SATCAL_CACHE_DIR",
        os.environ.get(
            "XDG_CACHE_HOME", os.path.join(os.path.expanduser("~"), ".cache")
        ),
    )
    cache_dir = os.path.join(base_cache, "satcal", "celestrak")
    os.makedirs(cache_dir, exist_ok=True)
    cache_key = f"{satcat_id}_{format.upper()}"
    cache_path = os.path.join(cache_dir, f"celestrak_{cache_key}.json")

    now = datetime.now().timestamp()
    max_age_seconds = 6 * 60 * 60  # 6 hours

    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r") as f:
                cached = json.load(f)
            fetched_at = float(cached.get("fetched_at", 0))
            if now - fetched_at <= max_age_seconds:
                return str(cached.get("data", ""))
        except Exception:
            # Ignore cache errors and fall back to network
            pass

    url = f"https://celestrak.org/NORAD/elements/gp.php?CATNR={satcat_id}&FORMAT={format.upper()}"
    response = requests.get(url)
    response.raise_for_status()
    text = response.text

    try:
        with open(cache_path, "w") as f:
            json.dump({"fetched_at": now, "data": text}, f)
    except Exception:
        # If we can't write the cache, still return the live data
        pass

    return text


def create_sat_entity_from_omm_csv(ts, csv_string: str) -> EarthSatellite:
    f = StringIO(csv_string)
    data = csv.DictReader(f)
    sat = [EarthSatellite.from_omm(ts, fields) for fields in data][0]
    return sat


def find_visible_passes(
    sat: EarthSatellite, location, eph, ts, hours_ahead: int = 6
) -> list[dict]:
    t0 = ts.now()
    t1 = ts.now() + (hours_ahead / 24)

    # Find rise/culmination/set events above 20°
    t, events = sat.find_events(location, t0, t1, altitude_degrees=20)
    # events: 0 = rise, 1 = culmination (max elevation), 2 = set

    event_labels = ["rise", "peak", "set"]
    passes: list[dict] = []
    current_pass: dict = {}

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
            "alt": float(alt.degrees),
            "az": float(az.degrees),
            "visible": bool(visible),
        }

        if name == "set":
            passes.append(current_pass)
            current_pass = {}

    return passes


def run(
    satcat_id: int,
    user_lat: float,
    user_lon: float,
    hours_ahead: int,
    *,
    verbose: bool = False,
    debug_logs: bool = False,
    json_output: bool = False,
    plain_output: bool = False,
    disable_color: bool = False,
) -> list[dict]:
    """
    Core execution for satcal.

    Returns the list of pass dictionaries so that callers (including tests)
    can inspect the structured result independently of CLI formatting.
    """
    log_level = (
        logging.DEBUG
        if (verbose or debug_logs or os.environ.get("SATCAL_DEBUG"))
        else logging.INFO
    )
    logging.basicConfig(
        level=log_level,
        format="%(levelname)s:%(name)s:%(message)s",
        force=True,
    )
    logger.debug("Running with satcat ID: %s", satcat_id)

    sync_satcat_csv()
    ephemeris = load("de421.bsp")
    ts = load.timescale()

    # Get satcat entry
    satcat_entry = find_satcat_entry_by_id(satcat_id)

    # Show some basic information about the satellite
    if satcat_entry:
        logger.debug("Satcat entry found")
        logger.debug("%s", satcat_entry)
        satellite_name = satcat_entry["OBJECT_NAME"]
        satellite_launch_date = satcat_entry["LAUNCH_DATE"]
        satellite_decay_date = satcat_entry["DECAY_DATE"]
        logger.debug("Name: %s", satellite_name)
        logger.debug("Launch Date: %s", satellite_launch_date)
        if satellite_decay_date:
            logger.debug(
                "Satellite will decay/decayed out of orbit on %s", satellite_decay_date
            )

    # Init the satellite
    omm_csv = get_celestrak_data_by_satcat_id(satcat_id, "CSV")
    sat = create_sat_entity_from_omm_csv(ts, omm_csv)

    passes = find_visible_passes(
        sat, wgs84.latlon(user_lat, user_lon), ephemeris, ts, hours_ahead
    )

    # When used as a library, callers can ignore CLI formatting and
    # use the returned data directly.
    if json_output:
        json.dump(passes, fp=sys.stdout)
        print()
    else:
        _print_human_readable_passes(
            passes,
            plain=plain_output,
            disable_color=disable_color,
        )

    return passes


def _use_color(*, disable_color: bool = False) -> bool:
    """
    Return True if colored output should be used for human-readable formatting.
    """
    if disable_color:
        return False
    if os.environ.get("NO_COLOR") or os.environ.get("SATCAL_NO_COLOR"):
        return False
    if not sys.stdout.isatty():
        return False
    term = os.environ.get("TERM", "")
    if term == "dumb":
        return False
    return True


def _print_human_readable_passes(
    passes: list[dict],
    *,
    plain: bool = False,
    disable_color: bool = False,
) -> None:
    """
    Print a human-readable summary of passes to stdout.

    - Default mode: multi-line, more descriptive layout.
    - Plain mode: one line per pass, tab-separated, easy to pipe to tools.
    """
    if not passes:
        print("No visible passes found in the requested window.")
        return

    if plain:
        # One line per pass: rise_time peak_time set_time peak_alt visible_any
        writer = csv.writer(sys.stdout, delimiter="\t")
        writer.writerow(
            ["rise_time", "peak_time", "set_time", "peak_alt_deg", "any_visible"]
        )
        for p in passes:
            rise = p.get("rise", {})
            peak = p.get("peak", {})
            set_ = p.get("set", {})
            any_visible = any(
                moment.get("visible")
                for moment in (rise, peak, set_)
                if isinstance(moment, dict)
            )
            writer.writerow(
                [
                    rise.get("time", ""),
                    peak.get("time", ""),
                    set_.get("time", ""),
                    peak.get("alt", ""),
                    str(bool(any_visible)),
                ]
            )
        return

    use_color = _use_color(disable_color=disable_color)
    bold = "\033[1m" if use_color else ""
    reset = "\033[0m" if use_color else ""

    def _fmt_float(value: float | int | str | None) -> str:
        try:
            return f"{float(value):.2f}"
        except (TypeError, ValueError):
            return ""

    for idx, p in enumerate(passes, start=1):
        rise = p.get("rise", {})
        peak = p.get("peak", {})
        set_ = p.get("set", {})
        any_visible = any(
            moment.get("visible")
            for moment in (rise, peak, set_)
            if isinstance(moment, dict)
        )

        header = f"Pass {idx}"
        if any_visible:
            header += " (visible)"
        print(f"{bold}{header}{reset}")
        print(
            f"  rise: {rise.get('time', '')}  alt={_fmt_float(rise.get('alt'))}°"
            f"  az={_fmt_float(rise.get('az'))}°  visible={rise.get('visible', False)}"
        )
        print(
            f"  peak: {peak.get('time', '')}  alt={_fmt_float(peak.get('alt'))}°"
            f"  az={_fmt_float(peak.get('az'))}°  visible={peak.get('visible', False)}"
        )
        print(
            f"   set: {set_.get('time', '')}  alt={_fmt_float(set_.get('alt'))}°"
            f"  az={_fmt_float(set_.get('az'))}°  visible={set_.get('visible', False)}"
        )
        if idx != len(passes):
            print()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="satcal",
        description=(
            "Predict when a satellite in Earth orbit will be visible from your "
            "location in the next few hours."
            "By default, prints a human-readable summary; use --json or  for structured output."
        ),
        epilog=(
            "Examples:\n"
            "  satcal 25544 51.501669 -0.141006 6\n"
            "  satcal 25544 51.501669 -0.141006 6 --json | jq '.'\n"
        ),
    )
    parser.add_argument("satcat_id", type=int, help="NORAD catalog ID of the satellite")
    parser.add_argument(
        "latitude", type=float, help="Observer latitude in decimal degrees"
    )
    parser.add_argument(
        "longitude", type=float, help="Observer longitude in decimal degrees"
    )
    parser.add_argument(
        "hours_ahead",
        type=int,
        help="How many hours ahead to search for visible passes",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON array of passes to stdout.",
    )
    parser.add_argument(
        "--plain",
        action="store_true",
        help=(
            "Plain, tab-separated summary (one line per pass) for easy piping to grep/awk."
        ),
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable colored output (also respected if NO_COLOR or SATCAL_NO_COLOR is set).",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose logging (debug output).",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging; equivalent to a more detailed verbose mode.",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="Print the satcal version and exit.",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    if argv is None:
        argv = sys.argv[1:]

    # Handle --version before full argument parsing so it does not require
    # positional arguments, following common CLI conventions.
    if "--version" in argv:
        try:
            from importlib.metadata import version, PackageNotFoundError  # type: ignore
        except Exception:  # pragma: no cover
            print("satcal (version information unavailable)")
            return
        try:
            print(f"satcal {version('satcal')}")
        except PackageNotFoundError:
            print("satcal (not installed as a package)")
        return

    parser = build_arg_parser()
    args = parser.parse_args(argv)

    try:
        run(
            satcat_id=args.satcat_id,
            user_lat=args.latitude,
            user_lon=args.longitude,
            hours_ahead=args.hours_ahead,
            verbose=args.verbose,
            debug_logs=getattr(args, "debug", False),
            json_output=getattr(args, "json", False),
            plain_output=getattr(args, "plain", False),
            disable_color=getattr(args, "no_color", False),
        )
    except FileNotFoundError as exc:
        logger.error(
            "Required data file was not found: %s. "
            "Try re-running with FORCE_SYNC_SATCAT=1 or check your cache directory.",
            exc,
        )
        raise SystemExit(3)
    except requests.exceptions.RequestException as exc:
        logger.error(
            "Network error while contacting Celestrak or downloading ephemeris data: %s",
            exc,
        )
        raise SystemExit(4)
    except Exception as exc:  # pragma: no cover
        if (
            args.verbose
            or getattr(args, "debug", False)
            or os.environ.get("SATCAL_DEBUG")
        ):
            # Let Python print a full traceback in verbose/debug modes.
            raise
        logger.error(
            "Unexpected error: %s. Re-run with --debug or SATCAL_DEBUG=1 for details.",
            exc,
        )
        raise SystemExit(1)
