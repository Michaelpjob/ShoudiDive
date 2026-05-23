"""Shared ERDDAP griddap helper for the data pipeline.

Stage 6 refactor — pulls the "build a griddap URL, GET the netCDF,
fall back to a secondary host if the primary connection fails" pattern
out of fetch.py + fetch_climatology.py + fetch_currents.py + ... so
each fetcher stops re-inventing it.

The two-tier design:

  * :func:`griddap_url` — pure URL builder. Same query shape every
    fetcher used: ``<base>/<dataset>.nc?<var>[(t_lo):1:(t_hi)]<pre_xy>
    [(lat_lo):stride:(lat_hi)][(lng_lo):stride:(lng_hi)]``.
  * :func:`griddap_fetch` — orchestrator. Builds the URL, fetches it
    via :func:`pipeline.lib.http.http_get`, caches the bytes on disk,
    walks a host-fallback list on connection failure, and returns the
    cached path.

What's intentionally NOT in this module:

  * NetCDF parsing. xarray.open_dataset() lives in the caller — every
    fetcher has slightly different post-parse logic (Kelvin→°C for
    MUR, flipud for PNG orientation, altitude-axis dropping for VIIRS)
    and trying to capture all of that in a shared helper produces a
    spaghetti config dict that's worse than the duplication it
    replaces. The helper stops at "bytes on disk".
  * Region bbox lookup. The bbox is passed in. The pipeline's
    region-switching (CA/PNW/tropical) lives upstream; the helper
    doesn't need to know about it.

Tests live in ``pipeline/tests/test_lib_erddap.py``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Iterable

import requests

from .http import http_get


# Default fall-back chain used when callers don't pass one. fetch.py
# pre-refactor had explicit per-layer `fallbacks` lists; new code that
# doesn't care can leave fallbacks empty (the primary host is the only
# attempt) and let the per-layer config decide whether to add more.


@dataclass(frozen=True)
class GriddapSource:
    """One ERDDAP host + dataset + variable triple.

    Multiple sources let a fetcher walk a fall-back chain — see
    :func:`griddap_fetch`. Each source can override the stride
    independently (the legacy fetch.py had this because the NOAA
    BLENDED SST fallback uses native 5 km grid while the primary MUR
    uses 1 km / stride 2).
    """

    host: str
    """ERDDAP griddap base URL, e.g. ``https://coastwatch.noaa.gov/erddap/griddap``."""

    dataset: str
    """Dataset id, e.g. ``jplMURSST41`` or ``noaacwNPPN20VIIRSDINEOFDaily``."""

    variable: str
    """Variable name in the dataset, e.g. ``analysed_sst`` or ``chlor_a``."""

    stride: int = 1
    """Lat/lng decimation stride. Set 2 for the 1 km MUR product so the
    returned grid stays manageable; 1 for native-stride products."""

    pre_xy_dims: str = ""
    """ERDDAP query fragment inserted BETWEEN time and lat/lng axes.

    VIIRS gap-filled has a single-element altitude axis at index 0,
    so callers pass ``"[0]"``. MUR has no extra axes — leave empty.
    """

    label: str | None = None
    """Optional human-readable name for log lines. Defaults to dataset."""

    extras: dict[str, Any] = field(default_factory=dict)
    """Free-form per-source metadata the caller wants stashed (e.g.
    ``source_label`` for manifest provenance). The helper ignores
    this field — it's purely for the caller's bookkeeping."""

    def key(self) -> str:
        """Filename-safe identifier for cache keys."""
        return str(self.dataset).replace("/", "_")


@dataclass(frozen=True)
class BBox:
    """A geographic bounding box in the same ``-180..180`` convention
    the rest of the pipeline uses."""

    lat_min: float
    lat_max: float
    lng_min: float
    lng_max: float

    @classmethod
    def from_dict(cls, d: dict[str, float]) -> "BBox":
        """Accept the ``regions.active_region().bbox`` dict shape."""
        return cls(
            lat_min=float(d["lat_min"]),
            lat_max=float(d["lat_max"]),
            lng_min=float(d["lng_min"]),
            lng_max=float(d["lng_max"]),
        )


def griddap_url(
    source: GriddapSource,
    bbox: BBox,
    time_lo: date | str,
    time_hi: date | str | None = None,
    *,
    stride: int | None = None,
    lng_offset_360: bool = False,
) -> str:
    """Build an ERDDAP griddap netCDF URL.

    Mirrors the URL shape used by :func:`pipeline.fetch.erddap_url`
    and :func:`pipeline.fetch_climatology.erddap_nc`:

    ::

        <host>/<dataset>.nc
            ?<variable>
            [(time_lo)T00:00:00Z:1:(time_hi)T23:59:59Z]
            <pre_xy_dims>
            [(lat_min):<stride>:(lat_max)]
            [(lng_min):<stride>:(lng_max)]

    Parameters
    ----------
    source
        ERDDAP source (host + dataset + variable + stride defaults).
    bbox
        Geographic bounding box.
    time_lo
        Lower time bound. Either a ``date`` (00:00:00Z is implied) or
        a pre-formatted ERDDAP time string (e.g. ``"2026-05-22T12:00:00Z"``).
    time_hi
        Upper time bound. ``None`` means "same calendar day as
        ``time_lo``" — encoded as ``T23:59:59Z``. The legacy fetchers
        always used this single-day window.
    stride
        Override ``source.stride``. Most callers leave this None.
    lng_offset_360
        Datasets storing longitude in 0..360° need the bbox bounds
        shifted before the request. Matches the
        ``fetch_climatology.erddap_nc(..., lng_360=True)`` flag.
    """
    s = stride if stride is not None else source.stride

    if isinstance(time_lo, date):
        time_lo_q = f"{time_lo.isoformat()}T00:00:00Z"
        time_hi_default_d: date = time_lo
    else:
        time_lo_q = str(time_lo)
        time_hi_default_d = None  # type: ignore[assignment]

    if time_hi is None:
        if time_hi_default_d is None:
            raise ValueError(
                "griddap_url: time_hi must be provided when time_lo is a "
                "string (no inferable calendar day)."
            )
        time_hi_q = f"{time_hi_default_d.isoformat()}T23:59:59Z"
    elif isinstance(time_hi, date):
        time_hi_q = f"{time_hi.isoformat()}T23:59:59Z"
    else:
        time_hi_q = str(time_hi)

    if lng_offset_360:
        lng_min = (bbox.lng_min + 360.0) % 360.0
        lng_max = (bbox.lng_max + 360.0) % 360.0
    else:
        lng_min, lng_max = bbox.lng_min, bbox.lng_max

    return (
        f"{source.host}/{source.dataset}.nc"
        f"?{source.variable}"
        f"[({time_lo_q}):1:({time_hi_q})]"
        f"{source.pre_xy_dims}"
        f"[({bbox.lat_min}):{s}:({bbox.lat_max})]"
        f"[({lng_min}):{s}:({lng_max})]"
    )


def cache_path_for(
    cache_dir: Path,
    *,
    prefix: str,
    source: GriddapSource,
    when: date | str,
    stride: int | None = None,
) -> Path:
    """Compute the on-disk cache filename for a griddap fetch.

    Layout:  ``{cache_dir}/{prefix}_{source.key()}_{when}_s{stride}.nc``

    Matches the convention fetch.py already uses (``f"{layer}_{key}_
    {d.isoformat()}_s{source_stride}.nc"``) so the migration doesn't
    invalidate any of the existing cache files.
    """
    s = stride if stride is not None else source.stride
    if isinstance(when, date):
        when_str = when.isoformat()
    else:
        when_str = str(when)
    return cache_dir / f"{prefix}_{source.key()}_{when_str}_s{s}.nc"


def griddap_fetch(
    sources: GriddapSource | Iterable[GriddapSource],
    bbox: BBox,
    time_lo: date | str,
    time_hi: date | str | None = None,
    *,
    cache_dir: Path,
    cache_prefix: str,
    stride: int | None = None,
    lng_offset_360: bool = False,
    timeout: int | float = 180,
    log: Any = None,
) -> tuple[Path, GriddapSource] | None:
    """Fetch an ERDDAP griddap netCDF, with host-fallback and disk cache.

    Walks ``sources`` in order. For each:

      1. Compute the on-disk cache path.
      2. If the cache file exists, return it immediately.
      3. Build the griddap URL and GET it via
         :func:`pipeline.lib.http.http_get` (which retries transient
         transport failures internally).
      4. On non-200 / transport exception, advance to the next source.
      5. On 200, write the bytes to the cache path and return.

    Parameters
    ----------
    sources
        Either a single ``GriddapSource`` or an iterable of them. When
        multiple are passed, the second and later are fall-backs used
        only when earlier ones fail with a transport error or non-200.
        Mirrors :func:`fetch.candidate_configs`.
    bbox, time_lo, time_hi, stride, lng_offset_360
        Forwarded to :func:`griddap_url`. Each ``source`` may carry its
        own stride; passing ``stride`` here applies to the helper
        consistently across the fallback chain.
    cache_dir
        Directory where the netCDF is cached. Created if missing.
    cache_prefix
        Filename prefix — typically the layer name ("sst", "chl",
        "kd490", "climo"). Picks the cache key apart from other
        layers using the same dataset id.
    timeout
        Per-attempt HTTP timeout (default 180 s).
    log
        Optional logger-like callable taking a single str arg. Defaults
        to ``print(..., flush=True)`` — matches the existing fetchers'
        stdout-only logging convention.

    Returns
    -------
    tuple[Path, GriddapSource] | None
        ``(cache_path, winning_source)`` on success, or ``None`` if
        every source failed. Tests inspect ``winning_source`` to assert
        which fallback fired.
    """
    if isinstance(sources, GriddapSource):
        sources_list = [sources]
    else:
        sources_list = list(sources)
    if not sources_list:
        raise ValueError("griddap_fetch: no sources provided")

    logfn = log if callable(log) else lambda msg: print(msg, flush=True)

    cache_dir.mkdir(parents=True, exist_ok=True)

    for i, source in enumerate(sources_list):
        suffix = "" if i == 0 else f" via {source.label or source.key()}"
        nc_path = cache_path_for(
            cache_dir,
            prefix=cache_prefix,
            source=source,
            when=time_lo if isinstance(time_lo, date) else str(time_lo).split("T")[0],
            stride=stride,
        )
        if nc_path.exists():
            return (nc_path, source)

        url = griddap_url(
            source,
            bbox,
            time_lo,
            time_hi,
            stride=stride,
            lng_offset_360=lng_offset_360,
        )
        when_label = time_lo if isinstance(time_lo, date) else str(time_lo)
        logfn(f"  GET {cache_prefix} {when_label}{suffix}")
        try:
            r = http_get(url, timeout=timeout)
        except requests.RequestException as exc:
            # http_get only raises with raise_on_failure=True (we don't
            # use that). Still — guard against future-proofing breakage.
            logfn(f"  {cache_prefix} {when_label}{suffix}: "
                  f"{exc.__class__.__name__} - skipping")
            continue

        if r is None:
            logfn(f"  {cache_prefix} {when_label}{suffix}: "
                  f"transport failed after retries - skipping")
            continue

        if r.status_code != 200:
            logfn(f"  {cache_prefix} {when_label}{suffix}: "
                  f"HTTP {r.status_code} - skipping")
            continue

        nc_path.write_bytes(r.content)
        return (nc_path, source)

    return None
