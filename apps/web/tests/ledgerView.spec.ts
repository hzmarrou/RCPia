import { describe, expect, it } from "vitest";

import type { LedgerEntry } from "../lib/ledger";
import { applies, groupEntries, readSex, tally } from "../lib/ledgerView";

/**
 * What the table may and may not decide for itself.
 *
 * The rule under test is narrow on purpose: a field is marked "sans objet" only when the
 * dossier *records* a sex the field cannot belong to. Nothing is inferred from the cancer
 * type, and nothing is removed.
 */

function entry(over: Partial<LedgerEntry> & { key: string }): LedgerEntry {
  return {
    category: "performance",
    label: over.key,
    value: null,
    highlight: "red",
    origin: "extracted",
    note: null,
    source_excerpt: null,
    ...over,
  } as LedgerEntry;
}

const MALE = entry({ key: "sex", category: "identity", value: "Homme", highlight: "neutral" });

describe("readSex", () => {
  it("reads what the dossier records, accents and case aside", () => {
    expect(readSex([entry({ key: "sex", value: "Homme" })])).toBe("male");
    expect(readSex([entry({ key: "sex", value: "  FÉMININ " })])).toBe("female");
    expect(readSex([entry({ key: "sex", value: "F" })])).toBe("female");
  });

  it("stays silent when the dossier does not say", () => {
    expect(readSex([entry({ key: "sex", value: null })])).toBeNull();
    expect(readSex([entry({ key: "sex", value: "non précisé" })])).toBeNull();
    expect(readSex([entry({ key: "age", value: "72" })])).toBeNull();
  });

  it("refuses to act on an uncertain sex", () => {
    // Amber is the ledger saying the reading is doubtful or the documents disagree. A
    // doubtful value must not rule fields out, however confidently it is spelled.
    expect(
      readSex([entry({ key: "sex", value: "Homme", highlight: "amber" })]),
    ).toBeNull();
  });
});

describe("applies", () => {
  it("marks a field the recorded sex rules out", () => {
    expect(applies(entry({ key: "vaginal_exam" }), "male")).toBe(false);
    expect(applies(entry({ key: "pregnancy" }), "male")).toBe(false);
    expect(applies(entry({ key: "iief5" }), "female")).toBe(false);
  });

  it("leaves everything applicable when no sex is recorded", () => {
    // Never guessed from the cancer type: a prostate diagnosis does not make a patient male.
    expect(applies(entry({ key: "vaginal_exam" }), null)).toBe(true);
    expect(applies(entry({ key: "pregnancy" }), null)).toBe(true);
  });

  it("does not touch a field either sex can have", () => {
    expect(applies(entry({ key: "creatinine" }), "male")).toBe(true);
    expect(applies(entry({ key: "g8_score" }), "female")).toBe(true);
  });
});

describe("groupEntries", () => {
  const entries = [
    MALE,
    entry({ key: "creatinine", category: "biology", value: "88", highlight: "neutral" }),
    entry({ key: "platelets", category: "biology", value: null, highlight: "red" }),
    entry({ key: "vaginal_exam", category: "examination" }),
  ];

  it("keeps the schema's reading order, not the order the API happened to send", () => {
    expect(groupEntries(entries, "male").map((group) => group.category)).toEqual([
      "identity",
      "examination",
      "biology",
    ]);
  });

  it("still counts a field ruled out by sex as the gap the API says it is", () => {
    // The specialists are sent the ledger the API holds, so marking must not subtract from
    // the count. A total quietly reduced here would show fewer gaps than the panel is told
    // about — and the gate promises the panel reads what is on screen.
    const examination = groupEntries(entries, "male").find(
      (group) => group.category === "examination",
    );
    expect(examination).toMatchObject({ present: 0, missing: 1, notApplicable: 1 });
  });

  it("keeps the ruled-out row on screen", () => {
    const examination = groupEntries(entries, "male").find(
      (group) => group.category === "examination",
    );
    // Hidden, a wrongly recorded sex would silently remove rows with nothing to show for it.
    expect(examination?.rows).toHaveLength(1);
    expect(examination?.rows[0].applicable).toBe(false);
  });

  it("counts the gaps that do apply", () => {
    const biology = groupEntries(entries, "male").find((group) => group.category === "biology");
    expect(biology).toMatchObject({ present: 1, missing: 1, notApplicable: 0 });
  });

  it("keeps a category the schema does not list, rather than dropping its rows", () => {
    const groups = groupEntries([entry({ key: "novelty", category: "genomics" })], null);
    expect(groups.map((group) => group.category)).toEqual(["genomics"]);
  });
});

describe("tally", () => {
  it("reports the API's arithmetic and counts the marked fields alongside", () => {
    const entries = [MALE, entry({ key: "vaginal_exam" }), entry({ key: "g8_score" })];
    expect(tally(entries, "male")).toEqual({
      read: 1,
      // Two gaps, because the API reports two gaps and sends both to the panel. One of them
      // is additionally marked sans objet, which is a reading aid, not a correction.
      missing: 2,
      uncertain: 0,
      notApplicable: 1,
    });
  });

  it("counts everything when the dossier records no sex", () => {
    const entries = [entry({ key: "vaginal_exam" }), entry({ key: "g8_score" })];
    expect(tally(entries, null)).toMatchObject({ missing: 2, notApplicable: 0 });
  });
});
