"use client";

import Link from "next/link";
import {
  type ComponentPropsWithoutRef,
  type ReactNode,
  useEffect,
  useId,
  useRef,
} from "react";

export function AppShell({
  children,
  current,
}: {
  children: ReactNode;
  current: "Convert";
}) {
  return (
    <>
      <a className="sr-only focus:not-sr-only" href="#main">
        Skip to content
      </a>
      <header className="border-b bg-surface">
        <nav
          aria-label="Primary"
          className="mx-auto flex max-w-5xl items-center gap-6 p-4"
        >
          <span className="font-semibold">Markweave</span>
          <Link
            aria-current={current === "Convert" ? "page" : undefined}
            className="text-accent underline-offset-4 hover:underline"
            href="/convert"
          >
            Convert
          </Link>
        </nav>
      </header>
      <main className="mx-auto max-w-5xl space-y-6 p-6" id="main">
        {children}
      </main>
    </>
  );
}

export function TextField({
  label,
  ...props
}: { label: string } & ComponentPropsWithoutRef<"input">) {
  const id = props.id ?? props.name;
  return (
    <label className="grid gap-2 font-medium" htmlFor={id}>
      {label}
      <input
        {...props}
        className="rounded-control border border-muted bg-surface px-3 py-2 font-normal"
        id={id}
      />
    </label>
  );
}

export function Alert({
  children,
  tone = "info",
}: {
  children: ReactNode;
  tone?: "danger" | "info";
}) {
  return (
    <div
      className={
        tone === "danger"
          ? "rounded-control border border-danger p-4 text-danger"
          : "rounded-control border border-accent p-4"
      }
      role="alert"
    >
      {children}
    </div>
  );
}

export function LoadingStatus({
  children,
  loading,
}: {
  children: ReactNode;
  loading: boolean;
}) {
  return (
    <div aria-live="polite" aria-busy={loading}>
      {loading ? "Loading…" : children}
    </div>
  );
}

export function Progress({ label, value }: { label: string; value: number }) {
  return (
    <label className="grid gap-2">
      {label}
      <progress className="w-full" max={100} value={value}>
        {value}%
      </progress>
    </label>
  );
}

export function Dialog({
  children,
  onClose,
  open,
  title,
}: {
  children: ReactNode;
  onClose: () => void;
  open: boolean;
  title: string;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const restoreFocus = useRef<HTMLElement | null>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      restoreFocus.current = document.activeElement as HTMLElement | null;
      dialog.showModal();
    } else if (!open && dialog.open) {
      dialog.close();
      restoreFocus.current?.focus();
      restoreFocus.current = null;
    }
  }, [open]);

  useEffect(
    () => () => {
      restoreFocus.current?.focus();
    },
    [],
  );

  return (
    <dialog
      aria-labelledby={titleId}
      className="m-auto max-w-lg rounded-control bg-surface p-6 shadow-xl"
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      ref={dialogRef}
    >
      <h2 className="text-xl font-semibold" id={titleId}>
        {title}
      </h2>
      <div className="mt-4">{children}</div>
    </dialog>
  );
}

export function DataTable({
  caption,
  children,
}: {
  caption: string;
  children: ReactNode;
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse">
        <caption className="sr-only">{caption}</caption>
        {children}
      </table>
    </div>
  );
}

export function ItemList({
  children,
  label,
}: {
  children: ReactNode;
  label: string;
}) {
  return (
    <ul aria-label={label} className="divide-y divide-muted" role="list">
      {children}
    </ul>
  );
}
