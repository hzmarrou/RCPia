import Link from "next/link";

import { SERVER_API_BASE_URL, fetchHealth } from "@/lib/api";


export const dynamic = "force-dynamic";

async function readApiStatus(): Promise<string> {
  try {
    const health = await fetchHealth(SERVER_API_BASE_URL);
    return health.status;
  } catch {
    return "unreachable";
  }
}

/**
 * The first page anyone sees, signed out.
 *
 * It used to be a health card and nothing else: what the service is, whether the API
 * answered, and the API's own base URL. No way in — a visitor who did not already know to
 * type `/login` had reached a dead end, which is how the people invited to try this actually
 * arrive. The address of the API is gone too; it told an anonymous visitor where the data
 * lives and told an invited clinician nothing.
 *
 * The status line stays, because "is it up?" is the one question this page can answer
 * without an account, and it is the question asked when someone cannot sign in.
 */
export default async function HomePage() {
  const apiStatus = await readApiStatus();

  return (
    <>
      <h1>RCPia</h1>
      <p>
        Provisional RCP preparation support for prostate and bladder cancer cases. Every
        output is a draft for human review, never a final decision.
      </p>
      <p>
        <Link href="/login">Se connecter</Link>
      </p>
      <p className="muted">
        RCPia est accessible sur invitation.{" "}
        <Link href="/request-access">Demander un accès</Link>.
      </p>
      <p className="muted">
        Service : <span data-testid="api-status">{apiStatus === "ok" ? "disponible" : apiStatus}</span>
      </p>
    </>
  );
}
