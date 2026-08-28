"""Lifecycle manager for the local Java ARAPI bridge process."""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path

import httpx

from helix_mcp.config import RuntimeSettings

_MAIN_CLASS = "com.example.helix.bridge.ArapiBridge"
_SUPPORTED_ARAPI_VERSION = re.compile(r"^21\.30(?:\.|$)")
_MAX_MANIFEST_BYTES = 65_536
_REQUIRED_ARAPI_JARS = {
    "arapi": re.compile(r"^arapi(?!ext).+\.jar$", re.IGNORECASE),
    "arapiext": re.compile(r"^arapiext.+\.jar$", re.IGNORECASE),
    "arlogger": re.compile(r"^arlogger.+\.jar$", re.IGNORECASE),
}


class ArapiBridgeProcessError(RuntimeError):
    """Sanitized bridge startup failure."""

    code = "ARAPI_BRIDGE_PROCESS_ERROR"


class ArapiRuntimeMissingError(ArapiBridgeProcessError):
    """A required local Java or ARAPI component is absent."""

    code = "ARAPI_RUNTIME_MISSING"


class ArapiRuntimeInvalidError(ArapiBridgeProcessError):
    """The configured ARAPI runtime is ambiguous or malformed."""

    code = "ARAPI_RUNTIME_INVALID"


class ArapiRuntimeVersionError(ArapiBridgeProcessError):
    """The configured ARAPI runtime version is unsupported."""

    code = "ARAPI_RUNTIME_VERSION_UNSUPPORTED"


@dataclass(frozen=True, slots=True)
class ArapiLibraries:
    """Validated BMC libraries required to build and run the bridge."""

    arapi: Path
    arapiext: Path
    arlogger: Path


class ArapiBridgeProcess:
    """Start and stop the bridge only when this process owns it."""

    __slots__ = ("_base_url", "_process", "_settings")

    def __init__(
        self,
        settings: RuntimeSettings,
        base_urls: tuple[str, ...],
    ) -> None:
        unique = set(base_urls)
        if len(unique) > 1:
            raise ArapiBridgeProcessError(
                "ARAPI targets must share one local bridge URL"
            )
        self._settings = settings
        self._base_url = next(iter(unique), None)
        self._process: asyncio.subprocess.Process | None = None

    @property
    def owned(self) -> bool:
        return self._process is not None

    async def start(self) -> None:
        if self._base_url is None:
            return
        self._validate_startup_requirements()
        if await self._healthy():
            return
        assert self._settings.arapi_bridge_jar_path is not None
        assert self._settings.arapi_lib_dir is not None
        jar = self._required_file(
            self._settings.arapi_bridge_jar_path,
            "ARAPI bridge JAR",
        )
        libraries = self._required_directory(
            self._settings.arapi_lib_dir,
            "ARAPI library directory",
        )
        classpath = os.pathsep.join((str(jar), str(libraries / "*")))
        try:
            self._process = await asyncio.create_subprocess_exec(
                "java",
                "-cp",
                classpath,
                _MAIN_CLASS,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except OSError:
            raise ArapiBridgeProcessError(
                "ARAPI bridge process could not be started"
            ) from None

        for _ in range(50):
            if self._process.returncode is not None:
                break
            if await self._healthy():
                return
            await asyncio.sleep(0.1)
        await self.aclose()
        raise ArapiBridgeProcessError("ARAPI bridge did not become healthy")

    async def check_startup_requirements(self) -> None:
        """Validate the complete local bridge runtime without starting it."""

        if self._base_url is None:
            return
        self._validate_startup_requirements()

    def _validate_startup_requirements(self) -> None:
        if shutil.which("java") is None:
            raise ArapiRuntimeMissingError("Java runtime is not available")
        self._required_file(
            self._settings.arapi_bridge_jar_path,
            "ARAPI bridge JAR",
        )
        libraries = self._required_directory(
            self._settings.arapi_lib_dir,
            "ARAPI library directory",
        )
        validate_arapi_libraries(libraries)

    async def aclose(self) -> None:
        process = self._process
        self._process = None
        if process is None or process.returncode is not None:
            return
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except TimeoutError:
            process.kill()
            await process.wait()

    async def _healthy(self) -> bool:
        assert self._base_url is not None
        try:
            async with httpx.AsyncClient(
                timeout=1,
                trust_env=False,
            ) as client:
                response = await client.get(
                    f"{self._base_url.rstrip('/')}/health"
                )
            return response.status_code == 200
        except httpx.RequestError:
            return False

    @staticmethod
    def _required_file(path: Path | None, label: str) -> Path:
        if path is None or not path.is_file():
            raise ArapiRuntimeMissingError(f"{label} is not configured")
        return path.resolve()

    @staticmethod
    def _required_directory(path: Path | None, label: str) -> Path:
        if path is None or not path.is_dir():
            raise ArapiRuntimeMissingError(f"{label} is not configured")
        return path.resolve()


def validate_arapi_libraries(directory: Path) -> ArapiLibraries:
    """Validate and return the unique supported BMC ARAPI libraries."""

    try:
        files = tuple(path for path in directory.iterdir() if path.is_file())
    except OSError:
        raise ArapiRuntimeInvalidError(
            "ARAPI library directory cannot be inspected"
        ) from None
    selected: dict[str, Path] = {}
    for component, pattern in _REQUIRED_ARAPI_JARS.items():
        matches = tuple(path for path in files if pattern.fullmatch(path.name))
        if not matches:
            raise ArapiRuntimeMissingError(
                f"required {component} library is not available"
            )
        if len(matches) > 1:
            raise ArapiRuntimeInvalidError(
                f"multiple {component} libraries are configured"
            )
        selected[component] = matches[0]

    for component, path in selected.items():
        attributes = _read_manifest(path)
        vendor = attributes.get("Implementation-Vendor", "")
        version = attributes.get("Implementation-Version", "")
        if not vendor.startswith("BMC Software") or not version:
            raise ArapiRuntimeInvalidError(
                f"{component} library manifest is invalid"
            )
        if _SUPPORTED_ARAPI_VERSION.match(version) is None:
            raise ArapiRuntimeVersionError(
                f"{component} library version is unsupported"
            )
    return ArapiLibraries(
        arapi=selected["arapi"],
        arapiext=selected["arapiext"],
        arlogger=selected["arlogger"],
    )


def _read_manifest(path: Path) -> dict[str, str]:
    try:
        with zipfile.ZipFile(path) as archive:
            info = archive.getinfo("META-INF/MANIFEST.MF")
            if info.file_size > _MAX_MANIFEST_BYTES:
                raise ArapiRuntimeInvalidError(
                    "ARAPI library manifest exceeds the size limit"
                )
            raw_manifest = archive.read(info)
    except (KeyError, OSError, zipfile.BadZipFile):
        raise ArapiRuntimeInvalidError(
            "ARAPI library archive is invalid"
        ) from None

    try:
        lines = raw_manifest.decode("utf-8").splitlines()
    except UnicodeError:
        raise ArapiRuntimeInvalidError(
            "ARAPI library manifest encoding is invalid"
        ) from None

    unfolded: list[str] = []
    for line in lines:
        if line.startswith(" ") and unfolded:
            unfolded[-1] += line[1:]
        else:
            unfolded.append(line)
    return {
        name: value.strip()
        for line in unfolded
        if ":" in line
        for name, value in (line.split(":", 1),)
    }
