# Deck template

`ProposalDeck_Template.pptx` is a PLACEHOLDER for the house slide template,
for building and testing the deck here. Cyrus has the house template itself,
already formatted, and keeps it: this file is never ported. The Cyrus update
carries the service package, the page and the router block, and this folder is
outside all three.

The deck adds every slide from a template's layout, so the frame and the text
styles come from the template, not from code (D174). It reads whichever template
`SCENARIO_DECK_TEMPLATE` names: this one here, the house template on Cyrus.
Without the setting, the deck is drawn as it was before templates. The
Proposal UID is not printed on a template deck: it goes in the workbook only
(and the file's hidden properties, which the register reads). The plan is
`proposals/deck-reference-restyle.html` ("The template").

It is 4:3 and carries:

| Where | What |
|---|---|
| Slide master | the thin rule across the top; the text styles by level |
| Layout `Content` | the placeholders below, each in its place and dressed |
| Theme `Proposal Deck` | GS Sans for titles and text alike, and a colour for each role |
| Embedded fonts | GS Sans, regular and bold, whole |
| Slide 1 | every style as a named sample (`Style · ...`), and the placeholders filled in; its speaker notes read `tool: template only`, so no deck delivers it |
| Marker slides | one for each slide the tool builds, in the deck's order: `cover`, `comparing-allocations`, `risk-return`, `implemented-model`, `composition`, `fee-structure`, `assumptions` |
| Its own slides | Fees Summary (`tool: when fees`; the weighted cost and the other charges as fields) before `fee-structure`; Fee Summary (`when fees`; the navy box's figures as fields) and Advisory Fee Schedule (`when casp`; the tier table's area, `tool: fee-ladder`) after it - as a house template would carry them, wording and all, with stand-in wording here. Each has its own slide-number field |

## What a deck asks for

The deck adds its slides from the layout `SCENARIO_DECK_LAYOUT` names, *Content*
unless told otherwise, and finds each placeholder on it by what it is:

| Role | How it is found | Here |
|---|---|---|
| Title | the title placeholder | idx 0, Title |
| Subtitle | a subtitle placeholder, or a text one named "subtitle", or one above the content box | idx 10, Subtitle |
| Content box | the largest text placeholder left | idx 11, Content |
| Footnotes | a text one named "footnote" or "source", or one below the content box | idx 12, Footnotes |
| Page number | the slide-number placeholder | idx 14, Page number |

A role the layout does not have is left out. What goes in a placeholder
carries no formatting of its own, so the template's faces, sizes and colours
are what a deck shows; tables and charts are fitted into the content box and
name the theme's body face. The deck leaves the template's theme and fonts as
they are.

The text levels in the content box are styles: 1 section heading, 2 body
text, 3 bullet, 4 note, 5 sub-bullet. The theme's colours are roles: accent1
the rules, accent2 the navy box, accent3 headings and names, accent6 the
proposed column's tint (accent4 and accent5 are not used). A figure prints as
the text around it does: white in the navy box, the body colour elsewhere.

The samples on the style slide are named `Style · Section rule`,
`Style · Navy box`, `Style · Divider` and `Style · Table`.

## Checking a template

The check reads a template without changing it and lists what the deck would
use: the layout, the placeholder each role maps to, the canvas, the content
box, the theme's faces and colours, every slide's marks, the slides with no
marker yet, and anything the deck would refuse.

```bash
cd proposal-tool/service
PYTHONPATH=. python3 -m cyrus_pmg.pmgService.scenario.houseTemplate ../deckTemplate/ProposalDeck_Template.pptx
```

Without a path it checks the template `SCENARIO_DECK_TEMPLATE` names; a second
argument names the layout. On Cyrus, run it against the house template before
the setting is set.

## Changing it

Open it in PowerPoint and change what you like: move or restyle the
placeholders in Slide Master view, change the theme's colours or fonts, restyle
a sample, add slides of your own. Keep the layout's name and the marks, and
every deck follows.

Once the file has been edited by hand, the file is the source. The builder
starts it over from scratch:

```bash
cd proposal-tool/service
PYTHONPATH=. python3 ../deckTemplate/build_deck_template.py
```

## Marks: where the tool's slides go

A template may hold slides of its own, which a deck leaves exactly as they are.
Marks tell the tool where its slides and figures go among them (the plan, "Your
template's own slides, and where the tool's go").

A mark goes on a slide, or on a shape or some text on a slide, never on the
deck: the deck is the template file itself, named once by
`SCENARIO_DECK_TEMPLATE`. A mark is plain text typed in PowerPoint, starting
`tool:` or wrapped in `{{ }}`. There are four kinds.

### A marker slide: the tool's whole slide goes here

1. Insert a slide where the tool's slide belongs, as slide 10, say. Any layout
   will do, and it can stay blank.
2. Open its speaker notes: View › Normal, then the Notes pane under the slide
   (View › Notes, or Notes on the status bar).
3. Type one line: `tool: comparing-allocations`.

The tool replaces that slide with its own, made from the `Content` layout. A
table that runs long adds its next slide straight after. To move it, drag its
thumbnail: the numbers follow from where it sits. The tool's cover is made only
at a `tool: cover` marker: a template without one is taken to have its own.

### A field: a figure inside your own slide

1. On your own slide, select the sample figure: the *0.00%* on Fee Summary,
   say.
2. Type `{{advisoryFee}}` in its place.
3. Format the field the way the figure should look: colour, size, bold.

The slide stays yours. The tool writes the figure over `{{advisoryFee}}` in the
field's formatting, so it looks as you typed it: white in a navy box, the body
colour elsewhere. Format the whole field alike, from the first brace to the
last: if it is mixed, the figure takes the formatting of its first character.

### An area: the tool's table on your own slide

1. On your own slide (Advisory Fee Schedule, say), draw a rectangle where the
   table goes, at the size it should be.
2. Open the Selection Pane: Home › Arrange › Selection Pane on a Mac, Home ›
   Select › Selection Pane on Windows (Alt+F10).
3. Double-click the rectangle's name and type `tool: fee-ladder`.

The tool takes the rectangle out and draws the table in its place and size; the
rest of the slide stays as you made it. The rectangle never reaches a client,
so a dashed outline is fine.

### A condition: a slide only some proposals get

Add a line to the speaker notes of your own slide:

| Line | The slide is kept |
|---|---|
| `tool: when fees` | for a priced proposal |
| `tool: when casp` | when the proposal is priced by CASP: Advisory Fee Schedule, say |
| `tool: when strategic` | when the proposal keeps the strategic allocation (not a custom portfolio, nor custom sleeves that move a weight) |
| `tool: when private markets` | when the proposal holds private markets |
| `tool: template only` | never: for notes to yourself, as on this placeholder's style slide |

A slide may carry several `tool:` lines, one to a line, and fields or an area
besides. Notes that do not start with `tool:` are the slide's own.

### The names the tool answers to

| Mark | Kind | The tool's slide or figure |
|---|---|---|
| `cover` | marker | The cover: Investment Proposal, the basis, the mandate, the PWA and the date |
| `comparing-allocations` | marker or area | Strategic Asset Allocation · Comparing Allocations, or just its table |
| `risk-return` | marker or area | Risk and Return Analysis, or just its table |
| `implemented-model` | marker | Implemented Model |
| `composition` | marker | Composition of the Implemented Model |
| `fee-structure` | marker | Portfolio Fee Structure |
| `fee-ladder` | area | The fee by asset tier, as on Advisory Fee Schedule (CASP pricing only) |
| `assumptions` | marker | Long-Term Capital Market Assumptions |
| `{{weightedCost}}` | field | The proposal's weighted cost (long-term, with private markets) |
| `{{advisoryFee}}` `{{productCost}}` `{{totalCost}}` | fields | The weighted advisory fee, the products' weighted cost, and the two together |
| `{{initialCost}}` | field | The initial weighted cost, for private markets |
| `{{mandateSize}}` `{{date}}` `{{primaryPwa}}` | fields | The mandate, the export's date, the PWA, as the register has them |
| `{{minimumFee}}` `{{custodyFee}}` `{{equityTradeBps}}` | fields | The minimum advisory fee, the annual custody fee, the equity and ETF transaction charge |
| `{{fxHighEdge}}` `{{fxLowEdge}}` `{{fxHighBps}}` `{{fxMidBps}}` `{{fxLowBps}}` | fields | The foreign exchange bands: the two trade-size edges and the rate in each band |

The commissions are entered per proposal by the PWA, on the scenario (the
plan, decision 4). The FX trade sizes are fixed at $5M and $1M, and the custody
fee is $5,000 unless the PWA changes it ($0 for GSBZ Custody). Where the
minimum advisory fee comes from is still open.

### What the tool does with them

- A slide without a mark comes out as it went in: wording, pictures, charts,
  notes and transitions.
- A marker whose slide does not apply is taken out: the fee slides of a
  proposal without fees, the risk slide of a custom portfolio. So is a slide
  whose area has nothing to draw, and a marker whose own condition fails
  takes the tool's slides out with it.
- A tool slide with no marker goes at the end, and the check names it, so
  nothing is lost while the marks are being placed.
- A name the tool does not know, or a figure it cannot work out, stops the
  export with the slide and the field named. A delivered deck never shows
  `{{…}}`.
- The tool takes its own `tool:` lines out of the notes of every delivered
  deck; the slide's other notes stay.
- Page numbers follow when they are PowerPoint's own (Insert › Slide Number). A
  contents slide whose numbers are typed as text is left alone.

The read-only check lists every mark it finds and on which slide, any name it
does not know, and any tool slide with no marker yet. Run it on Cyrus once the
house template is marked.

This placeholder carries one mark, `tool: template only`, on its style slide.
The marks are the plan's proposal (decision 2) and the deck code that reads
them is not written yet: marks placed now wait for it.

macOS Quick Look cannot preview a file whose notes were written by python-pptx;
PowerPoint opens it as usual.
