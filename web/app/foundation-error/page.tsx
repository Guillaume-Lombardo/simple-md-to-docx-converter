import { notFound } from "next/navigation";

export const dynamic = "force-dynamic";

export default function FoundationErrorPage() {
  if (process.env.MARKWEAVE_FRONTEND_ERROR_TEST !== "1") notFound();
  throw new Error("foundation error canary");
}
