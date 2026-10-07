import io
import shutil
import tarfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from pytest import fixture, raises

from .stageiv import (
    StageIV,
    _datetime,
    _download,
    _extract_member,
    _hours,
    _range,
    main,
    stage,
)


@fixture
def config(tmp_path):
    return {
        "platform": {"account": "a", "scheduler": "slurm"},
        "stageiv": {
            "archive_url": "https://example.com/stage4.{yyyymm}.tar",
            "cycles": {
                "start": "2022-02-03T12:00:00",
                "step": 0,
                "stop": "2022-02-03T12:00:00",
            },
            "execution": {
                "batchargs": {"walltime": "00:30:00"},
                "executable": "python -m eagle.data.stageiv stageiv.yaml",
            },
            "leadtimes": {"start": 6, "step": 6, "stop": 12},
            "output_path": str(tmp_path / "grib"),
            "rundir": str(tmp_path),
        },
    }


@fixture
def driverobj(config):
    return StageIV(
        config=config,
        batch=True,
        schema_file=Path(__file__).parent / "stageiv.jsonschema",
    )


def test_driver_name():
    assert StageIV.driver_name() == "stageiv"


def test_config_file(driverobj, tmp_path):
    path = tmp_path / "stageiv.yaml"
    assert not path.exists()
    driverobj.config_file()
    assert path.is_file()


def test_provisioned_rundir(driverobj, readytask, tmp_path):
    with patch.object(driverobj, "config_file", wraps=readytask) as config_file:
        driverobj.provisioned_rundir()
    config_file.assert_called_once_with()
    assert (tmp_path / "runscript.stageiv").is_file()


def test_time_helpers():
    dt = datetime.fromisoformat("2022-02-03T12:00:00")
    delta = timedelta(hours=6)
    assert _datetime(dt) is dt
    assert _datetime("2022-02-03T12:00:00") == dt
    assert _hours(delta) is delta
    assert _hours(6) == delta
    assert _hours("6h") == delta
    assert _hours("06:30:15") == timedelta(hours=6, minutes=30, seconds=15)
    assert list(_range(delta, timedelta(hours=18), delta)) == [
        timedelta(hours=6),
        timedelta(hours=12),
        timedelta(hours=18),
    ]
    assert list(_range(dt, dt, timedelta(0))) == [dt]
    with raises(ValueError, match="positive step"):
        list(_range(dt, dt + delta, timedelta(0)))


def test_download(tmp_path):
    path = tmp_path / "download" / "file"
    response = io.BytesIO(b"data")
    with patch("eagle.data.stageiv.urlopen", return_value=response) as urlopen:
        _download("https://example.com/file", path)
        _download("https://example.com/file", path)
    assert path.read_bytes() == b"data"
    urlopen.assert_called_once_with("https://example.com/file")


def test_download__failure(tmp_path):
    path = tmp_path / "download" / "file"
    with (
        patch("eagle.data.stageiv.urlopen", return_value=io.BytesIO(b"data")),
        patch("eagle.data.stageiv.shutil.copyfileobj", side_effect=OSError("failure")),
        raises(OSError, match="failure"),
    ):
        _download("https://example.com/file", path)
    assert not path.exists()
    assert not list(path.parent.iterdir())


def test_extract_member(tmp_path):
    archive = tmp_path / "archive.tar"
    content = b"precipitation"
    info = tarfile.TarInfo("field.grib2")
    info.size = len(content)
    with tarfile.open(archive, "w") as tar:
        tar.addfile(info, io.BytesIO(content))
    path = tmp_path / "output" / "field.grib2"
    _extract_member(archive, info.name, path)
    _extract_member(archive, info.name, path)
    assert path.read_bytes() == content
    with raises(FileNotFoundError, match=r"missing\.grib2"):
        _extract_member(archive, "missing.grib2", tmp_path / "missing.grib2")


def test_extract_member__directory_and_failure(tmp_path):
    archive = tmp_path / "archive.tar"
    directory = tarfile.TarInfo("directory")
    directory.type = tarfile.DIRTYPE
    content = b"precipitation"
    field = tarfile.TarInfo("field.grib2")
    field.size = len(content)
    with tarfile.open(archive, "w") as tar:
        tar.addfile(directory)
        tar.addfile(field, io.BytesIO(content))
    with raises(FileNotFoundError, match="directory"):
        _extract_member(archive, directory.name, tmp_path / "directory")
    path = tmp_path / "field.grib2"
    with (
        patch("eagle.data.stageiv.shutil.copyfileobj", side_effect=OSError("failure")),
        raises(OSError, match="failure"),
    ):
        _extract_member(archive, field.name, path)
    assert not path.exists()


def test_stage(config, tmp_path):
    cfg = config["stageiv"]
    monthly = tmp_path / "source-monthly.tar"
    daily_paths = []
    dates = [("20220203", "2022020318"), ("20220204", "2022020400")]
    for yyyymmdd, yyyymmddhh in dates:
        daily = tmp_path / f"ST4.{yyyymmdd}"
        content = yyyymmddhh.encode()
        info = tarfile.TarInfo(f"st4_conus.{yyyymmddhh}.06h.grb2")
        info.size = len(content)
        with tarfile.open(daily, "w") as tar:
            tar.addfile(info, io.BytesIO(content))
        daily_paths.append(daily)
    with tarfile.open(monthly, "w") as tar:
        for daily in daily_paths:
            tar.add(daily, arcname=daily.name)

    def download(_url, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            shutil.copyfile(monthly, path)

    with patch("eagle.data.stageiv._download", side_effect=download) as download_mock:
        paths = stage(cfg)
    assert [x.name for x in paths] == [
        "st4_conus.2022020318.06h.grb2",
        "st4_conus.2022020400.06h.grb2",
    ]
    assert download_mock.call_count == 2
    assert [x.read_text() for x in paths] == ["2022020318", "2022020400"]


def test_main(tmp_path, monkeypatch):
    path = tmp_path / "stageiv.yaml"
    path.write_text("rundir: /path/to/run\n")
    monkeypatch.setattr("sys.argv", ["stageiv", str(path)])
    with patch("eagle.data.stageiv.stage") as stage_mock:
        main()
    assert stage_mock.call_args.args[0]["rundir"] == "/path/to/run"


def test_schema(config, logged, tmp_path, validator, with_del, with_set):
    ok = validator(__file__, "stageiv", tmp_path)
    assert ok(config)
    assert not ok(with_del(config, "stageiv"))
    assert logged("'stageiv' is a required property")
    cfg = config["stageiv"]
    for key in [
        "archive_url",
        "cycles",
        "execution",
        "leadtimes",
        "output_path",
        "rundir",
    ]:
        assert not ok({"stageiv": with_del(cfg, key)})
        assert logged(f"'{key}' is a required property")
    assert not ok({"stageiv": with_set(cfg, None, "archive_url")})
    assert logged("is not of type 'string'")
    assert not ok(
        {"stageiv": with_set(cfg, "https://example.com/stage4.tar", "archive_url")}
    )
    assert logged("does not match")
