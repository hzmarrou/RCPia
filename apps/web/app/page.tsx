import { API_BASE_URL, fetchHealth } from "@/lib/api";

// Server-side rendering runs inside the container, so it may need an internal
// service URL (http://api:8000) instead of the browser-visible one.
const SERVER_API_BASE_URL = process.env.API_INTERNAL_BASE_URL ?? API_BASE_URL;

export const dynamic = "force-dynamic";

async function readApiStatus(): Promise<string> {
  try {
    const health = await fetchHealth(SERVER_API_BASE_URL);
    return health.status;
  } catch {
    return "unreachable";
  }
}

export default async function HomePage() {
  const apiStatus = await readApiStatus();

  return (
    <>
      <h1>RCPia</h1>
      <p>
        Provisional RCP preparation support for prostate and bladder cancer cases. Every
        output is a draft for human review, never a final decision.
      </p>
      <dl>
        <dt>API status</dt>
        <dd data-testid="api-status">{apiStatus}</dd>
        <dt>API base URL</dt>
        <dd>{SERVER_API_BASE_URL}</dd>
      </dl>
    </>
  );
}
