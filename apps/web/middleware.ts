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

export const config = {
  matcher: ["/dashboard/:path*", "/cases/:path*"],
};
