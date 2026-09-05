import { CATEGORY_LABELS, type LedgerEntry } from "@/lib/ledger";

/**
 * Turning the ledger into something a clinician can read down.
 *
 * The API sends one flat list of every field the schema knows for this cancer type, present
 * or absent, because absence is the point: a gap shown in red is what the review is for.
 * Ninety-two rows in one run is faithful and unreadable, and two things make it worse than
 * it needs to be — the categories the API already sends are thrown away, and fields that
 * cannot apply to this patient are counted among the gaps.
 *
 * Nothing here changes what the API says. It groups, and it marks; the entries are the same
 * entries, and the agents read the ledger, not this.
 */

/**
 * Fields describing an anatomy or a condition this patient does not have.
 *
 * Deliberately short, and deliberately not derived from the cancer type. `fact_schema.py`
 * already refuses to infer sex from a diagnosis — a prostate cancer does not make a patient
 * male, and encoding that assumption would misgender trans and non-binary patients. This
 * reads the sex the dossier *records*, and only for fields where the mismatch is anatomical
 * rather than statistical: a man has no pregnancy to report and nothing to examine
 * vaginally. Anything a clinician might legitimately record for either sex stays on screen.
 */
const SEX_SPECIFIC: Record<string, "male" | "female"> = {
  pregnancy: "female",
  vaginal_exam: "female",
  iief5: "male",
};

const MALE_WORDS = new Set(["homme", "masculin", "m", "h", "male"]);
const FEMALE_WORDS = new Set(["femme", "feminin", "f", "female"]);

/**
 * The sex the dossier records, or null when it does not record one.
 *
 * Null is the safe answer and the common one: with no recorded sex nothing is marked
 * inapplicable, and the table stays exactly as faithful as it was.
 */
export function readSex(entries: LedgerEntry[]): "male" | "female" | null {
  const sex = entries.find((entry) => entry.key === "sex");
  // An uncertain sex marks nothing. Amber is the ledger saying the documents disagree or
  // the reading is doubtful, and a doubtful value must not quietly rule fields out.
  if (sex === undefined || sex.highlight === "amber") return null;
  const recorded = sex.value;
  if (!recorded) return null;
  const word = recorded
    .normalize("NFD")
    // Combining marks, so "Féminin" and "Feminin" are the same word.
    .replace(/\p{M}/gu, "")
    .trim()
    .toLowerCase();
  if (MALE_WORDS.has(word)) return "male";
  if (FEMALE_WORDS.has(word)) return "female";
  return null;
}

/** Whether this field can apply to a patient of this sex. */
export function applies(entry: LedgerEntry, sex: "male" | "female" | null): boolean {
  const restricted = SEX_SPECIFIC[entry.key];
  return restricted === undefined || sex === null || restricted === sex;
}

export type LedgerRow = { entry: LedgerEntry; applicable: boolean };

export type LedgerGroup = {
  category: string;
  label: string;
  rows: LedgerRow[];
  /** Fields read from the dossier. */
  present: number;
  /**
   * Fields the API reports as absent — **including** the ones marked "sans objet".
   *
   * They are counted because the API counts them: the specialists are sent the ledger as the
   * API holds it, so a gap subtracted here would be a gap the screen says does not exist and
   * the panel is told about anyway. Marking is a reading aid on top of the count, not a
   * correction of it, until applicability is decided in one place — the backend.
   */
  missing: number;
  notApplicable: number;
};

// The order the schema declares, which runs roughly as a clinician reads a dossier: who the
// patient is, then their history, then what was found. Categories the API adds later fall in
// behind, in the order they arrive, rather than disappearing.
const ORDER = [
  "identity",
  "social",
  "history",
  "presentation",
  "performance",
  "examination",
  "imaging",
  "biology",
  "pathology",
  "decision",
];

/**
 * The entries, in categories, with the inapplicable ones marked rather than removed.
 *
 * Marked, not removed, on purpose: a row that vanished would take with it the only sign that
 * RCPia decided the field was irrelevant — and if the recorded sex is wrong, the clinician
 * needs to see the consequence of that error, not a shorter table.
 */
export function groupEntries(
  entries: LedgerEntry[],
  sex: "male" | "female" | null,
): LedgerGroup[] {
  const groups = new Map<string, LedgerGroup>();

  for (const entry of entries) {
    let group = groups.get(entry.category);
    if (group === undefined) {
      group = {
        category: entry.category,
        label: CATEGORY_LABELS[entry.category] ?? entry.category,
        rows: [],
        present: 0,
        missing: 0,
        notApplicable: 0,
      };
      groups.set(entry.category, group);
    }

    const applicable = applies(entry, sex);
    group.rows.push({ entry, applicable });
    if (!applicable) group.notApplicable += 1;
    if (entry.value === null) group.missing += 1;
    else group.present += 1;
  }

  const known = ORDER.filter((category) => groups.has(category));
  const extra = [...groups.keys()].filter((category) => !ORDER.includes(category));
  return [...known, ...extra].map((category) => groups.get(category) as LedgerGroup);
}

/**
 * What the completeness panel counts — the API's own arithmetic, unaltered.
 *
 * `notApplicable` is reported alongside rather than subtracted. The gate promises the
 * specialists read the version on screen, and they are sent the ledger the API holds: a
 * total quietly reduced here would break that promise in the one direction that matters,
 * by showing fewer gaps than the panel is actually told about.
 */
export function tally(entries: LedgerEntry[], sex: "male" | "female" | null) {
  return {
    read: entries.filter((entry) => entry.value !== null).length,
    missing: entries.filter((entry) => entry.highlight === "red").length,
    uncertain: entries.filter((entry) => entry.highlight === "amber").length,
    notApplicable: entries.filter((entry) => !applies(entry, sex)).length,
  };
}
