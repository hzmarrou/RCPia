# Unblinding key — *Évaluation double aveugle RCPia*

Supplied by the clinician, 17 August 2026. **This file is the only record of which fiche is
human and which is machine.** Without it every comparison inverts on the seven dossiers
where the machine document comes first, and the inversion would not look wrong — it would
look like RCPia matching the human benchmark closely while the real fiche scored badly.

Source documents: `Evaluation double aveugle RCPia/Dossier N/Dossier N PPS {1,2}.docx`.

| Dossier | PPS 1 | PPS 2 |
|---|---|---|
| 1 | **Réel** (human) | RCPia |
| 2 | **Réel** | RCPia |
| 3 | RCPia | **Réel** |
| 4 | **Réel** | RCPia |
| 5 | RCPia | **Réel** |
| 6 | RCPia | **Réel** |
| 7 | **Réel** | RCPia |
| 8 | RCPia | **Réel** |
| 9 | **Réel** | RCPia |
| 10 | RCPia | **Réel** |
| 11 | **Réel** | RCPia |
| 12 | **Réel** | RCPia |
| 13 | **Réel** | RCPia |
| 14 | RCPia | **Réel** |
| 15 | RCPia | **Réel** |

Human first in 8 of 15, machine first in 7 — a genuine randomisation, not a fixed order.

`pps-example-01.md` and `pps-example-02.md` in `examples/` are Dossier 1's pair, so `01` is
human and `02` is machine, consistent with this table and with the inference recorded in
`pps-format.md`.

## The RCPia column is an *earlier* build

"RCPia" here means the agent that existed when this evaluation was prepared, not the system
in this repository. Those documents are a **regression baseline** — what the previous build
produced — not a target to match. The human column is the clinical reference.

## Reading a difference

A difference between RCPia's output and the human fiche is a question, not a defect.
Dossier 1's human fiche prescribes intravenous albumin as nutritional repletion, which is a
practice error (see `pps-format.md`). Departures from the human document must be reviewed
individually with a clinician; counting them as errors would tune RCPia towards reproducing
mistakes.
