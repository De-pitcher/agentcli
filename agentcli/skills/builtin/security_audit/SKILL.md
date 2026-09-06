---
name: security-audit
description: Autonomous vulnerability, secret leak, and dependency supply-chain security audit
version: 1.0.0
author: agentcli
execution_mode: loop
max_iterations: 5
parameters:
  target_dir:
    type: string
    default: "."
    description: Directory path to audit
  scan_secrets:
    type: bool
    default: true
    description: Scan for hardcoded API keys, JWT tokens, and private credentials
required_tools: [grep_search, file_ops, diagnostics_check]
---
# Security & Vulnerability Audit

Perform an end-to-end security review of directory: `{{target_dir}}`.

## Parameters
- **Target Directory**: `{{target_dir}}`
- **Scan Secrets**: `{{scan_secrets}}`
- **Active Branch**: `{{active_branch}}`

## Audit Checklist
1. **Secret & Key Leaks**:
   - Search for API keys, private keys, JWT secrets, passwords, or tokens in committed source code or configuration.
2. **Command & SQL/NoSQL Injection**:
   - Inspect all raw shell commands, formatted strings in subprocess execution, or unsanitized DB queries.
3. **Authentication & Authorization**:
   - Verify proper JWT token verification, RBAC scoping, and tenant isolation boundaries.
4. **Input Sanitization & Path Traversal**:
   - Check file read/write paths for `../` path traversal vulnerabilities.
5. Provide a prioritized markdown vulnerability table (Severity, File, Line, Vulnerability, Remediation).
