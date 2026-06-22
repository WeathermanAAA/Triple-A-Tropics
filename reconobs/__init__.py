"""reconobs - aircraft-recon observations product (HDOB/VDM/dropsonde + TCPOD).

A self-contained, basin-agnostic ingest that decodes NHC recon bulletins
(vendored tropycal decoders), parses the CARCAH Plan of the Day, and writes a
per-storm JSON tree to R2 under ``recon/``. The /recon/ canvas viewer and the
CycloLab recon tab both hydrate from that tree. Isolated feed: it never reads
or writes track/ACE/climatology code or data.
"""
__version__ = "0.1.0"

from .build import build, SCHEMA_VERSION          # noqa: F401
from .tcpod import parse_tcpod                     # noqa: F401
