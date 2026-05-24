# Security Policy

## Supported Versions

Filedge is pre-1.0. Only the latest release on `main` receives security fixes.

| Version | Supported          |
| ------- | ------------------ |
| `main`  | :white_check_mark: |
| < 0.1   | :x:                |

## Reporting a Vulnerability

**Please do not report security vulnerabilities through public GitHub issues.**

Instead, report them privately via one of:

1. **GitHub Security Advisories** — preferred. Open a draft advisory at
   <https://github.com/tongqqiu/filedge/security/advisories/new>.
2. **Email** — `tongqing.qiu@gmail.com` with subject line `[filedge security]`.

Please include:

- A description of the issue and the impact you believe it has
- Steps to reproduce, or a proof-of-concept if available
- The version / commit SHA you tested against
- Any suggested mitigation

You should receive an acknowledgement within 7 days. We aim to ship a fix or a
mitigation within 30 days of confirmation, and will credit reporters in the
release notes unless they prefer to remain anonymous.

## Scope

In scope:

- The `filedge` Python package and its CLI
- The shipped connectors (`sqlite`, `postgres`, `bigquery`, `databricks`)
- The audit DB schema and state machine

Out of scope:

- Vulnerabilities in third-party dependencies (please report upstream; we will
  pull fixes via Dependabot once available)
- Misconfiguration of user-supplied destinations (e.g. a Postgres instance
  exposed to the public internet)
- Findings that require access to a user's local filesystem or credentials
