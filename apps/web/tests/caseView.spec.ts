import { describe, expect, it } from "vitest";

import CaseList from "../app/components/CaseList";
import { caseDate, casePresentation, groupCases } from "../lib/caseView";
import { caseSummary } from "./caseFixtures";
import { elements, text } from "./elements";

describe("the shared dashboard and workstation case list", () => {
  it("groups by deposit day, newest first, without mutating the API response", () => {
    const older = caseSummary("older", { created_at: "2026-09-05T16:00:00Z" });
    const earlier = caseSummary("earlier", { created_at: "2026-09-06T08:00:00Z" });
    const newer = caseSummary("newer", { created_at: "2026-09-06T10:00:00+01:00" });
    const cases = [older, earlier, newer];
    const groups = groupCases(cases);
    expect(groups.map((group) => group.key)).toEqual(["2026-09-06", "2026-09-05"]);
    expect(groups.flatMap((group) => group.cases.map((row) => row.id))).toEqual([
      "newer", "earlier", "older",
    ]);
    expect(cases).toEqual([older, earlier, newer]);
  });

  it("links each reference to its workstation and marks only the active case", () => {
    const tree = CaseList({
      cases: [caseSummary("one"), caseSummary("two", { cancer_type: "bladder" })],
      activeCaseId: "two",
    });
    const links = elements(tree).filter((element) => element.props.href);
    expect(links.map((link) => link.props.href)).toEqual(["/poste/one", "/poste/two"]);
    expect(links.map((link) => link.props["aria-current"])).toEqual([undefined, "page"]);
    expect(text(tree)).toContain("DEMO-one");
    expect(text(tree)).toContain("Vessie");
    const dates = elements(tree).filter((element) => element.type === "time");
    expect(dates[0].props.dateTime).toBe("2026-09-06T11:00:00Z");
    expect(dates[0].props["aria-label"]).toBe("Mis à jour le 6 septembre 2026 à 11:00 UTC");
  });

  it.each([
    ["draft", "Brouillon", "unknown"],
    ["parsing", "Préparation du dossier", "wait"],
    ["ready_for_review", "À vérifier", "review"],
    ["analyzing", "Analyse en cours", "wait"],
    ["completed", "Fiche prête", "done"],
    ["blocked", "Sans synthèse exploitable", "review"],
    ["future_status", "État inconnu", "unknown"],
  ])("labels %s in words as well as colour", (status, label, tone) => {
    expect(casePresentation(status)).toEqual({ label, tone });
    const tree = CaseList({ cases: [caseSummary("one", { status })] });
    expect(text(tree)).toContain(label);
    expect(elements(tree).some((node) => node.props.className === `case-dot ${tone}`)).toBe(true);
    if (status === "blocked") expect(text(tree)).not.toContain("manquant");
  });

  it("offers an explicit first-run state instead of a placeholder or blank list", () => {
    expect(text(CaseList({ cases: [] }))).toContain("Déposez votre premier dossier");
  });

  it("does not invent a date when one is unavailable", () => {
    const invalid = caseSummary("invalid", { created_at: "", updated_at: "invalid" });
    expect(caseDate(invalid.updated_at)).toBeNull();
    const groups = groupCases([invalid, caseSummary("valid")]);
    expect(groups.map((group) => group.key)).toEqual(["2026-09-06", "unknown"]);
    const tree = CaseList({ cases: [invalid] });
    expect(text(tree)).toContain("Date de dépôt inconnue");
    expect(text(tree)).toContain("Mise à jour inconnue");
    expect(elements(tree).some((node) => node.type === "time")).toBe(false);
  });

  it("normalizes offset timestamps for identical server and browser display", () => {
    expect(caseDate("2026-09-07T00:30:00+02:00")).toMatchObject({
      day: "2026-09-06", short: "06/09/2026", time: "22:30",
    });
  });
});
