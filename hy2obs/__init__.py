"""hy2obs - HY-2B/2C HSCAT 25-km ocean winds, a DELAYED daily look.

Source: Copernicus Marine native S3 (CloudFerro), fully anonymous - the
KNMI/OSI SAF HSCAT L2B binned to a daily 0.25-deg global grid, one file per
satellite x pass direction, landing D+1 ~19Z (19-43 h behind sensing; every
product is labeled delayed, never real-time). Ku-band is rain-sensitive:
rain/land/not-usable quality bits are masked before anything is drawn.
HY-2D is suspended upstream and deliberately not ingested.

Credits carried on every render: EUMETSAT/OSI SAF/KNMI · Generated using
E.U. Copernicus Marine Service Information (doi:10.48670/moi-00182) ·
OSI-114-a · HSCAT L1B courtesy NSOAS/CNSA.
"""
