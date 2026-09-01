import Link from "next/link";

export default function NotFound() {
  return (
    <main className="mx-auto max-w-2xl p-8">
      <h1 className="text-3xl font-semibold">Page not found</h1>
      <p className="mt-3">The requested page does not exist.</p>
      <Link className="mt-5 inline-block text-accent underline" href="/convert">
        Return to Convert
      </Link>
    </main>
  );
}
