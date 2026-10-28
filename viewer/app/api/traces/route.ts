import { NextResponse } from "next/server";

import { readRun } from "@/lib/server-runs";

export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  const rel = new URL(request.url).searchParams.get("path");
  if (!rel) return NextResponse.json({ error: "path is required" }, { status: 400 });
  try {
    const text = await readRun(rel);
    return new NextResponse(text, { headers: { "content-type": "application/json; charset=utf-8" } });
  } catch (error) {
    return NextResponse.json({ error: String(error) }, { status: 404 });
  }
}
