"use client";

export default function GlobalError({
  reset,
}: {
  error: Error;
  reset: () => void;
}) {
  return (
    <html lang="en">
      <body>
        <main className="mx-auto max-w-xl space-y-4 p-8">
          <h1 className="text-2xl font-semibold">Something went wrong</h1>
          <p>
            The page could not be displayed. No submitted content was retained.
          </p>
          <button
            className="rounded-control border px-4 py-2"
            onClick={reset}
            type="button"
          >
            Try again
          </button>
        </main>
      </body>
    </html>
  );
}
