# ------------------------------------------------------------------------------
import pickle as pk
from datetime import datetime

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import skimage.measure as skime
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.collections import PatchCollection
from matplotlib.patches import Circle
from PIL import Image
from scipy import ndimage
from scipy.ndimage import binary_opening
from skimage.color import rgb2hsv
from skimage.filters import threshold_otsu
from skimage.morphology import disk
from skimage.segmentation import clear_border, find_boundaries

from drosben.config import COLOUR_CONFIG_DIR
from drosben.image.utils import (
    colour_intervals_string,
    dots_recovered,
    evaluate_dots,
    extend_intervals,
    find_stringent_colour_intervals,
    grid_label,
    normalize_img,
    read_scan,
    slice_bbox,
)


# ------------------------------------------------------------------------------
def extract_colour_values(
    imgpath: str,  # or Path?
    *,
    expected_res: int = 150,
    expected_width_in: int = 8.4,  # A4 width ~8.3in, US letter width =8.5s
    expected_boxes: int = 10,
    expected_dots_per_box: int = 5,
    colour_positions=("top", "middle", "bottom", "mixed"),
):
    """
    `extract_HSV_values` reads the image of a Colour Calibration Sheet and
    returns the HSV value intervals for the different event colours and Otsu
    thresholds used during the process of segmenting the event dots.
    """

    def check_resolution(im):
        res = im.shape[1] / expected_width_in
        if res < expected_res:
            warn_msg.append(wrong_res(int(np.round(res))))
            aspect_r = im.shape[0] / im.shape[1]
            new_size = [
                int(np.round(expected_width_in * expected_res)),
                int(np.round(expected_width_in * expected_res * aspect_r)),
            ]
            im = np.array(Image.fromarray(im).resize(new_size))
        return im, warn_msg

    def sheet_to_boxes(im):
        """
        `sheet_to_boxes` reads the image of a Colour Calibration Sheet and
        returns the `regionprops` of the boxes for event 'dots
        """
        im = normalize_img(im, 8)
        im, warn_msg = check_resolution(im)
        # binarise, get all enclosed areas
        grim = np.min(im, axis=2)
        imbw = grim < threshold_otsu(grim)
        imbw_filled = ndimage.binary_fill_holes(imbw).astype(int)
        imbw_labelled = skime.label(imbw_filled)
        props = skime.regionprops(imbw_labelled)
        # keep only the 9 boxes where dots are collected
        imbw_cleaned = np.zeros(imbw_filled.shape, dtype="int")  # makes empty image
        areas = sorted([reg.area for reg in props])[-11:]  # there are 11 large boxes
        thresh_area_lo = areas[0] - 1  # thresh to remove regions < smallest box
        thresh_area_up = areas[-2] + 1  # thresh to remove largest box
        for blob in props:
            if thresh_area_lo <= blob.area <= thresh_area_up:
                imbw_cleaned[blob.coords[:, 0], blob.coords[:, 1]] = 1
        # order by grid
        boxim = imbw_cleaned > 0
        return im, boxim, warn_msg

    def box_to_dots():
        """
        `box_to_dots` takes a `regionprops` ROI and returns the labelled image
        for a 'box' with segmented event 'dots'. It also returns the Otsu
        threshold used to identify the dots.
        """
        # Remove interior of tube 'background' and leave colour dots and label each
        tube = np.min(im, axis=2)[box_slice]
        otsuT = threshold_otsu(tube)
        tube = tube > otsuT
        tube = ~np.multiply(tube, box.image)
        tube = clear_border(tube)
        tube = binary_opening(tube, disk(2))
        eventprops = skime.regionprops(skime.label(tube))
        return eventprops, otsuT

    def dot_to_colour(
        # col_names,
        # eventprops,
        # box_slice,
    ):
        """
        `dot_to_colour` extracts RBG and HSV colour data from dots in the image and
        returns them as a `pandas.DataFrame`.
        """
        eventtypedata = pd.DataFrame([], columns=col_names)
        for event in eventprops:
            # Will go through a single tube and determine HSV of each event
            event_slice = slice_bbox(event.bbox)
            ys, xs = np.where(event.image)
            event_R = np.round(np.mean(im[box_slice][event_slice][ys, xs, 0])).astype("uint8")
            event_G = np.round(np.mean(im[box_slice][event_slice][ys, xs, 1])).astype("uint8")
            event_B = np.round(np.mean(im[box_slice][event_slice][ys, xs, 2])).astype("uint8")
            event_RGB = np.dstack([np.array(event_R), np.array(event_G), np.array(event_B)])
            event_HSV = rgb2hsv(event_RGB).squeeze()
            expt_data = [
                box_number,
                event_HSV[0],
                event_HSV[1],
                event_HSV[2],
                event_R,
                event_G,
                event_B,
                otsuT,
                box_slice,
                event,
            ]
            DataRow = pd.DataFrame([expt_data], columns=col_names)
            eventtypedata = pd.concat(
                [eventtypedata if not eventtypedata.empty else None, DataRow], ignore_index=True
            )

        return eventtypedata

    def wrong_no_boxes(n_boxes):
        return f"\nThe number of input data boxes detected is {n_boxes}, \
while {expected_dots_per_box} were expected."

    def wrong_no_dots(box_n, n_dots, expected_dots):
        return f"\nThe number of dots detected in box {box_n} is {n_dots}, \
while {expected_dots} were expected."

    def wrong_res(res):
        return f"\nThe resolution of this scan seems to be ~{res}dpi:\n \
{imgpath.name}\n \
The recommended resolution is >150dpi. Drosben will interpolate to 150dpi."

    problems_maybe = """
The process will continue with the available data, but there may be problems after this point.
Consider re-scanning the Colour Calibration Form."""

    col_names = [
        "box_number",
        "Hue",
        "Saturation",
        "Value",
        "Red",
        "Green",
        "Blue",
        "otsuT",
        "boxslice",
        "dotprops",
    ]

    box_colours = {
        1: colour_positions[0],
        2: colour_positions[0],
        3: colour_positions[0],
        4: colour_positions[1],
        5: colour_positions[1],
        6: colour_positions[1],
        7: colour_positions[2],
        8: colour_positions[2],
        9: colour_positions[2],
        10: colour_positions[3],
    }

    # PROCESS IMAGE
    im = read_scan(str(imgpath))
    colour_values = pd.DataFrame(columns=col_names)  # initialise dot data frame
    warn_msg = []  # initialise warning messages

    # obtain the boxes with dots and check number
    im, boxim, warn_msg = sheet_to_boxes(im)
    # initialise segmentation results
    colourmask = np.ones(im.shape)
    colourmask = colourmask.astype(np.uint8) * 255
    grid = grid_label(boxim, visual=False)
    box_labelled = skime.regionprops(grid)
    if len(box_labelled) != expected_boxes:
        warn_msg.append(wrong_no_boxes(len(box_labelled)))
    colourmask[find_boundaries(grid)] = 0  # create outlines of boxes

    # for each box
    for box, box_number in zip(box_labelled, range(1, 11), strict=True):
        # find dots
        box_slice = slice_bbox(box.bbox)
        eventprops, otsuT = box_to_dots()
        # determine average colour values
        colourdf = dot_to_colour()
        colour_values = pd.concat(
            [colour_values if not colour_values.empty else None, colourdf],
            ignore_index=True,
        )
        n_dots_not_expected = len(eventprops) != expected_dots_per_box
        n_mixed_not_expected = len(eventprops) != expected_dots_per_box * 3
        mixed_box = box_number == expected_boxes
        if (not mixed_box) & n_dots_not_expected:
            warn_msg.append(wrong_no_dots(box_number, len(eventprops), expected_dots_per_box))
        elif mixed_box & n_mixed_not_expected:
            warn_msg.append(wrong_no_dots(box_number, len(eventprops), expected_dots_per_box * 3))
    # update dataframe
    # map HSV values to radians for continuous data
    colour_values["continuousHue"] = np.deg2rad(colour_values["Hue"] * 360)
    # map colour to expected colour
    colour_values["Corr_event"] = colour_values["box_number"].map(box_colours)
    # obtain average values per event category (except mixed)
    colour_values_by_event = colour_values.groupby("Corr_event")
    meancols = colour_values_by_event.agg({"Red": "mean", "Green": "mean", "Blue": "mean"})
    meancols = meancols.astype(np.uint8)
    colour_values["meanR"] = colour_values["Corr_event"].map(meancols["Red"])
    colour_values["meanG"] = colour_values["Corr_event"].map(meancols["Green"])
    colour_values["meanB"] = colour_values["Corr_event"].map(meancols["Blue"])

    # transfer to mask (except in mixed)
    for _ix, dot in colour_values[colour_values.Corr_event != "mixed"].iterrows():
        dotslice = slice_bbox(dot.dotprops.bbox)
        ys, xs = np.where(dot.dotprops.image)
        colourmask[dot.boxslice][dotslice][ys, xs, :] = [[dot.meanR, dot.meanG, dot.meanB]]
    # generic warning if some boxes/dots are off expectation
    if len(warn_msg) > 0:
        warn_msg.append(problems_maybe)

    return colour_values, warn_msg, im, colourmask


# ------------------------------------------------------------------------------
def define_colour_interval(
    colour_values,
    *,
    verbose=False,
    colour_positions=("top", "middle", "bottom", "mixed"),
):
    # prepare warning messages
    failed_HS_overlap = """
There are no Hue/Saturation value intervals that identify all events unambiguously.\n \
There is no confidence in this marker combination. YOU SHOULD NOT USE IT."""
    failed_HS_miss = """
Not all event dots used to evaluate the marker combination could be correctly assigned \
by Hue and Saturation\n \
to the corresponding colour. If you proceed with this marker combination and scanning \
method there is risk\n \
that some events will be missed."""
    failed_HS_recommend = """
It is recommended to use different markers. Alternatively, make sure that the scan of the \
Colour Calibration Form\n \
is adequate (e.g. use scanner instead of phone or improve illumination; increase resolution)."""

    warn_msg = []

    # define colours as non-overlapping Hue, Saturation intervals
    colour_intervals = find_stringent_colour_intervals(colour_values)
    if verbose:
        print("The initial colour intervals are:", flush=True)
        print(colour_intervals_string(colour_intervals), flush=True)
    colour_intervals = extend_intervals(colour_intervals)
    if verbose:
        print("The expanded colour intervals are:", flush=True)
        print(colour_intervals_string(colour_intervals), flush=True)
        warn_msg.append(failed_HS_overlap)
    # check dots are recognised to the intervals they defined
    colour_values = evaluate_dots(colour_intervals, colour_values)
    tested_col_vals = colour_values[colour_values.Corr_event != "mixed"]
    if not dots_recovered(tested_col_vals):
        warn_msg.append(failed_HS_miss)
    if len(warn_msg) > 0:
        warn_msg.append(failed_HS_recommend)

    # use colours so highest hue variability is used less frequently
    # dead < censored < carried-over
    colour_varty = {"top": [], "middle": [], "bottom": []}
    for k in colour_intervals:
        #hstd = colour_intervals[k]["Hmean-std"][1]
        #sstd = colour_intervals[k]["Smean-std"][1]
        #colour_varty[k] = hstd * sstd  # product of stds ~inverse of variability:
        colour_varty[k] = colour_intervals[k]["Hmean-std"][1]
    sorted_colours = sorted(colour_varty, key=colour_varty.get)
    #sorted_colours = sorted(colour_varty, key=colour_varty.get)
    colour_events = {
        "dead": sorted_colours[0],
        "carried-over": sorted_colours[1],
        "censored": sorted_colours[2],
    }

    return colour_intervals, warn_msg, colour_events


# ------------------------------------------------------------------------------
def mixed_dots_2mask(
    col_vals,
    colour_intervals,
    colourmask,
    *,
    expected_dots_per_box: int = 5,
    warn_msg: str,
):
    def wrong_mixed(n_dots, n_types, expected_dots_per_box):
        return f"\nThe number of detected 'mixed dots' is {n_dots}, of {n_types} \
        distinct  colours; {expected_dots_per_box} dots for each of 3 colours were expected."

    # mixed dots were evaluated earlier in determine_colour_interval
    mixed_dots = col_vals[col_vals.Corr_event == "mixed"]
    # the only thing to do is to check numbers...
    # 3 types of events, by construction:
    dot_n_ok = len(mixed_dots.index) == (3 * expected_dots_per_box)
    type_n_ok = len(np.unique(mixed_dots.event_match)) == 3
    if (not dot_n_ok) | (not type_n_ok):
        warn_msg.append(
            wrong_mixed(
                len(mixed_dots.index), len(np.unique(mixed_dots.event_match)), expected_dots_per_box
            )
        )
    # ... and update colourmask
    for _ix, dot in mixed_dots.iterrows():
        dotslice = slice_bbox(dot.dotprops.bbox)
        dotcol = dot.event_match
        if dotcol != "unmatched":
            RGB = [[int(x * 255) for x in colour_intervals[dotcol]["RGB"]]]
        else:
            RGB = [[70, 70, 70]]
        ys, xs = np.where(dot.dotprops.image)
        colourmask[dot.boxslice][dotslice][ys, xs, :] = RGB

    return colourmask, warn_msg


# ------------------------------------------------------------------------------
def generate_calibration_folder():
    # Use the shared Drosben user directory from config.py
    d = COLOUR_CONFIG_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


# ------------------------------------------------------------------------------
def generate_colourcal_report(
    col_vals,
    colour_intervals,
    colour_events,
    warn_msg_dots,
    warn_msg_colour,
    date,
    dirpath,
    im,
    colourmask,
):

    def get_figure_size():
        fig_width_cm = 21  # A4 page
        fig_height_cm = 29.7
        inches_per_cm = 1 / 2.54  # cm to inches
        fig_width = fig_width_cm * inches_per_cm  # A4 width, inches
        fig_height = fig_height_cm * inches_per_cm  # A4 height, inches
        fig_size = [fig_width, fig_height]
        return fig_size

    def HS_interval_plot(ax):
        import matplotlib as mpl
        import matplotlib.pyplot as plt
        import numpy as np
        from matplotlib import colormaps as cm
        from scipy.interpolate import interp1d

        # max saturation value
        satmax = np.max(
            [
                np.max(col_vals["Saturation"]),
                np.max([colour_intervals[x]["Smin-max"][1] for x in colour_intervals]),
            ]
        )

        # Define colormap normalization for 0 to 2*pi
        norm = mpl.colors.Normalize(0, 2 * np.pi)

        # Plot a color mesh on the polar plot
        # with the color set by the angle
        n = 200  # the number of secants for the mesh
        t = np.linspace(0, 2 * np.pi, n)  # theta values
        r = np.linspace(1.2, 1.1, 2)  # radius values change 0.6 to 0 for full circle
        rg, tg = np.meshgrid(r, t)  # create a r,theta meshgrid
        c = tg  # define color values as theta value
        # plot the colormesh on axis with colormap
        _ = ax.pcolormesh(t, r, c.T, norm=norm, cmap=cm["hsv"].resampled(2054))
        ax.set_yticklabels([])
        ax.set_xticklabels([])
        ax.spines["polar"].set_visible(False)
        ax.set_ylabel("Hue", c="grey", size=8)

        for colour in ["top", "middle", "bottom"]:
            plt.polar(
                np.unwrap(col_vals[col_vals.Corr_event == colour]["continuousHue"]),
                col_vals[col_vals.Corr_event == colour]["Saturation"],
                "o",
                c=colour_intervals[colour]["RGB"],
                alpha=0.3,
            )
        for colour in ["top", "middle", "bottom"]:
            plt.plot(
                (colour_intervals[colour]["Hmin-max"][0], colour_intervals[colour]["Hmin-max"][0]),
                (colour_intervals[colour]["Smin-max"][0], colour_intervals[colour]["Smin-max"][1]),
                ":",
                c=colour_intervals[colour]["RGB"],
            )
            plt.plot(
                (colour_intervals[colour]["Hmin-max"][1], colour_intervals[colour]["Hmin-max"][1]),
                (colour_intervals[colour]["Smin-max"][1], colour_intervals[colour]["Smin-max"][0]),
                ":",
                c=colour_intervals[colour]["RGB"],
            )
            if colour_intervals[colour]["Hmin-max"][0] < colour_intervals[colour]["Hmin-max"][1]:
                curves = [
                    [
                        [
                            colour_intervals[colour]["Hmin-max"][0],
                            colour_intervals[colour]["Hmin-max"][1],
                        ],
                        [
                            colour_intervals[colour]["Smin-max"][0],
                            colour_intervals[colour]["Smin-max"][0],
                        ],
                    ],
                    [
                        [
                            colour_intervals[colour]["Hmin-max"][0],
                            colour_intervals[colour]["Hmin-max"][1],
                        ],
                        [
                            colour_intervals[colour]["Smin-max"][1],
                            colour_intervals[colour]["Smin-max"][1],
                        ],
                    ],
                ]
            else:

                curves = [
                    [
                        [colour_intervals[colour]["Hmin-max"][0], 2 * np.pi],
                        [
                            colour_intervals[colour]["Smin-max"][0],
                            colour_intervals[colour]["Smin-max"][0],
                        ],
                    ],
                    [
                        [0, colour_intervals[colour]["Hmin-max"][1]],
                        [
                            colour_intervals[colour]["Smin-max"][0],
                            colour_intervals[colour]["Smin-max"][0],
                        ],
                    ],
                    [
                        [colour_intervals[colour]["Hmin-max"][0], 2 * np.pi],
                        [
                            colour_intervals[colour]["Smin-max"][1],
                            colour_intervals[colour]["Smin-max"][1],
                        ],
                    ],
                    [
                        [0, colour_intervals[colour]["Hmin-max"][1]],
                        [
                            colour_intervals[colour]["Smin-max"][1],
                            colour_intervals[colour]["Smin-max"][1],
                        ],
                    ],
                ]
            for curve in curves:
                x = np.linspace(curve[0][0], curve[0][1], 500)
                yy = interp1d(curve[0], curve[1])
                y = yy(x)
                plt.plot(x, y, ":", c=colour_intervals[colour]["RGB"])
        plt.arrow(0, 0, 0, satmax, color="grey", head_width=0.04)
        plt.text(0, 0, "Saturation", rotation=0, c="grey", size=8)

    def plot_coloursheet(figsize):
        plt.ioff()
        plt.rc("text", usetex=False)  # so that LaTeX is not required
        fig = plt.figure()
        fig.set_size_inches(figsize)
        gs = mpl.gridspec.GridSpec(58, 42, wspace=0.1, hspace=0.1)

        # all background???
        ax0 = fig.add_subplot(gs[:, :])
        ax0.set_xticks([])
        ax0.set_yticks([])
        for side in ["top", "bottom", "left", "right"]:
            ax0.spines[side].set_visible(False)

        # plot title/header
        ax1 = fig.add_subplot(gs[0:3, 2:40])
        ax1.set_xticks([])
        ax1.set_yticks([])
        for side in ["top", "bottom", "left", "right"]:
            ax1.spines[side].set_visible(False)
        ax1.text(
            0.5,
            1.1,
            "Colour Calibration Results",
            horizontalalignment="center",
            verticalalignment="center",
            fontsize="xx-large",
            fontweight="bold",
        )
        ax1.text(
            0.5,
            0.65,
            "Calibration performed on " + date,
            horizontalalignment="center",
            verticalalignment="center",
            fontsize="medium",
            fontweight="light",
        )
        ax1.text(
            0.5,
            0.2,
            "Scan file: " + dirpath.name,
            horizontalalignment="center",
            verticalalignment="center",
            fontsize="medium",
            fontweight="light",
        )

        # warn_msg_dots
        ax2 = fig.add_subplot(gs[5:17, 2:40])
        ax2.set_xticks([])
        ax2.set_yticks([])
        ax2.text(
            0.025,
            1,
            " Potential problems with detection of dots ",
            horizontalalignment="left",
            verticalalignment="center",
            fontsize="medium",
            fontweight="bold",
            backgroundcolor="w",
        )
        if len(warn_msg_dots) == 0:
            ax2.text(
                0.5,
                0.5,
                "None",
                horizontalalignment="left",
                verticalalignment="center",
                fontsize="medium",
            )
        else:  # this could do with updating
            for ix, msg in zip(range(len(warn_msg_dots)), warn_msg_dots, strict=True):
                ax2.text(
                    0.02,
                    1.08 - (0.12 * (1 + ix)),
                    msg,
                    horizontalalignment="left",
                    verticalalignment="top",
                    fontsize="x-small",
                )

        # warn_msg_colour
        ax3 = fig.add_subplot(gs[19:31, 2:40])
        ax3.set_xticks([])
        ax3.set_yticks([])
        ax3.text(
            0.025,
            1,
            " Potential problems with recognition of colour ",
            horizontalalignment="left",
            verticalalignment="center",
            fontsize="medium",
            fontweight="bold",
            backgroundcolor="w",
        )
        if len(warn_msg_colour) == 0:
            ax3.text(
                0.5,
                0.5,
                "None",
                horizontalalignment="left",
                verticalalignment="center",
                fontsize="medium",
            )
        else:  # this could do with updating
            for ix, msg in zip(range(len(warn_msg_colour)), warn_msg_colour, strict=True):
                ax3.text(
                    0.02,
                    1.2 - (0.30 * (1 + ix)),
                    msg,
                    horizontalalignment="left",
                    verticalalignment="top",
                    fontsize="x-small",
                )

        # HS intervals plot
        ax5 = fig.add_subplot(gs[36:56, 3:28], projection="polar")
        HS_interval_plot(ax5)

        # colour results
        ax6 = fig.add_subplot(gs[34:58, 28:40])
        ax6.set_xticks([])
        ax6.set_yticks([])
        xlim = 1200
        ylim = 2400
        ax6.set_xlim([0, xlim])
        ax6.set_ylim([0, ylim])
        ax6.set_xticks([])
        ax6.set_yticks([])
        ax6.text(
            xlim * 0.5,
            ylim * 0.8,
            " Marker hues ",
            horizontalalignment="center",
            verticalalignment="center",
            fontsize="medium",
            fontweight="bold",
            backgroundcolor=(0.8, 0.8, 0.8),
        )
        for side in ["top", "bottom", "left", "right"]:
            ax6.spines[side].set_visible(False)
        ax6.text(
            xlim * 0.5,
            ylim * 0.70,
            "Dead:",
            horizontalalignment="center",
            verticalalignment="center",
            fontsize="small",
        )
        ax6.text(
            xlim * 0.5,
            ylim * 0.55,
            "Censored:",
            horizontalalignment="center",
            verticalalignment="center",
            fontsize="small",
        )
        ax6.text(
            xlim * 0.5,
            ylim * 0.40,
            "Carried-over:",
            horizontalalignment="center",
            verticalalignment="center",
            fontsize="small",
        )
        for c in colour_intervals:
            patches = []
            anchor_x, anchor_y = (xlim * 0.5, ylim * 0.35)
            r = 40
            x = np.array([anchor_x] * 3)
            y = np.flip(np.array([anchor_y + ylim * 0.15 * i for i in range(3)]))
            c = [
                colour_intervals[colour_events["dead"]]["RGB"],
                colour_intervals[colour_events["censored"]]["RGB"],
                colour_intervals[colour_events["carried-over"]]["RGB"],
            ]
            for i, (x1, y1) in enumerate(zip(x, y, strict=True)):
                circle = Circle((x1, y1), r, facecolor=c[i])
                patches.append(circle)
            P = PatchCollection(patches, match_original=True)
            ax6.add_collection(P)

        ax4 = fig.add_subplot(gs[33:58, 2:40])
        ax4.set_facecolor([0, 0, 0, 0])
        ax4.set_xticks([])
        ax4.set_yticks([])
        ax4.text(
            0.025,
            1,
            " Hue and Saturation windows to determine event colour ",
            horizontalalignment="left",
            verticalalignment="center",
            fontsize="medium",
            fontweight="bold",
            backgroundcolor="w",
        )
        # saves the current figure into a pdf page
        pdf.savefig(dpi=600, orientation="portrait")
        plt.close()

    def plot_raster(figsize, A4raster, title):
        plt.ioff()
        fig, ax = plt.subplots(nrows=1, ncols=1)
        fig.set_size_inches(figsize)
        ax.set_xticks([])
        ax.set_yticks([])
        ax.imshow(A4raster)
        ax.text(
            A4raster.shape[1] / 6,
            A4raster.shape[0] / 40,
            title,
            horizontalalignment="center",
            verticalalignment="top",
            fontsize="large",
            fontweight="bold",
            backgroundcolor="silver",
        )
        pdf.savefig(dpi=600, orientation="portrait", bbox_inches="tight")
        plt.close()

    # Now to create PDF file:
    figsize = get_figure_size()
    filepath = dirpath / f"ColourCalReport_{date}.pdf"
    with PdfPages(str(filepath)) as pdf:
        plot_coloursheet(figsize)
        plot_raster(figsize, im, "Original scan")
        plot_raster(figsize, colourmask, "Dot/colour detection")

    return filepath


# ------------------------------------------------------------------------------
def pickle_colour(colour_values, colour_intervals, colour_events, date, dirpath):
    colour_data = {"dead": {}, "censored": {}, "carried-over": {}}
    for evt in ["dead", "censored", "carried-over"]:
        colour_data[evt]["HSinterval"] = [
            np.round(colour_intervals[colour_events[evt]]["Hmin-max"], 4).tolist(),
            np.round(colour_intervals[colour_events[evt]]["Smin-max"], 4).tolist(),
        ]
        colour_data[evt]["RGB"] = np.round(colour_intervals[colour_events[evt]]["RGB"], 4).tolist()
    colour_data["otsuT"] = np.min(colour_values.otsuT)
    pkpath = dirpath / f"ColourConfig_{date}.pkl"
    with pkpath.open("wb") as p:
        pk.dump(colour_data, p)


# ==============================================================================
# PROCESS
# ==============================================================================

expected_dots_per_box = 5


def colour_calibration(
    imgpath,
    verbose=False,
    explicit=False,
):
    if verbose:
        print(f"""
# -------------------------------------------------------------
Validating markers from scan:
{str(imgpath)}
""", flush=True)
    # get the data, evaluate adequacy
    bundle1 = extract_colour_values(imgpath, expected_dots_per_box=expected_dots_per_box)
    colour_values, warn_msg_dots, im, colourmask = bundle1
    # obtain colours
    bundle2 = define_colour_interval(colour_values, verbose=verbose)
    colour_intervals, warn_msg_colour, colour_events = bundle2
    if explicit:
        print(colour_intervals_string(colour_intervals), flush=True)
    # test colours
    colourmask, warn_msg_colour = mixed_dots_2mask(
        colour_values,
        colour_intervals,
        colourmask,
        expected_dots_per_box=expected_dots_per_box,
        warn_msg=warn_msg_colour,
    )
    # prepare to make files
    dirpath = generate_calibration_folder()
    date = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    if len(warn_msg_colour) > 0:
        warn_msg_colour = ["\n".join(warn_msg_colour)]
    if len(warn_msg_dots) > 0:
        warn_msg_dots = ["\n".join(warn_msg_dots)]
    # PDF report
    reportpath = generate_colourcal_report(
        colour_values,
        colour_intervals,
        colour_events,
        warn_msg_dots,
        warn_msg_colour,
        date,
        dirpath,
        im,
        colourmask,
    )
    # pickled results
    pickle_colour(colour_values, colour_intervals, colour_events, date, dirpath)

    return reportpath
