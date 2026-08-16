import { API_BASE_URL } from "@/lib/api";

export const SESSION_COOKIE_NAME = "rcpia_session";

export type Role = "admin" | "uploader" | "viewer";

export type CurrentUser = {
  id: string;
  organization_id: string;
  email: string;
  full_name: string | null;
  role: Role;
  is_active: boolean;
};

export class AuthError extends Error {}

async function readError(response: Response, fallback: string): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: string };
    return body.detail ?? fallback;
  } catch {
    return fallback;
  }
}

export async function login(email: string, password: string): Promise<CurrentUser> {
  const response = await fetch(`${API_BASE_URL}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    // The session cookie is set by the API and must be stored by the browser.
    credentials: "include",
    body: JSON.stringify({ email, password }),
  });

  if (!response.ok) {
    throw new AuthError(await readError(response, "Connexion impossible"));
  }
  return (await response.json()) as CurrentUser;
}

export async function activate(
  token: string,
  password: string,
  fullName: string | null,
): Promise<CurrentUser> {
  const response = await fetch(`${API_BASE_URL}/auth/activate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify({ token, password, full_name: fullName }),
  });

  if (!response.ok) {
    throw new AuthError(await readError(response, "Activation impossible"));
  }
  return (await response.json()) as CurrentUser;
}

export async function logout(): Promise<void> {
  await fetch(`${API_BASE_URL}/auth/logout`, { method: "POST", credentials: "include" });
}

/** Resolve the signed-in user from a server component, forwarding the session cookie. */
export async function fetchCurrentUser(
  baseUrl: string,
  cookieHeader: string,
): Promise<CurrentUser | null> {
  const response = await fetch(`${baseUrl}/auth/me`, {
    headers: { cookie: cookieHeader },
    cache: "no-store",
  });

  if (response.status === 401) {
    return null;
  }
  if (!response.ok) {
    throw new AuthError(`Unexpected status ${response.status} from /auth/me`);
  }
  return (await response.json()) as CurrentUser;
}
