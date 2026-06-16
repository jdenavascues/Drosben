from pathlib import Path

USERDATA = Path.home() / "Drosben_userdata"
COLOUR_CONFIG_DIR = USERDATA / "colour_configurations"
EXPERIMENTS_DIR = USERDATA / "experiments_data"

for p in (USERDATA, COLOUR_CONFIG_DIR, EXPERIMENTS_DIR):
    p.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Centralised path helpers
# ---------------------------------------------------------------------------


def experiment_rel_dirname(infodict) -> str:
    """Return <DD.MM.YYYY>_<exp-name-with-dashes>_<xidprefix>."""
    date = infodict["init_date"].replace("/", "")
    exp = infodict["exp_name"].replace(" ", "-")
    xid_prefix = infodict["xid"].split("-")[0]
    return f"{date}_{exp}_{xid_prefix}"


def experiment_dir_from_infodict(infodict):
    """Absolute experiment folder."""
    return EXPERIMENTS_DIR / experiment_rel_dirname(infodict)


def processed_datasheets_dir(storepath):
    return Path(storepath) / "processed_datasheets"


def datasheet_pdf_path(storepath, xid_prefix: str, rack: int, anon: bool):
    if anon:
        return Path(storepath) / f"{xid_prefix}_DataSheet_anon_rack{rack}.pdf"
    return Path(storepath) / f"{xid_prefix}_DataSheet_rack{rack}.pdf"


def labels_pdf_path(storepath, prefix: str, start: int, end: int):
    return Path(storepath) / f"{prefix}_RackLabels_page{start}-{end}.pdf"


def infodict_xlsx_path(storepath, xid_prefix: str):
    return Path(storepath) / f"{xid_prefix}_infodict.xlsx"


def infodict_pkl_path(storepath, xid_prefix: str):
    return Path(storepath) / f"{xid_prefix}_infodict.pkl"


def eventseq_xlsx_path(storepath, xid_prefix: str):
    return Path(storepath) / f"{xid_prefix}_eventseq.xlsx"


def eventseq_pkl_path(storepath, xid_prefix: str):
    return Path(storepath) / f"{xid_prefix}_eventseq.pkl"


def observations_xlsx_path(storepath, xid_prefix: str):
    return Path(storepath) / f"{xid_prefix}_observations.xlsx"


def stats_reports_dir(storepath):
    return Path(storepath) / "stats_reports"


def raw_image_filename(rack_no: int, page_no: int, tag: str = "raw") -> str:
    return f"DataSheet_r{rack_no}_p{page_no}_{tag}.jpg"


def raw_image_path(storepath, rack_no: int, page_no: int, tag: str = "raw"):
    return processed_datasheets_dir(storepath) / raw_image_filename(rack_no, page_no, tag)
