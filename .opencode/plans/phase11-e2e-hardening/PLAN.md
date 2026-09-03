# Phase 11 - E2E Hardening Implementation Plan

## Overview
Phase 11 focuses on End-to-End Reliability, Observability & Packaging. The goal is to make the agent reproducible, diagnosable, and installable on declared support targets.

## Implementation Plan

### Task 1: Hermetic Tests & Credential-Gated Smoke Tests
**Priority: High**
- [ ] Add hermetic tests (no external dependencies)
- [ ] Add credential-gated OpenRouter/MCP smoke tests
- [ ] Fix test-environment assumptions (TERM=dumb, writable temp paths)
- [ ] Add `--basetemp` for pytest temp dir

### Task 2: Structured Logging & Observability
**Priority: High**
- [ ] Add structured run logs (JSON format)
- [ ] Add correlation IDs for request tracing
- [ ] Add provider/tool timing instrumentation
- [ ] Implement cancellation support
- [ ] Add bounded retries with exponential backoff
- [ ] Add cleanup checks

### Task 3: Fresh-Install Validation
**Priority: High**
- [ ] Validate fresh-install console script (`agentcli`)
- [ ] Validate Docker behavior (build, run, entrypoint)
- [ ] Validate session persistence (SQLite)
- [ ] Validate config initialization
- [ ] Validate all documented commands

### Task 4: Python Support Matrix & Cross-Platform Validation
**Priority: Medium**
- [ ] Validate Python 3.11-3.14 support matrix
- [ ] Validate Windows/POSIX subprocess behavior
- [ ] Add CI matrix for Windows + Linux

### Task 5: CI/CD Pipeline Enhancements
**Priority: Medium**
- [ ] Add credential-gated OpenRouter/MCP smoke tests
- [ ] Add Windows + Linux CI matrix
- [ ] Add Docker build/test in CI
- [ ] Add hermetic test mode (no external deps)

### Task 5: Observability & Logging
**Priority: Medium**
- [ ] Structured JSON logging
- [ ] Correlation IDs for request tracing
- [ ] Provider/tool timing metrics
- [ ] Cancellation support
- [ ] Bounded retries with exponential backoff
- [ ] Cleanup verification

---

## Implementation Order

### Sprint 1: Test Infrastructure & CI/CD (Week 1)
1. Fix test environment assumptions (TERM=dumb, temp paths)
2. Add CI matrix for Windows + Linux
3. Add hermetic test mode
4. Add Docker build/test to CI

### Week 2: Observability & Reliability
1. Structured logging with correlation IDs
2. Provider/tool timing instrumentation
3. Cancellation support
4. Bounded retries with exponential backoff
5. Cleanup verification

### Week 3: Installation & Packaging Validation
1. Fresh-install console script validation
2. Docker build/test validation
3. Session persistence testing
8. Config initialization validation

### Week 4: Cross-Platform & Release
1. Windows + Linux CI matrix
9. Docker build/test in CI
10. Python 3.11-3.14 matrix validation
10. Packaging documentation

---

## Implementation Details

### 1. Test Environment Fixes
- Fix `TERM=dumb` assumption in tests
- Fix writable temp paths (use `--basetemp=.pytest-temp`)
- Add hermetic test mode (no external dependencies)

### 2. CI/CD Enhancements
- Add Windows + Linux CI matrix
- Add Docker build/test to CI
- Add credential-gated smoke tests (optional, gated by secrets)
- Add hermetic test mode flag

### 3. Observability
- Structured JSON logging with correlation IDs
- Provider/tool timing instrumentation
- Cancellation token support
- Bounded retries with exponential backoff
- Cleanup verification

### 4. Installation Validation
- Fresh-install console script test
- Docker build/run test
- Session persistence verification
- Config initialization validation
- All documented commands tested

### 5. Cross-Platform Validation
- Windows + Linux CI matrix
- Python 3.11-3.14 matrix
- Windows/POSIX subprocess behavior validation

---

## Implementation Order

1. **Week 1**: Test infrastructure fixes + CI/CD enhancements
2. **Week 2**: Observability (logging, correlation IDs, timing, retries, cleanup)
3. **Week 3**: Installation validation (fresh install, Docker, session persistence, config)
4. **Week 4**: Cross-platform validation, packaging docs, release automation

---

## Acceptance Criteria

- [ ] All quality gates pass from clean checkout
- [ ] Windows + Linux smoke suites pass (chat, agent mode, MCP, memory, plugin)
- [ ] Packaging documentation matches actual artifacts
- [ ] Hermetic tests pass without external dependencies
- [ ] Credential-gated smoke tests pass when credentials provided
- [ ] Windows + Linux CI matrix passes
- [ ] Docker build/test passes in CI
- [ ] Python 3.11-3.14 matrix validated
- [ ] Fresh-install validation passes
- [ ] Packaging documentation matches actual artifacts