---
name: test-generator
description: Autonomous unit and integration test generator for target modules with edge-case coverage
version: 1.0.0
author: agentcli
execution_mode: loop
max_iterations: 6
parameters:
  target_file:
    type: string
    default: "."
    description: Target source file or module to generate tests for
  framework:
    type: enum
    options: [pytest, unittest, vitest, jest, cargo]
    default: pytest
    description: Testing framework
  edge_cases:
    type: bool
    default: true
    description: Whether to explicitly generate extreme edge-case and boundary tests
required_tools: [grep_search, file_ops, shell_execution, diagnostics_check]
---
# Autonomous Test Generator

Generate comprehensive, hermetic tests for target: `{{target_file}}` using framework: `{{framework}}`.

## Parameters
- **Target File**: `{{target_file}}`
- **Framework**: `{{framework}}`
- **Include Edge Cases**: `{{edge_cases}}`
- **Active Workspace**: `{{workspace_dir}}`

## Instructions
1. Read `{{target_file}}` and understand all public methods, classes, signatures, and internal error branches.
2. Locate existing project test suites to follow naming conventions, fixtures, and directory structures.
3. Write isolated unit and integration test functions covering:
   - Happy path behaviors.
   - Boundary values, empty inputs, invalid types, and unexpected formats.
   - Async coroutines, exception handling, and teardown cleanup.
4. Execute test suite and verify 100% pass rate using `shell_execution` or `diagnostics_check`.
