"""Varda uses the PROJ/GDAL data bundled with its own packages, even when the
shell exports another installation's (e.g. conda base's PROJ_DATA): a
mismatched proj.db made PROJ fail and crashed Varda."""

import os
import subprocess
import sys

from varda.utilities.proj_data import bundledProjData


def test_foreign_proj_and_gdal_data_dirs_are_ignored():
    env = dict(
        os.environ,
        PROJ_DATA="/somewhere/else/share/proj",
        GDAL_DATA="/somewhere/else/share/gdal",
        PROJ_LIB="/somewhere/else/share/proj",
    )
    code = (
        "import os, varda; "
        "print(os.environ.get('PROJ_DATA'), os.environ.get('GDAL_DATA'), os.environ.get('PROJ_LIB'))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    projData, gdalData, projLib = out.stdout.split()[-3:]
    assert "else" not in projData and "else" not in gdalData and projLib == "None"


def test_usable_proj_data_is_kept():
    inside = str(bundledProjData())
    env = dict(os.environ, PROJ_DATA=inside)
    code = "import os, varda; print(os.environ.get('PROJ_DATA'))"
    out = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.split()[-1] == inside
