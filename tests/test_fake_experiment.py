import os

from drosben.experiment.manager import Experiment
from drosben.image.process import (
    generate_experiment_folder,
    generate_fake_infodict,
    initialise_experiment_files,
)


def test_fake_experiment(tmp_path):
    info = generate_fake_infodict(anon=True, varno=2)
    # temporarily override HOME to avoid polluting user profile
    old_home = os.environ.get("HOME")
    os.environ["HOME"] = str(tmp_path)
    try:
        storepath = generate_experiment_folder(info)
        initialise_experiment_files(info, storepath)
        X = Experiment(storepath=storepath)
        assert X.sequence is not None
        assert os.path.isdir(storepath)
    finally:
        if old_home is not None:
            os.environ["HOME"] = old_home
