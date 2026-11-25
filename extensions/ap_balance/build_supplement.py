"""Build the English supplement from frozen, verified experimental tables."""

import csv
import hashlib
import json
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


ROOT = Path(__file__).resolve().parent
REPOSITORY = ROOT.parents[1]
OUTPUT = REPOSITORY / "supplementary/extended_experiments.pdf"
INK = colors.HexColor("#181818")
ACCENT = colors.HexColor("#9F3E2D")
BODY = ParagraphStyle("Body", fontName="Times-Roman", fontSize=10.5, leading=13.8,
                      textColor=INK, spaceAfter=7, alignment=TA_LEFT)
HEADING = ParagraphStyle("Heading", parent=BODY, fontName="Times-Bold", fontSize=13,
                         leading=16, spaceBefore=9, spaceAfter=8, keepWithNext=True)
TITLE = ParagraphStyle("Title", parent=HEADING, fontSize=21, leading=25, spaceBefore=0)
SUBTITLE = ParagraphStyle("Subtitle", parent=BODY, fontSize=13.5, leading=17, spaceAfter=11)
CAPTION = ParagraphStyle("Caption", parent=BODY, fontSize=9, leading=11.5, spaceAfter=7)
CELL = ParagraphStyle("Cell", parent=BODY, fontSize=9, leading=11.3, spaceAfter=0)
CELL_CENTER = ParagraphStyle("CellCenter", parent=CELL, alignment=TA_CENTER)
CODE = ParagraphStyle("Code", parent=BODY, fontName="Courier", fontSize=8.1, leading=11,
                      leftIndent=8, spaceAfter=4)
WIDTH = A4[0] - 96


def load_json(name):
    return json.loads((ROOT / "data" / name).read_text(encoding="utf-8"))


def load_csv(name):
    with (ROOT / "data" / name).open(encoding="utf-8-sig") as stream:
        return list(csv.DictReader(stream))


def bounds(row, metric):
    lower, upper = float(row[metric + "_lower"]), float(row[metric + "_upper"])
    return f"{lower:.2f}" if abs(lower - upper) < 1e-10 else f"{lower:.2f}-{upper:.2f}"


def main():
    for name, digest in load_json("TABLE_HASHES.json").items():
        assert hashlib.sha256((ROOT / "data" / name).read_bytes()).hexdigest() == digest, name
    results = load_json("strict_results.json")
    selection = load_json("strict_selection.json")
    metrics = load_csv("strict_metrics.csv")
    curves = load_csv("strict_ratio_curve.csv")
    legacy = load_json("legacy_results.json")
    methods = [row["method"] for row in results["main"]]
    assert selection["A"] == selection["P"] == 486
    assert results["paired_method_records"] == 3888
    story, markdown = [], []

    def paragraph(text, style=BODY):
        story.append(Paragraph(text, style))
        markdown.append(text.replace("<b>", "**").replace("</b>", "**"))

    def heading(text):
        paragraph(text, HEADING)

    def table(label, headers, rows, widths, note=None):
        paragraph(label, CAPTION)
        cells = []
        for row_index, row in enumerate([headers] + rows):
            cells.append([
                Paragraph(("<b>" + escape(str(value)) + "</b>") if row_index == 0 else escape(str(value)),
                          CELL if column_index == 0 else CELL_CENTER)
                for column_index, value in enumerate(row)
            ])
        grid = Table(cells, colWidths=[WIDTH * part for part in widths], repeatRows=1, hAlign="LEFT")
        grid.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F2EEE8")),
            ("LINEABOVE", (0, 0), (-1, 0), 0.8, INK),
            ("LINEBELOW", (0, 0), (-1, 0), 0.5, INK),
            ("LINEBELOW", (0, -1), (-1, -1), 0.6, INK),
        ]))
        story.append(grid)
        story.append(Spacer(1, 6))
        markdown.extend(["| " + " | ".join(str(value) for value in headers) + " |",
                         "| " + " | ".join("---" for value in headers) + " |"])
        markdown.extend("| " + " | ".join(str(value) for value in row) + " |" for row in rows)
        if note:
            paragraph(note, CAPTION)

    def page():
        story.append(PageBreak())
        markdown.append("\n---\n")

    paragraph("ACCESS: Supplementary Experiments", TITLE)
    paragraph("Sensitivity to the Proportion of Partially Serviceable Requests", SUBTITLE)
    paragraph("Post-submission extension | DeepSeek Flash | Balanced cohort: 972 requests", CAPTION)
    heading("S1. Experimental question and design")
    paragraph(
        "This extension asks whether the overall output-fulfillment ranking changes when requests with "
        "authorized partial outputs account for a larger share of evaluation. In the original output-evaluation "
        "cohort, 1,402 benchmark-allowed requests and 80 reference-positive benchmark-denied requests make "
        "up 1,482 requests; the latter account for only 5.40%. We construct a balanced cohort containing "
        "486 fully authorized requests (A) and 486 requests with verified authorized partial-output references (P). "
        "All four methods are evaluated on the same 972 request IDs."
    )
    paragraph(
        "The experiment combines real model outputs from the original study and a subsequent run on 445 new P "
        "requests. The balanced analysis reuses those outputs; it performs no additional inference. Cohort "
        "eligibility and selection use input metadata and reference audits, independently of candidate scores. "
        "The protocol records a retrospective analysis rather than a prospective preregistration."
    )
    heading("S2. Cohort construction and reference validation")
    paragraph(
        "A request is a source question at a particular specificity level and effective role policy. L0, L1, "
        "and L2 vary how explicitly the original requirements are stated; L2 retains the original question. "
        "Different levels or policies for one source question are related observations. We identify repeated "
        "effective cases by source question, level, and allowed-column set, rather than by role name alone."
    )
    paragraph(
        "<b>Eligibility.</b> A requires the complete Gold SQL to be authorized by the native database engine "
        "and to execute successfully. This yields 1,341 eligible A requests. P requires the complete Gold "
        "SQL to be blocked while an authorized SQL witness reproduces a nonempty proper projection of the "
        "Gold output. The witness must preserve necessary conditions, all result rows, duplicate multiplicities, "
        "and required ordering. Transformations involving DISTINCT or set-projection changes, and projections "
        "that remove an aggregate and leave only grouping labels, are excluded from this strict cohort."
    )
    paragraph(
        "P contains 41 eligible original requests and all 445 expansion requests. The expansion uses existing "
        "role permissions for 148 requests and additive grants for 297; it contains no permission revocations. "
        "Its 445 requests span 58 source questions and 149 question/policy combinations. Permissions are fixed "
        "before each run and shared by all methods. A complete Gold query remains blocked in every added case."
    )
    paragraph(
        "<b>Selection.</b> All 486 eligible P requests are retained. We select 486 A requests without replacement, "
        "stratifying by dataset and level. Hamilton largest-remainder allocation determines stratum quotas; "
        "within each stratum, a fixed SHA-256 ordering of the seed and request ID determines selection. The "
        "seed is <font name='Courier' size='8.4'>ACCESS_AP50_20261116_v1</font>."
    )
    paragraph(
        "The reference audit covers 1,927 available requests and 558 cache-deduplicated reference queries. "
        "SQLite checks use its native authorizer; PostgreSQL checks use isolated transactional roles with "
        "explicit column grants and rollback. The 20 SQLite database snapshots used by the expansion match "
        "their recorded hashes. No candidate prediction is used to decide reference eligibility."
    )

    page()
    heading("S3. Composition and evaluation protocol")
    composition = load_csv("strict_composition.csv")
    selected_p = {}
    for row in composition:
        if row["partition"] == "P":
            key = row["dataset"], int(row["level"])
            selected_p[key] = selected_p.get(key, 0) + int(row["requests"])
    labels = {"bird": "BIRD", "spider": "Spider", "livesqlbench": "LiveSQLBench"}
    rows = [[labels[row["dataset"]], "L" + str(row["level"]), row["eligible"], row["selected"],
             selected_p[(row["dataset"], row["level"])]] for row in selection["A_strata"]]
    rows.append(["Total", "All", 1341, 486, 486])
    table("Table S1. Strict cohort by dataset and specificity level.",
          ["Dataset", "Level", "Eligible A", "Selected A", "Selected P"], rows,
          [0.29, 0.12, 0.19, 0.20, 0.20],
          "Each partition contains 162 requests at each level. The 972 requests represent 345 source-question "
          "clusters; P represents 62. Sampling A does not require selecting every sibling level of a question.")
    paragraph(
        "<b>Methods and settings.</b> We compare Direct + RBAC, Serial, Policy, and ACCESS using DeepSeek "
        "Flash. The expansion retains the original method implementations and prompt variants: Direct and "
        "Serial use their RBAC SQL prompts; Policy and ACCESS hand their refined requests to the original "
        "Text-to-SQL prompt. SQL generation uses a 4,096-token output budget, temperature 0, and thinking "
        "disabled. ACCESS retains the original configuration of 30 user turns, 80 calls, and two retries for "
        "unclosed sessions. Reference SQL supports the simulated user and independent evaluation; it is not "
        "included in the clarification-agent or SQL-backend input. Historical calls were made in separate batches."
    )
    heading("S4. Metrics and evidence treatment")
    paragraph(
        "<b>SafeEX</b> is the request-weighted mean of the fraction of fixed target output columns that "
        "jointly match the reference. Credit requires a fully authorized, successfully executing candidate "
        "and complete projected result rows, including duplicates and required order. Authorization covers "
        "all referenced columns, including non-output predicates, joins, aggregates, and ordering. "
        "<b>Full</b> is the fraction with request-level SafeEX equal to one. It measures complete fixed-reference "
        "fulfillment; a P reference includes only the retained outputs, so Full does not mean fulfilling the "
        "entire original request. Extra authorized candidate columns do not by themselves invalidate Full."
    )
    paragraph(
        "Refusal, absent SQL, confirmed unauthorized access, and failed execution receive zero. Failed "
        "expansion attempts remain in the denominator: ACCESS has 21 unresolved outcomes and one internal "
        "validation failure; Policy has one response-format failure. Delivered expansion requests whose "
        "backend generation was interrupted by a scheduler exit were completed from their existing sessions; "
        "the clarification retry budget and method code were unchanged."
    )
    paragraph(
        "The audit reconciles all 118 historically uncertain DeepSeek scoring records, not only selected "
        "records, without changing candidate SQL. When two empty results leave column correspondence "
        "unidentified, a request retains evidence bounds [0, 1], unless a confirmed failure establishes zero. "
        "Selected-cohort uncertainty occurs only in A: 3, 4, 5, and 4 records for Direct, Serial, Policy, and "
        "ACCESS, respectively. All P scores are resolved. Metric ranges below average the evidence endpoints; "
        "they are distinct from the bootstrap intervals in Section S7."
    )

    page()
    heading("S5. Results on the balanced cohort")
    by_partition = {(row["method"], row["value"]): row for row in metrics if row["group"] == "partition"}
    rows = [[row["method"], bounds(row, "SafeEX"), bounds(row, "Full"),
             bounds(by_partition[row["method"], "A"], "SafeEX"),
             bounds(by_partition[row["method"], "P"], "SafeEX")] for row in results["main"]]
    table("Table S2. Output fulfillment on the strict 50/50 cohort (%).",
          ["Method", "SafeEX", "Full", "SafeEX on A", "SafeEX on P"], rows,
          [0.22, 0.20, 0.20, 0.21, 0.17],
          "Overall N = 972; each partition N = 486. Ranges are scoring evidence bounds. A single value "
          "indicates resolved evidence. Methods share exactly the same requests and reference targets.")
    paragraph(
        "ACCESS reaches 53.85-54.26% overall SafeEX, compared with 38.41-38.93% for Policy and "
        "32.97-33.38% for Serial. Its overall Full is 52.37-52.78%, compared with 37.55-38.07% for "
        "Policy. The SafeEX lower-endpoint advantage over Policy is 15.44 percentage points; even ACCESS's "
        "lower endpoint exceeds Policy's upper endpoint by 14.92 points."
    )
    paragraph(
        "The partition results explain this ordering. ACCESS has lower SafeEX on A than Policy "
        "(57.39% versus 65.30% at lower endpoints), but substantially higher SafeEX on P "
        "(50.31% versus 11.52%). With equal partition sizes, overall SafeEX equals one half of each "
        "partition's score. The -7.91-point A difference and +38.79-point P difference therefore contribute "
        "-3.96 and +19.39 points to the overall difference, respectively, yielding +15.44 points before "
        "rounding. ACCESS's advantage here reflects stronger partial-output fulfillment alongside a "
        "remaining cost on fully authorized requests."
    )
    heading("S6. Sensitivity to the proportion of P requests")
    paragraph(
        "To construct each curve, we fix the same 486 A requests and add nested prefixes of P in a "
        "metadata-only SHA-256 ordering using the fixed seed and the tag P_curve. The ten target shares "
        "range from 5% to 50%. Integer counts determine the actual plotted shares. Every point aggregates "
        "the corresponding recorded request-level outputs; no score is generated by interpolating partition means."
    )
    story.append(Image(str(ROOT / "figures/composition_curves.png"), width=WIDTH, height=WIDTH * 3.65 / 11.4))
    markdown.append("![Composition curves](figures/composition_curves.png)")
    paragraph(
        "Figure S1. SafeEX and Full as the P proportion increases. Lines show lower evidence endpoints; "
        "shaded bands show upper endpoints. Bands represent scoring uncertainty, not confidence intervals. "
        "A is fixed; P is nested; all methods use the same requests at each point.", CAPTION
    )
    paragraph(
        "At the first point (26 P requests; 5.078%), ACCESS's SafeEX is 57.21-57.99%, below both Serial "
        "and Policy. At the 20.066% point (122 P requests), ACCESS's lower SafeEX and Full endpoints "
        "exceed both baselines' upper endpoints. At 50%, ACCESS's SafeEX is slightly lower than at the "
        "first point, while the baselines decrease more. The observed reversal comes from the request "
        "composition and the methods' different partition performance, rather than an increase in ACCESS's "
        "absolute score throughout the curve."
    )

    page()
    heading("S7. Sampling sensitivity and paired comparisons")
    paragraph(
        "<b>Choice of A subset.</b> We hold all 486 P requests fixed and draw 1,000 alternative A subsets "
        "of size 486 from the eligible pool using the same dataset-by-level quotas. Replicate seeds are "
        "formed deterministically from the fixed seed and the replicate index; every replicate is retained. "
        "This examines sensitivity to which eligible A requests are included, without rerunning a model."
    )
    sensitivity = results["A_resampling"]
    rows = [[row["method"], "1,000 / 1,000", f"{row['ACCESS_worst_gap_min']:.2f}"]
            for row in sensitivity if row["method"] != "ACCESS"]
    table("Table S3. A-subset sensitivity with P fixed.",
          ["Comparator", "ACCESS lower > comparator upper", "Smallest margin (pp)"],
          rows, [0.25, 0.47, 0.28],
          "The margin compares ACCESS's lower score with the comparator's upper score within the same "
          "replicate. Results describe the audited candidate pool.")
    paragraph(
        "<b>Paired source-question bootstrap.</b> We draw 2,000 bootstrap samples of the 345 source-question "
        "clusters with replacement (seed 20261116). A cluster is identified by dataset, database, and original "
        "question ID. Its weight is shared across all methods, levels, policies, and A/P membership. Each "
        "replicate normalizes A and P separately and averages them equally, preserving the 50/50 estimand. "
        "The 95% percentile intervals below concern differences between lower-endpoint scores."
    )
    comparisons = [row for row in results["paired_cluster_bootstrap"] if row["metric"] == "SafeEX"]
    rows = [[row["comparison"].replace("ACCESS minus ", ""),
             f"{row['lower_endpoint_difference']:.2f}",
             f"[{row['paired_cluster_CI95_low']:.2f}, {row['paired_cluster_CI95_high']:.2f}]",
             f"[{row['evidence_difference_low']:.2f}, {row['evidence_difference_high']:.2f}]"] for row in comparisons]
    table("Table S4. ACCESS minus comparator: overall SafeEX differences (pp).",
          ["Comparator", "Difference", "Bootstrap 95% interval", "Evidence range"], rows,
          [0.25, 0.14, 0.32, 0.29],
          "Evidence range = [ACCESS lower - comparator upper, ACCESS upper - comparator lower]. "
          "Bootstrap intervals describe source-question resampling and are not evidence bounds.")
    paragraph(
        "All three SafeEX difference intervals have positive lower endpoints. The same paired procedure "
        "also gives positive Full difference intervals: 23.07-34.67 points versus Direct, 14.80-25.78 "
        "versus Serial, and 9.03-20.37 versus Policy. A-subset sensitivity and cluster bootstrap address "
        "different sources of variation: the former changes the selected A requests with P fixed, while "
        "the latter preserves dependencies among variants of each source question."
    )
    heading("S8. Interpretation and scope")
    paragraph(
        "The results show that overall output-fulfillment rankings can change when evaluation contains "
        "more requests with authorized partial outputs. This supports reporting both overall scores and "
        "A/P-specific performance. It also identifies an improvement target for ACCESS: retaining its "
        "partial-service benefit while reducing losses on fully authorized requests."
    )
    paragraph(
        "The curves describe this finite audited pool. P differs from A in source-question, dataset, and "
        "policy composition; the expansion adds BIRD and Spider cases, not new LiveSQLBench cases. P's "
        "486 requests arise from 62 source questions, so permission and specificity variants do not supply "
        "486 independent questions. The analysis does not isolate a causal effect of P proportion or "
        "estimate its prevalence in deployment. It evaluates output fulfillment, not safe handling on the "
        "remaining denied requests outside A + P."
    )

    page()
    heading("S9. Alignment with the original reference convention")
    paragraph(
        "For comparison with the original reference convention, a separate analysis retains all 80 original "
        "P references plus the 445 expansion requests, and samples 525 A requests from the original "
        "1,402-request A pool. This produces 1,050 requests and 4,200 method/request records. Its outcomes "
        "are reported separately from the strict cohort because their eligibility definitions differ."
    )
    table("Table S5. Balanced analysis using the original reference convention (%).",
          ["Method", "SafeEX", "Full"],
          [[row["method"], bounds(row, "SafeEX"), bounds(row, "Full")] for row in legacy["main"]],
          [0.36, 0.32, 0.32],
          "A = 525, P = 525. This cohort overlaps the strict cohort and is not an independent replication.")
    paragraph(
        "The strict definition excludes 39 original P references: 27 already have an authorized complete "
        "Gold query, six involve DISTINCT or set-projection transformations, and six retain only grouping "
        "labels after removal of an aggregate. Its A eligibility also excludes 61 original A requests "
        "whose complete Gold queries are blocked by native authorization. These are reference-eligibility "
        "decisions for this extension; the original benchmark files and recorded predictions remain unchanged. "
        "ACCESS leads the three comparators under both balanced definitions."
    )
    heading("S10. Reproducibility and verification")
    paragraph(
        "The source experiment package preserves inputs, effective permissions, target positions, Gold and "
        "witness SQL, all four methods' candidate SQL and outcomes, native audit evidence, selection lists, "
        "and recomputation scripts. Independent verification confirms equal A/P counts, unique effective "
        "cases, complete four-method pairing, fixture payload hashes, and request-level metric aggregation. "
        "This check passes for both cohorts: 3,888 records for strict and 4,200 for the original convention."
    )
    paragraph(
        "The repository directory <font name='Courier' size='8.5'>extensions/ap_balance/</font> includes "
        "the frozen tables used in this PDF, audit summaries, cohort metadata, table hashes, and the "
        "document/figure build scripts. The curve table includes exact counts at every point. These files "
        "support rebuilding the report; the full local experiment package is needed for request-level "
        "reaggregation. Native SQL re-execution additionally requires the recorded database snapshots."
    )
    paragraph("Run the following commands from the complete source experiment package:")
    for command in ["python code/verify.py", "python code/select_cohorts.py", "python code/analyze.py"]:
        paragraph(command, CODE)
    paragraph(
        "Selection reconstruction uses only the request universe and reference audit. Analysis reuses "
        "recorded outputs, applies hash-bound native scoring reconciliations, and recomputes metrics, "
        "composition curves, resampling checks, and paired intervals. Verification checks the frozen "
        "package manifest before reconstruction. Rendering is independent of model inference."
    )
    paragraph(
        "Implementation base: Git commit <font name='Courier' size='8.3'>54f6d73e8fce20c176aa439588291916eb6279ee</font>. "
        "Strict selection SHA-256: <font name='Courier' size='7.4'>"
        "ae53725c49c832c3ac385477ef881e23be673147b2a5a29aca676cb1b078c967</font>. "
        "Request-universe and source-file hashes are recorded in the accompanying provenance tables.", CAPTION
    )
    paragraph(
        "Code and supporting materials: "
        "<link href='https://github.com/wangcanran/access-text2sql' color='#9F3E2D'>"
        "github.com/wangcanran/access-text2sql</link>.", CAPTION
    )

    def decorate(canvas, document):
        canvas.saveState()
        canvas.setFillColor(INK)
        canvas.setFont("Helvetica", 8)
        canvas.drawString(48, A4[1] - 31, "ACCESS | SUPPLEMENTARY EXPERIMENTS")
        canvas.setStrokeColor(ACCENT)
        canvas.setLineWidth(0.7)
        canvas.line(48, A4[1] - 39, A4[0] - 48, A4[1] - 39)
        canvas.setFillColor(INK)
        canvas.setFont("Times-Roman", 9)
        canvas.drawCentredString(A4[0] / 2, 27, str(document.page))
        canvas.restoreState()

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    document = SimpleDocTemplate(str(OUTPUT), pagesize=A4, rightMargin=48, leftMargin=48,
                                 topMargin=55, bottomMargin=43, title="ACCESS: Supplementary Experiments",
                                 author="", subject="Sensitivity to partially serviceable request composition")
    document.build(story, onFirstPage=decorate, onLaterPages=decorate)
    (ROOT / "extended_experiments.md").write_text("\n\n".join(markdown) + "\n", encoding="utf-8")
    print(OUTPUT)


if __name__ == "__main__":
    main()
