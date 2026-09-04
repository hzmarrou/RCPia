# Onco-gériatrie — wiring status

**Wired, dormant, and not convened by anything.** `agents/geriatric_frailty/` exists and the
agent runs, but no panel includes it: `AUTO_PANEL_ROLES` is empty in `core/config.py`,
`docker-compose.yml`, `.env.example` and `container-apps.bicep`. It reaches a case only if
someone asks for it by name through the analysis API, which accepts it because
`geriatric_frailty` has always been in `AGENT_ROLES`.

`brain.md` is a byte-for-byte copy of `agent-geriatric-frailty-brain-v1.md`. Keep it that way:
the design document is the source, the agent folder is the deployment.

## Why it was wired

Measured 4 September 2026 over 319 valid runs. Generalist agents spend about a third of their
fourteen guideline slots on geriatric and health-status passages, and cite them at **19%**
against **56%** for everything else — roughly 3.7 slots per run that never reach the opinion.

Handed to this agent instead, the same class of passage lands differently: 14 of 14 slots
on-topic, 5 cited. The passages were not useless; they were going to the wrong reader.

**This does not reclaim the generalists' slots.** `query/builder.py` builds each agent's query
from the case and that agent's own role — never from panel composition — so adding a
speciality changes nothing about what medical oncology retrieves. That remains open, and the
options on it (a larger budget, or a diversity cap in reranking) are retrieval policy.

## What was measured, including the part that went wrong

Four runs on REGEN5 cases:

| run | bundle | reasoning | total | outcome |
|---|---|---|---|---|
| REGEN5-12 | 144s | 83s | 227s | succeeded |
| REGEN5-09 | — | — | >600s | **timed out**, requeued |
| REGEN5-08 | 10s | 82s | 94s | succeeded |
| REGEN5-08 (urology, same worker, back to back) | 10s | 73s | 85s | succeeded |

The 144-second bundle and the timeout led to a first conclusion that this agent's retrieval was
twenty times slower than its peers. **That was wrong.** Timing `build_bundle` directly on one
case gives urology 8.2s, medical oncology 7.3s and this agent 7.4s, and the back-to-back pair
above puts it 9 seconds — 11% — behind urology end to end.

Both slow events happened while three worker containers were being stopped and restarted around
them. The likely cause is contention or throttling on the shared Azure OpenAI resource, not the
role. It is not proven, and one unexplained 600-second timeout is the reason this stays dormant.

## Before enabling it anywhere

- Establish what the timeout was. A run that exceeds `AGENT_TIMEOUT_SECONDS` is requeued and
  spends the budget again; in a panel the synthesis waits for it.
- The brain is `v1` and unreviewed, like every passage in the corpus. This agent's opinions
  would reach a fiche.
- `radiology`, `pathology` and `safety_reviewer` are empty stubs in `agents/` — a folder with
  `.pi/.gitkeep` and nothing else. They are not wired, and the fiche is right to list them as
  absent.
