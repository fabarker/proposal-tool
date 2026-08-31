"""Development mirror of the isg-cyrus-pmg host topology.

This package reproduces, inside epsilon-phi, the shape of
``isg-cyrus-pmg/src/cyrus_pmg`` so the Proposal Tool back end is written in its
final form. It is deliberately named ``cyrus_pmg`` so that every import in the
transplantable files is byte-identical to what it will be in the host.

Two kinds of module live here:

* Stand-ins for files the host already has (``dashboard/dashboardFrontend.py``,
  ``pmgService/isgPMGService.py``, ``pmgService/config.py``,
  ``pmgService/core/accessControl.py``). These are not copied into the host;
  they exist to reproduce the topology and to document the exact insertions the
  host files need. Each says so in its docstring.
* The transplant payload: ``dashboard/proposalTool/`` (the page folder),
  ``pmgService/scenario/`` (the whole Proposal Tool back end) and the scenario
  endpoint block in ``pmgService/dashboardRouter.py``.

``proposal-tool/service/TRANSPLANT.md`` carries the file-by-file porting list.
"""
