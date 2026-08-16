export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export type HealthResponse = {
  status: string;
};

export async function fetchHealth(
  baseUrl: string = API_BASE_URL,
): Promise<HealthResponse> {
  const response = await fetch(`${baseUrl}/health`, { cache: "no-store" });

  if (!response.ok) {
    throw new Error(`API health check failed with status ${response.status}`);
  }

  return (await response.json()) as HealthResponse;
}
