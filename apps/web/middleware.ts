import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

import { SESSION_COOKIE_NAME } from "@/lib/auth";

/**
 * Coarse gate in front of the private area. It only checks that a session cookie is
 * present — whether that session is real, live, and sufficiently privileged is decided
 * by the API, which is the only thing holding the data.
 */
export function middleware(request: NextRequest) {
  if (request.cookies.get(SESSION_COOKIE_NAME)) {
    return NextResponse.next();
  }

  const login = new URL("/login", request.url);
  login.searchParams.set("next", request.nextUrl.pathname);
  return NextResponse.redirect(login);
}

/**
 * Every private area, and adding one here is part of adding the route.
 *
 * A page left out of this list still refuses to show anything — the API holds the data and
 * answers 401 — but the refusal surfaces as "404, this page could not be found" rather than
 * a redirect to the login form, so a signed-out clinician is told the case does not exist.
 * `/poste` shipped that way for exactly one commit.
 */
export const config = {
  matcher: ["/dashboard/:path*", "/cases/:path*", "/poste/:path*"],
};
