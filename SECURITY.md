# Security Policy

Thanks for helping keep NextRole and its users safe.

## Supported Versions

NextRole is an early-stage project. Security fixes are applied to the latest code on the `main`
branch; we do not maintain backported patches for older commits, tags, or forks.

| Version                        | Supported          |
| ------------------------------ | ------------------ |
| `main`                         | :white_check_mark: |
| older commits, tags, and forks | :x:                |

## Reporting a Vulnerability

Please do **not** open a public GitHub issue for suspected vulnerabilities.

Use GitHub's private vulnerability reporting flow:

- Open a report on the
  [Security advisories page](https://github.com/tam159/next-role/security/advisories/new)
- Include reproduction steps or a proof of concept, the affected files/endpoints/workflows, the
  impact, and any suggested mitigation

If you are unable to use private reporting, email the maintainer at
[`npt.dc@outlook.com`](mailto:npt.dc@outlook.com) with the same details.

## Response Expectations

- Initial acknowledgment target: within 72 hours
- Status updates: at least once every 7 days while a fix is being prepared
- Public disclosure: after a fix is available or a coordinated disclosure date is agreed

Please give us reasonable time to investigate and ship a fix before making details public.

## Scope and Threat Model Notes

NextRole is currently designed for local, single-user, trusted-use workflows (see the README
[Limitations](README.md#limitations) section). Even so, please report issues involving secrets,
prompt injection, unsafe file access, container exposure, dependency vulnerabilities, or
authentication and authorization boundaries.

Reports that depend on shared-hosting, multi-tenant isolation, or public-internet exposure should
include the deployment assumptions required for the issue to apply.
