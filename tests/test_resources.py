from importlib.resources import files

from drosben import resources


def test_template_exists():
    p = files(resources) / "rack_labels_template.pdf"
    assert p.is_file(), "Template PDF not found in package resources"
