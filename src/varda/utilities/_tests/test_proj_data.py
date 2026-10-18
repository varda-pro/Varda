import sqlite3
from pathlib import Path

from varda.utilities.proj_data import OPT_OUT_VARIABLE, pinProjData


def _projFolder(root: Path, name: str, layout=(1, 3), iau=True) -> Path:
    folder = root / name
    folder.mkdir()
    db = sqlite3.connect(folder / "proj.db")
    db.execute("CREATE TABLE metadata (key TEXT, value TEXT)")
    db.executemany(
        "INSERT INTO metadata VALUES (?, ?)",
        [
            ("DATABASE.LAYOUT.VERSION.MAJOR", str(layout[0])),
            ("DATABASE.LAYOUT.VERSION.MINOR", str(layout[1])),
        ],
    )
    db.execute("CREATE TABLE geodetic_crs (auth_name TEXT, code TEXT)")
    db.execute(
        "INSERT INTO geodetic_crs VALUES (?, '1')", ("IAU_2015" if iau else "EPSG",)
    )
    db.commit()
    db.close()
    return folder


def _gdalFolder(root: Path, name: str, files=("gdalvrt.xsd",)) -> Path:
    folder = root / name
    folder.mkdir()
    for f in files:
        (folder / f).write_text("")
    return folder


def test_older_layout_is_overridden(tmp_path):
    bundled, old = _projFolder(tmp_path, "b"), _projFolder(tmp_path, "o", (1, 2))
    environ = {"PROJ_DATA": str(old), "PROJ_LIB": str(old)}
    messages = pinProjData(environ, bundled, None)
    assert environ == {"PROJ_DATA": str(bundled)}
    assert len(messages) == 1 and "older" in messages[0]


def test_missing_iau_is_overridden(tmp_path):
    bundled, noIau = _projFolder(tmp_path, "b"), _projFolder(tmp_path, "n", iau=False)
    environ = {"PROJ_LIB": str(noIau)}
    messages = pinProjData(environ, bundled, None)
    assert environ == {"PROJ_DATA": str(bundled)}
    assert "IAU_2015" in messages[0]


def test_equal_and_newer_with_iau_are_kept(tmp_path):
    bundled = _projFolder(tmp_path, "b")
    for name, layout in (("same", (1, 3)), ("newer", (1, 4)), ("major", (2, 0))):
        folder = _projFolder(tmp_path, name, layout)
        environ = {"PROJ_DATA": str(folder)}
        assert pinProjData(environ, bundled, None) == []
        assert environ == {"PROJ_DATA": str(folder)}


def test_folder_without_database_is_overridden(tmp_path):
    bundled, empty = _projFolder(tmp_path, "b"), tmp_path / "empty"
    empty.mkdir()
    environ = {"PROJ_DATA": str(empty)}
    pinProjData(environ, bundled, None)
    assert environ["PROJ_DATA"] == str(bundled)


def test_opt_out_leaves_everything(tmp_path):
    bundled, old = _projFolder(tmp_path, "b"), _projFolder(tmp_path, "o", (1, 2))
    environ = {OPT_OUT_VARIABLE: "1", "PROJ_DATA": str(old)}
    assert pinProjData(environ, bundled, None) == []
    assert environ["PROJ_DATA"] == str(old)


def test_unset_is_untouched(tmp_path):
    environ = {}
    bundled, gdal = _projFolder(tmp_path, "b"), _gdalFolder(tmp_path, "g")
    assert pinProjData(environ, bundled, gdal) == []
    assert environ == {}


def test_gdal_data_lacking_schemas_is_overridden(tmp_path):
    (tmp_path / "pkg").mkdir()
    bundled = _gdalFolder(tmp_path / "pkg", "gdal_data")
    foreign = _gdalFolder(tmp_path, "foreign", files=())
    environ = {"GDAL_DATA": str(foreign)}
    messages = pinProjData(environ, None, bundled)
    assert environ["GDAL_DATA"] == str(bundled) and "gdalvrt.xsd" in messages[0]


def test_complete_foreign_gdal_data_is_kept(tmp_path):
    (tmp_path / "pkg").mkdir()
    bundled = _gdalFolder(tmp_path / "pkg", "gdal_data")
    foreign = _gdalFolder(tmp_path, "foreign")
    environ = {"GDAL_DATA": str(foreign)}
    assert pinProjData(environ, None, bundled) == []
