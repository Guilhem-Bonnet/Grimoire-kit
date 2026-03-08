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
