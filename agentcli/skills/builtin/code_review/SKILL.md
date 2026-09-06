---
name: code-review
description: Comprehensive code review analyzing logic, security vulnerabilities, edge cases, and design patterns
version: 1.0.0
author: agentcli
execution_mode: loop
max_iterations: 5
parameters:
  target:
    type: string
    default: "."
    description: File or directory path to review
  focus:
    type: enum
    options: [all, security, performance, correctness, architecture, style]
    default: all
    description: Specific review focus area
required_tools: [grep_search, file_ops, diagnostics_check]
---
# Autonomous Code Review

Please perform a thorough, actionable code review for the target: `{{target}}`.

## Review Focus
- **Active Focus Area**: `{{focus}}`
- **Active Git Branch**: `{{active_branch}}`
- **Workspace Directory**: `{{workspace_dir}}`

## Step-by-Step Review Instructions
1. Inspect the target path `{{target}}` using `file_ops` and `grep_search`.
2. Run any project diagnostics using `diagnostics_check` to detect underlying linter or type defects.
3. Review logic for:
   - **Correctness**: Off-by-one errors, unhandled edge cases, null/none dereferences.
   - **Security**: Injection risks, sensitive credential leaks, insecure deserialization, unsafe shell executions.
   - **Performance**: High algorithmic complexity, unindexed searches, unclosed handles or locks.
   - **Architecture & Modularity**: Adherence to project conventions and single-responsibility principles.
4. Output a structured findings report:
   - **Critical Issues** (Immediate attention required)
   - **Warnings & Improvements** (Maintainability & performance enhancements)
   - **Code Examples** (Precise before/after refactor snippets)
