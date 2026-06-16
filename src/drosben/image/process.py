import io
import pickle as pk
from ast import literal_eval
from colorsys import rgb_to_hsv
from importlib.resources import files
from itertools import islice
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pyqrcode
from math import dist
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.collections import PatchCollection
from matplotlib.lines import Line2D
from matplotlib.patches import Circle, Rectangle
from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from scipy.ndimage import (
    binary_dilation,
    binary_opening,
    binary_erosion,
    distance_transform_edt,
    binary_fill_holes,
)
from skimage.filters import (
    median,
    threshold_otsu,
    threshold_multiotsu,
    sobel,
)
from skimage.feature import peak_local_max
from skimage.io import imsave
from skimage.measure import label, regionprops
from skimage.morphology import disk
from skimage.segmentation import clear_border, watershed

from drosben import resources
from drosben.config import (
    EXPERIMENTS_DIR,
    datasheet_pdf_path,
    eventseq_pkl_path,
    eventseq_xlsx_path,
    infodict_pkl_path,
    infodict_xlsx_path,
    labels_pdf_path,
    observations_xlsx_path,
    processed_datasheets_dir,
    raw_image_filename,
)
from drosben.image.utils import (
    BoxDetectionError,
    DateDetectionError,
    InputAreaDetectionError,
    QRcodeError0,
    QRcodeError2,
    DSError2,
    SubAreaDetectionError,
    TubesDetectionError,
    UBoxDetectionError1,
    UBoxDetectionError2,
    bwareaopen,
    gray_kde_thresholding,
    grid_label,
    iswithinpercent,
    ma_mean,
    normalize_img,
    read_scan,
    pad_rgb,
    selem_frac,
    slice_bbox,
    split_n,
    straighten_datasheet,
    tupleintor,
    order_points_clockwise,
    to_gray,
    decode_qr,
    avg_qr_side,
    zeroed_border,
    crop_datasheet_from_qr,
)


def generate_fake_infodict(anon=True, varno=2):
    
    # experiment name for the user
    exp_name = 'Effect on treatment T on genotype G'

    # colour intervals in HS(V) space and otsu threshold to segment marks
    # RGB reference marker pen colours
    colour_data = {
        'dead': {
            'HSinterval': [[6.212, 0.4068], [0.1416, 0.9379]],
            'RGB': [0.8086, 0.4426, 0.3733]},
        'censored': {
            'HSinterval': [[3.4268, 4.7487], [0.0, 1.0677]],
            'RGB': [0.3271, 0.3514, 0.5746]},
        'carried-over': {
            'HSinterval': [[0.5616, 2.5051], [0.0, 0.8138]],
            'RGB': [0.5529, 0.6567, 0.435]},
        'otsuT': 135}

    # experimental arms/strata
    # variable 1
    var1 = {'var1_name': 'genotype',
            # different var1 values will have indices 0-3
            'var1_lvls': ['wild-type', 'G[-]', '', '']}
    # variable 2
    var2 = {'var2_name': 'treatment',
            # different var2 values will have indices 0-3
            'var2_lvls': ['control', 'T1', 'T2', '']}

    # start date
    init_date = 'dd.mm.yyyy'

    # distribution of the experimental arms within physical racks
    racks = [
        {'rack_no': 1,
         # tubes 1,2,3,5,6,7,9,10,11 contain wild-type (index 0)
         'var1': np.array([0, 0, 0, None, 0, 0, 0, None, 0, 0, 0, None],
                          dtype=object),
         # tubes 1,2,3 contain control treatment (0), T1 (1) or T2 (2)
         'var2': np.array([0, 0, 0, None, 1, 1, 1, None, 2, 2, 2, None],
                          dtype=object),
         # all tubes with flies in have 10 flies each
         'fly_no': np.array([10, 10, 10, None, 10, 10, 10, None, 10, 10, 10, None],
                            dtype=object)},

        # same but with G[-] mutant
        {'rack_no': 2,
         'var1': np.array([1, 1, 1, None, 1, 1, 1, None, 1, 1, 1, None],
                          dtype=object),
         'var2': np.array([0, 0, 0, None, 1, 1, 1, None, 2, 2, 2, None],
                          dtype=object),
         'fly_no': np.array([10, 10, 10, None, 10, 10, 10, None, 10, 10, 10, None],
                            dtype=object)}
    ]
    
    # maximum expected lifespan to estimate how long the experiment will last
    max_expec_lfspn = 200
    
    # flips per week, to estimate how many rack templates for datasheets are needed
    fpw = 3

    # whether the user wants to have blind data recording
    anon = True

    # unique identifier (randomly generated)
    from uuid import uuid4
    xid = uuid4()
    xid = xid.urn.split(":")[2]

    # to provide flexibility
    if varno == 1:
        var2 = {"var2_name": "", "var2_lvls": ["", "", "", ""]}
        z = np.hstack(
            [
                [None, None, None, None],
                [None, None, None, None],
                [None, None, None, None],
            ]
        )
        for r in racks:
            r["var2"] = z

    # pack all in a dictionary and return
    keys = [
        "exp_name",
        "colour_data",
        "var1",
        "var2",
        "init_date",
        "racks",
        "xid",
        "max_expec_lfspn",
        "fpw",
        "anon",
        "colour_data",
    ]
    values = [
        exp_name,
        colour_data,
        var1,
        var2,
        init_date,
        racks,
        xid,
        max_expec_lfspn,
        fpw,
        anon,
        colour_data,
    ]
    infodict = dict(zip(keys, values))

    return infodict


def make_readable_infodict(infodict):
    # empty df to collect stuff
    infocols = ["Field", "Value", "Levels", "Sublevels", "Comments"]
    infodf = pd.DataFrame(columns=infocols)
    # break down infodict information in rows
    for key, val in infodict.items():
        if isinstance(val, dict):
            for subkey, subval in val.items():
                dat = [[key, subkey, subval, "N/A", "N/A"]]
                infodf = pd.concat(
                    [infodf if not infodf.empty else None, pd.DataFrame(dat, columns=infocols)],
                    ignore_index=True,
                )
        elif isinstance(val, list) and key == "racks":
            for item in val:
                for subkey, subval in item.items():
                    dat = [[key, subkey, list(item.items())[0][1], subval, "N/A"]]
                    infodf = pd.concat(
                        [infodf if not infodf.empty else None, pd.DataFrame(dat, columns=infocols)],
                        ignore_index=True,
                    )
        else:
            dat = [[key, val, "N/A", "N/A", "N/A"]]
            infodf = pd.concat(
                [infodf if not infodf.empty else None, pd.DataFrame(dat, columns=infocols)],
                ignore_index=True,
            )
    # text to be changed for readability
    replacements = {
        "exp_name": "Experiment name",
        "colour_data": "Colour HSV/RGB data",
        "var1": "Variable 1",
        "var2": "Variable 2",
        "var1_name": "Variable 1 Name",
        "var2_name": "Variable 2 Name",
        "var1_lvls": "Variable 1 Levels",
        "var2_lvls": "Variable 2 Levels",
        "rack_no": "Rack no.",
        "fly_no": "Number of flies",
        "init_data": "Initial date",
        "racks": "MultiFlipper rack",
        "xid": "Experiment ID",
        "max_expec_lfspn": "Maximum expected lifespan",
        "fpw": "Flips per week",
        "anon": "Anonymous DataSheet file created",
    }
    comments = {
        "colour_data": "Intervals of Hue and Saturation values ([0..1]) to classify an event in the filled-in DataSheet and point RGB values ([0..1]) to visualise correspondence between type of event and colour in the PDF DataSheet file for print",
        "init_date": "Day one of the measured lifespan - DD.MM.YYYY format",
        "racks": "Sublevels represent the 12 tubes of a rack, with numbers being either 0-based indices corresponding to the Levels of each Variable, or the number of flies per tube",
        "xid": "Randomly generated, guaranteed unique ID (UUID)",
        "max_expec_lfspn": "For the purposes of creating a DataSheet PDF file with enough pages. Default is 120.",
        "fpw": "Planned passaging/fly counting frequency for the purposes of creating a DataSheet PDF file with enough pages. Default is 3.",
    }
    # change text
    # first 'comments' as key strings will be changed with 'replacements'
    for key, comment in comments.items():
        for idx in infodf.index[infodf["Field"] == key]:
            infodf.loc[idx, "Comments"] = comment
    for abrev, expand in replacements.items():
        for idx in infodf.index[infodf["Field"] == abrev]:
            infodf.loc[idx, "Field"] = expand
        for idx in infodf.index[infodf["Value"] == abrev]:
            infodf.loc[idx, "Value"] = expand
    return infodf


def generate_rack_labels(infodict, storepath):
    xid = infodict["xid"][:5]
    prefix = infodict["xid"].split("-")[0]
    annotations = compute_rack_annotations(infodict)
    qrdata = pyqrcode.create(str(xid))
    # store binary info in list and convert to array to make image
    side = 270
    rep = np.ceil(side / len(qrdata.text().split("\n"))).astype(int)
    bin_qr = []
    for line in qrdata.text().split("\n")[3:-4]:
        bin_qr.append(np.array([int(c) for c in line[3:-3]]))
    bin_qr = [np.repeat(x, rep) for x in bin_qr]
    QR = []
    [QR.append(x) for x in bin_qr for r in range(rep)]
    QR = np.array(QR)
    QR = QR < 1
    from matplotlib import image as mplim

    QRpath = storepath / "QRcode.png"
    mplim.imsave(str(QRpath), QR, cmap="gray")

    # Modifying template file with QR code and metadata
    def insert_metadata_to_PDF(ix, racks):

        def get_rack_levels(rack):
            all_levels = []
            rack_var1_lvls = np.unique([x for x in rack["var1"] if x is not None]).tolist()
            all_levels.append([infodict["var1"]["var1_lvls"][x] for x in rack_var1_lvls])
            rack_var2_lvls = np.unique([x for x in rack["var2"] if x is not None])
            all_levels.append([infodict["var2"]["var2_lvls"][x] for x in rack_var2_lvls])
            all_levels = [x for x in all_levels if len(x) > 0][0]
            return all_levels

        # left side
        # (0,0)
        def draw00(rack):
            can.drawImage(QRpath, 65, 540, width=70, height=70, mask="auto")
            can.setFont("Helvetica-Bold", 14)
            can.drawCentredString(213, 500, "Rack no. " + str(rack["rack_no"]))
            can.drawCentredString(100, 520, "Rack no. " + str(rack["rack_no"]))
            can.setFont("Helvetica", 14)
            can.drawCentredString(213, 610, infodict["init_date"])
            can.drawCentredString(213, 590, "ID: " + infodict["xid"][:5])
            can.drawCentredString(213, 570, "'" + infodict["exp_name"][:10] + "...'")
            arm_text = annotations.get(rack["rack_no"], {}).get("label_text", "")
            for ix, L in enumerate(arm_text.split(" | ")):
                can.setFont("Helvetica-Oblique", 11)
                can.drawCentredString(213, 555 - (ix * 15), L)

        # (1,0)
        def draw10(rack):
            can.drawImage(QRpath, 65, 340, width=70, height=70, mask="auto")
            can.setFont("Helvetica-Bold", 14)
            can.drawCentredString(213, 300, "Rack no. " + str(rack["rack_no"]))
            can.drawCentredString(100, 320, "Rack no. " + str(rack["rack_no"]))
            can.setFont("Helvetica", 14)
            can.drawCentredString(213, 410, infodict["init_date"])
            can.drawCentredString(213, 390, "ID = " + infodict["xid"][:5])
            can.drawCentredString(213, 370, "'" + infodict["exp_name"][:10] + "...'")
            arm_text = annotations.get(rack["rack_no"], {}).get("label_text", "")
            for ix, L in enumerate(arm_text.split(" | ")):
                can.setFont("Helvetica-Oblique", 11)
                can.drawCentredString(213, 355 - (ix * 15), L)

        # (2,0)
        def draw20(rack):
            can.drawImage(QRpath, 65, 140, width=70, height=70, mask="auto")
            can.setFont("Helvetica-Bold", 14)
            can.drawCentredString(213, 100, "Rack no. " + str(rack["rack_no"]))
            can.drawCentredString(100, 120, "Rack no. " + str(rack["rack_no"]))
            can.setFont("Helvetica", 14)
            can.drawCentredString(213, 210, infodict["init_date"])
            can.drawCentredString(213, 190, "ID = " + infodict["xid"][:5])
            can.drawCentredString(213, 170, "'" + infodict["exp_name"][:10] + "...'")
            arm_text = annotations.get(rack["rack_no"], {}).get("label_text", "")
            for ix, L in enumerate(arm_text.split(" | ")):
                can.setFont("Helvetica-Oblique", 11)
                can.drawCentredString(213, 155 - (ix * 15), L)

        # right side
        # (0,1)
        def draw01(rack):
            can.drawImage(QRpath, 300, 540, width=70, height=70, mask="auto")
            can.setFont("Helvetica-Bold", 14)
            can.drawCentredString(448, 500, "Rack no. " + str(rack["rack_no"]))
            can.drawCentredString(335, 520, "Rack no. " + str(rack["rack_no"]))
            can.setFont("Helvetica", 14)
            can.drawCentredString(448, 610, infodict["init_date"])
            can.drawCentredString(448, 590, "ID = " + infodict["xid"][:5])
            can.drawCentredString(448, 570, "'" + infodict["exp_name"][:10] + "...'")
            arm_text = annotations.get(rack["rack_no"], {}).get("label_text", "")
            for ix, L in enumerate(arm_text.split(" | ")):
                can.setFont("Helvetica-Oblique", 11)
                can.drawCentredString(448, 555 - (ix * 15), L)

        # (1,1)
        def draw11(rack):
            can.drawImage(QRpath, 300, 340, width=70, height=70, mask="auto")
            can.setFont("Helvetica-Bold", 14)
            can.drawCentredString(448, 300, "Rack no. " + str(rack["rack_no"]))
            can.drawCentredString(335, 320, "Rack no. " + str(rack["rack_no"]))
            can.setFont("Helvetica", 14)
            can.drawCentredString(448, 410, infodict["init_date"])
            can.drawCentredString(448, 390, "ID = " + infodict["xid"][:5])
            can.drawCentredString(448, 370, "'" + infodict["exp_name"][:10] + "...'")
            arm_text = annotations.get(rack["rack_no"], {}).get("label_text", "")
            for ix, L in enumerate(arm_text.split(" | ")):
                can.setFont("Helvetica-Oblique", 11)
                can.drawCentredString(448, 355 - (ix * 15), L)

        # (2,1)
        def draw21(rack):
            can.drawImage(QRpath, 300, 140, width=70, height=70, mask="auto")
            can.setFont("Helvetica-Bold", 14)
            can.drawCentredString(448, 100, "Rack no. " + str(rack["rack_no"]))
            can.drawCentredString(335, 120, "Rack no. " + str(rack["rack_no"]))
            can.setFont("Helvetica", 14)
            can.drawCentredString(448, 210, infodict["init_date"])
            can.drawCentredString(448, 190, "ID = " + infodict["xid"][:5])
            can.drawCentredString(448, 170, "'" + infodict["exp_name"][:10] + "...'")
            arm_text = annotations.get(rack["rack_no"], {}).get("label_text", "")
            for ix, L in enumerate(arm_text.split(" | ")):
                can.setFont("Helvetica-Oblique", 11)
                can.drawCentredString(448, 155 - (ix * 15), L)

        draws = [draw00, draw01, draw10, draw11, draw20, draw21]

        packet = io.BytesIO()
        # create a new PDF with Reportlab
        can = canvas.Canvas(packet, pagesize=A4)
        can.setFillColorRGB(1, 0, 0)
        can.drawCentredString(275, 690, infodict["exp_name"])
        for draw, rack in zip(islice(draws, len(racks)), racks):
            draw(rack)
        can.save()
        packet.seek(0)
        new_pdf = PdfReader(packet)

        # read template PDF from the installed package's resources
        template_path = files(resources) / "rack_labels_template.pdf"
        with open(template_path, "rb") as fh:
            existing_pdf = PdfReader(fh)
            output = PdfWriter()
            # add QR codes and metadata to template
            page = existing_pdf.pages[0]
            output.add_page(page)  # attach it first
            output.pages[-1].merge_page(new_pdf.pages[0])
            # Output new experiment-specific PDF
            label_path = labels_pdf_path(storepath, prefix, ix * 6 + 1, ix * 6 + 6)
            with label_path.open("wb") as out_f:
                output.write(out_f)

    for ix, racks in enumerate(split_n(infodict["racks"], 6)):
        insert_metadata_to_PDF(ix, racks)

    # Remove templates
    try:
        QRpath.unlink()
    except Exception:
        pass


def compute_rack_annotations(infodict):
    """
    Compute concise, human-readable rack annotations.

    Rules:
      - If var1 has exactly one level -> show that single level (e.g. "WT")
      - If var1 has >1 level         -> show "multiple genotypes"
      - Same rules for var2
      - If both exist, join with " | "
      - If no info available, return {"label_text": "mixed"}
    """
    ann = {}

    var1_name = infodict.get("var1", {}).get("var1_name", "").strip()
    var2_name = infodict.get("var2", {}).get("var2_name", "").strip()
    var1_lvls = infodict.get("var1", {}).get("var1_lvls", ["", "", "", ""])
    var2_lvls = infodict.get("var2", {}).get("var2_lvls", ["", "", "", ""])

    # Normalize plural names
    def pluralize(name):
        # avoid double 'ss' ending
        name = name.strip()
        if name.endswith("s"):
            return name
        return name + "s"

    for r in infodict["racks"]:
        rack_no = r["rack_no"]
        v1_idx = r["var1"]
        v2_idx = r["var2"]
        fly = r["fly_no"]

        used_positions = [i for i, fn in enumerate(fly) if fn]

        v1_used = {v1_idx[i] for i in used_positions if v1_idx[i] is not None}
        v2_used = {v2_idx[i] for i in used_positions if v2_idx[i] is not None}

        # Convert index sets into labels
        def single_label(used, levels):
            if len(used) == 1:
                ix = next(iter(used))
                try:
                    return levels[int(ix)] or ""
                except:
                    return ""
            elif len(used) > 1:
                return None  # means "multiple"
            else:
                return ""  # means "no info"

        v1_label = single_label(v1_used, var1_lvls)
        v2_label = single_label(v2_used, var2_lvls)

        parts = []

        # var1 label
        if v1_label == "":
            pass  # nothing
        elif v1_label is None:
            if var1_name:
                parts.append(f"multiple {pluralize(var1_name.lower())}")
        else:
            parts.append(v1_label)

        # var2 label
        if v2_label == "":
            pass
        elif v2_label is None:
            if var2_name:
                parts.append(f"multiple {pluralize(var2_name.lower())}")
        else:
            parts.append(v2_label)

        if not parts:
            parts = ["mixed"]

        label_text = " | ".join(parts)

        ann[rack_no] = {
            "label_text": label_text,
            "single_arm": (("multiple" not in label_text) and ("mixed" not in label_text)),
        }

    return ann


def generate_DataSheets(infodict, pages_no, rack, storepath, anon):

    def get_figure_size():
        fig_width_cm = 21  # A4 page
        fig_height_cm = 29.7
        inches_per_cm = 1 / 2.54  # cm to inches
        fig_width = fig_width_cm * inches_per_cm  # A4 width, inches
        fig_height = fig_height_cm * inches_per_cm  # A4 height, inches
        fig_size = [fig_width, fig_height]
        return fig_size

    # HEADER ELEMENTS

    def create_QR_array(page, rack):
        xid = infodict["xid"]
        data = {"xid": xid, "page_no": page, "rack_no": rack}
        # create QR object
        qrdata = pyqrcode.create(str(data))
        # store binary info in list and convert to array to make image
        bin_qr = []
        for line in qrdata.text().split("\n")[:-1]:
            bin_qr.append(np.array([int(c) for c in line]))
        bin_qr = np.array(bin_qr)
        return bin_qr < 1

    def plot_QRcode(ax, QR):
        ax.imshow(QR, "gray")
        ax.set_xticks([])
        ax.set_yticks([])
        for side in ["top", "bottom", "left", "right"]:
            ax.spines[side].set_visible(False)

    def plot_Data(ax, page, rack, arm_text=None):
        ax.set_facecolor("#FFFFFF")
        ax.set_xlim([0, 1100])
        ax.set_ylim([0, 300])
        ax.set_xticks([])
        ax.set_yticks([])
        for side in ["top", "bottom", "left", "right"]:
            ax.spines[side].set_visible(False)

        # Title: experiment name
        expname = infodict["exp_name"][:53]
        ax.text(550, 280, expname, ha="center", va="center", fontsize="medium", fontweight="bold")

        # Rack / Page
        rackNpage = f"Rack {rack}  –  Page {page}"
        ax.text(
            550, 200, rackNpage, ha="center", va="center", fontsize="x-large", fontweight="bold"
        )

        # Optional arm label (only if provided and non-anon)
        if arm_text:
            ax.text(
                550, 150, arm_text, ha="center", va="center", fontsize="medium", fontstyle="italic"
            )

    def plot_Anon(ax, page, rack):
        ax.set_facecolor("#FFFFFF")
        ax.set_xlim([0, 1100])
        ax.set_ylim([0, 300])
        ax.set_xticks([])
        ax.set_yticks([])
        for side in ["top", "bottom", "left", "right"]:
            ax.spines[side].set_visible(False)
        expname = "\n".join(["Experiment ID:", infodict["xid"]])
        ax.text(
            550,
            280,
            expname,
            horizontalalignment="center",
            verticalalignment="center",
            fontsize="medium",
            fontweight="bold",
        )
        rackNpage = f"Rack {rack}  –  Page {page}"
        ax.text(
            550,
            140,
            rackNpage,
            horizontalalignment="center",
            verticalalignment="center",
            fontsize="x-large",
            fontweight="bold",
        )
        ax.text(
            40,
            10,
            "User name: . . . . . . . . . . . . . . . . . . . . . . . . . . . . .",
            horizontalalignment="left",
            verticalalignment="center",
            fontsize="small",
            fontweight="normal",
        )

    def plot_RGB(ax):
        ax.set_facecolor("#FFFFFF")
        ax.set_xlim([0, 500])
        ax.set_ylim([0, 500])
        ax.set_xticks([])
        ax.set_yticks([])
        ax.text(
            250,
            390,
            "Marker hues",
            horizontalalignment="center",
            verticalalignment="center",
            fontsize="medium",
        )
        ax.text(
            370,
            270,
            "Dead:",
            horizontalalignment="right",
            verticalalignment="center",
            fontsize="small",
        )
        ax.text(
            370,
            190,
            "Censored:",
            horizontalalignment="right",
            verticalalignment="center",
            fontsize="small",
        )
        ax.text(
            370,
            110,
            "Carried-over:",
            horizontalalignment="right",
            verticalalignment="center",
            fontsize="small",
        )
        patches = []
        anchor_x, anchor_y = (420, 120)
        r = 15
        x = np.array([anchor_x] * 3)
        y = np.flip(np.array([anchor_y + 80 * i for i in range(3)]))
        c = [
            infodict["colour_data"]["dead"]["RGB"],
            infodict["colour_data"]["censored"]["RGB"],
            infodict["colour_data"]["carried-over"]["RGB"],
        ]
        for i, (x1, y1) in enumerate(zip(x, y)):
            circle = Circle((x1, y1), r, facecolor=c[i])
            patches.append(circle)
        P = PatchCollection(patches, match_original=True)
        ax.add_collection(P)

    def plot_rack_region(ax, n):
        greylevel = (0.6, 0.6, 0.6)
        ax.set_xticks([])
        ax.set_yticks([])
        ax.set_facecolor(greylevel)
        ax.spines["top"].set_visible(False)
        ax.spines["left"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.spines["bottom"].set_visible(False)
        ax.set_xlim([0, 900])
        ax.set_ylim([0, 500])
        patches = []
        fsizes = [
            "xx-small",
            "x-small",
            "small",
            "medium",
            "large",
            "x-large",
            "xx-large",
        ]
        # plot rack DATE writing field
        anchor_x, anchor_y = (35, 440)
        ax.text(anchor_x, anchor_y, "Date:", fontsize=fsizes[2])
        line = Line2D(
            [anchor_x + 120, anchor_x + 600],
            [anchor_y, anchor_y],
            lw=0.5,
            color="black",
            axes=ax,
        )
        ax.add_line(line)
        # define TUBE patches
        anchor_x, anchor_y = (350, 90)
        d = 140
        r = d / 2
        offset_y = d * np.cos(np.pi / 6)  # 30 deg
        x = np.hstack(
            [
                anchor_x + (d * np.array([i for i in (range(4))])),
                anchor_x - r + (d * np.array([i for i in (range(4))])),
                anchor_x + (d * np.array([i for i in (range(4))])),
            ]
        )
        y = np.hstack(
            [
                np.array([anchor_y] * 4),
                np.array([anchor_y] * 4) + offset_y,
                np.array([anchor_y] * 4) + offset_y * 2,
            ]
        )
        for x1, y1 in zip(x, y):
            circle = Circle(
                (x1, y1),
                r,
                facecolor="#FFFFFF",
                edgecolor="#000000",
                linewidth=1,
                alpha=1,
            )
            patches.append(circle)
        # define datebox enclosing patch
        x, y = (25, 20)
        w, h = (150, 385)
        rectangle = Rectangle(
            (x, y),
            w,
            h,
            facecolor=greylevel,
            edgecolor="#000000",
            linewidth=1,
            capstyle="butt",
        )
        patches.append(rectangle)
        # define DATEBOX patches
        ax.text(50, 100, "Days since last flip", fontsize=fsizes[1], rotation="vertical")
        width = 28
        anchor_x, anchor_y = (125, 35)
        d = width * 0.7
        x = [anchor_x] * 7
        y = [anchor_y + (anchor_y + d) * i for i in range(7)]
        y = np.flip(np.array(y)).tolist()
        for i, (x1, y1) in enumerate(zip(x, y)):
            rectangle = Rectangle(
                (x1, y1),
                width,
                width,
                facecolor="#FFFFFF",
                edgecolor="#000000",
                linewidth=1,
                alpha=1,
                capstyle="butt",
            )
            ax.text(x1 - 30, y1 + 5, i + 1, fontsize=fsizes[0])
            patches.append(rectangle)
        # plot rack NUMBER
        ax.text(828, 430, n, fontsize=fsizes[5], fontname="Arial", fontweight="bold")
        # plot all patches
        P = PatchCollection(patches, match_original=True)
        ax.add_collection(P)

    def plot_datasheet(QR, page, rack, fig_size):
        plt.ioff()
        plt.rc("text", usetex=False)  # so that LaTeX is not required
        fig = plt.figure()
        fig.set_size_inches(fig_size)
        gs = matplotlib.gridspec.GridSpec(58, 42, wspace=0.1, hspace=0.1)
        ax0 = fig.add_subplot(gs[:, :])
        ax0.set_xticks([])
        ax0.set_yticks([])
        for side in ["top", "bottom", "left", "right"]:
            ax0.spines[side].set_visible(False)
        # QR code
        ax1 = fig.add_subplot(gs[2:12, 2:12])
        plot_QRcode(ax1, QR)
        # Experiment data and datasheet number
        ax2 = fig.add_subplot(gs[3:11, 12:-10])
        ## compute annotations
        annotations = compute_rack_annotations(infodict)
        arm_text = annotations.get(rack, {}).get("label_text") if not anon else None
        if anon:
            plot_Anon(ax2, page, rack)
        else:
            plot_Data(ax2, page, rack, arm_text=arm_text)
        # This needs to add the event colours
        ax3 = fig.add_subplot(gs[3:11, -10:-2])
        plot_RGB(ax3)
        ax4, ax5, ax6, ax7 = [
            fig.add_subplot(gs[13 + (i * 11) : 23 + (i * 11), 2:20])
            for i in range(4)
        ]
        ax8, ax9, ax10, ax11 = [
            fig.add_subplot(gs[13 + (i * 11) : 23 + (i * 11), 22:40])
            for i in range(4)
        ]
        axes = [ax4, ax8, ax5, ax9, ax6, ax10, ax7, ax11]
        for i, ax in enumerate(axes):
            ax.set_facecolor("#bbbbbb")
            ax.spines["top"].set_visible(False)
            ax.spines["left"].set_visible(False)
            ax.spines["right"].set_visible(False)
            ax.spines["bottom"].set_visible(False)
            plot_rack_region(ax, i + 1)
        gs.tight_layout(fig, pad=0)

    # Now to create PDF file:
    figsize = get_figure_size()
    filename_pfx = infodict["xid"].split("-")[0]
    filepath = datasheet_pdf_path(storepath, filename_pfx, rack, anon)
    with PdfPages(str(filepath)) as pdf:
        #         for rack in range(len(infodict['racks'])+1)[1:]:
        for page in range(pages_no + 1)[1:]:
            QR = create_QR_array(page, rack)
            plot_datasheet(QR, page, rack, figsize)
            pdf.savefig(dpi=300, orientation="portrait")
            plt.close("all")


# ==============================================================================
# ADDING DATA
# ==============================================================================


# ------------------------------------------------------------------------------
def extract_DataSheet_metadata(dsfilepath, verbose):
    """
    `extract_DataSheet_metadata` reads the QR code from a scanned datasheet image
    file to obtain metadata for the corresponding experiment.
    It will also use the side length of the QR code image as a scaling factor for
    the downstream image analysis.
    Limitations:
    - Image file format: works with 8-bit TIFF, JPEG and PNG, not with PDF.
    - QR detection is angle & resolution sensitive (<0.5deg, >120dpi)
    """
    dsfilepath = Path(dsfilepath)

    image = read_scan(str(dsfilepath))
    image = pad_rgb(image)
    # In case JPG opened as imageio.core.util.Array:
    image = np.array(image)
    image = image[:, :, 0:3]  # to remove alpha channel
    # normalize to 8-bit if needed
    if np.max(image) >= 1 or np.max(image) > 255:
        image = normalize_img(image, 8)

    data, qr_corners, orientation, mode = decode_qr(image, str(dsfilepath))
    if not data:
        raise QRcodeError(dsfilepath)
    img_cropped = crop_datasheet_from_qr(image, str(dsfilepath), qr_corners, orientation)

    # Approximate QR side from detected corners; fallback to a sensible default
    QRside = avg_qr_side(qr_corners) if qr_corners is not None else 200
    # Parse metadata safely (QR encodes a simple dict)
    #metadata = literal_eval(data)
    metadata = eval(data)
    
    if verbose:
        md_msg = f"Rack {metadata['rack_no']}, " \
            + f"page {metadata['page_no']} of experiment:" \
            + f"\{metadata['xid']}" \
            + f"\n(QR detected with {mode})"
        print(md_msg)
    
    return metadata, img_cropped, QRside


# ------------------------------------------------------------------------------
def get_existing_experiments(verbose = True):
    allXdir = Path(EXPERIMENTS_DIR)
    if not allXdir.exists():
        if verbose: print(Xdirlist_message.format(str(allXdir)))
        return []
    Xdirlist = [
        p
        for p in allXdir.iterdir()
        if p.is_dir() and any("_infodict.pkl" in q.name for q in p.iterdir())
    ]
    if not Xdirlist:
        if verbose: print(Xdirlist_message.format(str(allXdir)))
        return []
    infodict_list = []
    for Xdir in Xdirlist:
        pklfile = [x for x in Xdir.iterdir() if x.name.endswith("infodict.pkl")][0]
        with pklfile.open("rb") as f:
            infodict_list.append([str(Xdir), pk.load(f)])
    return infodict_list


# ------------------------------------------------------------------------------
def match_ds_experiment(metadata, infodict_list, dsfilepath):
    Xmatch = [x for x in infodict_list if x[1]["xid"] == metadata["xid"]]
    if len(Xmatch) > 1:
        feedback_text = Xmatch_message_more.format(
            one=dsfilepath,
            two=EXPERIMENTS_DIR,
        )
        return feedback_text
    elif len(Xmatch) == 0:
        feedback_text = Xmatch_message_none.format(
            one=dsfilepath,
            two="_" + metadata["xid"].split("-")[0],
            three=EXPERIMENTS_DIR,
        )
        return feedback_text
    else:
        Xmatch = Xmatch[0]
        return Xmatch[0]


# ------------------------------------------------------------------------------
def find_input_regions(dsimg, QRside, page_no, out="regions"):
    # segment all grey areas
    grim = np.min(dsimg, axis=2)
    bwim = gray_kde_thresholding(grim)
    bwim2 = binary_dilation(bwim, disk(1))
    bwregions = regionprops(label(bwim2))
    bwareas = [x.area for x in bwregions]
    bwareas.sort()
    # keep only rack regions
    largest8 = bwareas[-8:]
    thresh = largest8[0] - 1
    bwim3 = bwareaopen(bwim2, thresh)
    bwim4 = binary_fill_holes(bwim3)
    imlbl = grid_label(bwim4)
    inpt_regs = regionprops(imlbl)
    # test for number/size of objects (QRside)
    QR_ireg_area_ratio = 0.4 # adjusted parameter here
    p = 5                    # adjusted parameter here
    ireg_expected_area = np.power(QRside, 2)/QR_ireg_area_ratio
    ireg_areas = [x.area for x in inpt_regs]
    comply = [iswithinpercent(x, ireg_expected_area, p) for x in ireg_areas]
    if not all(comply):
        # then calculate number of noncompliant iregs and raise error
        m = np.sum(~np.array(comply))
        raise InputAreaDetectionError(len(inpt_regs), m, p)
    # return object filtered by areas2analyse
    if out == "image":
        return imlbl
    elif out == "regions":
        return inpt_regs

    
# ------------------------------------------------------------------------------
def split_ireg(dsimg, ireg, QRside, path:str, ix:int, visual=False):

    # adjusted parameters:
    rreg_2_QR = (1.1)  # ratio tubes area vs QR code
    dreg_2_QR = (0.35) # ratio date area vs QR code
    p = 5              # % variation allowed against expected
    opening_frac = 20  # ratio QRside v. strel for binary opening
    reduction = 0.9    # ratio object size vs QRside vs date area side
    rreg_expected_area = np.power(QRside, 2)*rreg_2_QR
    dreg_expected_area = np.power(QRside, 2)*dreg_2_QR
    
    # -- get non-grey area in the box
    # first try with gray_kde_thresholding
    i = slice_bbox(ireg.bbox)  # input region slice
    A = np.min(dsimg[i], axis=2)  # A: areas
    # scan kernel bandwith to obtain good grayscale thresholdning
    bwth = 15 # 10 covers most cases but not all
    gray_frac = 0 # forced between .4 and .7, usually .5x
    while gray_frac < 0.4 and bwth > 0:
        G, (lo, hi) = gray_kde_thresholding(A, bwth=bwth, out="all")  # G: grey
        if np.sum(G.flatten())/(G.shape[0]*G.shape[1]) < 0.7:
            gray_frac = np.sum(G.flatten())/(G.shape[0]*G.shape[1])
        bwth -= 1
    # if this does not work try threshold_multiotsu
    if gray_frac < 0.4:
        lo, hi = threshold_multiotsu(A).tolist()
        G = np.logical_and(A > lo, A < hi)
        gray_frac = np.sum(G.flatten())/(G.shape[0]*G.shape[1])
    # if this does not work give up
    if gray_frac < 0.4:
        raise DSError2(path, ix)
    bwim = G < 1  # BW image
    
    # process to extract data and tube regions
    bwim = zeroed_border(bwim, np.floor(bwim.shape[1]/100).astype(int))
    bwim = binary_fill_holes(bwim)
    selem = selem_frac(QRside, opening_frac)
    bwim = binary_opening(bwim, selem)
    # filter by area
    thresh_size = dreg_2_QR * np.power(QRside, 2) * reduction
    bwim = bwareaopen(bwim, thresh_size)    
    
    # split in date and events regions and check their number and size
    # extract regions and index them by x position (L->R)
    dr_regs = regionprops(label(bwim))
    dr_regs = sorted(dr_regs, key=lambda x: x.centroid[1])
    # check number and size of regions
    if (
        len(dr_regs) != 2
        and not iswithinpercent(dr_regs[0].area, dreg_expected_area, p)
        and not iswithinpercent(dr_regs[1].area, rreg_expected_area, p)
    ):
        raise SubAreaDetectionError(ireg.label, len(dr_regs), p)
    # in case we want to visualise results
    if visual:
        fig, axes = plt.subplots(1, 3, figsize=(15, 5))
        for ax, im in zip(axes, [A, bwim * 255, (hi < A) * 255]):
            ax.imshow(im, cmap="gray")
    return dr_regs[0], dr_regs[1]


# ------------------------------------------------------------------------------
def find_time_interval(dsimg, QRside, ireg, dreg):
    # print('\tFinding time interval for region {}.'.format(ireg.label))
    i = slice_bbox(ireg.bbox)  # input region slice
    d = slice_bbox(dreg.bbox)  # date region slice
    G = np.min(dsimg[i][d], axis=2)  # G: grey
    B, (dummy, hi) = gray_kde_thresholding(G, bwth=10, out="all")
    # get the tick boxes
    box_regs = []
    strel_side = 6 # <- adjusted parameter here
    while len(box_regs) !=7:
        selem = np.ones((strel_side, strel_side))
        box_2_QR = (0.0028 * 0.8)  # ratio dreg area to QR area, margin included
        # find the date boxes
        C = B < 1  # B: boxes for dates
        C = clear_border(C)
        C = C[:, int(C.shape[1]/2):] # remove letters
        C = np.pad(C, ((0,0),(1,0)), mode="minimum") # ensure nontouching border
        C = binary_opening(C, selem)
        C = binary_dilation(C, selem)
        #     B = binary_opening(B, selem=selem)
        #     B = binary_dilation(B, selem=selem)
        C = bwareaopen(C, box_2_QR * np.power(QRside, 2))
        # check there's 7
        box_regs = regionprops(label(C))
        strel_side -= 1
        if strel_side == 1:
            raise BoxDetectionError(ireg.label, len(box_regs))
    # find the unticked ones
    U = hi < G  # U: unchecked boxes
    U = binary_opening(U, selem)
    U = binary_dilation(U, selem)
    # need to compare directly with C for centroid position, so:
    U = U[:, int(U.shape[1]/2):] # remove letters
    U = np.pad(U, ((0,0),(1,0)), mode="minimum") # ensure nontouching border
    U = bwareaopen(U, box_2_QR * np.power(QRside, 2))
    # check there's 6 or 7
    untk_regs = regionprops(label(U))
    if len(untk_regs) < 6:
        raise UBoxDetectionError1(ireg.label, len(untk_regs))
    elif len(untk_regs) > 7:
        raise UBoxDetectionError2(ireg.label, len(untk_regs))
    # find the ticked one
    ## order date boxes by y position
    box_regs = sorted(box_regs, key=lambda x: x.centroid[0])
    ticked_boxes = []
    for blank in untk_regs:
        for ix, box in enumerate(box_regs):
            t = tupleintor(blank.centroid)
            test = [(x, y) == t for (x, y) in box.coords]
            if any(test):
                ticked_boxes.append(ix)
    # days since last flip
    dslf = list(set(list(range(7))) - set(ticked_boxes))
    # check that there is one or zero
    if len(dslf) == 1:
        status = 1
        ndays = dslf[0] + 1
    elif len(dslf) == 0:
        status = 0
        ndays = None
    else:
        raise DateDetectionError(len(dslf), ireg.label)
    return status, ndays


# ------------------------------------------------------------------------------
def evaluate_DataSheet_usage(
    dsimg,
    QRside,
    page_no,
    path:str,
    verbose,
):
    # messaging
    if verbose: print('\nEvaluating usage of databoxes:')

    # find rectangles with tubes and days since prev. flip
    inpt_regs = find_input_regions(dsimg, QRside, page_no)
    # split them in tubes vs days areas and check whether used
    K = ["ireg", "dreg", "rreg", "status", "ndays"]
    inpt_regs_status = {x: [] for x in K}
    for ix, ireg in enumerate(inpt_regs):
        dreg, rreg = split_ireg(dsimg, ireg, QRside, path, ix)
        status, ndays = find_time_interval(dsimg, QRside, ireg, dreg)
        V = [ireg, dreg, rreg, status, ndays]
        [inpt_regs_status[x].append(y) for x, y in zip(K, V)] # inplace assignation
        if verbose:
            dbx_msg = f"\tDatabox ix. {ix} {'IS ' if status else 'is NOT '}populated."
            print(dbx_msg)

    # turn status 0/1 into index
    inpt_regs_status["status"] = np.nonzero(inpt_regs_status["status"])[0]
    x = inpt_regs_status["status"]
    {k: [v[z] for z in x] for k, v in inpt_regs_status.items()} # only consider those with changes
    if verbose:
        ix2print = ", ".join([str(d) for d in x[:-1]]) + f' and {str(x[-1])}'
        print(f"\tData will be extracted from databoxes ix. {ix2print}")

    return inpt_regs_status


# ------------------------------------------------------------------------------
def determine_data2record(
    inpt_regs_status,
    page_no,
    rack_no,
    sequence,
    OW,
    verbose,
):
    # data input areas that have been used in this DataSheet
    used = [int(i + 1) for i in inpt_regs_status["status"]]
    # determine which of these areas have recorded before
    recorded = sequence.loc[
        (sequence["Page_no."] == page_no)
        & (sequence["Rack_no."] == rack_no)
        ]["Area_no."]
    recorded = np.unique(recorded).tolist()
    # input areas that are new
    used_new = list(sorted(set(used) - set(recorded)))
    # decide which ones to record and log decision
    if OW:
        inputs2record = [x - 1 for x in used]
    else:
        inputs2record = [x - 1 for x in used_new]
    inpt_regs_add = {k: [v[z] for z in inputs2record]
                     for k, v in inpt_regs_status.items()}
    
    if verbose:
        if recorded:
            inputs2ow = ", ".join([ str(x) for x in [y-1 for y in recorded[:-1]] ]) \
                + f' and {str(recorded[-1]-1)}'
        else:
            inputs2ow = "[ none ]"
        if inputs2record:
            inputs2print = ", ".join([str(x) for x in inputs2record[:-1]]) \
                + f' and {str(inputs2record[-1])}'
        else:
            inputs2print = "[ none ]"
        
        ow_msg = f"\n\tPreexisting data from databoxes ix. {inputs2ow} will be overwritten."
        notow_msg = f"\n\tNew data in databoxes ix. {inputs2ow} will be ignored."
        write_msg = "\nEvaluating overlap between existing and newly extracted data." \
            + f"\n\tData written from databoxes ix. {inputs2print} will be recorded." \
            + f"{ow_msg if OW else notow_msg}"
        print(write_msg)
    
    return inpt_regs_add


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

def process_DataSheet_image(
    infodict,
    sequence,
    dsimg,
    QRside,
    page_no,
    rack_no,
    dsfilepath,
    inpt_regs_add,
    storepath,
    OW,
    dry_run,
    verbose,
):
    imfilename = name_save_image(
        storepath,
        dry_run,
        rack_no,
        page_no,
        dsimg,
        OW,
        verbose,
    )
    
    for ix in range(len(inpt_regs_add["status"])):
        if verbose: print(f"Evaluating databox index {ix}")
        ireg, rreg, dreg, ndays = extract_new_wipe_old_records(
            ix,
            inpt_regs_add,
            sequence,
            page_no,
            rack_no
        )
        tube_regs = find_tubes(
            ireg,
            rreg,
            dsimg
        )
        for tx, treg in zip(range(12), tube_regs):
            if verbose: print(f"\tEvaluating tube index {tx}")
            # get genotype and treatment
            var1_ix = infodict["racks"][rack_no - 1]["var1"][tx]
            var2_ix = infodict["racks"][rack_no - 1]["var2"][tx]
            fly_no = infodict["racks"][rack_no - 1]["fly_no"][tx]
            # to remove empty tubes
            if not fly_no:
                pass
            # actual data recording
            else:
                # get variable levels in this tube
                var1val, var2val = tube_config(
                    var1_ix,
                    var2_ix,
                    infodict
                )
                # find events in tube
                evnt_dict = find_events(
                    ireg,
                    rreg,
                    treg,
                    dsimg,
                    QRside,
                    infodict
                )
                # update tube, overwriting if needed
                sequence = update_raw(
                    evnt_dict,
                    ireg,
                    dreg,
                    rreg,
                    treg,
                    page_no,
                    rack_no,
                    var1val,
                    var2val,
                    imfilename,
                    sequence,
                    ndays,
                )
    return sequence


# -- helpers for process_DataSheet_image() -------------------------------------

def find_tubes(
    ireg,
    rreg,
    dsimg,
):
    i = slice_bbox(ireg.bbox)  # input region slice
    r = slice_bbox(rreg.bbox)  # rack slice
    S = np.min(dsimg[i][r], axis=2)  # T: tubes
    lo, hi = gray_kde_thresholding(S, out="thresh")
    T = hi < S # used to be "lo < T", robustness tbc
    # remove anything that cannot be a tube,
    # since radius ~ T.shape[0]/6 (w/o accounting for hexagonal packing!)
    size_thresh = int(np.power(T.shape[0]/6.6, 2) * np.pi) # 6.6 adds 10% buffer to radius
    T = bwareaopen(T, size_thresh)
    T = binary_fill_holes(T) # to remove events
    # marker-controlled watershed segmentation
    # distance transform
    D = distance_transform_edt(T)# * E
    # distance peaks are at the centers of tubes
    P = peak_local_max(D, min_distance = int(T.shape[0]/6))
    M = np.zeros(T.shape)
    M[P[:,0], P[:,1]] = 1
    M = label(M)
    W = watershed(sobel(T), M)
    Q = W*T
    if np.max(Q) != 12:
        raise TubesDetectionError(ireg.label, 12, np.max(Q))
    tube_regs = regionprops(grid_label(Q))
    return tube_regs


def tube_config(
    var1_ix,
    var2_ix,
    infodict,
):
    if var1_ix is not None:
        var1val = infodict["var1"]["var1_lvls"][var1_ix]
    else:
        var1val = None
    if var2_ix is not None:
        var2val = infodict["var2"]["var2_lvls"][var2_ix]
    else:
        var2val = None
    return (var1val, var2val)


def find_events(
    ireg,
    rreg,
    treg,
    dsimg,
    QRside,
    infodict,
):
    i = slice_bbox(ireg.bbox)  # input region slice
    r = slice_bbox(rreg.bbox)  # rack slice
    t = slice_bbox(treg.bbox)  # tube slice
    E = np.min(dsimg[i][r][t], axis=2)  # E: events
    E = ~np.multiply(E, treg.image)
    E = clear_border(E)
    E = median(
        E, disk(np.ceil(QRside / 150))
    )  # <------------------------ adjusted parameter here
    # segment or decide that it is empty
    span = np.max(E) - np.min(E)
    if span < infodict["colour_data"]["otsuT"]:
        return {}
    else:
        event_list = [] # evnt_dict = {}
        # check that Otsu is not skewed down by background
        otsuE = threshold_otsu(E)
        if (
            otsuE / infodict["colour_data"]["otsuT"] < 0.25
        ):  # <--------- adjusted parameter here
            event_regs = regionprops(label(infodict["colour_data"]["otsuT"] < E))
        else:
            event_regs = regionprops(label(threshold_otsu(E) < E))
        # store info for event as {1: (H,S), event_slice, event_type}
        for event in event_regs:
            event_data = {k: None for k in ['slice', 'type', 'assignation']}
            e = slice_bbox(event.bbox)
            eR = ma_mean(dsimg[i][r][t][e][:, :, 0], event.image) / 255
            eG = ma_mean(dsimg[i][r][t][e][:, :, 1], event.image) / 255
            eB = ma_mean(dsimg[i][r][t][e][:, :, 2], event.image) / 255
            # capture Hue and Saturation; turn Hue from 0-1 to radians
            HueSat = [x for x in rgb_to_hsv(eR, eG, eB)[:2]]
            HueSat[0] = np.deg2rad(HueSat[0] * 360) % (2*np.pi)
            # assign event type
            event_type, assign_mode = id_event(HueSat, infodict)
            # populate event_data
            event_data['slice'] = e
            event_data['type'] = event_type
            event_data['assignation'] = assign_mode
            event_list.append(event_data)
        
        return event_list


def id_event(HueSat, infodict):
    H, S = HueSat
    event_type = None
    assign_mode = None
    for x in ["dead", "censored", "carried-over"]:
        HueLo = infodict["colour_data"][x]["HSinterval"][0][0]
        HueHi = infodict["colour_data"][x]["HSinterval"][0][1] % (2*np.pi)
        SatLo = infodict["colour_data"][x]["HSinterval"][1][0]
        SatHi = infodict["colour_data"][x]["HSinterval"][1][1]
        # for normal colour intervals:
        if HueLo < HueHi:
            if (HueLo < H < HueHi):
                if (SatLo < S < SatHi):
                    event_type = x
                    assign_mode = 'HueSat'
                else:
                    event_type = x
                    assign_mode = 'Hue'
        # for inverted intervals (red hues)
        else:
            if (0 < H < HueLo) | (HueHi < H < 2*np.pi):
                if (SatLo < S < SatHi):
                    event_type = x
                    assign_mode = 'HueSat'
                else:
                    event_type = x
                    assign_mode = 'Hue'
    # if there was no match
    if not event_type:
        event_type = 'unassigned'
        assign_mode = 'na'
    return event_type, assign_mode


def name_save_image(
    storepath,
    dry_run,
    rack_no,
    page_no,
    dsimg,
    OW,
    verbose,
):

    # define datasheet directory within experiment directory
    datafolder = processed_datasheets_dir(storepath)
    if not dry_run:
        datafolder.mkdir(parents=True, exist_ok=True)
    # define "inaugural" ds filename: DataSheet_rack_page_raw.jpg
    imfilename = raw_image_filename(rack_no, page_no, "raw")
    
    # check if there are previous files for this datasheet
    root_fn = "_".join(imfilename.split("_")[:3])
    previous = []
    if not dry_run and datafolder.exists():
        previous = [x.name for x in datafolder.iterdir() if root_fn in str(x)]
    
    # overwrite mode: delete previous datasheets
    if previous and OW and not dry_run:
        if verbose: print(message_ow_image.format(imfilename, datafolder))
        for f in previous:
            try:
                (datafolder / f).unlink()
            except Exception:
                pass
        imsave(str(datafolder / imfilename), dsimg)
        # non-overwrite mode: save ds as a new file: DataSheet_rack_page_lowercase.jpg
    elif previous and not OW and not dry_run:
        if verbose: print(message_savenew_image.format(root_fn, datafolder))
        suffix = len(previous)  # 0,1,2,…
        imfilename = raw_image_filename(rack_no, page_no, f"v{suffix}")
        imsave(str(datafolder / imfilename), dsimg)
    # when no overwriting needed:
    else:
        if not dry_run:
            imsave(str(datafolder / imfilename), dsimg)
            if verbose: print(message_first_image.format(imfilename, datafolder))
        else:
            imfilename = raw_image_filename(rack_no, page_no, "DRYRUN")
            if verbose: print(message_dryrun_image.format(imfilename, datafolder))

    return imfilename

def extract_new_wipe_old_records(
    ix,
    inpt_regs_add,
    sequence,
    page_no,
    rack_no,
):    
    ireg = inpt_regs_add["ireg"][ix]
    rreg = inpt_regs_add["rreg"][ix]
    dreg = inpt_regs_add["dreg"][ix]
    ndays = inpt_regs_add["ndays"][ix]
    dropids = sequence[
        (sequence["Page_no."] == page_no)
        & (sequence["Rack_no."] == rack_no)
        & (sequence["Area_no."] == ireg.label)
    ].index
    sequence.drop(dropids, inplace=True)
    # no need to return df as it is modified in place
    return (ireg, rreg, dreg, ndays)


def update_raw(
    event_list,
    ireg,
    dreg,
    rreg,
    treg,
    page_no,
    rack_no,
    var1val,
    var2val,
    imfilename,
    df,
    ndays,
):
    if len(event_list) == 0:
        # add date with no events
        dt = [
            [
                ndays,                 # 'Days_int'
                0,                     # 'Dead'
                0,                     # 'Censored'
                0,                     # 'CarriedOver'
                0,                     # 'unassigned'
                treg.label,            # 'Tube_no.'
                ireg.label,            # 'Area_no.'
                page_no,               # 'Page_no.'
                rack_no,               # 'Rack_no.'
                var1val,               # infodict['var1']['var1_name']
                var2val,               # infodict['var2']['var2_name']
                imfilename,            # 'FileName'
                None,                  # 'Dead_slices'
                None,                  # 'Dead_assignation'
                None,                  # 'Censored_slices'
                None,                  # 'Censored_assignation'
                None,                  # 'CarriedOver_slices'
                None,                  # 'CarriedOver_assignation'
                None,                  # 'unassigned_slices'
                slice_bbox(treg.bbox), # 'Tube_slice'
                slice_bbox(rreg.bbox), # 'Rack_slice'
                slice_bbox(dreg.bbox), # 'Date_slice'
                slice_bbox(ireg.bbox), # 'Area_slice'
            ]
        ]
        df = pd.concat(
            [df if not df.empty else None, pd.DataFrame(dt, columns=df.columns)],
            ignore_index=True,
        )
    else:
        # fill sequence for full data storage / future segmentation troubleshooting
        # turn individual events in counts per type
        event_types = ["dead", "censored", "carried-over", "unassigned"]
        ecounts = {k: {'number':0, 'slices':[], 'assignations':[]} for k in event_types}
        for event in event_list:
            ecounts[ event['type'] ] ['number'] += 1
            ecounts[ event['type'] ] ['slices'].append( event['slice'] )
            ecounts[ event['type'] ] ['assignations'].append( event['assignation'] )
        dt = [
            [
                ndays,                                   # 'Days_int'
                ecounts["dead"]['number'],               # 'Dead'
                ecounts["censored"]['number'],           # 'Censored'
                ecounts["carried-over"]['number'],       # 'CarriedOver'
                ecounts["unassigned"]['number'],         # 'unassigned'
                treg.label,                              # 'Tube_no.'
                ireg.label,                              # 'Area_no.'
                page_no,                                 # 'Page_no.'
                rack_no,                                 # 'Rack_no.'
                var1val,                                 # infodict['var1']['var1_name']
                var2val,                                 # infodict['var2']['var2_name']
                imfilename,                              # 'FileName'
                ecounts["dead"]['slices'],               # 'Dead_slices'
                ecounts["dead"]['assignations'],         # 'Dead_assignation' 
                ecounts["censored"]['slices'],           # 'Censored_slices'
                ecounts["censored"]['assignations'],     # 'Censored_assignation'
                ecounts["carried-over"]['slices'],       # 'CarriedOver_slices'
                ecounts["carried-over"]['assignations'], # 'CarriedOver_assignation'
                ecounts["unassigned"]['slices'],         # 'unassigned_slices'
                slice_bbox(treg.bbox),                   # 'Tube_slice'
                slice_bbox(rreg.bbox),                   # 'Rack_slice'
                slice_bbox(dreg.bbox),                   # 'Date_slice'
                slice_bbox(ireg.bbox),                   # 'Area_slice'
            ]
        ]
        df = pd.concat(
            [df if not df.empty else None, pd.DataFrame(dt, columns=df.columns)],
            ignore_index=True,
        )
    return df


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
# MESSAGES

Xdirlist_message = """
The storage directory for Drosben experiments could not be found:
{}
Consider whether the DataSheets correspond to an Experiment initiated in
a different computer or whether the storage directory has been removed.\n
"""

Xmatch_message_more = """The DataSheet from file:\n
\t{one}
seems to match more than one logged experiment.
This is mostly likely due to duplication of a folder in the storage directory:\n
\t{two}
outside the Drosben software."""

Xmatch_message_none = """The DataSheet from file \n
\t{one}
does not match any logged experiment.
This is mostly likely due to deletion of a folder ending in:\n
\t{two}
from in the storage directory:\n
\t{three}
or you using a different computer for DataSheet reading."""

# warning_unassigned = """
# There are unassigned events in rack {}, tube(s) {}.
# The software detected {} unassigned events{}. 
# These could be unintended spots in the DataSheet scan (e.g. due to debris
# in the scanner or stains in the DataSheet form), or real events where
# the colour deviates too much from the expected hue/saturation values.
# These will be eliminated from the data spreadsheet for analysis so this
# will further reduce the total number of observations in these tubes.
# (They will be kept in the 'readable' spreadsheet for manual tracking
# and analysis if desired.)
# Close inspection of the DataSheet scans is encouraged to detect potential
# human mistakes or problems of segmentation that can be corrected by
# re-filling and scanning the DataSheet forms in a clearer/cleaner way.
# """

# warning_null = """
# There are no data recorded for rack {}, tube(s) {}. 
# {} expected to contain {} flies{}.
# Check that there have not been errors when filling the DataSheet.
# """

# warning_missing = """
# There are missing events observed in rack {}, tube(s) {}. 
# {} expected to contain initially {} flies{}.
# But the observations recorded are only {}{}.
# """

# warning_excess = """
# There are excessive events observed in rack {}, tube(s) {}. 
# {} expected to contain initially {} flies{}. 
# But the observations recorded are more: {}{}.
# """

# message_null = """
#     The analysis will proceed with the current data.\n"""

# message_missing = """
#     The analysis will proceed with the current data (stored in the Excel file:
#     {},  
#     and assuming that the original number of flies matches the number of observations. 
#     A more stringent analysis would consider the missing flies as censored at day 1,
#     or at the end of the experiment if these missing events come from ending the experiment
#     before all individuals have died (this is the default behaviour).
#     You can perform this analysis by using the sheet named `Observations_stringent_analysis` 
#     in the same Excel file.\n"""

# message_excess = """
#     The analysis will proceed with the current data.
#     You are encouraged to carefully examine the DataSheet for errors in segmentation,
#     in filling in the data, or the initial loading of tubes.\n"""

# message_unassigned = """
#     The analysis will proceed with the current data.
#     You are encouraged to carefully examine the DataSheet for errors in segmentation,
#     or in filling in the data.\n"""

message_ow_image = """
Saving DataSheet image {} in directory
{}
Any previous version of this file will be overwritten.\n"""

message_savenew_image = """
Saving DataSheet image {} in directory
{}
Any previous image files of this DataSheet will be preserved.
This program does not check that previously recorded data
is consistent in the new DataSheet.
Please manually check and consider overwriting to avoid conflicts.\n"""

message_first_image = """
Saving DataSheet image {} in directory
{}\n"""

message_dryrun_image = """
Running in dry run mode.
{} is passed as image file name.
No images saved in directory
{}\n"""