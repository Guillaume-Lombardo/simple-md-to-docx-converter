# Architecture

## Current state

The repository provides an installable Python 3.14 package, its development toolchain, and a
FastAPI application shell. T06 introduces local authentication and authorization behind explicit
user, session, hashing, token, clock, and readiness ports. Its adapters are intentionally
in-memory until T12; there is still no conversion engine, durable persistence adapter, worker, or
deployment implementation.

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

The HTTP authentication boundary and its application ports now exist. Remaining boundaries
describe the delivery direction and will be introduced by their corresponding tickets.

## Storage profiles

The standalone profile will use SQLite, atomic files under `/data`, one application replica, and an
embedded worker. The distributed profile will use PostgreSQL, S3-compatible object storage, and
separately scalable workers. Shared repository and object-store interfaces must receive the same
contract tests when they are introduced. T06 defines storage-neutral account and session
repository ports but does not select either profile; T12 must implement and contract-test both
persistent adapters.

## Security and runtime

Document-controlled network access is forbidden. Future engine adapters must use fixed arguments,
isolated workspaces, bounded resources, deadlines, cancellation, and explicit environment
allowlists. The final UBI 9 image must run rootlessly with an arbitrary UID and a read-only root
filesystem. Runtime validation belongs to the dedicated toolchain and container tickets.

Values that remain unresolved in section 14 of the product specification are deliberately not
encoded here.
