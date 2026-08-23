# Architecture

## Current state

The repository currently provides only an installable Python 3.14 package and its development
toolchain. It intentionally contains no conversion engine, Web application, persistence adapter,
worker, or deployment implementation.

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

These boundaries describe the delivery direction, not implemented APIs. Their contracts will be
introduced by the corresponding tickets.

## Storage profiles

The standalone profile will use SQLite, atomic files under `/data`, one application replica, and an
embedded worker. The distributed profile will use PostgreSQL, S3-compatible object storage, and
separately scalable workers. Shared repository and object-store interfaces must receive the same
contract tests when they are introduced. This bootstrap does not select or configure either
profile.

## Security and runtime

Document-controlled network access is forbidden. Future engine adapters must use fixed arguments,
isolated workspaces, bounded resources, deadlines, cancellation, and explicit environment
allowlists. The final UBI 9 image must run rootlessly with an arbitrary UID and a read-only root
filesystem. Runtime validation belongs to the dedicated toolchain and container tickets.

Values that remain unresolved in section 14 of the product specification are deliberately not
encoded here.
