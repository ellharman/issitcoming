## satcal

satcal is a small CLI tool for predicting when a given Earth–orbiting satellite will be visible from your location in the next few hours. It pulls orbital data from Celestrak and uses the [Skyfield](https://pypi.org/project/skyfield/) library to compute passes that are both above 20° elevation and actually visible (satellite sunlit, observer in darkness).

### Installation

- **Prerequisites**: Python 3.10+

```bash
pip install satcal
```

or with `uv`:

```bash
uv tool install satcal
```

This installs `satcal` to your path.

### Usage

Once installed, use the `satcal` CLI:

```bash
satcal <satcat_id> <latitude> <longitude> <hours_ahead> [options]
```

- **satcat_id**: NORAD catalog ID (integer) of the satellite.
- **latitude / longitude**: Observer location in decimal degrees.
- **hours_ahead**: How many hours ahead of the current time to search for passes.

Common options:

- **-v / --verbose**: Enable more detailed logging to stderr.
- **--debug**: Developer-focused debug logging (or set `SATCAL_DEBUG=1`).
- **--json**: Emit a machine-readable JSON array of passes to stdout.
- **--plain**: Emit a plain, tab-separated summary (one line per pass) to stdout.
- **--no-color**: Disable colored/styled output (also respected if `NO_COLOR` or `SATCAL_NO_COLOR` is set).
- **--version**: Print the installed `satcal` version and exit.

Example (International Space Station over central London, looking 6 hours ahead):

```bash
satcal 25544 51.501669 -0.141006 6
```

You can also stream structured output to tools like `jq`:

```bash
satcal 25544 51.501669 -0.141006 6 --json | jq '.'
```

The script will:

- **Sync SATCAT data** from Celestrak into `satcat.csv` (re-downloaded if older than 1 day, or if `FORCE_SYNC_SATCAT=1` is set in the environment).
- Print basic satellite info (name, launch date, and decay date if present) if running verbosely
- **Compute visible passes** using Skyfield and pretty-print a list of passes, each containing:
  - rise / peak / set times (UTC, ISO format)
  - elevation and azimuth in degrees
  - a `visible` flag indicating whether the pass is actually observable (sat sunlit, observer in darkness).

#### Output

By default, `satcal` prints a human-readable summary of each visible pass, e.g.

```text
Pass 1 (visible)
  rise: 2026-03-14T19:35:36Z  alt=20.00°  az=220.04°  visible=True
  peak: 2026-03-14T19:37:37Z  alt=44.44°  az=155.69°  visible=True
   set: 2026-03-14T19:39:37Z  alt=20.00°  az=91.46°   visible=False
```

- One **“Pass N”** block per visible-altitude pass in the requested window.
- Each block contains rise, peak, and set moments with time, altitude, azimuth, and a `visible` flag.

When `--json` is passed, the result is printed as a single JSON array on stdout, e.g.

```json
[
  {
    "rise": {
      "time": "2026-03-14T19:35:36Z",
      "alt": 20.004231034654826,
      "az": 220.04202056913311,
      "visible": true
    },
    "peak": {
      "time": "2026-03-14T19:37:37Z",
      "alt": 44.435153007618425,
      "az": 155.68658191565754,
      "visible": true
    },
    "set": {
      "time": "2026-03-14T19:39:37Z",
      "alt": 19.997122770793396,
      "az": 91.46019198197804,
      "visible": false
    }
  }
]
```

- The outer array is one element per visible‑altitude pass found in the requested window.
- Within each pass object:
  - `time` _(string)_: UTC timestamp in ISO 8601 format, e.g. `"2026-03-14T19:37:37Z"`.
  - `alt` _(number)_: altitude in degrees.
  - `az` _(number)_: azimuth in degrees.
  - `visible` _(boolean)_: whether the satellite is sunlit and the observer is in darkness at that moment.

When `--plain` is passed, output is a tab-separated table, one line per pass:

```text
rise_time\tpeak_time\tset_time\tpeak_alt_deg\tany_visible
2026-03-14T19:35:36Z\t2026-03-14T19:37:37Z\t2026-03-14T19:39:37Z\t44.44\tTrue
```

- `rise_time`, `peak_time`, `set_time`: ISO 8601 UTC timestamps.
- `peak_alt_deg`: peak altitude in degrees.
- `any_visible`: `True` if any of rise/peak/set is visible.

### Notes

- Run with `FORCE_SYNC_SATCAT=1` or `satcal ...` after a day has passed to refresh SATCAT.
- Cache files are stored under:
  - SATCAT CSV: `$SATCAL_CACHE_DIR` or `$XDG_CACHE_HOME`/`~/.cache` + `/satcal/satcat/satcat.csv`
  - Celestrak GP data: `$SATCAL_CACHE_DIR` or `$XDG_CACHE_HOME`/`~/.cache` + `/satcal/celestrak/`
- Use `NO_COLOR=1` or `SATCAL_NO_COLOR=1` to fully disable colored output.
