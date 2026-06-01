# Security Policy

## Supported Versions

NextRole is an early-stage project. Security fixes are currently applied to the latest code on `main`.

| Version | Supported |
| --- | --- |
| `main` | :white_check_mark: |
| older commits, tags, and forks | :x: |

## Reporting a Vulnerability

Please do **not** open a public GitHub issue for suspected vulnerabilities.

If GitHub's **Private vulnerability reporting** option is available in this repository's Security tab, use that channel so maintainers can investigate without exposing users or deploys. Include:

- a short description of the issue
- affected files, endpoints, or workflows
- clear reproduction steps or a proof of concept
- impact assessment and any suggested mitigation

If private vulnerability reporting is not enabled yet, contact the maintainer directly through their GitHub profile before disclosing details publicly.

## Response Expectations

- Initial acknowledgment target: within 72 hours
- Status updates target: at least once every 7 days while a fix is being prepared
- Public disclosure: after a fix is available or a coordinated disclosure date is agreed

## Scope and Threat Model Notes

NextRole is currently designed for local, single-user, trusted-use workflows. Even so, please report issues involving secrets, prompt injection, unsafe file access, container exposure, dependency vulnerabilities, or authentication and authorization boundaries.
