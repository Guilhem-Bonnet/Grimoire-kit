# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| latest  | :white_check_mark: |

## Reporting a Vulnerability

If you discover a security vulnerability in Grimoire-kit, **please do NOT open a public issue**.

Instead, report it privately:

1. **Email**: Send details to the maintainer via the contact listed on the [GitHub profile](https://github.com/Guilhem-Bonnet).
2. **GitHub Security Advisory**: Use the [private vulnerability reporting](https://github.com/Guilhem-Bonnet/Grimoire-kit/security/advisories/new) feature.

### What to include

- A description of the vulnerability
- Steps to reproduce (if applicable)
- Potential impact assessment
- Suggested fix (if you have one)

### Response timeline

- **Acknowledgement**: within 48 hours
- **Initial assessment**: within 7 days
- **Fix or mitigation**: best-effort within 30 days, depending on severity

### Severity classification

We follow [CVSS 3.1](https://www.first.org/cvss/) scoring to classify vulnerabilities:

| Severity | CVSS Score | Examples | Target fix timeline |
|----------|-----------|----------|---------------------|
| **Critical** | 9.0–10.0 | Remote code execution, data exfiltration, supply-chain compromise | 7 days |
| **High** | 7.0–8.9 | Authorization bypass, unintended filesystem access, prompt injection leading to tool execution | 14 days |
| **Medium** | 4.0–6.9 | Denial of service, information disclosure (non-sensitive), agent output manipulation | 30 days |
| **Low** | 0.1–3.9 | Performance degradation, edge-case crashes, cosmetic information leaks | Next release |

### Supported versions

We provide security fixes for:
- The **latest** release on PyPI
- The **main** branch (development)

### Scope

This policy covers:
- The Python SDK (`src/grimoire/`)
- The MCP server (`src/grimoire/mcp/`)
- The shell scripts (`grimoire-init.sh`, `framework/`)
- Agent persona files that could influence tool execution

### MCP-specific considerations

Grimoire-kit exposes an MCP server. If you find issues related to:
- Prompt injection via tool payloads
- Unauthorized filesystem access
- Data exfiltration through agent outputs

These are considered **high severity** and will be prioritised accordingly.
