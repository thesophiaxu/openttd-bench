import { NextResponse } from "next/server";

import { listRuns, outputsDir } from "@/lib/server-runs";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const runs = await listRuns();
    return NextResponse.json({ root: outputsDir(), runs });
  } catch (error) {
    return NextResponse.json({ error: String(error) }, { status: 500 });
  }
}
