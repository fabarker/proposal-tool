"""The Proposal Tool back end - the transplant payload (PORTING.md §3.2, §6).

Everything the scenario endpoints need lives in this package, and the package
imports no other part of the host: intra-package relative imports, the
standard library, openpyxl and pandas only. The endpoint block in
dashboardRouter.py is its one caller; the host's auth module is reached from
that block, never from here. A copy of this directory into
``cyrus_pmg/pmgService/`` therefore needs no edits.

    scenarioPort.py       the port contract every adapter implements (spec 4.2)
    types.py              BasisInput, MandateInput, PortfolioKey, the errors
    rules.py              product rules: thresholds, naming, availability, the
                          export filename (D75)
    payloads.py           the wire shapes shared by every adapter
    saaKeys.py            portfolio NAME -> key: the closed vocabularies (D54)
    universe.py           the strategic universe read from SCENARIO_SAA_SOURCE
    portfolio_weights.py  asset metadata, hedge ratios, the engine bridge
                          (runtime copy of proposal-tool/backend/portfolio_weights.py,
                          which remains the source of record)
    engine.py             the one module naming the analytics library - bake only
    liveAdapter.py        ScenarioPort over the analytics library - bake only
    fixturesAdapter.py    ScenarioPort over synthetic analytics - tests, demos
    bakedAdapter.py       ScenarioPort over the delivered store - production
    bake.py               the offline bake and the store layout (D17)
    assetEstimates.py     per-asset long-term estimates for the assumptions sheet
    registry.py           adapter selection via SCENARIO_ADAPTER
    scenarioStore.py      file-backed scenario state, shared across workers
    advisors.py           the Primary PWA directory (advisors.xlsx)
    products.py           the delivered product catalogue (D56, D63)
    sleeves.py            the sleeve library facade (VARIANTS, listSleeves, ...)
    sleeveRepo.py         the library itself: a SQLite store the service writes,
                          seeded once from SCENARIO_SLEEVES_SEED (D57, D65)
    sleeveTools.py        census / export / import for that store
    fees.py               the fee framework and the delivered rate card (D55)
    feeTools.py           census / diff / accept for the card
    workbook.py           the whole Excel proposal with openpyxl alone (D67, D73)
    proposalRegister.py   every delivered proposal, kept for ever (D69, D75)
    accountRequests.py    account opening requests against a proposal (D76)

Data files beside the modules: advisors.xlsx, fees.json, feeRates.csv,
assetEstimates.json. Everything else the package reads is named by a
SCENARIO_* variable (PORTING.md §11.4) and delivered as data (Appendix C).
"""
