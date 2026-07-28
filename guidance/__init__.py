"""Model GUIDANCE ingest for ``/models/``.

The rendered-field half of ``/models/`` answers "what does this model's
atmosphere look like". This package answers "where and how strong do the aids
say the storm will be" - the ATCF deck guidance that every other site treats as
a separate product. Keeping both on ONE storm-keyed page is the point: the class
of site splits into field viewers and guidance viewers, and nobody does both.

Modules:
  * :mod:`guidance.atcf` - a-deck / b-deck parsing + QC, and the honest
    accounting of what the PUBLIC deck withholds.
"""
