from __future__ import annotations

import argparse
import shutil
import tarfile
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import TYPE_CHECKING, TypeVar
from urllib.request import urlopen

from iotaa import Asset, collection, task  # provided by uwtools
from uwtools.api.config import get_yaml_config
from uwtools.api.driver import DriverTimeInvariant

if TYPE_CHECKING:
    from collections.abc import Iterator

_Time = TypeVar("_Time", datetime, timedelta)


class StageIV(DriverTimeInvariant):
    """
    Stage native six-hour CONUS Stage IV precipitation files.
    """

    @task
    def config_file(self):
        """
        The Stage IV config, provisioned to the run directory.
        """
        path = self.rundir / "stageiv.yaml"
        yield self.taskname("config")
        yield Asset(path, path.is_file)
        yield None
        get_yaml_config(self.config).dump(path)

    @classmethod
    def driver_name(cls) -> str:
        return "stageiv"

    @collection
    def provisioned_rundir(self):
        """
        Run directory provisioned with all required content.
        """
        yield self.taskname("provisioned run directory")
        yield [self.config_file(), self.runscript()]

    @property
    def _runscript_path(self) -> Path:
        return self.rundir / "runscript.stageiv"


def _datetime(value: datetime | str) -> datetime:
    return value if isinstance(value, datetime) else datetime.fromisoformat(value)


def _hours(value: int | str | timedelta) -> timedelta:
    if isinstance(value, timedelta):
        return value
    if isinstance(value, int):
        return timedelta(hours=value)
    parts = [int(x) for x in value.split(":" if ":" in value else "h") if x]
    if value.endswith("h"):
        return timedelta(hours=parts[0])
    parts.extend([0] * (3 - len(parts)))
    return timedelta(hours=parts[0], minutes=parts[1], seconds=parts[2])


def _range(start: _Time, stop: _Time, step: timedelta) -> Iterator[_Time]:
    if step <= timedelta(0):
        if start != stop:
            msg = "A positive step is required when start and stop differ"
            raise ValueError(msg)
        yield start
        return
    value = start
    while value <= stop:
        yield value
        value += step


def validtimes(config: dict) -> list[datetime]:
    """
    Return unique forecast valid times requested by the EAGLE configuration.
    """
    cycles = config["cycles"]
    leadtimes = config["leadtimes"]
    cycle_values = _range(
        _datetime(cycles["start"]),
        _datetime(cycles["stop"]),
        _hours(cycles["step"]),
    )
    leadtime_values = tuple(
        _range(
            _hours(leadtimes["start"]),
            _hours(leadtimes["stop"]),
            _hours(leadtimes["step"]),
        )
    )
    return sorted(
        {cycle + leadtime for cycle in cycle_values for leadtime in leadtime_values}
    )


def _download(url: str, path: Path) -> None:
    if path.is_file():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with (
        urlopen(url) as source,  # noqa: S310 - schema restricts this to HTTPS.
        NamedTemporaryFile(dir=path.parent, delete=False) as target,
    ):
        tmp = Path(target.name)
        try:
            shutil.copyfileobj(source, target)
            tmp.replace(path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise


def _extract_member(archive: Path, member: str, path: Path) -> None:
    if path.is_file():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as tar:
        try:
            source = tar.extractfile(member)
        except KeyError as e:
            msg = f"Member {member} not found in {archive}"
            raise FileNotFoundError(msg) from e
        if source is None:
            msg = f"Member {member} not found in {archive}"
            raise FileNotFoundError(msg)
        with source, NamedTemporaryFile(dir=path.parent, delete=False) as target:
            tmp = Path(target.name)
            try:
                shutil.copyfileobj(source, target)
                tmp.replace(path)
            except BaseException:
                tmp.unlink(missing_ok=True)
                raise


def stage(config: dict) -> list[Path]:
    """
    Download monthly archives and extract requested six-hour CONUS fields.
    """
    rundir = Path(config["rundir"])
    output = Path(config["output_path"])
    results = []
    for validtime in validtimes(config):
        yyyymm = validtime.strftime("%Y%m")
        yyyymmdd = validtime.strftime("%Y%m%d")
        yyyymmddhh = validtime.strftime("%Y%m%d%H")
        monthly = rundir / "archives" / f"stage4.{yyyymm}.tar"
        daily = rundir / "daily" / f"ST4.{yyyymmdd}"
        result = output / f"st4_conus.{yyyymmddhh}.06h.grb2"
        _download(config["archive_url"].format(yyyymm=yyyymm), monthly)
        _extract_member(monthly, f"ST4.{yyyymmdd}", daily)
        _extract_member(daily, result.name, result)
        results.append(result)
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("config_file", type=Path)
    args = parser.parse_args()
    stage(get_yaml_config(args.config_file).data)


if __name__ == "__main__":  # pragma: no cover
    main()
