"""Cached, retrying fetches of SEV certificates from the AMD KDS."""

from __future__ import annotations

import hashlib
import re
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import attestation_report as ar

MAX_ATTEMPTS = 3
BACKOFF_BASE_S = 10.0
_CA_FILES = ("ark.pem", "ask.pem")
_RATE_LIMITED = re.compile(r"\b429\b|too many requests|rate.?limit", re.IGNORECASE)


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class KdsCache:
    """A KDS fetcher bound to one cache directory, to avoid re-fetching certs.

    ``cache_dir`` set to ``None`` disables caching.
    """

    cache_dir: Path | None = None
    timeout: float = 60.0

    def __post_init__(self) -> None:
        if self.cache_dir is not None:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    def fetch_ca(self, report_path: Path, dest_dir: Path) -> tuple[bool, str]:
        """Fetch ARK+ASK into ``dest_dir``."""
        return self._fetch("ca", report_path, dest_dir)

    def fetch_vcek(self, report_path: Path, dest_dir: Path) -> tuple[bool, str]:
        """Fetch the VCEK into ``dest_dir``, using this cache and retrying a 429
        with backoff."""
        return self._fetch("vcek", report_path, dest_dir)

    def _cache_mapping(self, kind: str, report_path: Path) -> list[tuple[str, str]] | None:
        """Cache file mapping for this fetch, or ``None`` when caching is off."""
        if self.cache_dir is None:
            return None

        if kind == "ca":
            return [(n, n) for n in _CA_FILES]

        try:
            report = ar.read(report_path)
        except (ar.ReportError, OSError):
            return None

        name = _vcek_cache_name(report)
        return None if name is None else [("vcek.pem", name)]

    def _fetch(self, kind: str, report_path: Path, dest_dir: Path) -> tuple[bool, str]:
        report_path, dest_dir = Path(report_path), Path(dest_dir)
        mapping = self._cache_mapping(kind, report_path)

        dest_dir.mkdir(parents=True, exist_ok=True)

        if mapping and _fetch_from_cache(mapping, self.cache_dir, dest_dir):
            return True, f"{kind} loaded from KDS cache ({self.cache_dir})"

        # cache miss: fetch from KDS
        args = _fetch_args(kind, report_path, dest_dir)
        try:
            proc = _run_fetch(args, timeout=self.timeout)
        except subprocess.TimeoutExpired:
            return False, f"snpguest fetch {kind} timed out after {self.timeout:.0f}s"

        if proc.returncode != 0:
            return False, f"snpguest fetch {kind} failed: {(proc.stderr or '').strip()}\n  {shlex.join(args)}"

        if mapping:
            _store_in_cache(mapping, dest_dir, self.cache_dir)
        return True, f"{kind} fetched from KDS -> {dest_dir}"


# --------------------------------------------------------------------------- #
# Cache helpers
# --------------------------------------------------------------------------- #

def _vcek_cache_name(report: ar.AttestationReport) -> str | None:
    if report.reported_tcb is None:
        return None
    key = hashlib.sha256(str(report.reported_tcb).encode()).hexdigest()[:16]
    return f"vcek-{key}.pem"


def _fetch_from_cache(mapping: list[tuple[str, str]], cache_dir: Path, dest_dir: Path) -> bool:
    """Copy cached certs into ``dest_dir``. True iff every expected file was present."""
    if not all((cache_dir / cached).is_file() for _, cached in mapping):
        return False
    try:
        for fetched, cached in mapping:
            shutil.copy2(cache_dir / cached, dest_dir / fetched)
    except OSError:
        return False  # treat corrupt cache as miss
    return True


def _store_in_cache(mapping: list[tuple[str, str]], dest_dir: Path, cache_dir: Path) -> None:
    """Publish freshly fetched certs to the cache"""
    if not all((dest_dir / fetched).is_file() for fetched, _ in mapping):
        return
    try:
        for fetched, cached in mapping:
            shutil.copy2(dest_dir / fetched, cache_dir / cached)
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# snpguest fetch helpers
# --------------------------------------------------------------------------- #

def _fetch_args(kind: str, report_path: Path, dest_dir: Path) -> list[str]:
    """The ``snpguest fetch`` argv for ``kind`` ("ca" or "vcek")."""
    if kind == "ca":
        return ["snpguest", "fetch", "ca", "pem", str(dest_dir), "-r", str(report_path)]
    return ["snpguest", "fetch", "vcek", "pem", str(dest_dir), str(report_path)]


def _run_fetch(
    args: list[str],
    *,
    timeout: float,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    sleep: Callable[[float], None] = time.sleep,
) -> subprocess.CompletedProcess:
    """Run ``snpguest fetch`` with 429-only backoff retry."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        proc = runner(args, capture_output=True, text=True, timeout=timeout)
        if proc.returncode == 0 or attempt == MAX_ATTEMPTS:
            return proc
        if not _is_rate_limited(f"{proc.stdout}\n{proc.stderr}"):
            return proc  # only retry for 429
        sleep(BACKOFF_BASE_S * attempt)


def _is_rate_limited(text: str) -> bool:
    return bool(_RATE_LIMITED.search(text))
