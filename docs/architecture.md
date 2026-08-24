# Architecture

## Current state

The repository provides an installable Python 3.14 package, its development toolchain, and a
FastAPI application shell. Local authentication and authorization use persistent SQL repositories
selected by an explicit storage profile. Stable-identifier object-store ports use atomic files or
AWS S3-compatible operations. Template identity, ownership, visibility-aware search, user
preferences, fallback selection, and future-mutation authorization now have storage-neutral domain
and persistence boundaries. Isolated adapters validate Markdown and resources, render Mermaid,
produce DOCX with Pandoc, and produce bounded PDF with LibreOffice. Conversion submissions now use
an owner-scoped idempotent API, a durable state machine, transactional SQLite/PostgreSQL claims,
leases, heartbeats, deterministic recovery, cancellation, atomic result publication, and bounded
embedded/external worker loops. Template content/version APIs and deployment wiring remain future
work.

## Target system

The product specification defines a server-rendered FastAPI application and asynchronous workers.
The application will submit durable conversion jobs, while workers will invoke local document
engines and store immutable inputs and outputs. Pandoc produces DOCX, Mermaid CLI with local
Chromium produces diagrams, and LibreOffice converts DOCX to PDF.

The intended boundaries are:

- the HTTP and Web layer authenticates users, validates requests, and exposes job state;
- the application layer coordinates conversion and template workflows;
- domain code defines jobs, templates, ownership, and stable state transitions;
- adapters isolate document engines, repositories, object storage, and the filesystem;
- workers claim persisted jobs, enforce resource limits, and publish results atomically.

The HTTP authentication, conversion-job, worker orchestration, and storage boundaries now exist.
T15 will connect the worker processor to immutable template versions and the delivered document
engines. T18 will supply approved operational values, and T20 will wire final runtime modes.

## Storage profiles

The standalone profile uses SQLite, atomic files under `/data`, one application replica, and the
delivered single embedded-worker lifecycle. The distributed profile uses PostgreSQL,
S3-compatible object storage, and the same worker loop in separately scalable processes.
Transactional PostgreSQL claims use `FOR UPDATE SKIP LOCKED`; SQLite remains restricted to one
application replica. Shared repository and object-store interfaces receive the same contract tests
for both profiles. T06 defines storage-neutral account and session
repository ports are implemented by one transactional SQL adapter contract-tested against SQLite
and PostgreSQL. The object-store contract is shared by atomic files and the AWS S3-compatible
adapter; RustFS exercises that contract in CI without entering application interfaces.

The user repository contract includes an authentication-version compare-and-set after password
verification and an atomic security mutation that increments that version. Sessions capture the
accepted version and reject stale values. This separates expensive Argon2 work from the storage
transaction while preventing reset, disable, reactivation, and successful-login rehash races. T12
must map these operations to real SQLite and PostgreSQL transactions; separate read/write calls do
not satisfy the contract.

## Security and runtime

Document-controlled network access is forbidden. Delivered engine adapters use fixed arguments,
isolated workspaces, bounded resources, deadlines, cancellation where applicable, and explicit
environment allowlists. The PDF adapter terminates the complete LibreOffice process group and
validates the output structurally before publication. The final UBI 9 image must run rootlessly
with an arbitrary UID and a read-only root filesystem. Final-image validation belongs to T20/T21.

Values that remain unresolved in section 14 of the product specification are deliberately not
encoded here.

## Template boundary

T14 stores immutable-owner template identities, per-user preferences, and the singleton system
fallback in the profile database. Search normalization is computed in application code for
cross-profile parity, while visibility predicates and pagination execute in the database. The
authorization service exposes an explicit administrator-intervention context for T15 audit
persistence. See `docs/templates.md` for the exact delivered and deferred behavior.
