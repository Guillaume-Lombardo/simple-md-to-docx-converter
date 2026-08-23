# Architecture

## Current state

The repository provides an installable Python 3.14 package, its development toolchain, and a
FastAPI application shell. Local authentication and authorization use persistent SQL repositories
selected by an explicit storage profile. Stable-identifier object-store ports use atomic files or
AWS S3-compatible operations. Template identity, ownership, visibility-aware search, user
preferences, fallback selection, and future-mutation authorization now have storage-neutral domain
and persistence boundaries. There is still no template content/version API, conversion engine,
queue, worker, or deployment implementation.

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

The HTTP authentication and storage boundaries now exist. Remaining boundaries describe the
delivery direction and will be introduced by their corresponding tickets.

## Storage profiles

The standalone profile will use SQLite, atomic files under `/data`, one application replica, and an
embedded worker. The distributed profile will use PostgreSQL, S3-compatible object storage, and
separately scalable workers. Shared repository and object-store interfaces must receive the same
contract tests when they are introduced. T06 defines storage-neutral account and session
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

Document-controlled network access is forbidden. Future engine adapters must use fixed arguments,
isolated workspaces, bounded resources, deadlines, cancellation, and explicit environment
allowlists. The final UBI 9 image must run rootlessly with an arbitrary UID and a read-only root
filesystem. Runtime validation belongs to the dedicated toolchain and container tickets.

Values that remain unresolved in section 14 of the product specification are deliberately not
encoded here.

## Template boundary

T14 stores immutable-owner template identities, per-user preferences, and the singleton system
fallback in the profile database. Search normalization is computed in application code for
cross-profile parity, while visibility predicates and pagination execute in the database. The
authorization service exposes an explicit administrator-intervention context for T15 audit
persistence. See `docs/templates.md` for the exact delivered and deferred behavior.
