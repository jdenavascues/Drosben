# example usage:  python 01_First_Use_Colour_Calibration.py ../tests/data/RGB.pdf

import argparse
from pathlib import Path
import warnings
from drosben.image.colourcal import colour_calibration

def evaluate_pens(calpath):
    
    calpath = Path(calpath)
    print(f'Analysing pen markers from scan file {calpath}')
    warnings.filterwarnings('ignore', category = FutureWarning)
    reportpath = colour_calibration(calpath)
    print(f'Results have been saved in:\n{reportpath}')

description = "This script evaluates the suitability of a pen marker trio for recording deaths, censorings and carry-overs in Drosben datasheets."
help_msg = "Path to the scanned colour calibration form file."

if __name__ == "__main__":
    
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("calpath", help=help_msg)
    args = parser.parse_args()
    if Path(args.calpath).is_file():
        evaluate_pens(args.calpath)
    else:
        raise ValueError(f"{args.calpath} is not a file")