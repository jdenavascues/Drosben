from __future__ import annotations

import datetime as dt
import pickle as pk
import uuid
import warnings

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

# Always force openpyxl for .xlsx
_XLS_ENGINE_READ = "openpyxl"
_XLS_ENGINE_WRITE = "xlsxwriter"


class InfodictValidationError(Exception):
    """Raised when infodict validation fails."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("\n".join(errors))


def _normalize_date(value: Any) -> str:
    """
    Accepts 'DD.MM.YYYY' string or Excel date-like values; returns 'DD.MM.YYYY' string.
    """
    if value is None or (isinstance(value, float) and np.isnan(value)):
        raise ValueError("init_date is missing")

    if isinstance(value, str):
        s = value.strip()
        # Already in DD.MM.YYYY
        try:
            dt.datetime.strptime(s, "%d.%m.%Y")
            return s
        except ValueError:
            pass
        # Try other common formats
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d"):
            try:
                d = dt.datetime.strptime(s, fmt)
                return d.strftime("%d.%m.%Y")
            except ValueError:
                continue
        raise ValueError(f"init_date not in a recognized format: {value!r}")

    # Excel dates may come as Timestamp/Datetime/date etc.
    if hasattr(value, "strftime"):
        return value.strftime("%d.%m.%Y")

    raise ValueError(f"Unsupported date type: {type(value)}")


def _parse_bool(value: Any, default: bool = False) -> bool:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return default
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    return s in ("true", "1", "yes", "y")


def _pad_levels(levels: list[str], target: int = 4) -> list[str]:
    # ensure length exactly 'target', fill with "".
    lv = [x if x is not None else "" for x in levels]
    if len(lv) < target:
        lv += [""] * (target - len(lv))
    else:
        lv = lv[:target]
    return lv


def _load_colour_data_from_pkl(pkl_path: Path) -> dict[str, Any] | None:
    if pkl_path.is_file():
        with pkl_path.open("rb") as f:
            data = pk.load(f)
        # Expect the structure like:
        # {'dead': {'HSinterval': [[loH,hiH],[loS,hiS]], 'RGB': [r,g,b]}, 'censored': {...}, 'carried-over': {...}, 'otsuT': int}
        return data
    return None


def infodict_from_excel(
    xlsx_path: str | Path,
    *,
    strict: bool = False,
    return_errors: bool = True,
) -> tuple[dict[str, Any] | None, list[str], list[str]]:
    """
    Build infodict from Excel, collecting *all* problems first.

    Returns:
        (infodict | None, error_notes)

    Behavior:
        - If strict=True and there are errors -> raises InfodictValidationError
        - If strict=False and there are errors -> returns (None, warnings, errors)
        - If no errors -> returns (infodict, warnings, [])

    Notes:
        - Suppresses openpyxl UserWarning about data validation internally.
        - Accepts blank cells where appropriate; does not require zeros.
        - A lot of the tests are redundant with the Excel template provided.
    """
    xlsx_path = Path(xlsx_path)
    if not xlsx_path.is_file():
        raise FileNotFoundError(f"Excel file not found: {xlsx_path}")

    # Read sheets (suppress noisy data-validation warnings)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        try:
            meta_df = pd.read_excel(xlsx_path, sheet_name="Metadata", engine="openpyxl")
            vars_df = pd.read_excel(xlsx_path, sheet_name="Variables", engine="openpyxl")
            racks_df = pd.read_excel(xlsx_path, sheet_name="Racks", engine="openpyxl")
            calib_df = pd.read_excel(xlsx_path, sheet_name="Calibration", engine="openpyxl")
        except Exception as e:
            raise InfodictValidationError([f"File {xlsx_path.name} is not valid: {e}"])

    error_notes: list[str] = []

    # --- Metadata (catching for later) ----------------------------------------
    if not {"key", "value"}.issubset(set(meta_df.columns)):
        error_notes.append("Sheet 'Metadata' must have columns: key, value")
        # continue to try collecting everything else

    # normalize keys to lowercase, strip
    if {"key", "value"}.issubset(set(meta_df.columns)):
        meta_map = {str(k).strip().lower(): v for k, v in meta_df[["key", "value"]].values}
    else:
        meta_map = {}

    # experiment's name
    exp_name = str(meta_map.get("exp_name", "")).strip()
    if not exp_name:
        error_notes.append("'exp_name' (Metadata) is required.")

    # initial date
    raw_date = meta_map.get("init_date")
    try:
        init_date = _normalize_date(raw_date)
    except Exception as e:
        init_date = None
        error_notes.append(f"init_date problem: {e}")

    # anon not very important so any 'problems' turned into False
    anon = _parse_bool(meta_map.get("anon"), default=False)
    TFoptions = ['true', 'false', '0', '1', 'yes', 'no', 'y', 'n']
    if str(meta_map.get("anon")).strip().lower() not in TFoptions:
        error_notes.append(f"'anon' must be either True or False")
        
    # max_expec_lfspn
    max_expec_lfspn = meta_map.get("max_expec_lfspn", 200)
    try:
        max_expec_lfspn = int(max_expec_lfspn)
        if max_expec_lfspn <= 0:
            raise ValueError
    except Exception:
        error_notes.append("'max_expec_lfspn' must be a positive integer.")

    # fpw
    fpw = meta_map.get("fpw", 3)
    try:
        fpw = int(fpw)
        if not (1 <= fpw <= 7):
            raise ValueError
    except Exception:
        error_notes.append("'fpw' must be an integer in [1..7].")

    xid = str(meta_map.get("xid", "")).strip() or uuid.uuid4().urn.split(":")[2]

    # --- Variables (catching for later) ---------------------------------------
    need_var_cols = {"var", "name", "lvl1", "lvl2", "lvl3", "lvl4"}
    if not need_var_cols.issubset(set(vars_df.columns)):
        error_notes.append("Sheet 'Variables' must have columns: var, name, lvl1, lvl2, lvl3, lvl4")
        # default to empties so the rest of the checks don’t explode
        var1_name = ""
        var2_name = ""
        var1_lvls = ["", "", "", ""]
        var2_lvls = ["", "", "", ""]
    else:
        # Make 'var' case-insensitive
        rowmap = {str(row["var"]).strip().lower(): row for _, row in vars_df.iterrows()}
        if not {"var1", "var2"}.issubset(rowmap.keys()):
            error_notes.append("Sheet 'Variables' must contain rows for var1 and var2.")
            var1_name = ""
            var2_name = ""
            var1_lvls = ["", "", "", ""]
            var2_lvls = ["", "", "", ""]
        else:
            var1_row = rowmap["var1"]
            var2_row = rowmap["var2"]
            var1_name = str(var1_row["name"]).strip() if not pd.isna(var1_row["name"]) else ""
            var2_name = str(var2_row["name"]).strip() if not pd.isna(var2_row["name"]) else ""
            var1_lvls = _pad_levels(
                [var1_row["lvl1"], var1_row["lvl2"], var1_row["lvl3"], var1_row["lvl4"]]
            )
            var2_lvls = _pad_levels(
                [var2_row["lvl1"], var2_row["lvl2"], var2_row["lvl3"], var2_row["lvl4"]]
            )

    var1_level_set = {x for x in var1_lvls if x}
    var2_level_set = {x for x in var2_lvls if x}

    # --- Racks (NA-friendly & lenient) ---------------------------------------
    need_rack_cols = {"rack_no", "tube_no", "var1_level", "var2_level", "fly_no"}
    if not need_rack_cols.issubset(set(racks_df.columns)):
        error_notes.append(
            "Sheet 'Racks' must have columns: rack_no, tube_no, var1_level, var2_level, fly_no"
        )
        racks_list: list[dict[str, Any]] = []
    else:
        # Clean empty rows & missing identities
        racks_df = racks_df.dropna(how="all")
        racks_df = racks_df.dropna(subset=["rack_no", "tube_no"], how="any")
        # Safe int cast
        try:
            racks_df["rack_no"] = racks_df["rack_no"].astype(int)
            racks_df["tube_no"] = racks_df["tube_no"].astype(int)
        except Exception as e:
            error_notes.append(f"rack_no and tube_no must be integers. Underlying error: {e}")
            racks_df = racks_df[[]]  # force empty so loop is skipped

        racks_list: list[dict[str, Any]] = []
        for rack_no in sorted(racks_df["rack_no"].unique()):
            rack_rows = racks_df[racks_df["rack_no"] == rack_no].copy()
            tubes = rack_rows["tube_no"].tolist()
            bad_tubes = [t for t in tubes if t not in range(1, 13)]
            if bad_tubes:
                error_notes.append(
                    f"Rack {rack_no}: tube_no must be in 1..12. Found: {sorted(bad_tubes)}"
                )
            if len(rack_rows) < 12:
                print(f"Rack {rack_no}: only {len(rack_rows)} tube rows found. ",
                      "Remaining tubes will be considered empty.",
                      flush=True)

            var1_arr = np.empty(12, dtype=object)
            var1_arr[:] = None
            var2_arr = np.empty(12, dtype=object)
            var2_arr[:] = None
            fly_arr = np.empty(12, dtype=object)
            fly_arr[:] = None

            def _idx(level_name: str, defined_levels: list[str]) -> int | None:
                if not isinstance(level_name, str) or not level_name.strip():
                    return None
                return defined_levels.index(level_name) if level_name in defined_levels else None

            for _, row in rack_rows.iterrows():
                tix = int(row["tube_no"]) - 1
                if tix < 0 or tix > 11:
                    # already recorded above, just skip
                    continue

                v1 = (
                    ""
                    if pd.isna(row.get("var1_level", ""))
                    else str(row.get("var1_level", "")).strip()
                )
                v2 = (
                    ""
                    if pd.isna(row.get("var2_level", ""))
                    else str(row.get("var2_level", "")).strip()
                )
                fn = row.get("fly_no", "")

                if v1 and v1 not in var1_level_set:
                    error_notes.append(
                        f"Rack {rack_no} tube {tix+1}: var1_level '{v1}' not in {var1_lvls}"
                    )
                if v2 and v2 not in var2_level_set:
                    error_notes.append(
                        f"Rack {rack_no} tube {tix+1}: var2_level '{v2}' not in {var2_lvls}"
                    )

                var1_arr[tix] = _idx(v1, var1_lvls)
                var2_arr[tix] = _idx(v2, var2_lvls)

                if pd.isna(fn) or fn == "":
                    fly_arr[tix] = None
                else:
                    try:
                        fn_int = int(fn)
                        if fn_int <= 0:
                            raise ValueError
                        fly_arr[tix] = fn_int
                    except Exception:
                        error_notes.append(
                            f"Rack {rack_no} tube {tix+1}: fly_no must be a positive integer or blank."
                        )

            racks_list.append(
                {
                    "rack_no": rack_no,
                    "var1": var1_arr,
                    "var2": var2_arr,
                    "fly_no": fly_arr,
                }
            )
    racks_list = [rack for rack in racks_list if any(rack['fly_no'])]    

    # --- Calibration -----------------------------------------------
    colour_data: dict[str, Any] = {}
    if calib_df is not None and {"key", "value"}.issubset(calib_df.columns):
        c_map = {str(k).strip().lower(): v for k, v in calib_df[["key", "value"]].values}
        pkl_path = c_map.get("colour_config_pkl", "")
        if isinstance(pkl_path, str) and pkl_path.strip():
            pkl_path = pkl_path.strip()
            # quick'n'dirty patch for the sample xlsx file (pkl_path is the file name)
            if not Path(pkl_path).is_file():
                pkl_path = str(xlsx_path.parent / pkl_path)
            # continue
            cd = _load_colour_data_from_pkl(Path(pkl_path))
            if cd is None:
                error_notes.append(
                    f"Calibration file not found or unreadable: {pkl_path!r}."
                )
            else:
                colour_data = cd

    # --- assemble & finalise --------------------------------------------------
    # If there are errors and strict=True, raise once with all of them
    if error_notes and strict:
        raise InfodictValidationError(error_notes)

    # If there are errors but we're lenient, return None + the issues
    if error_notes:
        return None, error_notes

    # No errors -> build the infodict
    infodict: dict[str, Any] = {
        "exp_name": exp_name,
        "colour_data": colour_data,
        "var1": {"var1_name": var1_name, "var1_lvls": var1_lvls},
        "var2": {"var2_name": var2_name, "var2_lvls": var2_lvls},
        "init_date": init_date,  # guaranteed set when no errors
        "racks": racks_list,  # possibly with Nones for empty tubes
        "xid": xid,
        "max_expec_lfspn": max_expec_lfspn,
        "fpw": fpw,
        "anon": anon,
    }

    return infodict, error_notes
