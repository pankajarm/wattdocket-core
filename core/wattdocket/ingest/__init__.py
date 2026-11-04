"""WattDocket ingestion lane — public data in, one deterministic ledger out.

Modules, and the invariant each one is responsible for:

* ``echo``    — facility identity and status (SPEC §2 ``Facility``); every row
  is geocoded or it does not enter the ledger (no silently dropped rows).
* ``egrid``   — annual subregion rates; INV-1 lives or dies here, because eGRID
  publishes rates in lb/MWh and *mass* in short tons per year.
* ``ercot``   — 5-minute grid intervals; INV-2 (UTC keys), INV-3 (hour labels
  are interval-STARTing and stored alongside the UTC key), INV-5 (storage is
  excluded from generation and from the intensity numerator), INV-6 (every
  derived intensity is PROVISIONAL and carries its factor set).
* ``geo``     — address -> eGRID subregion bound to a facility (SPEC §2
  ``GridBinding``), with the geocoder and the polygon service named.
* ``ledger``  — the single read-only artifact, plus ``--verify``.

Nothing in this package calls a model. Every number is fetched, parsed and
multiplied; every failure raises with the URL that failed.
"""

from .net import FixtureFetcher, IngestionError, UrlFetcher, default_fetcher

__all__ = [
    "FixtureFetcher",
    "IngestionError",
    "UrlFetcher",
    "default_fetcher",
]
