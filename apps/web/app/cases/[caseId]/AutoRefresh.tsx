"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

/**
 * Re-fetch the page while the case is still being worked on.
 *
 * **Without this the app looks broken to a first-time user.** Parsing takes around half a
 * minute and the specialists a further three to five, and nothing on the page changed until
 * the clinician thought to reload it -- so the honest state, "still working", was
 * indistinguishable from the failure state, "nothing is happening". Neither the upload nor the
 * analysis has any other way of announcing that it finished.
 *
 * Server components cannot poll, so this is a client island that asks Next to re-render the
 * server page. It stops on its own once `active` goes false, rather than polling for the life
 * of the tab.
 */
export default function AutoRefresh({
  active,
  intervalMs = 10_000,
}: {
  active: boolean;
  intervalMs?: number;
}) {
  const router = useRouter();

  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => router.refresh(), intervalMs);
    return () => clearInterval(timer);
  }, [active, intervalMs, router]);

  return null;
}
