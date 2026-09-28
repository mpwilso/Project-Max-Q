# adapters/ — one file per ATS

`sweep.py` loads every `adapters/<name>.py` at import (`load_plugins()`). The original built-in
adapters live in `sweep.py`; every new board type goes here.

## Contract

```python
NAME = "icims_jibe"          # must equal the file stem, and is the "ats" value in targets.json
ENUMERABLE = True            # True only if list_jobs returns the WHOLE board (absence = closed)
REQUIRED = ["base"]          # keys the targets.json entry must carry

def list_jobs(t, h, smoke=False):
    """Return [h.norm(company, NAME, id, title, location, url, posted, description, comp, extra)]."""

def detail(t, row, h):       # optional; omit when the list read already carries the JD text
    """Fill row["description"], and posted/location/comp where the list lacked them. Return row."""

def lookup(t, jid, h):       # optional, for ENUMERABLE = False boards
    """One req by id: the full h.norm(...) row if open, None if closed, raise if the lookup failed.
    Tracked reqs a sampled read did not return are checked this way instead of vanishing."""
```

`h` is `sweep.plugin_helpers()`: `get`, `post_json`, `get_text`, `request` (all share the retry
policy: 429/5xx and dropped connections), `norm`, `strip_html`, `iso_date`, `days_since`,
`money_range`, `comp_from_description`, `quote`, `requests`, `UA`, `DELAY`, `TODAY`.

Rules every adapter follows (each one guards a failure mode seen on a real board):
- **Silent zero is an error.** A tenant that answers 200 with no rows when it should have rows
  raises, it does not return `[]` quietly (Meta doc_id rotation, HubSpot/Twilio empty boards).
- **Page caps lie.** Paginate on what actually came back, never on the limit you asked for
  (Eightfold 10-row cap, Oracle 200-row cap echoed as 500).
- **`smoke=True` reads one page only.**
- **Locations joined with ` | `.** Add a bare `Remote` segment when the board flags remote.
  The location gate is fail-closed and needs a US marker.
- **Dates go through `h.iso_date`.** Store a refresh date as `extra["refreshed"]` with
  `extra["date_kind"] = "refreshed"`, never as `posted`.
- **Ids are the board's stable job id**, not a URL slug that changes on retitle.
- **No state.** Adapters never write files.

## Probe before adding to targets.json

```
python adapters/probe.py <name> '{"company":"X","ats":"<name>", ...}' --detail 3
```

On Windows PowerShell, where a JSON argument gets split at spaces, put the entry in a file and use
`--target-file entry.json`, or probe an entry already in targets.json with `--company "Name"`.
A small existing adapter such as `adapters/teamtailor.py`, with `tests/adapters/test_teamtailor.py`,
is a good one to copy.

Then add a fixture-backed test under `tests/adapters/test_<name>.py` (saved JSON/HTML in
`tests/fixtures/<name>/`, HTTP replaced by a stub `h`), so the adapter is checked offline by
`python -m unittest discover -s tests -t .`.
