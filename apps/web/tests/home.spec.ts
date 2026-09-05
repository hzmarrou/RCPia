import type { ReactElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import HomePage from "../app/page";

/**
 * What the front door must offer.
 *
 * These walk the element tree the page actually produces rather than reading its source, so
 * a link that drifted into a comment or behind a branch that never runs would fail here. No
 * rendering library is involved: a server component is a function returning elements, and
 * the question being asked — is there a way in, and is the API's address published — is
 * answered by the tree itself.
 */

type Node = { type?: unknown; props?: Record<string, unknown> } | string | number | null;

function walk(node: unknown, seen: { hrefs: string[]; text: string[] }): void {
  if (node === null || node === undefined || typeof node === "boolean") return;
  if (typeof node === "string" || typeof node === "number") {
    seen.text.push(String(node));
    return;
  }
  if (Array.isArray(node)) {
    for (const child of node) walk(child, seen);
    return;
  }
  const element = node as Exclude<Node, string | number | null>;
  const props = (element?.props ?? {}) as Record<string, unknown>;
  if (typeof props.href === "string") seen.hrefs.push(props.href);
  walk(props.children, seen);
}

async function render(status = 200, body = '{"status":"ok"}') {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(body, { status })),
  );
  const tree = (await HomePage()) as ReactElement;
  const seen = { hrefs: [] as string[], text: [] as string[] };
  walk(tree, seen);
  return { ...seen, joined: seen.text.join(" ") };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the landing page", () => {
  it("offers a way in", async () => {
    // It was a health card with no link at all: a visitor who did not already know to type
    // /login had reached a dead end, which is how invited clinicians actually arrive.
    const { hrefs } = await render();
    expect(hrefs).toContain("/login");
  });

  it("points people with no account at the right place", async () => {
    const { hrefs } = await render();
    expect(hrefs).toContain("/request-access");
  });

  it("does not publish the API base URL", async () => {
    // Told an anonymous visitor where the data lives; told an invited clinician nothing.
    const { joined, hrefs } = await render();
    expect(joined).not.toMatch(/https?:\/\/[^\s]*:\d{2,5}/);
    expect(hrefs.join(" ")).not.toMatch(/https?:\/\//);
  });

  it("says the service is available in words", async () => {
    const { joined } = await render();
    expect(joined).toContain("disponible");
  });

  it("says so plainly when the API cannot be reached", async () => {
    // The question this page exists to answer without an account, asked by someone who has
    // just failed to sign in.
    const { joined } = await render(503, "");
    expect(joined).toContain("unreachable");
  });

  it("keeps the provisional framing", async () => {
    const { joined } = await render();
    expect(joined).toMatch(/draft for human review/);
  });
});
