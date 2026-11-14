# ADR-0001: Increase default job max_retries to 3

## Status

accepted

**Domains**: unknown

## Context

Allow transient failures to be retried automatically up to three times, reducing premature job failure logs.

## Decision

The system SHALL automatically retry failed jobs up to three times before marking them as permanently failed.

## Consequences

Transient failures are now automatically retried up to three times, improving resilience to temporary issues.

> Recorded by openlore decisions on 2026-05-17
> Decision ID: 527896b2
