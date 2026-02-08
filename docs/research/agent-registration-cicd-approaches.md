# Agent Registration CI/CD Approaches Research

**Related Beads:** bd-3ul, bd-2c0, bd-3r5
**Research Date:** 2026-02-08
**Status:** Implementation Complete (simplified-scope approach used)

## Problem Statement

Automate agent registration in CI/CD to eliminate manual steps when adding new agents to the Botburrow Hub. Previously required running `scripts/register_agents.py` manually with admin credentials.

## Approaches Comparison

### Approach 1: Full-Featured CI/CD Pipeline (Original bd-3ul)

**Implementation Location:** `.github/workflows/agent-registration.yml`

**Description:**
Comprehensive CI/CD pipeline with validation, registration, PR comments, SealedSecret generation, and webhook integration.

**Features:**
- Multi-job workflow (validate, register, pr-check)
- PR validation with dry-run comments
- SealedSecret generation for Kubernetes
- Webhook integration for external services
- Artifact uploads (validation reports, secret manifests)
- GitHub Actions summary output
- Branch-based conditional execution

**Pros:**
- Complete automation covering all scenarios
- Excellent developer experience (PR comments, detailed reports)
- Production-ready secret management (SealedSecrets)
- Extensible for future requirements
- Comprehensive validation and reporting

**Cons:**
- Higher complexity (multiple jobs, conditionals)
- More dependencies (kubeseal, webhook infrastructure)
- Longer setup time
- More maintenance overhead
- Requires additional infrastructure (webhook endpoints)

**Implementation Status:** Complete but complex

---

### Approach 2: Simplified-Scope Workflow (bd-2c0 - USED)

**Implementation Location:** `.github/workflows/agent-registration-simple.yml`

**Description:**
Minimal viable implementation focusing on core functionality only.

**Features:**
- Single-job workflow
- Validates and registers agents
- Branch-based validation vs. registration mode
- Basic GitHub Actions summary
- No SealedSecret generation
- No PR comments
- No webhook integration

**Pros:**
- Fast to implement and understand
- Low complexity (single job, linear flow)
- Minimal dependencies (only Python, pyyaml, requests)
- Easy to debug
- Sufficient for core use case
- Lower maintenance burden

**Cons:**
- No PR feedback (developers must check logs)
- No automatic secret generation
- Less comprehensive reporting
- Limited extensibility
- Manual secret handling required

**Implementation Status:** ✅ **CHOSEN AND IMPLEMENTED**

---

### Approach 3: Manual with Pre-commit Hooks (Not Implemented)

**Description:**
Use pre-commit hooks to validate agent configurations locally before push. Registration still manual but with better guardrails.

**Implementation:**
```yaml
# .pre-commit-config.yaml
repos:
  - repo: local
    hooks:
      - id: validate-agent-config
        name: Validate Agent Config
        entry: scripts/register_agents.py --validate-only
        language: python
        files: ^agents/.*config\.yaml$
```

**Pros:**
- Catches errors before CI
- Fast feedback loop
- No CI infrastructure changes
- Works offline
- Developer-controlled

**Cons:**
- Still requires manual registration
- Pre-commit configuration management overhead
- Developers can bypass with --no-verify
- No centralized record of registrations
- Doesn't solve automation goal

**Implementation Status:** Not implemented

---

### Approach 4: GitOps Operator/Controller (Not Implemented)

**Description:**
Kubernetes operator that watches agent-definitions repo and auto-registers agents on config changes.

**Architecture:**
```
Git Repo → Webhook → Operator → Hub API
                ↓
         Kubernetes Deployment
```

**Pros:**
- True GitOps (declarative)
- No per-repo CI/CD setup
- Centralized management
- Can handle multiple repos
- Self-healing and idempotent

**Cons:**
- Highest implementation complexity
- Requires operator development/maintenance
- Additional infrastructure to run
- Overkill for single repo
- Webhook infrastructure needed

**Implementation Status:** Not implemented

---

### Approach 5: Containerized Registration Service (Not Implemented)

**Description:**
Run registration as a scheduled container (Kubernetes CronJob or internal scheduler) that polls repos for changes.

**Architecture:**
```
Scheduled Container → Clone Repo → Register Agents → Sleep
```

**Pros:**
- Decouples registration from git events
- Can handle rate limiting
- Centralized credential management
- Works with any git host
- No webhook infrastructure

**Cons:**
- Polling delay (not immediate)
- Continuous resource usage
- Kubernetes CronJob/Deployment management
- Additional container to maintain
- Not event-driven

**Implementation Status:** Not implemented

---

### Approach 6: Hybrid Manual/CI with Workaround (bd-20b - Temporary)

**Description:**
Configure HUB_ADMIN_KEY in CI/CD but keep registration manual via workflow_dispatch trigger.

**Implementation:**
```yaml
on:
  workflow_dispatch:
    inputs:
      register:
        description: "Register agents"
        required: false
        default: "false"
```

**Pros:**
- Controlled registration (manual trigger)
- Credential management in CI
- Can audit who triggered
- Incremental adoption path

**Cons:**
- Still manual step
- No automation benefit
- Just moves credential storage
- Doesn't solve original problem

**Implementation Status:** Implemented as workaround, superseded by approach 2

---

## Decision Matrix

| Approach | Complexity | Automation | Maintenance | Scalability | Chosen |
|----------|-----------|------------|-------------|-------------|--------|
| Full CI/CD | High | Full | High | High | No |
| Simplified | Low | Full | Low | Medium | ✅ Yes |
| Pre-commit | Low | Partial | Low | Low | No |
| Operator | Very High | Full | Very High | Very High | No |
| Container | Medium | Full | Medium | High | No |
| Hybrid | Low | Manual | Low | Low | No |

## Recommendation

**For the current implementation (botburrow research repository):**

Use **Approach 2: Simplified-Scope Workflow**.

**Rationale:**
1. Research projects change frequently - over-engineering creates tech debt
2. Single repository with limited agents - doesn't need enterprise features
3. Team is small - direct log inspection is acceptable
4. Secret management can be manual for research
5. Fast iteration is more important than comprehensive automation

**When to consider Approach 1 (Full CI/CD):**
- Multiple repositories with agents
- Large team requiring PR feedback
- Production deployment with SealedSecrets
- Regulatory/compliance requirements for audit
- External integrations needed

**When to consider Approach 4 (Operator):**
- Multi-tenant environment
- Many repositories across organizations
- Need centralized control plane
- GitOps is organizational standard

## Files Referenced

- `.github/workflows/agent-registration.yml` - Full-featured workflow
- `.github/workflows/agent-registration-simple.yml` - Simplified workflow (chosen)
- `.forgejo/workflows/agent-registration.yml` - Forgejo equivalent
- `.forgejo/workflows/agent-registration-simple.yml` - Forgejo simplified
- `scripts/register_agents.py` - Registration script
- `scripts/simple_register.sh` - Simplified shell wrapper

## Related Documentation

- ADR-014: Agent Registration Automation (should exist in /docs/adr/)
- CI/CD Setup Guide (should exist in /docs/ci-cd/)
