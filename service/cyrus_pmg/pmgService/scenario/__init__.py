"""The Proposal Tool back end - the transplant payload.

Everything the scenario endpoints need lives in this package:

    scenarioPort.py       the eight-method contract (spec section 4.2)
    types.py              BasisInput, MandateInput, PortfolioKey, errors
    rules.py              product rules: thresholds, naming, availability
    payloads.py           the wire shapes shared by both adapters
    portfolio_weights.py  the supplied model-allocation universe (runtime copy
                          of proposal-tool/backend/portfolio_weights.py, which
                          remains the source of record)
    fixturesAdapter.py    ScenarioPort over static data - no analytics
    liveAdapter.py        ScenarioPort over the SAA library - real analytics
    engine.py             the one module naming that library (port seam)
    scenarioStore.py      file-backed scenario state (survives restarts and is
                          shared across service workers)
    advisors.py           the Primary PWA directory (Excel-backed reference)
    sleeves.py            the PMG sleeve library (authored stub data)
    workbook.py           Excel writers: the implementation sheet, and the
                          fixtures workbook
    registry.py           adapter selection via SCENARIO_ADAPTER

The package uses only intra-package relative imports plus the host's
``..core.accessControl`` seam, so a copy into ``cyrus_pmg/pmgService/`` needs
no edits.
"""
