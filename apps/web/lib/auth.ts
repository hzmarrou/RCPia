import { API_BASE_URL } from "@/lib/api";

export const SESSION_COOKIE_NAME = "rcpia_session";

export type Role = "admin" | "uploader" | "viewer";

export const ROLE_LABELS: Record<Role, string> = {
  admin: "Administrateur",
  uploader: "Déposant",
  viewer: "Lecteur",
};

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
    const body = (await response.json()) as { detail?: unknown };
    return typeof body.detail === "string" ? body.detail : fallback;
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

export type Access =
  | { state: "authorized"; user: CurrentUser }
  | { state: "unauthenticated" }
  | { state: "forbidden" };

/**
 * Ask the API whether this session may use the dashboard.
 *
 * Deliberately not "fetch the user, then check their role here": the API owns
 * authorization, and duplicating the rule in the frontend is how the two drift apart.
 * A viewer must be refused by the same 403 that protects the data.
 */
export async function fetchDashboardAccess(
  baseUrl: string,
  cookieHeader: string,
): Promise<Access> {
  const response = await fetch(`${baseUrl}/auth/dashboard`, {
    headers: { cookie: cookieHeader },
    cache: "no-store",
  });

  if (response.status === 401) {
    return { state: "unauthenticated" };
  }
  if (response.status === 403) {
    return { state: "forbidden" };
  }
  if (!response.ok) {
    throw new AuthError(`Unexpected status ${response.status} from /auth/dashboard`);
  }
  return { state: "authorized", user: (await response.json()) as CurrentUser };
}

export type InvitationSummary = {
  id: string;
  email: string;
  role: Role;
  expires_at: string;
};

export type InvitationIssued = InvitationSummary & { token: string };
export type InvitationsAccess =
  | { state: "authorized"; invitations: InvitationSummary[] }
  | { state: "unauthenticated" }
  | { state: "forbidden" };

export async function fetchInvitations(
  baseUrl: string,
  cookieHeader: string,
): Promise<InvitationsAccess> {
  const response = await fetch(`${baseUrl}/auth/invitations`, {
    headers: { cookie: cookieHeader },
    cache: "no-store",
  });
  if (response.status === 401) return { state: "unauthenticated" };
  if (response.status === 403) return { state: "forbidden" };
  if (!response.ok) {
    throw new AuthError(`Unexpected status ${response.status} from /auth/invitations`);
  }
  return { state: "authorized", invitations: (await response.json()) as InvitationSummary[] };
}

export async function createInvitation(email: string, role: Role): Promise<InvitationIssued> {
  const response = await fetch(`${API_BASE_URL}/auth/invitations`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify({ email, role }),
  });
  if (!response.ok) {
    throw new AuthError(await readError(response, "Création de l'invitation impossible."));
  }
  return (await response.json()) as InvitationIssued;
}

export async function revokeInvitation(id: string): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/auth/invitations/${id}`, {
    method: "DELETE",
    credentials: "include",
  });
  if (!response.ok) {
    throw new AuthError(await readError(response, "Révocation de l'invitation impossible."));
  }
}

export function invitationLink(origin: string, token: string): string {
  const url = new URL("/activate", origin);
  url.hash = new URLSearchParams({ token }).toString();
  return url.toString();
}

/** Look an invitation up by token. POST, so the token stays out of the URL. */
export async function lookupInvitation(token: string): Promise<InvitationSummary> {
  const response = await fetch(`${API_BASE_URL}/auth/invitations/lookup`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token }),
  });

  if (!response.ok) {
    throw new AuthError(await readError(response, "Invitation introuvable ou expirée"));
  }
  return (await response.json()) as InvitationSummary;
}
