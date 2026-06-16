from itertools import combinations, groupby as igroupby
from math import atan2, degrees
from pathlib import Path

import numpy as np
from numpy.ma import array as ma_array
np.seterr(divide='ignore', invalid='ignore')

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import pandas as pd
import pymupdf as fitz

from PIL import Image
from zxingcpp import read_barcodes
import cv2
from scipy.ndimage import (
    binary_dilation,
    rotate as nd_rotate,
)
from skimage.feature import canny
from skimage.measure import label, regionprops
from skimage.morphology import disk
from skimage.transform import (
    probabilistic_hough_line,
    rotate as sk_rotate,
)


# ------------------------------------------------------------------------------


def straighten_datasheet(image):
    """To reorient the scanned paper sheet...
    - https://stackoverflow.com/questions/46731947/
    detect-angle-and-rotate-an-image-in-python
    - https://scikit-image.org/docs/dev/auto_examples/edges/
    plot_line_hough_transform.html
    #sphx-glr-auto-examples-edges-plot-line-hough-transform-py
    """

    def quadrantTR(angle):
        if angle > np.pi / 2:
            angle = angle - (np.pi / 2 * np.floor(2 * angle / np.pi))
        elif angle < 0:
            angle = angle + (np.pi / 2 * np.ceil(-2 * angle / np.pi))
        return angle

    grim = np.min(image, axis=2)
    bwim = gray_kde_thresholding(grim)
    bwim2 = binary_dilation(bwim, disk(1))
    bwregions = regionprops(label(bwim2))
    bwareas = [x.area for x in bwregions]
    bwareas.sort()
    largest8 = bwareas[-8:]
    thresh = largest8[0] - 1
    bwim3 = bwareaopen(bwim2, thresh)
    edges = canny((bwim3 * 255).astype(float), 3, 10, 25)
    lines = probabilistic_hough_line(edges, threshold=100, line_length=50, line_gap=3)
    angles = []
    for (x1, y1), (x2, y2) in lines:
        angle = atan2(y2 - y1, x2 - x1)
        angle = quadrantTR(angle)
        angle = degrees(angle)
        angles.append(angle)
    if np.min(angles) != 0:
        rotangle = np.mean([x for x in angles if x > 0 and x < 5])
        image = sk_rotate(image, rotangle, mode="symmetric")
    return image


# ------------------------------------------------------------------------------


def grid_label(
    binary_image,
    best_axis=0,
    kernel="gaussian",
    bandwidth_method="fractional",
    noise=0.05,
    visual=False,
):
    """
    GRID_LABEL takes a binary image and labels its segmented objects
    according to a grid.
    The pattern is assumed to be a square grid (straight or zig-zagging)
    with the imprecision of the positioning of the objects respect to the
    grid being lower than 1/20th of the distance between rows or columns.
    The function uses Kernel Density Estimation smoothing to find either
    the rows or the columns of the grid, and once these are defined, the
    other axis is ordered by simple sorting.

    Parameters:
        binary_image: 2d array
            Boolean array (or numerical populated by 0 or 1 only)
        best_axis: integer
            Axis which will be smoothed by KDE (this will be useful if
            there is a zigzag pattern). Default is 0.
        kernel: string
            Kernel used by the KDE algorithm. In the tests so far the
            performance of the gaussian was the best by far, and using
            this (the default) is strongly encouraged. Other options
            are: 'tophat', 'epanechnikov', 'exponential', 'linear' and
            'cosine'. See also:
            https://scikit-learn.org/stable/modules/generated/
            sklearn.neighbors.KernelDensity.html
        bandwidth_method: string
            Method used to automatically determine the bandwidth for the
            KDE smoothing. All standard binning methods (Scott's and
            Silverman's rules, Cross validation) proved to be return
            either too narrow or too wide bands, so a homemade rule has
            been implemented as default, whereby the bandwidth is
            ~1/20th of the difference between the shortest inter-grid
            distance and the longest within-grid variation. The fraction
            of 1/20 can be changed with the 'noise' parameter. This
            'fractional' method is the default, and the other three are
            also accessible, but not recommended.
            See also:
            https://docs.scipy.org/doc/scipy-0.15.1/reference/generated/
            scipy.stats.gaussian_kde.html
            https://jakevdp.github.io/blog/2013/12/01/
            kernel-density-estimation/
        noise: float, value within (0,1)
            Level of noise accepted for the 'fractional' method, ~1/20th
            (see 'binwidth_method')

    Returns:
        labelled image: 2d array (int)
            Labelled image with objects numbered from left to rigth,
            then top to bottom.
    """
    from copy import copy
    from warnings import warn

    import numpy as np
    from scipy.signal import argrelextrema
    from sklearn.model_selection import GridSearchCV

    # from skimage.measure import label, regionprops
    from sklearn.neighbors import KernelDensity

    # argcheck
    assert isinstance(binary_image, np.ndarray)
    if binary_image.ndim != 2:
        raise ValueError("The input array must be bidimensional")
    assert isinstance(best_axis, int)
    if best_axis not in (0, 1):
        best_axis = 0
        warn("best_axis value was not in (0,1)," + "it was assigned its default value, 0.")
    assert isinstance(kernel, str)
    kernels = ["gaussian", "tophat", "epanechnikov", "exponential", "linear", "cosine"]
    if kernel not in kernels:
        kernel = "gaussian"
        warn("kernel was not valid, it was assigned" + "its default value, 'gaussian'.")
    assert isinstance(bandwidth_method, str)
    methods = ["fractional", "scott", "silverman", "cross_val"]
    if bandwidth_method not in methods:
        bandwidth_method = "fractional"
        warn("bandwidth_method was not valid, it was assigned" + "its default value, 'fractional'.")
    assert isinstance(noise, float)
    if not (noise < 1 and noise > 0):
        noise = 0.05
        warn("noise value was not between (0,1), it was assigned" + "its default value, 0.05.")
    assert isinstance(visual, bool)

    # Determine centroids of objects
    L = label(binary_image)
    blobs = regionprops(L)
    centroids = np.array([x.centroid for x in blobs])
    best_coords = copy(centroids[:, best_axis])
    best_coords.sort(axis=0)
    best_coords = best_coords[:, np.newaxis]
    n_sampling = len(best_coords) * 50
    sampling_coords = np.linspace(0, np.max(best_coords) * 1.1, n_sampling)[:, np.newaxis]

    # Determine bandwidth for KDE
    def bandwidth_selector(vector, bandwidth_method, noise):
        if bandwidth_method == "fractional":
            S = np.sort(np.diff(vector[:, 0]))
            D = np.diff(S)
            idx = np.where(np.max(D) == D)[0][0]
            bw = S[idx] + (np.max(D) - S[idx]) * noise
            return bw
        elif bandwidth_method == "scott":
            return np.power(len(vector), -1.0 / 4)
        elif bandwidth_method == "silverman":
            return np.power(len(vector) * 3.0 / 4.0, -1.0 / 5.0)
        elif bandwidth_method == "cross_val":
            spacing = np.linspace(np.min(vector), np.max(vector), len(vector) * 2)
            grid = GridSearchCV(KernelDensity(), {"bandwidth": spacing}, cv=len(vector))
            grid.fit(vector)
        return grid.best_params_["bandwidth"]

    # KDE smoothing
    bandwidth = bandwidth_selector(best_coords, bandwidth_method, noise)
    kde = KernelDensity(kernel=kernel, bandwidth=bandwidth).fit(best_coords)
    log_dens = kde.score_samples(sampling_coords)
    # Obtain grid limits of coordinate values
    best_axis_centres = sampling_coords[argrelextrema(np.exp(log_dens), np.greater)[0]][:, 0]
    best_axis_lims = best_axis_centres[:-1] + np.diff(best_axis_centres) / 2
    best_axis_lims = np.concatenate(
        (
            np.min(sampling_coords).reshape(
                1,
            ),
            best_axis_lims,
            np.max(sampling_coords).reshape(
                1,
            ),
        )
    )
    # Organise centroids in grid:
    ordered_centroids = []
    z = zip(best_axis_lims[:-1], best_axis_lims[1:])
    for lower, upper in z:
        grid_unit_idx = np.logical_and(
            centroids[:, best_axis] > lower, centroids[:, best_axis] < upper
        )
        grid_unit = centroids[grid_unit_idx, :]
        ordered_centroids.append(grid_unit)
    # Identify the other axis
    s = set((0, 1))
    s.remove(best_axis)
    other_coord = list(s)[0]
    # Sort by order within the grid axis
    ordered_centroids = [c[c[:, other_coord].argsort()] for c in ordered_centroids]
    coordinates = np.array([coord for unit in ordered_centroids for coord in unit])
    # Create the labeled image
    grid_labeled = np.zeros(L.shape).astype(int)
    for blob in blobs:
        idx = np.where(np.all(coordinates == blob.centroid, axis=1))[0][0]
        grid_labeled[blob.coords[:, 0], blob.coords[:, 1]] = idx + 1

    # Visualisation
    if visual:
        # import matplotlib.pyplot as plt
        plt.rcParams["figure.figsize"] = [10, 6]
        fig = plt.figure()
        fig.suptitle("Grid labelling of binary image", fontsize=16)
        ax1 = plt.subplot2grid((2, 2), (0, 0), colspan=2)
        ax1.plot(
            sampling_coords[:, 0],
            np.exp(log_dens),
            "-",
            label=f"Bandwidth = '{np.round(bandwidth, 1)}'",
        )
        ax1.legend(loc="upper left")
        np.random.seed(1)
        height = np.max(np.exp(log_dens))
        ax1.plot(
            best_coords[:, 0],
            -(0.05 * height) - (0.15 * height) * np.random.random(best_coords.shape[0]),
            "+k",
        )
        for thresh in best_axis_lims:
            ax1.axvline(x=thresh)
        ax1.title.set_text(f"Smoothing coordinates, axis={best_axis}")
        ax2 = plt.subplot2grid((2, 2), (1, 0))
        ax2.imshow(L)
        ax2.title.set_text("Original labelling")
        ax3 = plt.subplot2grid((2, 2), (1, 1))
        ax3.imshow(grid_labeled)
        ax3.title.set_text("Square grid labelling")
        plt.subplots_adjust(hspace=0.4)
        plt.show()
    return grid_labeled


# ------------------------------------------------------------------------------


def slice_bbox(BoundingBox):
    """SLICE_BBOX obtains a slice object from the coordinates of a
    skimage.measure.regionprops Bounding Box"""
    s = np.s_[BoundingBox[0] : BoundingBox[2], BoundingBox[1] : BoundingBox[3]]
    return s


# ------------------------------------------------------------------------------


def normalize_img(img, bit_depth, bw=False):
    """
    NORMALIZE_IMG takes a numpy array (intended to contain image data)
    and normalizes the signal values between 0 and (2^bit_depth)-1, with
    bit_depth taking the values 1, 8 or 16. Depending of the value of
    bit_depth and the argument bw, the output will be:

    bit_depth  bw         Max value       Data type
    -----------------------------------------------
     1         False             1        float16
     1         True              1        boolean
     8         either          255        uint8
    16         either        65535        uint16
    """
    if bit_depth not in [1, 8, 16]:
        raise ValueError("The bit depth must take one of the values: 1, 8, 16")
    max_value = np.power(2, bit_depth) - 1
    base = np.float64(img - img.min())
    norm = img.max() - img.min()
    output = base * max_value / norm
    if bit_depth == 1 and not bw:
        output = np.float32(output)
    elif bit_depth == 1 and bw:
        output = np.greater(output, 0)
    elif bit_depth == 8:
        output = np.uint8(output)
    elif bit_depth == 16:
        output = np.uint16(output)
    elif bit_depth == 32:
        output = np.uint32(output)
    return output


# ------------------------------------------------------------------------------


def bwareaopen(bw, sz):
    """
    BWAREAOPEN takes a b/w image and removes the connected areas smaller
    than the specified size. The output is a boolean array.
    """
    L = label(bw)
    props = regionprops(L)  # , ['Area','BoundingBox','Coordinates','Image'])
    bw_open = np.zeros(bw.shape, dtype="int")
    for blob in props:
        if blob.area >= sz:
            s = slice_bbox(blob.bbox)
            # check that the blob is not a background island:
            if np.sum(np.multiply(bw[s], blob.image)) > 0:
                # transfer the blob to the new image
                bw_open[blob.coords[:, 0], blob.coords[:, 1]] = 1
    return normalize_img(bw_open, 1, True)


# ------------------------------------------------------------------------------


def gray_kde_thresholding(greyscale_im, bwth=5, visual=False, out="image"):
    """
    Uses KDE to find peaks of intermediate levels of grey and find upper
    and lower thresholds
    """
    from scipy.signal import argrelextrema
    from sklearn.neighbors import KernelDensity

    intensities = np.sort(greyscale_im.flatten())
    # Kernel Density Estimation - adjusted for a sample of ~2000 vals
    if len(intensities) <= 4000:
        sample_inty = intensities
    else:
        jump = int(len(intensities) / 4000)
        sample_inty = intensities[:, np.newaxis][::jump]
    kde = KernelDensity(kernel="gaussian", bandwidth=bwth).fit(sample_inty)
    # getting the pdf to find peaks
    KDEsampling = np.linspace(0, np.max(intensities), np.max(intensities))[:, np.newaxis]
    density = np.exp(kde.score_samples(KDEsampling))
    # to make sure that the white peak is a peak if it goes upwards all to 255:
    density = np.concatenate(([0], density[1:-1], [0]))
    # finds peaks of abundance of greyscale values
    inty_peaks = KDEsampling[argrelextrema(density, np.greater)[0]][:, 0]
    # gets middle points between peaks
    inty_limits = inty_peaks[:-1] + np.diff(inty_peaks) / 2
    inty_limits = np.concatenate(
        (
            np.min(KDEsampling).reshape(
                1,
            ),
            inty_limits,
            np.max(KDEsampling).reshape(
                1,
            ),
        )
    )
    # get the absolute values of the peaks
    abs_peak_h8s = density[argrelextrema(density, np.greater)[0]]
    # ------------------------------------------------------------------------------

    def dval(density, value):
        return np.where(density == value)[0][0]

    if len(abs_peak_h8s) > 3:
        # get the height of the values respect their environment
        rel_peak_h8s = np.array(
            [
                x
                - np.mean(
                    density[
                        np.max((0, dval(density, x) - 10)) : np.min((255, dval(density, x) + 10))
                    ]
                )
                for x in abs_peak_h8s
            ]
        )
        # consider just the values of the three "higher-than-environ" peaks
        real_peaks = abs_peak_h8s[rel_peak_h8s > sorted(rel_peak_h8s)[-4]]
    elif len(abs_peak_h8s) == 3:
        real_peaks = abs_peak_h8s
    elif len(abs_peak_h8s) == 2:
        # if KDE is too smooth maybe only 2 of black/grey/white peaks found
        bit8_peaks = [dval(density, x) for x in abs_peak_h8s]
        distances = np.diff([0] + bit8_peaks + [255])[0::2]
        # if distance from 0 to first peak is longest the index is zero:
        missing_white_peak = np.where(distances == np.max(distances))[0]
        if missing_white_peak:
            real_peaks = [abs_peak_h8s[1]]
        else:
            real_peaks = [abs_peak_h8s[0]]
    else:
        raise ValueError(f"""These image data do not seem to contain three main
    grayscale levels using KDE with Gaussian kernel, bandwidth={bwth} and {len(sample_inty)} samples.
    Try using Otsu thresholding.""")

    # find width of central peak
    if len(abs_peak_h8s) >= 3:
        # grayscale value of the second peak
        level = np.sort([dval(density, x) for x in real_peaks])[1]
        # now back to inty_limits to get the boundaries around the 2nd peak
        lo_lim = int(np.max(inty_limits[inty_limits < level]))
        hi_lim = int(np.min(inty_limits[inty_limits > level]))
    # abs_peak_h8s can only be >=3 or ==2
    else:
        from scipy.signal import peak_widths
        # real_peaks only has the central one
        central_peak = [dval(density, real_peaks[0])] # central_peak must be 1d-array-like
        results_base = peak_widths(density, central_peak, rel_height=0.85)
        lo_lim = results_base[2]
        hi_lim = results_base[3]
    
    # thresholding
    bwim = np.logical_and(greyscale_im > lo_lim, greyscale_im < hi_lim)
    
    if visual:
        import matplotlib.pyplot as plt
        plt.plot(KDEsampling, density)
        plt.axvline(x=lo_lim, color="k")
        plt.axvline(x=hi_lim, color="k")
        plt.axvline(x=level, color="r")
        plt.show()
    if out == "image":
        return bwim
    elif out == "thresh":
        return (lo_lim, hi_lim)
    elif out == "all":
        return bwim, (lo_lim, hi_lim)


# ------------------------------------------------------------------------------

def zeroed_border(img, w=2):
    out = img.copy()
    out[:w, :] = 0           # top
    out[-w:, :] = 0          # bottom
    out[:, :w] = 0           # left
    out[:, -w:] = 0          # right
    return out

# ------------------------------------------------------------------------------


def isbetween(a, b):
    return a > sorted(b)[0] and a < sorted(b)[-1]


# ------------------------------------------------------------------------------


def iswithinpercent(a, b, p):
    return a > b * (100 - p) / 100 and a < b * (100 + p) / 100


# ------------------------------------------------------------------------------


def tupleintor(t):
    return tuple(int(np.round(x)) for x in t)


# ------------------------------------------------------------------------------


def ma_mean(a, m):
    return np.mean(ma_array(a, mask=~m))


# ------------------------------------------------------------------------------


def selem_frac(QRside, n):
    """
    This is to find proportions to QRside of all the different
    morphological operations. So far we have the following factors:
    - square structuring element for input areas: 26
    - sqare selem for date, rack areas: 20
    - square selem for date box areas: 35 -- also 29...
    """
    return np.ones((int(QRside / n), int(QRside / n)))


# ------------------------------------------------------------------------------


def split_n(alist, n):
    floor = len(alist) // n
    split_list = [alist[n * x : n * x + n] for x in range(floor)]
    split_list.append(alist[n * (floor - 1) + n :])
    return split_list


# ------------------------------------------------------------------------------


def all_equal(iterable):
    "https://stackoverflow.com/a/3844832"
    g = igroupby(iterable)
    return next(g, True) and not next(g, False)


# ------------------------------------------------------------------------------


def is_angle_in_interval(angle, start, end):
    """Checks if an angle is within [start, end] modulo 2*pi."""
    # Normalize everything
    angle = angle % (2 * np.pi)
    start = start % (2 * np.pi)
    end = end % (2 * np.pi)

    if start < end:
        return start <= angle <= end
    else:  # Interval wraps around 2*pi
        return angle >= start or angle <= end


# ------------------------------------------------------------------------------


def read_scan(path : str, *, page=0):
    """
    Read a scan from disk.

    Behaviour:
    ----------
    1) First, try reading the file as a normal raster image using skimage.io.imread.
       If this works, return that array immediately.

    2) If raster read fails, attempt to treat the file as a PDF:
         - Open with PyMuPDF
         - Extract all image XObjects via page.get_images(full=True)
         - Require exactly ONE embedded image (scanner behaviour)
         - Return that image as a NumPy array (uint8)

    3) If neither raster nor PDF image extraction succeeds → raise an error.

    Parameters
    ----------
    path : str or Path
        Input file path.
    page : int
        Page index for PDFs (default 0). Ignored for raster images.

    Returns
    -------
    np.ndarray
        (H, W, 3) uint8 RGB array or (H, W) grayscale array depending
        on what the embedded image actually contains.
    """
    path = Path(path)

    # Try raster read
    try:
        with Image.open(str(path)) as im:
            im.load()  # force full read, not lazy/thumbnail
            arr = np.asarray(im.convert("RGB"))
        return arr
    except Exception:
        pass  # Not a raster image → proceed to PDF logic

    # Try PDF with embedded image extraction
    try:
        doc = fitz.open(str(path))
    except Exception:
        raise RuntimeError(f"File is neither a raster image nor a readable PDF: {path}")

    if page < 0 or page >= doc.page_count:
        raise IndexError(f"Requested page {page+1} out of range (PDF has {doc.page_count} pages).")

    # scanner may create a PDF with multiple overlayed PNGs
    pix = doc[page].get_pixmap()
    pil_img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    doc.close()
    arr = np.asarray(pil_img)

    return arr


# ------------------------------------------------------------------------------


def hue_overlaps(hue1, hue2):
    first_2nd = any([is_angle_in_interval(x, hue1[0], hue1[1]) for x in hue2])
    secnd_1st = any([is_angle_in_interval(x, hue2[0], hue2[1]) for x in hue1])
    return first_2nd | secnd_1st


# ------------------------------------------------------------------------------


def saturation_overlaps(sat1, sat2):
    first_2nd = any([sat1[0] < x < sat1[1] for x in sat2])
    secnd_1st = any([sat2[0] < x < sat2[1] for x in sat1])
    return first_2nd | secnd_1st


# ------------------------------------------------------------------------------


def align_radians(hues: int | np.ndarray):
    """
    Make sure no radian values are across a 2pi border.
    """
    _2pi = 2 * np.pi
    if isinstance(hues, (pd.Series, np.ndarray, list)):
        hues_type = type(hues)
        hues = np.array(hues)
    else:
        raise ValueError()
    hues = hues % _2pi
    hues_2pi = np.round(hues / _2pi)  # 0 or 1 if close to 2pi/0
    # `round` makes division not equal at the 1pi boundary in intervals
    if not all_equal(hues_2pi):
        to_add_2pi = np.abs(hues_2pi - 1)
        if len(hues) == 2 and is_angle_in_interval(np.pi, hues[0], hues[1]):
            pass
        else:
            # it can only be zeros and ones
            to_add_2pi = np.abs(hues_2pi - 1)
            hues += to_add_2pi * _2pi
    return hues_type(hues)

    
# ------------------------------------------------------------------------------

    
def to_gray(u8rgb):
    if u8rgb.ndim == 3:
        return cv2.cvtColor(u8rgb, cv2.COLOR_RGB2GRAY)
    return u8rgb


# ------------------------------------------------------------------------------


def approx_square_orientation(TL, TR, BR, BL):
    """For angles < 90°. Assumes corners are in an overall upright orientation
    or the square does not have relevant internal asymmetries."""
    # observed angles
    thetaT = compute_angle(TL, TR) # should be:  0°
    thetaL = compute_angle(TL, BL) #           +90°
    thetaB = compute_angle(BR, BL) #          +180°
    thetaR = compute_angle(BR, TR) #           -90°, if QR is straight
    orientation = np.round(thetaT, thetaL-90, thetaB-180, thetaR+90)
    return int(np.round(orientation))


# ------------------------------------------------------------------------------


def decode_qr(u8rgb, path: str):

    def try_cv2(u8rgb, path):
        det = cv2.QRCodeDetector()
        gray = to_gray(u8rgb)
        data, points, _ = det.detectAndDecode(gray)
        if not data:
            raise QRcodeError0(path)
        points = np.squeeze(points)
        TL, TR, BR, BL = points  # was qr_corners — probably a bug in the original?
        orientation = approx_square_orientation(TL, TR, BR, BL)
        return data, points, orientation, 'cv2'

    barcodes = read_barcodes(u8rgb)

    if len(barcodes) == 0:
        # nothing detected — fall back to cv2
        return try_cv2(u8rgb, path)

    elif len(barcodes) == 1:
        data = barcodes[0].text
        if not data:
            # detected but not decoded — fall back to cv2
            return try_cv2(u8rgb, path)
        p = barcodes[0].position
        points = np.array([
            [p.top_left.x,     p.top_left.y],
            [p.top_right.x,    p.top_right.y],
            [p.bottom_right.x, p.bottom_right.y],
            [p.bottom_left.x,  p.bottom_left.y],
        ])
        orientation = barcodes[0].orientation
        return data, points, orientation, 'zxing'

    else:
        # more than 1 QR code found
        raise QRcodeError2(path)

    
# ------------------------------------------------------------------------------


def perim2bbox(arr):
    return (
        int(np.round(np.min(arr[:,0]))),
        int(np.round(np.min(arr[:,1]))),
        int(np.round(np.max(arr[:,0]))),
        int(np.round(np.max(arr[:,1])))
    )


# ------------------------------------------------------------------------------


def wrap_angle_deg(angle):
    """Wrap angle to [-180, 180)."""
    angle = (angle + 180) % 360 - 180
    return angle


# ------------------------------------------------------------------------------


def compute_angle(p, q):
    """Angle of vector p→q in degrees."""
    vx, vy = q - p
    return np.degrees(np.arctan2(vy, vx))


# ------------------------------------------------------------------------------


def get_quadrant(point, origin):
    """
    Find quadrant of a point respect to another point following convention of
    numbering from the top-right quadrant, counterclockwise.
    """
    x, y = point[0] - origin[0], point[1] - origin[1]
    if x == 0 or y == 0:
        return None  # Point is on an axis
    if x > 0 and y > 0:
        return 1  # Top-right
    elif x < 0 and y > 0:
        return 2  # Top-left
    elif x < 0 and y < 0:
        return 3  # Bottom-left
    else:
        return 4  # Bottom-right


# ------------------------------------------------------------------------------


def crop_datasheet_from_qr(
    image,
    path: str,
    qr_corners,         # coordinates of the QR code image corners
    orientation,        # a [-180, 180] angle (from xzing) or None (from cv2)
    mm_vertical = 43,   # mm distance between TL and BL corners in A4
    mm_2top    = 4,     # 4 mm of 'margin'
    mm_2right  = 147.5,
    mm_2left   = 7.5,
    mm_2bottom = 231,
):
    """
    image: RGB ndarray [H, W, 3]
    qr_corners: np.array shape (4,2) with cv2/ZXing QR points (centres of aligning squares)
                QR points are expected around the top-left corner of the image
    orientation: either a [-180, 180] angle (if QR was detected as default with zXing)
                 or None (if QR code is detected with cv2)
    """
    
    # Correct coarse rotations (90° multiples)
    point = ( int(np.mean(qr_corners[:,0])),   # QR centre from (x,y)
              -int(np.mean(qr_corners[:,1])) ) # y<0 as it grows 'downwards' in arrays/images
    origin = ( int(image.shape[1]/2),
               -int(image.shape[0]/2) )        # image centre from (y,x)
    quadrant = get_quadrant(point, origin)     # expected 2 for normal orientation
    rot90_k = {2:0, 1:1, 4:2, 3:3}[quadrant]   # {quadrant:CCW rotations needed}
    # correct image and recalculate QR position/orientation
    if rot90_k>0:
        image = np.rot90(image, k=rot90_k)
        _, qr_corners, orientation, _ = decode_qr(image, path)
    
    # Correct small rotations
    if orientation > 0:
        image = nd_rotate(image, orientation, reshape=True)
        _, qr_corners, orientation, _ = decode_qr(image, path)

    # Use position of QR code to crop the DataSheet informative area
    px_vertical = np.linalg.norm(qr_corners[3] - qr_corners[0]) # BL-TL
    px_per_mm = px_vertical / mm_vertical
    # reference coords:
    Ledge_QR, Tedge_QR = np.min(np.round( qr_corners ).astype(np.int16), axis=0)
    Redge_QR, Bedge_QR = np.max(np.round( qr_corners ).astype(np.int16), axis=0)
    crop_bbox = (
        Tedge_QR - int(mm_2top    * px_per_mm),
        Ledge_QR - int(mm_2left   * px_per_mm),
        Bedge_QR + int(mm_2bottom * px_per_mm),
        Redge_QR + int(mm_2right  * px_per_mm)
    )
    # crop_bbox = tuple(
    #     [np.round(x).astype(np.int16) for x in crop_bbox]
    # )
    image_cropped = image[slice_bbox(crop_bbox)]
    return image_cropped


# ------------------------------------------------------------------------------


def avg_qr_side(pts):
    pts = pts.squeeze().astype(float)
    pts = order_points_clockwise(pts)
    d = [np.linalg.norm(pts[i] - pts[(i + 1) % 4]) for i in range(4)]
    return int(np.round(np.mean(d)))

    
# ------------------------------------------------------------------------------

    
def find_stringent_colour_intervals(
    col_vals,
    *,
    lo=2 / 15,  # <-- quantiles adjusted empirically
    hi=13 / 15,  # <-- quantiles adjusted empirically
):
    """
    Obtain intervals of Hue and Saturation values that include conservatively
    segments of one group of ("top", "middle" and "bottom"), following the
    organisation of the Colour Calibration Form. It also captures other statistics
    of each group, like mean and std values.

    Behaviour
    ---------
    `find_stringent_colour_intervals` checks that hue values close to 0 and 1
    do count as a coherent interval using radians and placing every value over
    2pi, and that the `Hmin-max` interval is sorted with min<max, so the interval
    can be expanded later naturally by addition/subtraction.

    Parameters
    ----------
    col_vals : pd.DataFrame
        Input dataframe with colour parameter values in the columns `Saturation`
        and `continuousHue` (in radians) and categories in `Corr_event`.

    Returns
    -------
    colour_intervals : Dict
        Dictionary of dictionaries with keys "top", "middle" and "bottom" and
        within each:
        - number pairs:
            - defining hue interval:                   "Hmin-max"
            - defining the saturation interval:        "Smin-max"
            - defining hue statistics:                 "Hmean-std"
            - defining saturation statistics:          "Smean-std"
        - number triplet defining the mean RGB values: "RGB"
    """

    _2pi = 2 * np.pi

    colour_intervals = {"top": {}, "middle": {}, "bottom": {}}
    for c in colour_intervals:
        # HS values
        dataHue = col_vals[col_vals.Corr_event == c]["continuousHue"]
        # we cannot compare hue radians across 2pi/0, so 'align'
        dataHue = align_radians(dataHue)
        dataSat = col_vals[col_vals.Corr_event == c]["Saturation"]
        # with 80% central data, in case there are outliers
        dataHue_core = dataHue[(dataHue > dataHue.quantile(lo)) & (dataHue < dataHue.quantile(hi))]
        if len(dataHue_core) == 0:
            raise MarkerError(c)
        std_hue_core = np.std(dataHue_core)
        std_sat = np.std(dataSat)
        colour_intervals[c]["Hmin-max"] = [
            (np.min(dataHue_core) % _2pi).tolist(),
            (np.max(dataHue_core) % _2pi).tolist(),
        ]
        colour_intervals[c]["Smin-max"] = [np.min(dataSat).tolist(), np.max(dataSat).tolist()]
        colour_intervals[c]["Hmean-std"] = [
            (np.mean(dataHue_core) % _2pi).tolist(),
            std_hue_core.tolist(),
        ]
        colour_intervals[c]["Smean-std"] = [np.mean(dataSat).tolist(), std_sat.tolist()]
        # final check that 'Hmin-max' min<max or adjust 2pi:
        Hinterval = align_radians(colour_intervals[c]["Hmin-max"])
        Hinterval = np.sort(Hinterval).tolist()

        # RGB part as float
        R = col_vals[col_vals.Corr_event == c]["meanR"].tolist()[0]
        G = col_vals[col_vals.Corr_event == c]["meanG"].tolist()[0]
        B = col_vals[col_vals.Corr_event == c]["meanB"].tolist()[0]
        colour_intervals[c]["RGB"] = [R / 255, G / 255, B / 255]

    return colour_intervals


# ------------------------------------------------------------------------------

    
def colour_overlap(
    colour_intervals,
    verbose=False,
):
    """
    Determine whether two Hue/Saturation intervals overlap.

    Parameters
    ----------
    colour_intervals : Dict
        Dictionary of dictionaries with keys "top", "middle" and "bottom" and
        within each:
        - number pairs:
            - defining hue interval:                   "Hmin-max"
            - defining the saturation interval:        "Smin-max"
            - defining hue statistics:                 "Hmean-std"
            - defining saturation statistics:          "Smean-std"
        - number triplet defining the mean RGB values: "RGB"

    Returns
    -------
    Bool
    """
    overlapping = []
    for c1, c2 in combinations(colour_intervals.keys(), 2):
        hue1 = colour_intervals[c1]["Hmin-max"]
        hue2 = colour_intervals[c2]["Hmin-max"]
        sat1 = colour_intervals[c1]["Smin-max"]
        sat2 = colour_intervals[c2]["Smin-max"]
        overlap = hue_overlaps(hue1, hue2) & saturation_overlaps(sat1, sat2)
        overlapping.append(overlap)
    overlapping = any(overlapping)
    if verbose:
        print(f"""
The colour intervals {"" if overlapping else "DO NOT"} OVERLAP:
Hue1:{hue1}\t\tSat1:{sat1}
Hue2:{hue2}\t\tSat2:{sat2}
""")
    return overlapping


# ------------------------------------------------------------------------------


def angle_distance(a, b):
    if (a > b) & is_angle_in_interval(0, a, b):
        return b + (2 * np.pi) - a
    else:
        return b - a


# ------------------------------------------------------------------------------

    
def order_points_clockwise(pts):
    pts = np.array(pts)

    # 1. Compute centroid
    c = np.mean(pts, axis=0)

    # 2. Compute angle of each point around centroid
    angles = np.arctan2(pts[:,1] - c[1], pts[:,0] - c[0])

    # 3. Sort by angle (clockwise)
    sort_idx = np.argsort(angles)
    ordered = pts[sort_idx]

    return ordered


# ------------------------------------------------------------------------------


def extend_intervals(
    colour_intervals,
    span_sat_n=1,
    verbose=False,
):
    # check initial intervals do not overlap
    if colour_overlap(colour_intervals):
        raise OverlapError1(colour_intervals)

    # expand Hues
    # starting points of hue intervals by group
    hue_starts = {k: colour_intervals[k]["Hmin-max"][0] for k in colour_intervals}
    # order by angle of group hues
    hue_order = [
        key for x in sorted(hue_starts.values()) for key, val in hue_starts.items() if val == x
    ]
    # new boundaries 1/3rd of distance between max/min vals
    _2pi = 2 * np.pi
    for x, k in enumerate(hue_order):
        kpos = hue_order[(x + 1) % len(hue_order)]
        kpre = hue_order[(x + 2) % len(hue_order)]
        kmin = colour_intervals[k]["Hmin-max"][0] % _2pi
        kmax = colour_intervals[k]["Hmin-max"][1] % _2pi
        kpos_min = colour_intervals[kpos]["Hmin-max"][0] % _2pi
        kpre_max = colour_intervals[kpre]["Hmin-max"][1] % _2pi
        klower = (kmin - angle_distance(kpre_max, kmin) / 3) % _2pi
        kupper = (kmax + angle_distance(kmax, kpos_min) / 3) % _2pi
        colour_intervals[k]["Hmin-max"] = align_radians([klower, kupper])

    # expand Saturation
    for k in colour_intervals:
        Smin = colour_intervals[k]["Smin-max"][0]
        Smax = colour_intervals[k]["Smin-max"][1]
        expansion = (Smax - Smin) * span_sat_n
        Smin = max(Smin - expansion, 0)
        Smax = min(Smax + expansion, 1)
        colour_intervals[k]["Smin-max"] = [Smin, Smax]

    # final test before returning
    if colour_overlap(colour_intervals):
        raise OverlapError2(colour_intervals)

    return colour_intervals


# ------------------------------------------------------------------------------
def match_dot2colour(h, s, colour_intervals):
    # assume there can be no multiple matches
    match = None
    for c in colour_intervals.keys():
        Hmin = colour_intervals[c]["Hmin-max"][0]
        Hmax = colour_intervals[c]["Hmin-max"][1]
        Smin = colour_intervals[c]["Smin-max"][0]
        Smax = colour_intervals[c]["Smin-max"][1]
        inhue = is_angle_in_interval(h, Hmin, Hmax)
        insat = Smin < s < Smax
        if inhue & insat:
            match = c
    return match if match else "unmatched"


# ------------------------------------------------------------------------------
def evaluate_dots(colour_intervals, col_vals):
    col_vals["event_match"] = None
    for ix, row in col_vals.iterrows():
        match = match_dot2colour(row.continuousHue, row.Saturation, colour_intervals)
        col_vals.loc[ix, "event_match"] = match
    return col_vals


# ------------------------------------------------------------------------------
def dots_recovered(col_vals):
    tt = col_vals.event_match == col_vals.Corr_event
    return all(tt)


# ------------------------------------------------------------------------------

    
def colour_intervals_string(colour_intervals):
    s = """
    \t  Atop  \t\t Middle \t\t Bottom
----\t--------\t\t--------\t\t--------
Hue:\t{a}\t\t{b}\t\t{c}
Sat:\t{d}\t\t{e}\t\t{f}
""".format(
        a=np.round(colour_intervals["top"]["Hmin-max"], 2),
        b=np.round(colour_intervals["middle"]["Hmin-max"], 2),
        c=np.round(colour_intervals["bottom"]["Hmin-max"], 2),
        d=np.round(colour_intervals["top"]["Smin-max"], 2),
        e=np.round(colour_intervals["middle"]["Smin-max"], 2),
        f=np.round(colour_intervals["bottom"]["Smin-max"], 2),
    )
    return s


# ------------------------------------------------------------------------------


def pad_rgb(img, padding=10):
    ym = np.where(img.sum(axis=2)==np.max(img.sum(axis=2)))[0][0]
    xm = np.where(img.sum(axis=2)==np.max(img.sum(axis=2)))[1][0]
    brightest = img[ym, xm, :]
    padded_layers = []
    for ix, val in zip(range(3), brightest):
        padded = np.pad(img[:,:,ix], padding, 'constant', constant_values=val)
        padded_layers.append(padded)
    return np.dstack(padded_layers)


# ------------------------------------------------------------------------------
# Visualisation for QA / debugging

def qa_mosaic(
    obs: pd.DataFrame,
    infodict: dict,
    storepath: str,
    mode: str = "single_tube",      # 'single_tube' | 'all_tubes' | 'rack_area'
    tube_no: int | None = None,     # used by 'single_area' and 'all_areas'
    stratum: tuple | None = None,   # e.g. (var1_lvl, var2_lvl) — None means all
    page: int | None = None,        # used by 'rack'
    rack_area_no: int | None = None,    # used by 'rack'
    crop_level: str = "tube",       # 'rack' | 'tube' | 'full'
    n_cols: int | None = None,      # override auto column count
    figsize_mm: tuple = (210, 297), # A4 portrait; swap for landscape
    dpi: int = 150,
    title: str | None = None,
    save_path: str | Path | None = None,
    obs_colors: dict | None = None,
):
    """
    Produce a temporal mosaic of observation-area crops for QA / debugging.

    Modes
    -----
    single_tube : time-ordered panels for one tube_no or racks within an optional stratum
    all_tubes   : one row per tube (1-12), one column per time point, within a stratum
    rack        : all tubes within a specific page + rack area combination
    """
    img_dir = Path(storepath) / 'processed_datasheets'

    # ── 1. filter ────────────────────────────────────────────────────────────
    mask = pd.Series(True, index=obs.index)

    if stratum is not None:
        v1name = infodict["var1"]["var1_name"]
        v2name = infodict["var2"]["var2_name"]
        v1, v2 = stratum
        mask &= (obs[v1name] == v1) & (obs[v2name] == v2)

    if mode == "single_tube":
        if tube_no is None:
            raise ValueError("tube_no is required for mode='single_tube'")
        mask &= obs["Tube_no."] == tube_no

    elif mode == "all_tubes":
        pass

    elif mode == "rack_area":
        if page is None or rack_area_no is None:
            raise ValueError("`page` and `rack_area_no` are required for mode='rack_area'")
        mask &= (
            (obs["Page_no."] == page)
            & (obs["Rack_no."] == rack_area_no)
        )

    sub = obs[mask].copy()
    if sub.empty:
        raise ValueError("No rows match the given filters.")

    # ── 2. sort temporally ───────────────────────────────────────────────────
    sort_cols = ["Days_int", "Page_no.", "Rack_no.", "Tube_no."]
    sort_cols = [c for c in sort_cols if c in sub.columns]
    sub = sub.sort_values(sort_cols).reset_index(drop=True)

    # ── 3. build panel list ──────────────────────────────────────────────────
    if mode == "all_tubes":
        panels = _panels_all_tubes(sub, img_dir, crop_level)
    else:
        panels = [_make_panel(row, img_dir, crop_level)
                  for _, row in sub.iterrows()]

    # ── 4. layout ────────────────────────────────────────────────────────────
    n = len(panels)
    if n_cols is None:
        n_cols = max(1, int(np.ceil(np.sqrt(n * 1.4))))
    n_rows = int(np.ceil(n / n_cols))

    w_in = figsize_mm[0] / 25.4
    h_in = figsize_mm[1] / 25.4
    fig, axes = plt.subplots(
        n_rows,
        n_cols,
        figsize=(w_in, h_in),
        dpi=dpi,
        squeeze=False,
    )
    
    default_colors = {
        "Dead": "#e6194b", "Censored": "#3cb44b",
        "CarriedOver": "#4363d8", "unassigned": "#f58231",
    }
    colors = {**default_colors, **(obs_colors or {})}

    for idx, panel in enumerate(panels):
        r, c = divmod(idx, n_cols)
        ax = axes[r][c]
        ax.imshow(panel["crop"])
        ax.set_title(_panel_title(panel), fontsize=6, pad=2)
        _draw_overlays(ax, panel, colors)
        ax.axis("off")

    for idx in range(n, n_rows * n_cols):
        r, c = divmod(idx, n_cols)
        axes[r][c].set_visible(False)

    if title is None:
        title = _auto_title(mode, tube_no, stratum, page, rack_area_no)
    fig.suptitle(title, fontsize=9, y=0.995)
    plt.tight_layout(rect=[0, 0, 1, 0.993])

    if save_path:
        fig.savefig(save_path, dpi=dpi, bbox_inches="tight")
        print(f"Saved → {save_path}")
    else:
        plt.show()

    return fig, axes


# ── helpers ───────────────────────────────────────────────────────────────────


def _unpack_slice(s) -> tuple[int, int]:
    """Accept either a slice object or a (start, stop) tuple."""
    if isinstance(s, slice):
        return s.start, s.stop
    return s[0], s[1]


def _crop_from_row(img_array: np.ndarray, row: pd.Series, crop_level: str) -> np.ndarray:
    slice_col_priority = {
        "rack_area": "Rack_slice",
        "data_area": "Area_slice",
        "tube":      "Tube_slice",
        "full":      None,
    }
    col = slice_col_priority.get(crop_level)
    if col and col in row and row[col] is not None:
        slc = row[col]
        row_slc = slice(*slc[0]) if isinstance(slc[0], tuple) else slc[0]
        col_slc = slice(*slc[1]) if isinstance(slc[1], tuple) else slc[1]
        return img_array[row_slc, col_slc]
    return img_array


def _make_panel(row: pd.Series, img_dir: Path, crop_level: str) -> dict:
    filepath = img_dir / row["FileName"]
    img = read_scan(str(filepath))
    crop = _crop_from_row(img, row, crop_level)
    return {
        "crop":         crop,
        "row":          row,
        "tube":         row.get("Tube_no."),
        "timeint":      row.get("Days_int"),
        "page":         row.get("Page_no."),
        "rack":         row.get("Rack_no."),
        "observations": {
            "Dead":        row.get("Dead"),
            "Censored":    row.get("Censored"),
            "CarriedOver": row.get("CarriedOver"),
            "unassigned":  row.get("unassigned"),
        },
    }


def _panels_all_tubes(sub, img_dir, crop_level):
    panels = []
    for _, row in sub.iterrows():
        panels.append(_make_panel(row, img_dir, crop_level))
    return panels


def _panel_title(panel: dict) -> str:
    parts = []
    if panel["tube"] is not None:
        parts.append(f"tube {panel['tube']}")
    if panel["timeint"] is not None:
        parts.append(f"t+{panel['timeint']}d")
    if panel["page"] is not None:
        parts.append(f"p{panel['page']}")
    obs_summary = ", ".join(
        f"{k}={v}" for k, v in panel["observations"].items() if v
    )
    if obs_summary:
        parts.append(obs_summary)
    return "  |  ".join(parts)


def _draw_overlays(ax, panel: dict, colors: dict):
    row = panel["row"]
    crop = panel["crop"]
    H, W = crop.shape[:2]

    obs_slice_map = {
        "Dead":        "Dead_slices",
        "Censored":    "Censored_slices",
        "CarriedOver": "CarriedOver_slices",
        "unassigned":  "unassigned_slices",
    }

    area_slc = row.get("Tube_slice")
    row_offset = _unpack_slice(area_slc[0])[0] if area_slc is not None else 0
    col_offset = _unpack_slice(area_slc[1])[0] if area_slc is not None else 0

    for obs_key, slc_col in obs_slice_map.items():
        if slc_col not in row or row[slc_col] is None:
            continue
        val = panel["observations"].get(obs_key)
        if not val:
            continue
    
        slc_list = row[slc_col]
        if not isinstance(slc_list, (list, tuple)):
            slc_list = [slc_list]          # wrap single slice for uniform handling
    
        for slc in slc_list:
            r0, r1 = _unpack_slice(slc[0])
            c0, c1 = _unpack_slice(slc[1])
            r0 -= row_offset
            r1 -= row_offset
            c0 -= col_offset
            c1 -= col_offset
            color = colors.get(obs_key, "white")
            rect = mpatches.Rectangle(
                (c0, r0), c1 - c0, r1 - r0,
                linewidth=0.8, edgecolor=color,
                facecolor="none", linestyle="--",
            )
            ax.add_patch(rect)
            ax.text(c0 + 2, r0 + 2, obs_key, fontsize=4,
                    color=color, va="top", ha="left")


def _auto_title(mode, tube_no, stratum, page, rack_area_no) -> str:
    parts = [f"mode: {mode}"]
    if tube_no is not None:
        parts.append(f"tube {tube_no}")
    if stratum is not None:
        parts.append(f"V1={stratum[0]}, V2={stratum[1]}")
    if page is not None:
        parts.append(f"page {page}, rack {rack_area_no}")
    return "  ·  ".join(parts)


# ------------------------------------------------------------------------------
# ERRORS


class SubAreaDetectionError(Exception):
    def __init__(self, n, m, p):
        print(f"""
SEGMENTATION ERROR:
The program cannot detect correctly the 2 regions where the date and
events are recorded for data area no. {n}. The program detects {m}
regions, some of which may deviate from their expected size by {p}%.
Please make sure that this DataSheet is not defaced/badly scanned.
            """, flush=True)


class BoxDetectionError(Exception):
    def __init__(self, n, m):
        print(f"""
SEGMENTATION ERROR:
The program cannot detect correctly the 7 tick boxes for date
intervals in data area no. {n}. The program detects {m} boxes.
Please make sure that this DataSheet is not defaced/badly scanned.
            """, flush=True)


class UBoxDetectionError1(Exception):
    def __init__(self, n, m):
        print(f"""
SEGMENTATION ERROR:
The program detects too few unticked boxes in data input area no. {n},
where the program detects {m} boxes. Please make sure that this DataSheet
is not defaced/badly scanned or that you ticked too many boxes.
        """, flush=True)


class UBoxDetectionError2(Exception):
    def __init__(self, n, m):
        print(f"""
SEGMENTATION ERROR:
The program detects too many unticked boxes in data input area no. {n},
where the program detects {m} boxes.
Please make sure that this DataSheet is not defaced/badly scanned.
        """, flush=True)


class DateDetectionError(Exception):
    def __init__(self, n, m):
        print(f"""
SEGMENTATION ERROR:
The program cannot detect correctly which date box was ticked in the
data area no. {m}. There should be 1 box ticked, but the program
detects {n}.
Please make sure that this DataSheet is not defaced/badly scanned.
        """, flush=True)


class TubesDetectionError(Exception):
    def __init__(self, rack, expected, actual_found):
        print(f"""
SEGMENTATION ERROR:
The program cannot detect correctly segment objects in the rack in
input area no. {rack}.
{expected} tubes are expected, the program finds {actual_found}.
Please make sure that this DataSheet is not defaced/badly scanned.
        """, flush=True)


class InputAreaDetectionError(Exception):
    def __init__(self, n, m, p):
        print(f"""
SEGMENTATION ERROR:
The program cannot detect correctly the eight data collection areas
in the DataSheet. The program detects {n} areas, but {m} of them differ
from the expected size by at least {p}%.
Please make sure that this DataSheet is not defaced/badly scanned.
        """, flush=True)


class MetadataError(Exception):
    def __init__(self, conflict, path):
        print(f"""
SEGMENTATION ERROR:
The metadata from this DataSheet file does not correspond to this
experiment. The analysis cannot proceed. The problematic file is:\n
'{path}'.
The conflicting fields are: {", ".join(conflict)}.
Please make sure that this file comes from a printout of this
Experiment DataSheet file.
        """, flush=True)


class QRcodeError0(Exception):
    def __init__(self, path):
        print(f"""
SEGMENTATION ERROR:
The QR code from this DataSheet could not be identified or could not
be read read. The analysis cannot proceed. The problematic file is:\n
'{path}'.
QR code recognition is sensitive to angle (>0.5 deg) and
resolution (<120dpi, no compression). Try to re-scan the DataSheet
with better quality and/or in a more straight position.
Please make sure that this file comes from a printout of this
Experiment DataSheet file.
""", flush=True)


class QRcodeError2(Exception):
    def __init__(self, path):
        print(f"""
SEGMENTATION ERROR:
More than one QR codes are detected in this scan. It is likely it does 
not correspond to a drosben DataSheet. The problematic file is:\n
'{path}'.
Please review this scan manually.
""", flush=True)
    
    
class DSError1(Exception):
    def __init__(self, path):
        print(f"""
DATASHEET ERROR 1:
No date intervals for this DataSheet could be determined. The
analysis cannot proceed. The problematic file is:\n
'{path}'.
Please make sure that this DataSheet is not defaced/badly scanned.
""", flush=True)


class DSError2(Exception):
    def __init__(self, path, ix):
        print(f"""
DATASHEET ERROR 2:
No date intervals for this DataSheet could be determined. The
analysis cannot proceed. The problematic file is:\n
'{path}',
and the undetermined data box is no. {ix+1}.
Please make sure that this DataSheet is not defaced/badly scanned.
""", flush=True)


class MarkerError(Exception):
    def __init__(self, c: str):
        print(f"""
MARKER ERROR:
There are no useful Hue or Saturation values to determine the colour at the {c} of the form.
This is probably because it is black (or very dark). You must not use this marker.
""", flush=True)


class OverlapError1(Exception):
    def __init__(self, colour_intervals):
        print(f"""
Colour interval overlap error:
The Hue/Saturation value intervals that contain the markers for each of the colours overlap.
{colour_intervals_string(colour_intervals)}
There is no confidence in this marker pen combination to compile data with the Drosben method.
Do not use them.
""", flush=True)


class OverlapError2(Exception):
    def __init__(self, colour_intervals):
        print(f"""
Colour interval overlap error:
Expansion of Hue/Saturation intervals to detect distinct events has led to overlap:
{colour_intervals_string(colour_intervals)}
This is probably a bug caused by a use case that was not foreseen.
Please try a different marker pen set or report the problem through GitHub.
        """, flush=True)
