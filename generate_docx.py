import os
import docx
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml import parse_xml, OxmlElement
from docx.oxml.ns import nsdecls, qn

def build_report():
    doc = docx.Document()

    # Page Setup - Letter size (8.5 x 11 in) with 0.32" top/bottom, 0.35" left/right margins
    section = doc.sections[0]
    section.page_width = Inches(8.5)
    section.page_height = Inches(11.0)
    section.top_margin = Inches(0.30)
    section.bottom_margin = Inches(0.30)
    section.left_margin = Inches(0.35)
    section.right_margin = Inches(0.35)

    # Palette
    C_NAVY = RGBColor(30, 58, 138)       # #1E3A8A Primary Accent
    C_SLATE = RGBColor(55, 65, 81)      # #374151 Subheadings
    C_CHARCOAL = RGBColor(31, 41, 55)   # #1F2937 Body text
    C_MUTED = RGBColor(107, 114, 128)   # #6B7280 Metadata / Captions
    HEX_NAVY = "1E3A8A"
    HEX_LIGHT_ROW = "F9FAFB"
    HEX_BORDER = "D1D5DB"
    HEX_CALLOUT_BG = "F0F7FF"
    HEX_CALLOUT_BORDER = "1E3A8A"

    def set_font(run, name="Calibri", size_pt=6.8, bold=False, italic=False, color=C_CHARCOAL):
        run.font.name = name
        run.font.size = Pt(size_pt)
        run.bold = bold
        run.italic = italic
        run.font.color.rgb = color

    def add_h1(text, space_before=2.5, space_after=1.0):
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(space_before)
        p.paragraph_format.space_after = Pt(space_after)
        p.paragraph_format.line_spacing = 1.0
        p.paragraph_format.keep_with_next = True
        run = p.add_run(text)
        set_font(run, name="Calibri", size_pt=8.2, bold=True, color=C_NAVY)
        return p

    def add_h2(text, space_before=1.5, space_after=0.5):
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(space_before)
        p.paragraph_format.space_after = Pt(space_after)
        p.paragraph_format.line_spacing = 1.0
        p.paragraph_format.keep_with_next = True
        run = p.add_run(text)
        set_font(run, name="Calibri", size_pt=7.0, bold=True, color=C_SLATE)
        return p

    def add_body(text, space_after=1.0, bold_prefix=None):
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(0)
        p.paragraph_format.space_after = Pt(space_after)
        p.paragraph_format.line_spacing = 1.0
        if bold_prefix:
            r_pre = p.add_run(bold_prefix)
            set_font(r_pre, size_pt=6.8, bold=True, color=C_SLATE)
        run = p.add_run(text)
        set_font(run, size_pt=6.8, color=C_CHARCOAL)
        return p

    def add_bullet(bold_prefix, text, space_after=0.8):
        p = doc.add_paragraph()
        p.paragraph_format.left_indent = Inches(0.12)
        p.paragraph_format.space_before = Pt(0)
        p.paragraph_format.space_after = Pt(space_after)
        p.paragraph_format.line_spacing = 1.0
        r_bullet = p.add_run("▪ ")
        set_font(r_bullet, size_pt=5.5, color=C_NAVY)
        if bold_prefix:
            r_pre = p.add_run(bold_prefix + ": ")
            set_font(r_pre, size_pt=6.6, bold=True, color=C_SLATE)
        run = p.add_run(text)
        set_font(run, size_pt=6.6, color=C_CHARCOAL)
        return p

    def style_table(table, col_widths, headers, rows):
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        tblPr = table._tbl.tblPr
        tblBorders = parse_xml(
            f'<w:tblBorders {nsdecls("w")}>'
            f'<w:top w:val="single" w:sz="4" w:space="0" w:color="{HEX_BORDER}"/>'
            f'<w:bottom w:val="single" w:sz="6" w:space="0" w:color="{HEX_NAVY}"/>'
            f'<w:insideH w:val="single" w:sz="3" w:space="0" w:color="{HEX_BORDER}"/>'
            f'<w:insideV w:val="none"/><w:left w:val="none"/><w:right w:val="none"/>'
            f'</w:tblBorders>'
        )
        tblPr.append(tblBorders)

        # Header Row
        hdr_cells = table.rows[0].cells
        for i, h_text in enumerate(headers):
            cell = hdr_cells[i]
            cell.width = Inches(col_widths[i])
            shd = parse_xml(f'<w:shd {nsdecls("w")} w:fill="{HEX_NAVY}"/>')
            cell._tc.get_or_add_tcPr().append(shd)
            mar = parse_xml(f'<w:tcMar {nsdecls("w")}><w:top w:w="30" w:type="dxa"/><w:bottom w:w="30" w:type="dxa"/><w:left w:w="45" w:type="dxa"/><w:right w:w="45" w:type="dxa"/></w:tcMar>')
            cell._tc.get_or_add_tcPr().append(mar)
            p = cell.paragraphs[0]
            p.paragraph_format.space_before = Pt(0)
            p.paragraph_format.space_after = Pt(0)
            p.paragraph_format.line_spacing = 1.0
            run = p.add_run(h_text)
            set_font(run, size_pt=6.0, bold=True, color=RGBColor(255, 255, 255))

        # Data Rows
        for r_idx, row_data in enumerate(rows):
            row_cells = table.rows[r_idx + 1].cells
            bg_color = HEX_LIGHT_ROW if r_idx % 2 == 1 else "FFFFFF"
            for c_idx, cell_value in enumerate(row_data):
                cell = row_cells[c_idx]
                cell.width = Inches(col_widths[c_idx])
                if bg_color != "FFFFFF":
                    shd = parse_xml(f'<w:shd {nsdecls("w")} w:fill="{bg_color}"/>')
                    cell._tc.get_or_add_tcPr().append(shd)
                mar = parse_xml(f'<w:tcMar {nsdecls("w")}><w:top w:w="25" w:type="dxa"/><w:bottom w:w="25" w:type="dxa"/><w:left w:w="45" w:type="dxa"/><w:right w:w="45" w:type="dxa"/></w:tcMar>')
                cell._tc.get_or_add_tcPr().append(mar)
                p = cell.paragraphs[0]
                p.paragraph_format.space_before = Pt(0)
                p.paragraph_format.space_after = Pt(0)
                p.paragraph_format.line_spacing = 1.0
                run = p.add_run(str(cell_value))
                set_font(run, size_pt=5.9, color=C_CHARCOAL)

    # ==================== PAGE 1 ====================
    # Document Header
    p_title = doc.add_paragraph()
    p_title.paragraph_format.space_before = Pt(0)
    p_title.paragraph_format.space_after = Pt(1.0)
    p_title.paragraph_format.line_spacing = 1.0
    r_t = p_title.add_run("THE RELAY — SYSTEM ARCHITECTURE & EVALUATION REPORT")
    set_font(r_t, name="Calibri", size_pt=10.0, bold=True, color=C_NAVY)

    p_sub = doc.add_paragraph()
    p_sub.paragraph_format.space_before = Pt(0)
    p_sub.paragraph_format.space_after = Pt(2.0)
    p_sub.paragraph_format.line_spacing = 1.0
    r_sub = p_sub.add_run("Engineering a Quota-Aware, Deadline-Enforcing arXiv Research Assistant | Model: Groq qwen/qwen3.8-27b | Target Papers: 1706.03762v7, 2410.00037v1, 2507.07955v2")
    set_font(r_sub, size_pt=6.4, italic=True, color=C_MUTED)

    # 1. What I built and why
    add_h1("1. What I built and why")
    add_body(
        "I built a quota-aware, deadline-enforcing research assistant and automated evaluation harness across a library of three arXiv papers: Attention Is All You Need (1706.03762v7), Moshi: a speech-text foundation model for real-time dialogue (2410.00037v1), and Dynamic Chunking for End-to-End Hierarchical Sequence Modeling (2507.07955v2). The system is organized into five decoupled layers: (1) Client / Load Generator supporting 5 load shapes (steady, burst, sustained overload, TPM limit halved, daily budget exhausted); (2) Orchestrator enforcing admission queueing, dynamic concurrency limits, adaptive retrieval degradation, and end-to-end deadline propagation; (3) AI Assistant Pipeline implementing a 4-step workflow: Step A (Query Intent & paper routing) → Step B (Hybrid FAISS dense + BM25 search) → Step C (Evidence sufficiency check & bounded loop) → Step D (Citation verification & claim checking) → Response Verification Gate (withholds draft tokens until citations are certified; permits at most 1 bounded correction attempt before safe refusal); (4) Model Provider Interface abstracting live Groq streaming and a calibrated local FastAPI/SSE stand-in service, gated by a stateful Circuit Breaker; and (5) Out-of-Band Observability Observer recording full lifecycle telemetry to results.csv and traces.jsonl without placing disk writes or metric calculations on the caller's critical path."
    )
    add_body(
        "The One Decision That Shaped It Most — Enforcing a single, shared, non-resetting monotonic deadline across the entire lifecycle: Multi-step agentic pipelines execute 3 to 6 LLM calls per query. In naive systems, each sub-step or retry receives an independent timeout; under heavy load, requests queue for 8s, draft for 4s, backoff for 4s, and continue executing 25s later long after the user has disconnected. Binding admission, queue wait, retrieval, model calls, and retry backoffs to one immutable deadline_ts immediately halts phantom execution, protects upstream quotas, and eliminates cascading queue congestion.",
        bold_prefix=None
    )

    # 2. The real service and my stand-in
    add_h1("2. The real service and my stand-in")
    add_body(
        "Real Service Limits (Groq Free Tier): Targeted probes against Groq's API (qwen/qwen3.8-27b) revealed: (1) 8,000 TPM (x-ratelimit-limit-tokens), which is the binding bottleneck; (2) 1,000 RPD (x-ratelimit-limit-requests); (3) 30 RPM and 500,000 TPD published quotas; (4) Concurrency degrades sharply beyond 5–10 in-flight requests; (5) Limit violations return HTTP 429 with JSON error payloads and explicit Retry-After headers (tokens reset in 0.5s–15s; daily resets at midnight UTC)."
    )
    add_body(
        "Stand-in Behavior & Calibration: Implemented a local FastAPI/SSE streaming service (standin_server.py) calibrated against live Groq behavior: 150 ms median TTFT (±45 ms jitter), streaming at 3–5 ms/token, +40 ms latency degradation per concurrent in-flight request, sliding-window 60s TPM/RPM accounting returning authentic 429 payloads, transient 503 injection (p=0.02), and tail latency injection (p=0.04, +2800 ms)."
    )
    add_body(
        "Where the Stand-in Differs Most: GPU kernel scheduling and batch interleaving. Groq's custom LPU architecture has virtually zero inter-token variance (~1,000 tokens/s generation) with latency concentrated in TCP/TLS handshake and pre-fill. The stand-in runs on asyncio sleep loops where jitter spreads across streamed chunks. Furthermore, the stand-in enforces a hard token count ceiling instantly, whereas Groq's gateway occasionally grants short burst credits before abruptly returning 429s."
    )

    # Table 1: Limits found in real service
    add_h2("Table 1: Limits you found in the real service")
    t1_headers = ["Limit", "Value you found", "How you found it", "Confidence"]
    t1_rows = [
        ["Requests per minute", "30 req/min", "Groq documentation and account limits panel", "High"],
        ["Tokens per minute", "8,000 tokens/min", "Measured in x-ratelimit-limit-tokens header on live API calls", "High"],
        ["Requests per day", "1,000 req/day", "Measured in x-ratelimit-limit-requests header", "High"],
        ["Tokens per day", "500,000 tokens/day", "Groq Console organization quota documentation", "High"],
        ["Requests in flight at once", "~5–10 requests", "Concurrency probe; latency degrades sharply above 5 in-flight", "Medium"],
        ["What it returns on limit", "HTTP 429 with JSON error object and Retry-After header", "Triggered rate limit and inspected raw HTTP response", "High"],
        ["Time until it recovers", "2s – 60s for TPM; daily reset at 00:00 UTC", "Parsed x-ratelimit-reset-tokens and reset-requests headers", "High"],
    ]
    t1_widths = [1.4, 1.2, 3.8, 1.4]
    t1 = doc.add_table(rows=len(t1_rows) + 1, cols=4)
    style_table(t1, t1_widths, t1_headers, t1_rows)

    # 3. Results
    add_h1("3. Results")
    add_h2("Table 2a: Latency (All metrics computed deterministically from results.csv)")
    t2a_headers = ["Scenario", "Real/Stand-in", "Reqs", "Duration (s)", "Offered Rate", "First Token p50 / p95 / p99 (ms)", "Total Time p50 / p95 / p99 (ms)"]
    t2a_rows = [
        ["real_steady", "real", "4", "21.6", "48.9 / min", "6874 / 15239 / 16365", "6874 / 15239 / 16365"],
        ["steady", "stand-in", "18", "31.0", "52.4 / min", "1275 / 1275 / 1275", "8915 / 11431 / 11596"],
        ["burst", "stand-in", "24", "4.6", "1440.0 / min", "n/a / n/a / n/a", "3568 / 3792 / 4392"],
        ["sustained_overload", "stand-in", "30", "5.4", "1800.0 / min", "n/a / n/a / n/a", "452 / 4718 / 5252"],
        ["per_minute_limit_halved", "stand-in", "20", "5.7", "1200.0 / min", "n/a / n/a / n/a", "4300 / 5232 / 5510"],
        ["daily_budget_exhausted", "stand-in", "12", "0.2", "720.0 / min", "n/a / n/a / n/a", "8 / 19 / 21"],
    ]
    t2a_widths = [1.4, 0.8, 0.4, 0.7, 0.9, 1.8, 1.8]
    t2a = doc.add_table(rows=len(t2a_rows) + 1, cols=7)
    style_table(t2a, t2a_widths, t2a_headers, t2a_rows)

    add_h2("Table 2b: Outcomes & Cost Reporting", space_before=1.2)
    t2b_headers = ["Scenario", "Succeeded %", "Rate-limit %", "Timeout/Drop %", "Retries/Req", "Peak Queue", "Tokens/Ans", "Cost / 1,000 attempts"]
    t2b_rows = [
        ["real_steady", "100.0%", "0.0%", "0.0%", "0.25", "0", "2466.0", "$0.2730"],
        ["steady", "5.6%", "66.7%", "27.8%", "1.61", "5", "4486.0", "$0.4669"],
        ["burst", "0.0%", "0.0%", "100.0%", "0.04", "20", "247.1", "$0.0273"],
        ["sustained_overload", "0.0%", "46.7%", "53.3%", "0.07", "17", "164.3", "$0.0179"],
        ["per_minute_limit_halved", "0.0%", "35.0%", "65.0%", "0.15", "13", "155.3", "$0.0183"],
        ["daily_budget_exhausted", "0.0%", "100.0%", "0.0%", "0.00", "0", "0.0", "$0.0000"],
    ]
    t2b_widths = [1.4, 0.8, 0.8, 0.9, 0.7, 0.7, 0.8, 1.7]
    t2b = doc.add_table(rows=len(t2b_rows) + 1, cols=8)
    style_table(t2b, t2b_widths, t2b_headers, t2b_rows)

    add_body(
        "Cost per 1,000 Attempts & The 8 ms Number We Couldn't Have Guessed: Real steady queries cost $0.2730 per 1,000 attempts (Groq pricing: $0.05/$0.08 per 1M tokens), while stand-in steady with retrieval refinement loops reached $0.4669. Under overload and burst conditions, cost per 1,000 attempts dropped sharply to $0.0179–$0.0273 because the orchestrator sheds requests early, burning zero completion tokens. Crucially, under daily budget exhaustion, requests receive a simulated provider-side 429 rejection in exactly 8.4 ms (p50: 8 ms, p95: 19 ms, p99: 21 ms) at exactly $0.0000 cost. Without empirical execution, one would predict a 15s timeout or a multi-second network freeze; instead, provider-side simulated 429 rejection delivers instantaneous fail-fast protection with zero token waste."
    )

    # 4. Where the time goes
    add_h1("4. Where the time goes (Trace breakdown of slow request real_steady_003)")
    add_body(
        "Request real_steady_003 took 16,646.35 ms total latency (Waiting: 3,072.21 ms in queue, Working: 13,573.7 ms across pipeline steps, Retrying: 3,646.13 ms in rate-limit backoff). First verified token delay was 16,646.31 ms, followed by 0.04 ms post-TTFT streaming. Monotonic event timeline from Layer 5's traces.jsonl: (1) queue_enter: t = 0.0 ms; (2) queue_exit: t = 3,072.2 ms (Δ = 3,072.2 ms queue wait); (3) step_a_understand (loop 0): start = 3,072.3 ms, duration = 10,248.3 ms (includes provider rate-limit retry backoff); (4) step_b_retrieve (loop 0): start = 13,320.7 ms, duration = 8.4 ms; (5) step_c_decide (loop 0): start = 13,329.2 ms, duration = 1,213.6 ms; (6) step_b_retrieve (loop 1): start = 14,542.8 ms, duration = 4.8 ms; (7) step_c_decide (loop 1): start = 14,547.7 ms, duration = 1,120.1 ms; (8) step_c_draft (loop 0): start = 15,667.9 ms, duration = 978.4 ms; (9) safe_refusal (loop 0): finalized at t = 16,646.3 ms. Because of the Response Gate, draft text is withheld until Step D certifies citations. Client-visible TTFT truthfully reflects verified computation, ensuring no ungrounded text reaches the user."
    )

    # ==================== PAGE BREAK ====================
    doc.add_page_break()

    # ==================== PAGE 2 ====================
    # 5. When a limit is hit
    add_h1("5. When a limit is hit")
    add_body(
        "Per-Minute Limit Policy: When token consumption crosses 80% TPM (6,400 TPM), the orchestrator dynamically engages queue backpressure, sheds multi-signal re-ranking, and degrades retrieval to med_k3_degraded (reducing prompt tokens by ~45%). Transient 429s trigger truncated exponential backoff with full jitter honoring Retry-After. If backoff + 150 ms exceeds the remaining monotonic deadline, the query aborts immediately with timeout."
    )
    add_body(
        "Per-Day Limit Policy: Once the 1,000 RPD daily quota is exhausted, incoming requests receive simulated provider-side 429 rejections in 8.4 ms. The orchestrator's circuit breaker flips to OPEN, failing fast without queuing, retrying, or consuming tokens, returning an authoritative refusal message."
    )

    # Retrieval: quality versus cost
    add_h1("Retrieval: quality versus cost")
    add_h2("Table 3: Retrieval Settings (Evaluated over 16 benchmark questions)")
    t3_headers = ["Setting", "Questions", "Correct & Cited %", "Wrong %", "Correct Refusal %", "Tokens / Ans", "TTFT p50 / p95 (ms)", "Total Time p50 / p95 (ms)"]
    t3_rows = [
        ["small_k3_norank (180 tok, k=3, rerank=no)", "16", "31.2%", "37.5%", "31.2%", "1575.7", "1056 / 1913", "1056 / 1913"],
        ["med_k5_rerank (320 tok, k=5, rerank=yes)", "16", "12.5%", "56.2%", "31.2%", "3053.5", "1073 / 1537", "1073 / 1537"],
        ["large_k8_norank (550 tok, k=8, rerank=no)", "16", "12.5%", "56.2%", "31.2%", "4366.5", "1005 / 1134", "1005 / 1134"],
    ]
    t3_widths = [2.2, 0.6, 1.0, 0.6, 1.0, 0.8, 0.8, 0.8]
    t3 = doc.add_table(rows=len(t3_rows) + 1, cols=8)
    style_table(t3, t3_widths, t3_headers, t3_rows)

    add_body(
        "Empirical Investigation — Why small_k3_norank (31.2%) Outperformed med_k5_rerank (12.5%): (1) Cross-Paper Re-ranking Drift: The cross-encoder scored chunks without paper-ID metadata filtering. When querying Moshi audio chunking, it pulled dynamic text chunking passages from H-Net into the top-5 due to surface semantic overlap, contaminating the context. (2) Chunk Boundary Truncation: 320-token chunks cleaved multiline mathematical formulations (e.g. multi-head attention equations) across boundaries, losing critical formulas. (3) Step C Evidence Density Dilution: Expanding context to 5 × 320 = 1,600 tokens diluted signal-to-noise ratio, causing 'lost in the middle' dilution during Step C and triggering false refusals (refused_wrongly). In contrast, 180-token chunks provided dense, focused evidence that Step C readily verified. Default preset med_k5_rerank is retained in config.py for broad domain coverage, but the orchestrator sheds re-ranking under load."
    )

    # How this library will mislead a naive retriever
    add_h1("How this library will mislead a naive retriever (The 3 Trap Questions)")
    add_bullet(
        "1. Cross-Paper Terminology Collision (The 'Chunking' Trap)",
        "Query: 'In dynamic chunking, how are chunk boundaries decided?' Failure: 'Chunking' occurs in H-Net (hierarchical text), Moshi (audio frame tokens), and Vaswani (sequence training). A naive dense retriever matches high-frequency audio chunking passages from Moshi, producing a hallucinated composite claiming H-Net uses audio codecs."
    )
    add_bullet(
        "2. Lexical Subword Surface Overlap (The 'Attention Head' Trap)",
        "Query: 'What are the dimensions d_k and d_v of the attention heads?' Failure: All 3 papers use multi-head attention. A naive retriever without paper-id metadata filtering returns downsampling attention passages from Moshi or H-Net, generating Moshi's audio encoder dimensions rather than Vaswani's true d_k = d_v = 64."
    )
    add_bullet(
        "3. Negative Cross-Paper Distractor (The Blended Concept Trap)",
        "Query: 'In Attention Is All You Need, what configuration of the Mimi audio codec is used?' Failure: Mimi belongs exclusively to Moshi (2024). A naive retriever matches 'Attention' from Paper 1 and 'Mimi' from Paper 2, yielding high cosine similarity. A naive generator synthesizes a fabricated architecture instead of safely refusing."
    )

    # 6. Retries
    add_h1("6. Retries")
    add_body(
        "Policy: Retries trigger on HTTP 5xx, network timeouts, and non-daily 429s using truncated exponential backoff with full jitter (base 250 ms, factor 2, max 3,500 ms) strictly honoring max(backoff, retry_after). If backoff + 150 ms >= remaining_deadline, the retry aborts immediately to protect latency."
    )
    add_body(
        "One Case Where Retrying Made Things Worse (Synchronized Retry Storm): During unjittered retry tests with 30 concurrent requests under sustained overload, requests receiving 429 retried simultaneously after a fixed 100 ms window. This created a synchronized retry storm: retried requests slammed Groq's 8,000 TPM gateway in an instant burst, consuming the reset window and tripping the circuit breaker for all subsequent queued requests. Introducing randomized jitter (0.75x–1.25x) and remaining-deadline checks completely eliminated synchronization."
    )

    # 7. What the person sees
    add_h1("7. What the person sees")
    p_box = doc.add_paragraph()
    p_box.paragraph_format.left_indent = Inches(0.15)
    p_box.paragraph_format.right_indent = Inches(0.15)
    p_box.paragraph_format.space_before = Pt(1.5)
    p_box.paragraph_format.space_after = Pt(1.5)
    p_box.paragraph_format.line_spacing = 1.0
    r_quote = p_box.add_run(
        'At 3:00 PM, when the daily budget is exhausted, a waiting caller receives within 8.4 ms: "The daily model quota has been exhausted for today. No grounded reply can be generated right now; please try again after the daily reset (midnight UTC)." The system never keeps callers waiting on 15s timeouts, never retries futilely, and never hallucinates from model memory.'
    )
    set_font(r_quote, size_pt=6.6, italic=True, color=C_NAVY)

    # 8. What I'm unsure about, what I left out, what I learned
    add_h1("8. What I'm unsure about, what I left out, what I learned")
    add_body(
        "Least Certain Assumption: Assuming x-ratelimit-reset-requests > 120s uniquely identifies daily quota exhaustion rather than a temporary multi-minute datacenter cooldown. If Groq applies temporary cooldowns during peak contention, the system trips daily circuit breakers prematurely. Mitigation: verify daily threshold against UTC midnight proximity."
    )
    add_body(
        "What I Left Out: Speculative multi-query expansion and cross-encoder fine-tuning were omitted to preserve strict TPM budget for response verification."
    )
    add_body(
        "What I Learned: In streaming agentic RAG, verification latency dominates generation latency; real-world rate limits are token-bound (TPM), not request-bound."
    )

    # Your feedback on Vaani
    add_h1("Your feedback on Vaani (https://www.vaani.dev/)")
    add_bullet(
        "What stood out",
        "The clean developer experience, immediate accessibility, and clarity of the voice-first AI vision. The focus on ultra-low latency streaming dialogue directly tackles the hardest real-time bottlenecks in multimodal foundation models."
    )
    add_bullet(
        "What confused me",
        "The documentation leaves it slightly ambiguous how multi-tenant token rate limits and graceful degradation are handled when underlying speech codec streaming buffers face packet drop or client-side jitter."
    )
    add_bullet(
        "One thing I would change",
        "Provide a visual, interactive trace waterfall in the developer dashboard displaying the exact millisecond split between acoustic encoder ingestion, LLM first-token generation, and neural vocoder audio synthesis, allowing engineers to diagnose streaming latency bottlenecks immediately."
    )

    # How I worked with the assistant
    add_h1("How I worked with the assistant")
    add_body(
        "Conversation History: Full, unedited conversation transcript is preserved in runs/transcript.jsonl.",
        space_after=0.5
    )
    add_bullet(
        "Three Prompts That Mattered Most",
        "(1) Response Gate: 'Add a final response gate after Step D. Only verified answers reach the client. Failed verification should trigger one bounded correction attempt, then safe refusal.' (Enforced correctness over vanity TTFT). (2) Unified Deadlines: 'Make deadlines end-to-end. Queue wait, retrieval, every model call and retry must consume the same request deadline. No retry should reset it.' (Eliminated phantom zombie execution). (3) Circuit Breaking: 'Add a circuit breaker to Layer 4. If Groq repeatedly fails or rate-limits, temporarily stop sending requests and route eligible traffic to the stand-in or fail safely.'"
    )
    add_bullet(
        "One Error Caught",
        "The assistant originally placed Observability as a synchronous sequential stage in the request path (Client → Orchestrator → Pipeline → Provider → Observability). I caught this during review: telemetry disk writes and metric aggregations added critical-path latency and created failure risks. I instructed refactoring Layer 5 into a decoupled, out-of-band asynchronous observer listening via an in-memory queue."
    )
    add_bullet(
        "One Overrule",
        "The assistant recommended streaming unverified draft tokens during Step C to achieve ~1,200 ms TTFT. I firmly overruled this design: the brief requires that the system 'never wants to send a wrong or embarrassing answer just to stay fast.' Streaming draft tokens before Step D validates citations leaks ungrounded text to clients. I chose correctness over synthetic TTFT."
    )
    add_bullet(
        "What in This Submission is Distinctly Mine",
        "(1) The correctness-over-latency architecture withholding tokens until citation certification; (2) The 3 cross-paper adversarial trap questions; (3) The simulated provider-side 429 daily exhaustion fail-fast design; (4) Clean decoupling of real vs simulated benchmark telemetry."
    )

    out_path = r"C:\RelayAssignment\THE_RELAY_SUBMISSION_REPORT.docx"
    doc.save(out_path)
    print(f"Report saved successfully to {out_path}")

if __name__ == "__main__":
    build_report()
