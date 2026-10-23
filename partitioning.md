# Council: oversize input (size check and partitioning)

Read this only when [core.md](core.md#artifact-selection) says the input is too large for one cascade.

Before packing a single cascade, apply a **size check**: if a careful human could not review the same packet in one sitting, do **not** dump it into one `artifact.md`. Cascade seats cannot sub-delegate or read unlisted paths; they will not auto-partition.

**When too large, run the cut-plan pipeline:**

1. **Seat 0 / chat model** proposes a size-bounded **framing** artifact: goals, constraints, interfaces/invariants, non-goals, partition map, stable path pointers. Exclude rationale essays, history, full code, and anything only one partition needs.
2. **Gemini breadth** on the cut plan (a cheap standalone breadth pass, or a Framing Council whose first seat is `role: breadth-auditor`).
3. **Human veto when input is large:** show the proposed partitions and wait for approve / reject / reshape before launching partition Councils. For small single-packet work, human veto is optional.
4. **Gate + freeze:** no partition Councils until framing has no open blocking gaps (or the human explicitly accepts remaining blockers as known risks). Then freeze/version the framing; do not edit it silently mid-flight.
5. **Partition Councils:** one run per cut. Put a short **invariants excerpt** in each `request.md`; list the full framing file only for partitions that touch shared interfaces.
6. **Framing flaws found mid-partition:** stop new partitions; fix/bump framing; re-run only affected partitions. Do not locally fork the shared framing inside one partition artifact.
7. Optional **integration** Council on the merged plan / conflict list.

Shared mutable contracts: prefer freeze-then-concurrent partitions; if the contract cannot freeze, run dependents sequentially with the contract owner first.

**Packing rules (every run):** `request.md` = short hints only; `artifact.md` = full review target (not a whole-tree dump); list only paths seats must read; set `review_roots` only for CLI `--add-dir` needs; prefer live disk over packed excerpts; never summarize the codebase into the artifact to "save tokens". Implementation artifacts should be tighter than prose (hot paths / changed files only; path-fenced bodies).

Do **not** silently digest or RAG-chunk `artifact.md` as a token optimizer. Packet hygiene (open-gaps, path prompts, envelopes, gaps-only reviewers, same-model skip, the parser summary) is the approved compression.
