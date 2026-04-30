// Generate the CECS-427 technical report as a .docx file.
// Run:  node build_report.js
// Output: report.docx
const fs = require("fs");
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  Header, Footer, AlignmentType, PageOrientation, LevelFormat,
  BorderStyle, WidthType, ShadingType, HeadingLevel, PageBreak,
  TabStopType, TabStopPosition, PageNumber,
} = require("docx");

// ---------- helpers ----------
const SERIF = "Cambria";
const SANS = "Calibri";

const noBorder = { style: BorderStyle.NONE, size: 0, color: "FFFFFF" };
const blankBorders = { top: noBorder, bottom: noBorder, left: noBorder, right: noBorder };

const ruleParagraph = () =>
  new Paragraph({
    spacing: { before: 120, after: 120 },
    border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: "888888", space: 6 } },
    children: [new TextRun("")],
  });

const center = (text, opts = {}) =>
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: opts.spacing,
    children: [new TextRun({ text, font: opts.font ?? SERIF, size: opts.size ?? 24, bold: opts.bold, italics: opts.italics })],
  });

const body = (text, opts = {}) =>
  new Paragraph({
    alignment: AlignmentType.JUSTIFIED,
    spacing: { after: 120, line: 300 },
    children: [new TextRun({ text, font: SERIF, size: 22, ...opts })],
  });

const heading = (text) =>
  new Paragraph({
    heading: HeadingLevel.HEADING_1,
    spacing: { before: 280, after: 120 },
    children: [new TextRun({ text, font: SERIF, size: 28, bold: true })],
  });

// ---------- cover page ----------
const coverPage = [
  // CSULB header (text-only stand-in for the logo block)
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 600, after: 60 },
    children: [
      new TextRun({ text: "CALIFORNIA STATE UNIVERSITY", font: SANS, size: 28, bold: true, characterSpacing: 40 }),
    ],
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { after: 360 },
    children: [
      new TextRun({ text: "LONG BEACH", font: SANS, size: 40, bold: true, characterSpacing: 60 }),
    ],
  }),
  center("Technical Report", { font: SERIF, size: 36, italics: true, spacing: { before: 720, after: 200 } }),
  center("CECS-427", { font: SERIF, size: 24 }),
  center("Dynamic Networks", { font: SERIF, size: 24, spacing: { after: 200 } }),
  ruleParagraph(),
  // Big project title
  center("Robo-Taxi Adoption and", { font: SERIF, size: 56, spacing: { before: 360 } }),
  center("Urban Congestion in Los Angeles", { font: SERIF, size: 56, spacing: { after: 200 } }),
  ruleParagraph(),

  // Authors / Submitted-to two-column layout via borderless table
  new Paragraph({ children: [new TextRun("")], spacing: { before: 600 } }),
  new Table({
    width: { size: 9360, type: WidthType.DXA },
    columnWidths: [4680, 4680],
    borders: {
      top: noBorder, bottom: noBorder, left: noBorder, right: noBorder,
      insideHorizontal: noBorder, insideVertical: noBorder,
    },
    rows: [
      new TableRow({
        children: [
          new TableCell({
            borders: blankBorders,
            width: { size: 4680, type: WidthType.DXA },
            margins: { top: 80, bottom: 80, left: 120, right: 120 },
            children: [
              new Paragraph({
                alignment: AlignmentType.LEFT,
                children: [new TextRun({ text: "Authors: Carlos Ponce, [Teammate 2],", font: SERIF, size: 22 })],
              }),
              new Paragraph({
                alignment: AlignmentType.LEFT,
                children: [new TextRun({ text: "[Teammate 3], [Teammate 4]", font: SERIF, size: 22 })],
              }),
            ],
          }),
          new TableCell({
            borders: blankBorders,
            width: { size: 4680, type: WidthType.DXA },
            margins: { top: 80, bottom: 80, left: 120, right: 120 },
            children: [
              new Paragraph({
                alignment: AlignmentType.CENTER,
                children: [new TextRun({ text: "Submitted to:", font: SERIF, size: 22 })],
              }),
              new Paragraph({
                alignment: AlignmentType.CENTER,
                children: [new TextRun({ text: "[Professor Name]", font: SERIF, size: 22 })],
              }),
            ],
          }),
        ],
      }),
    ],
  }),
  center("April 29, 2026", { font: SERIF, size: 22, spacing: { before: 600 } }),
  new Paragraph({ children: [new PageBreak()] }),
];

// ---------- body ----------
const ABSTRACT = "This report presents a network-flow model for evaluating the impact of robo-taxi adoption on urban traffic congestion in central Los Angeles. We construct a directed graph of the LA freeway-and-arterial network from OpenStreetMap, calibrate baseline edge flows against Google Maps observations, and simulate vehicle-miles traveled (VMT), travel time, and unmet demand under varying robo-taxi shares, deadheading ratios, and fleet capacities. The shared graph supports three downstream analyses: structural and demand-weighted centrality, mode-choice equilibrium, and adoption dynamics. Initial scenarios show that, absent binding fleet constraints, robo-taxi adoption increases VMT roughly linearly with share, driven by deadhead repositioning and the occupancy gap between robo-taxis and private cars.";

const INTRO_1 = "Autonomous mobility-on-demand services are projected to reshape urban traffic in major U.S. metropolitan areas within the next decade. Industry deployments in Phoenix and San Francisco have already demonstrated technical feasibility, and Los Angeles is widely expected to be among the next markets. From a network-science perspective, a centralized robo-taxi fleet introduces a class of flows that did not previously exist on the road graph: empty repositioning between drop-off zones and next-pickup zones, commonly termed deadheading.";
const INTRO_2 = "These deadhead miles, combined with the lower vehicle occupancy of single-passenger trips relative to private cars, raise the question of whether robo-taxi adoption will worsen or alleviate congestion. This project formalizes the question on a real road graph: given a calibrated network, an origin-destination demand matrix, and a set of operator decisions (fleet size, deadheading discipline, pricing posture), what is the equilibrium impact on total VMT, average travel time, and unmet demand?";

const MODEL_1 = "We model the road network as a directed graph G = (V, E). Nodes V partition into two types: attractor zones near downtown landmarks (Downtown Core, USC, Union Station, Koreatown, LA Live), and throughput zones representing residential and connector intersections. Each edge e in E carries attributes: capacity c_e in vehicles per hour (derived from lane count multiplied by 1,800), free-flow time t0_e from OpenStreetMap speed limits, length L_e, road name, and a calibrated baseline flow b_e drawn from observed Google Maps traffic.";
const MODEL_2 = "Three origin-destination demand matrices D_t are defined for time-of-day t in {AM peak, midday, PM peak}. AM-peak demand flows from throughput to attractor zones, PM-peak reverses, and midday represents a diffuse exchange among the attractor zones.";
const MODEL_3 = "Travel time on each edge follows the Bureau of Public Roads (BPR) function: t_e(x_e) = t0_e (1 + alpha (x_e / c_e)^beta), with alpha = 0.15 and beta = 4. Trips are split among modes: a fraction phi chooses robo-taxi, with the remainder split 70/30 between personal car (occupancy 1.2) and transit (treated as off-road).";
const MODEL_4 = "Robo-taxi deadheading is modeled as additional empty trips. Origins are distributed proportional to drop-off frequency, and destinations are weighted by a demand-personalized PageRank vector pi computed on G: E[j -> k] = rho * D_j * pi_k / sum_i pi_i, for j != k, where rho is the deadhead ratio and D_j is the total robo-taxi drop-off volume at node j. A fleet-capacity constraint of fleet_cap multiplied by trips_per_vehicle_per_period bounds total served robo-taxi trips; excess demand is recorded as unmet and triggers a surge flag.";

const SOL_1 = "The system is implemented as three layered Python modules. la_network.py fetches the road graph from OpenStreetMap via the OSMnx library, restricts it to motorway, trunk, and primary classifications, and applies geometric intersection consolidation to reduce the raw 1,600-edge graph to a tractable 71-node, 199-edge representation while preserving freeway topology.";
const SOL_2 = "calibration.py overlays observed flow-to-capacity ratios from Google Maps' typical-traffic layer, indexed by road name, direction (inbound or outbound relative to downtown), and time of day, with segment-level overrides at known pinch points such as the I-10 / I-110 stack. graph_model.py performs the simulation itself: it routes loaded trips on shortest paths by length, computes deadhead OD pairs from the PageRank-weighted distribution described above, applies BPR delays to obtain per-edge travel times, and aggregates VMT and trip-time metrics.";
const SOL_3 = "Three downstream analyses build on the shared graph. A centrality study compares betweenness and demand-personalized PageRank to identify infrastructure that is structurally critical, demand-critical, or both. A game-theoretic analysis solves for mode-choice equilibrium under congestion-dependent utility, where the utility of each mode depends on the realized travel times induced by the current mode split. An adoption-dynamics analysis iterates a logistic-growth model in which robo-taxi share evolves in response to realized travel time, fare, and surge state.";
const SOL_4 = "A Streamlit interface exposes the model's six controls (time of day, background traffic, customer demand, robo-taxi share, deadheading ratio, and fleet cap) and renders the network on a Mapbox basemap, with edges colored by utilization and a panel listing the five most-congested segments by name.";

const CONC_1 = "Initial scenarios reveal a robust pattern: in the absence of binding fleet constraints, robo-taxi adoption monotonically increases VMT, with the increase driven by deadhead repositioning and the occupancy gap between robo-taxis (one passenger per trip) and private cars (1.2). When the fleet is capped tightly, total VMT plateaus or decreases, but at the cost of unmet demand and surge pricing, a cost shouldered by riders rather than by the road. Freeway corridors show asymmetric response by time of day: AM peak congests inbound segments of I-110 and US-101, while their outbound counterparts pick up empty deadhead flow.";
const CONC_2 = "Future work includes (i) integrating real-time traffic feeds for continuous calibration in place of static Google Maps observations, (ii) modeling first- and last-mile circulation on residential streets, currently excluded for tractability, (iii) coupling fleet-repositioning optimization with the adoption-dynamics module to study operator-rider equilibria as time-evolving systems, and (iv) extending the demand model to incorporate land-use feedbacks, since persistent congestion on a corridor changes long-run residential and commercial location decisions.";

const reportBody = [
  heading("Abstract"),
  body(ABSTRACT),

  heading("Introduction"),
  body(INTRO_1),
  body(INTRO_2),

  heading("Notation and Model"),
  body(MODEL_1),
  body(MODEL_2),
  body(MODEL_3),
  body(MODEL_4),

  heading("Proposed Solution"),
  body(SOL_1),
  body(SOL_2),
  body(SOL_3),
  body(SOL_4),

  heading("Conclusion and Future Work"),
  body(CONC_1),
  body(CONC_2),
];

// ---------- assemble ----------
const doc = new Document({
  styles: {
    default: { document: { run: { font: SERIF, size: 22 } } },
    paragraphStyles: [
      {
        id: "Heading1",
        name: "Heading 1",
        basedOn: "Normal",
        next: "Normal",
        quickFormat: true,
        run: { font: SERIF, size: 28, bold: true },
        paragraph: { spacing: { before: 280, after: 120 }, outlineLevel: 0 },
      },
    ],
  },
  sections: [
    {
      properties: {
        page: {
          size: { width: 12240, height: 15840 },
          margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 },
        },
      },
      footers: {
        default: new Footer({
          children: [
            new Paragraph({
              alignment: AlignmentType.CENTER,
              children: [
                new TextRun({ text: "Page ", font: SERIF, size: 18 }),
                new TextRun({ children: [PageNumber.CURRENT], font: SERIF, size: 18 }),
              ],
            }),
          ],
        }),
      },
      children: [...coverPage, ...reportBody],
    },
  ],
});

Packer.toBuffer(doc).then((buf) => {
  fs.writeFileSync("report.docx", buf);
  console.log("wrote report.docx (" + buf.length + " bytes)");
});
