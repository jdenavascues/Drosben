# example usage:  python 02_Initialise_Experiment_from_Excel.py ../tests/data/experiment_sample.xlsx

import argparse
from pathlib import Path
from drosben.experiment.info import infodict_from_excel
from drosben.experiment.manager import Experiment
from drosben.image.process import (
    generate_experiment_folder,
    initialize_experiment_files
)

def intialise_experiment(xlspath):
    
    xlspath = Path(xlspath)
    print(f'Initialising experiment with metadata from {xlspath}')
    infodict, errors = infodict_from_excel(xlspath)
    print(errors, '\n',
          f"experiment: {infodict['exp_name']}\n",
          f"variable 1 is {infodict['var1']['var1_name']}\n",
          f"variable 2 is {infodict['var2']['var2_name']}"
         )
    store = generate_experiment_folder(infodict)
    initialize_experiment_files(infodict, store)
    print(f'Experiment folder created at:\n{store}')
    X = Experiment(storepath=store)

description = "This script generates a directory to contain all raw and processed data for an experiment as well as the initial PDF files to label the racks and collect data using a trio of colour marker pens."
help_msg = "Path to the Excel file containing the experimental details."

if __name__ == "__main__":
    
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("xlspath", help=help_msg)
    args = parser.parse_args()
    if Path(args.xlspath).is_file():
        intialise_experiment(args.xlspath)
    else:
        raise ValueError(f"{args.xlspath} is not a file")