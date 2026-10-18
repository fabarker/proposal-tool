"""The Proposal Tool back end.

Everything the scenario endpoints need lives in this package, and the package
imports no other part of the service: intra-package relative imports, the
standard library, openpyxl, pandas and python-pptx only. It never imports the
analytics library either: the bake is produced elsewhere and delivered as
data, and nothing here computes a figure (D126). The endpoint block in
dashboardRouter.py is its one caller; the auth module is reached from that
block, never from here.

    scenarioPort.py       the port contract every adapter implements (spec 4.2)
    types.py              BasisInput, MandateInput, PortfolioKey, the errors
    rules.py              product rules: thresholds, naming, availability, the
                          export filename (D75)
    payloads.py           the wire shapes shared by every adapter
    saaKeys.py            portfolio NAME -> key: the closed vocabularies (D54)
    universe.py           the strategic universe read from SCENARIO_SAA_SOURCE
    portfolio_weights.py  asset metadata and the weight universe's loaders
    fixturesAdapter.py    ScenarioPort over synthetic analytics - tests, demos
    bakedAdapter.py       ScenarioPort over the delivered bake - production, and
                          the only source of analytics there is (D126)
    bake.py               the store layout the service reads, the census, and a
                          fixtures bake for tests; a real bake is delivered
                          (D17, D126)
    assetEstimates.py     per-asset long-term estimates for the assumptions sheet
    registry.py           adapter selection via SCENARIO_ADAPTER
    scenarioStore.py      file-backed scenario state, shared across workers
    advisors.py           the Primary PWA directory (advisors.xlsx)
    products.py           the product catalogue, kept in the repository and
                          edited from the console; loaded once from
                          SCENARIO_PRODUCTS_SOURCE, then by import (D63, D165)
    sleeves.py            the sleeve library facade (VARIANTS, listSleeves, ...)
    sleeveRepo.py         the library itself: a SQLite store the service writes,
                          seeded once from SCENARIO_SLEEVES_SEED (D57, D65)
    sleeveRules.py        which strategic portfolios an edition of a sleeve is for (D89)
    sleeveTools.py        census / export / import for that store, and the
                          catalogue's export, import and history
    fees.py               the fee framework: pricing from the card in force (D55)
    feeCard.py            the fee card, kept in the repository and edited on the
                          console's Fees tab; loaded once from
                          SCENARIO_FEES_SOURCE (D166)
    feeTools.py           census / diff / accept / export for the card
    repositoryFeed.py     the repository's record as one feed: sleeves,
                          products, the fee card, overlays, the split (D167)
    sheetDoc.py           the report as data: the four sheets built once (D119)
    workbook.py           the Excel renderer of those Docs (D67, D73, D119)
    pptWriter.py          the PowerPoint renderer: the deck, fitted and locked (D121-D125)
    houseFonts.py         the house faces' files, and their embedding in a deck (D125)
    fontMetrics.py        generated: the embedded faces' metrics, for the fit (D124)
    proposalRegister.py   every delivered proposal, kept for ever (D69, D75)
    accountRequests.py    account opening requests against a proposal (D76)

Data files beside the modules: advisors.xlsx, fees.json, feeRates.csv (the
card the repository is first loaded with when SCENARIO_FEES_SOURCE names no
other), assetEstimates.json. Everything else the package reads is named by a
SCENARIO_* variable and delivered as data.
"""
