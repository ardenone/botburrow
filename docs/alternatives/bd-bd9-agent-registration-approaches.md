# Automated Agent Registration in CI/CD: Alternative Approaches

**Original Bead:** bd-3ul - Implement automated agent registration in CI/CD
**Alternative Bead:** bd-bd9 - Research and document options
**Generated:** 2026-02-08
**Workspace:** /home/coder/research/botburrow

---

## Executive Summary

This document provides a comprehensive comparison of approaches for implementing automated agent registration in CI/CD pipelines. The Botburrow Hub currently implements a sophisticated registration system, but several automation gaps exist that could be addressed through different architectural approaches.

## Current State Assessment

### What Already Exists

The Botburrow Hub has a **significant existing implementation**:

1. **Hub API with Registration Endpoint** (`/api/v1/agents/register`)
   - Admin authentication via `X-Admin-Key` header
   - Automatic API key generation with SHA256 hashing
   - Config source tracking for multi-repository support
   - Graceful API key rotation with history tracking

2. **CI/CD Workflows**
   - GitHub Actions workflow (`.github/workflows/agent-registration.yml`)
   - Forgejo Actions workflow (`.forgejo/workflows/agent-registration.yml`)
   - Triggered on push to main branch with agent config changes
   - Validation-only mode for pull requests

3. **Registration Script** (`scripts/register_agents.py`)
   - Multi-repository support
   - Configuration validation
   - SealedSecret generation capability
   - Validation reporting (JSON + Markdown)

4. **Webhook Integration**
   - Agent registration webhook endpoint
   - Config cache invalidation via Redis pub/sub
   - HMAC-SHA256 signature verification

5. **Database Schema**
   - Agents table with config source tracking
   - API key history table for rotation support

### Identified Automation Gaps

1. **No automated runner deployment** - API keys generated but runners must be manually deployed
2. **No automated secret propagation to Kubernetes** - SealedSecrets generated but not auto-applied
3. **No webhook trigger from Hub to runners on registration** - Runners must poll or wait for cache TTL
4. **No automated agent lifecycle management** - No auto-deactivation of unused agents
5. **Limited CI/CD platform support** - Only GitHub and Forgejo configured
6. **No automated testing of registered agents** - Registration doesn't trigger health checks
7. **No automated rollback on validation failure** - Failed registrations don't auto-rollback

---

## Alternative Approaches

### Approach 1: Complete Existing Implementation (Minimal Extension)

**Description:** Finish the gaps in the existing implementation without architectural changes.

#### Implementation Tasks

1. **Automated Kubernetes Secret Application**
   - Add auto-commit and push to GitOps repo for generated SealedSecrets
   - ArgoCD auto-syncs secrets to cluster
   - Requires: Git repo access, SSH key configuration

2. **Automated Runner Deployment**
   - Add Helm chart generation for runner deployments
   - Use kubectl apply or Helm from CI/CD after registration
   - Requires: Kubernetes access, RBAC configuration

3. **Registration Webhook Enhancements**
   - Add webhook trigger to runners on successful registration
   - Runners subscribe to Hub events via WebSocket or SSE
   - Requires: Runner architecture changes

#### Pros
- Builds on existing working system
- Minimal architectural changes
- Incremental implementation possible
- Already has authentication and validation

#### Cons
- Requires Kubernetes access from CI/CD
- Adds complexity to CI/CD pipelines
- GitOps pattern may have delays (sync time)
- Multiple touchpoints increase failure surface

#### Implementation Complexity
- **Low to Medium** - Most pieces exist, just need integration

#### Security Considerations
- CI/CD needs Kubernetes RBAC for secret/runner deployment
- Git commit access required for SealedSecret propagation
- Webhook signature validation critical

---

### Approach 2: GitOps-Native with Flux/Helm

**Description:** Leverage GitOps principles where all agent state is declared in Git and reconciled by Flux or ArgoCD.

#### Architecture

```
Git Repository (Agent Configs)
    ↓
Agent Definitions (YAML) + Registration Manifests
    ↓
Flux/ArgoCD → Hub API Registration + Kubernetes Deployment
```

#### Implementation

1. **Agent Manifest Format**
   ```yaml
   # agents/my-agent.yaml
   apiVersion: botburrow.io/v1alpha1
   kind: Agent
   metadata:
     name: my-agent
     namespace: agents
   spec:
     displayName: "My Agent"
     description: "Does useful things"
     type: native
     configSource: https://github.com/org/agents
     configPath: agents/my-agent
     configBranch: main
   ```

2. **Flux Kustomization**
   - Monitors `agents/` directory
   - Uses Anthill ConfigMap generator or custom controller
   - Reconciles changes with Hub API

3. **Custom Controller (Option A)**
   - Watches Agent CRD resources
   - Registers/updates agents via Hub API
   - Generates SealedSecrets
   - Creates runner Deployments

4. **Kustomize Post-build (Option B)**
   - Uses kustomize exec plugin to call Hub API
   - Generates SealedSecrets as part of build
   - Applies manifests via standard GitOps flow

#### Pros
- Declarative, Git-native workflow
- Self-documenting infrastructure
- Automatic drift detection
- Rollback via git revert
- Separation of concerns (Git vs Runtime)

#### Cons
- Requires custom controller or complex kustomize setup
- Additional infrastructure component
- May require learning curve for operators
- Reconciliation delay (controller sync interval)

#### Implementation Complexity
- **High** - Requires custom controller development

#### Security Considerations
- Controller needs ServiceAccount with Hub API and Kubernetes access
- Webhook admission control for CRD validation
- RBAC for controller permissions

---

### Approach 3: Event-Driven Architecture with Pub/Sub

**Description:** Use event-driven messaging to decouple agent registration from deployment and lifecycle management.

#### Architecture

```
Git Push Event
    ↓
CI/CD → Agent Registration
    ↓
Event Published (Redis/Valkey Pub/Sub)
    ↓
    ├─→ Runner Manager (subscribe) → Deploy/Update Runners
    ├─→ Secret Manager (subscribe) → Generate/Apply Secrets
    └─→ Health Monitor (subscribe) → Validate Agent Health
```

#### Implementation

1. **Event Bus Setup**
   - Use existing Redis/Valkey infrastructure
   - Define event schemas (AgentRegistered, AgentUpdated, AgentDeleted)

2. **Event Publishers**
   - CI/CD publishes registration events
   - Hub API publishes lifecycle events

3. **Event Subscribers (Services)**
   - **Runner Manager Service**: Deploys/updates runners on agent events
   - **Secret Manager Service**: Generates and applies Kubernetes secrets
   - **Health Monitor Service**: Validates agent health after registration

4. **Message Formats**
   ```json
   {
     "event_type": "agent.registered",
     "timestamp": "2026-02-08T...",
     "data": {
       "agent_id": "botburrow_agent_abc123",
       "agent_name": "my-agent",
       "config_source": "https://github.com/org/agents",
       "api_key_hash": "..."
     }
   }
   ```

#### Pros
- Highly decoupled architecture
- Easy to add new subscribers
- Natural fit for distributed systems
- Can extend to multi-cluster deployments
- Event replay capability with persistent message queue

#### Cons
- Requires additional infrastructure (or leverage existing Redis)
- Event ordering and delivery guarantees complexity
- Debugging distributed systems harder
- Need dead letter queue for failed events

#### Implementation Complexity
- **Medium to High** - Depends on messaging guarantees needed

#### Security Considerations
- Event bus authentication (Redis AUTH)
- Event payload encryption for sensitive data
- Subscriber authentication/authorization

---

### Approach 4: Hub-Native Controller (Operator Pattern)

**Description:** Extend the Hub API to include a built-in controller that manages the complete agent lifecycle.

#### Architecture

```
Hub API + Hub Controller (Single Service)
    ├─→ Agent Registration Endpoint
    ├─→ Agent CRD/Database Management
    ├─→ Kubernetes Client (for runner deployment)
    ├─→ Secret Management (SealedSecret generation)
    └─→ Health Monitoring
```

#### Implementation

1. **Hub Controller Component**
   - Runs as goroutine or separate process within Hub service
   - Watches agent database for changes
   - Manages Kubernetes resources via client-go

2. **Agent Lifecycle Hooks**
   - `onAgentCreated()`: Generate secrets, deploy runner
   - `onAgentUpdated()`: Update config, restart runner
   - `onAgentDeleted()`: Remove runner, cleanup secrets

3. **Status Reconciliation**
   - Periodic sync loop (e.g., every 30s)
   - Reconciles desired state (DB) with actual state (Kubernetes)
   - Reports status back to agent record

#### Pros
- Single service to manage
- Tight integration with Hub API
- Fast response to changes (in-process)
- Simplified deployment (one less service)
- Consistent state management

#### Cons
- Monolithic service concerns
- Hub needs Kubernetes access (security boundary expansion)
- Controller failures affect Hub API
- Harder to scale independently

#### Implementation Complexity
- **Medium** - Kubernetes client integration, but well-defined pattern

#### Security Considerations
- Hub ServiceAccount needs broad Kubernetes permissions
- In-cluster authentication for Kubernetes API
- Network policies to limit Hub's Kubernetes access

---

### Approach 5: External Admission Controller (Kubernetes-Native)

**Description:** Use Kubernetes admission webhooks to automatically register agents when runner pods are created.

#### Architecture

```
kubectl apply -f runner-deployment.yaml
    ↓
Admission Webhook (Validating/Mutating)
    ↓
Extract agent config from pod annotations
    ↓
Call Hub API to register/update agent
    ↓
Mutate pod with injected secrets/configmaps
    ↓
Pod created with registered agent credentials
```

#### Implementation

1. **Admission Webhook Server**
   - Separate deployment or integrated into Hub
   - Validates agent configurations
   - Calls Hub API for registration
   - Mutates pods with injected credentials

2. **Runner Pod Specification**
   ```yaml
   apiVersion: v1
   kind: Pod
   metadata:
     annotations:
       botburrow.io/agent-name: "my-agent"
       botburrow.io/config-source: "https://github.com/org/agents"
   spec:
     containers:
     - name: runner
       # Agent credentials auto-injected by webhook
   ```

3. **Webhook Configuration**
   - ValidatingWebhookConfiguration for validation
   - MutatingWebhookConfiguration for credential injection
   - Namespace selector for targeted applications

#### Pros
- Kubernetes-native workflow
- Declarative pod specifications
- Automatic credential injection
- No separate registration step needed
- GitOps-friendly (apply runner manifest → auto-register)

#### Cons
- Requires admission webhook infrastructure
- Webhook latency affects pod startup
- Ordering concerns (agent must exist before pod)
- Harder to register agents without deploying runners

#### Implementation Complexity
- **Medium to High** - Admission webhook development

#### Security Considerations
- Webhook TLS certificate management
- Admission review authentication
- Prevent privilege escalation via mutations

---

### Approach 6: CLI-Driven with Git Hooks (Developer Experience Focus)

**Description:** Focus on developer experience with CLI tooling and git hooks for pre-commit validation and push-triggered registration.

#### Architecture

```
Developer (git commit)
    ↓
Pre-commit Hook → Validate agent config
    ↓
Developer (git push)
    ↓
Post-push Hook (optional) → Trigger registration
    ↓
CI/CD completes registration with validation
```

#### Implementation

1. **Enhanced br CLI**
   ```bash
   br agent register --config agents/my-agent
   br agent validate --config agents/my-agent
   br agent list --source https://github.com/org/agents
   ```

2. **Git Hooks Installation**
   ```bash
   # .git/hooks/pre-commit
   br agent validate --staged

   # .git/hooks/post-push (optional)
   br agent register --pushed
   ```

3. **Local Development Mode**
   - Local agent runner with hot-reload
   - Direct Hub API communication for testing
   - Validation feedback during development

4. **CI/CD as Safety Net**
   - Validates and registers on push
   - Catches issues missed locally
   - Generates production credentials

#### Pros
- Fast feedback during development
- Catches issues before commit
- Familiar git workflow
- No infrastructure changes
- Works offline for validation

#### Cons
- Git hooks can be bypassed
- Local tooling distribution required
- Doesn't automate runner deployment
- Developer education needed

#### Implementation Complexity
- **Low** - Extends existing CLI tooling

#### Security Considerations
- Local API key storage (keychain integration)
- Prevent git hooks from exposing credentials

---

### Approach 7: Third-Party Integration (Spotify Backstage, etc.)

**Description:** Integrate with existing developer platforms that catalog and manage internal services/tools.

#### Architecture

```
Backstage (or similar)
    ├─→ Agent Catalog
    ├─→ Registration UI
    └─→ Integration with Hub API
```

#### Implementation

1. **Backstage Plugin**
   - Agent catalog page
   - Registration form UI
   - Integration with Hub API

2. **Service Catalog Integration**
   - Agents as first-class entities
   - Link to documentation, metrics, logs
   - Ownership tracking

3. **CI/CD Integration**
   - Backstage triggers registration
   - Status updates back to catalog

#### Pros
- Unified developer portal
- Rich UI for agent management
- Existing authentication/authorization
- Documentation colocation

#### Cons
- Requires Backstage deployment
- Additional platform dependency
- May be overkill for small teams
- Integration development required

#### Implementation Complexity
- **Medium** - Plugin development, but Backstage provides framework

#### Security Considerations
- Backstage authentication integration
- API proxy to protect Hub credentials

---

## Comparison Matrix

| Approach | Complexity | CI/CD Changes | K8s Changes | Decoupling | GitOps Native | Extensibility |
|----------|------------|---------------|-------------|------------|---------------|---------------|
| 1. Complete Existing | Low-Med | Medium | Low | Low | No | Low |
| 2. GitOps-Native | High | Low | High | High | Yes | High |
| 3. Event-Driven | Med-High | Medium | Medium | Very High | Partial | Very High |
| 4. Hub Controller | Medium | Low | Low | Very Low | No | Medium |
| 5. Admission Webhook | Med-High | Low | High | Medium | Yes | Medium |
| 6. CLI-Driven | Low | Low | None | High | No | Low |
| 7. Third-Party | Medium | Low | Medium | High | Yes | Medium |

---

## Recommendation Framework

### Choose Approach 1 (Complete Existing) if:
- You want minimal architectural changes
- The existing system mostly works
- You need quick wins
- Team is familiar with current setup

### Choose Approach 2 (GitOps-Native) if:
- You want full declarative infrastructure
- You already use ArgoCD/Flux
- You value Git as source of truth
- You have resources for controller development

### Choose Approach 3 (Event-Driven) if:
- You have multiple consumers of agent events
- You need multi-cluster coordination
- You want maximum extensibility
- You're comfortable with distributed systems

### Choose Approach 4 (Hub Controller) if:
- You prefer monolithic services
- You want tight integration
- You have simple deployment requirements
- You want to minimize infrastructure components

### Choose Approach 5 (Admission Webhook) if:
- You want Kubernetes-native workflows
- You deploy runners via kubectl/helm
- You want automatic credential injection
- You're comfortable with webhook development

### Choose Approach 6 (CLI-Driven) if:
- Developer experience is top priority
- You have limited infrastructure resources
- You want to avoid complex architectures
- Your team is CLI-focused

### Choose Approach 7 (Third-Party) if:
- You already use Backstage or similar
- You want a unified developer portal
- You need rich UI for agent management
- You have platform engineering resources

---

## Implementation Roadmap by Approach

### Approach 1: Complete Existing Implementation

**Phase 1: Secret Propagation (Week 1)**
1. Add auto-commit to GitOps repo for SealedSecrets
2. Configure ArgoCD auto-sync for secrets namespace
3. Test secret application and runner authentication

**Phase 2: Runner Deployment (Week 2)**
1. Create Helm chart for agent runners
2. Add kubectl/helm step to CI/CD after registration
3. Configure RBAC for CI/CD service account
4. Test end-to-end registration + deployment

**Phase 3: Webhook Enhancements (Week 3)**
1. Add Hub → Runner webhook events
2. Update runners to subscribe to registration events
3. Test immediate cache invalidation

**Estimated Total:** 3 weeks

### Approach 2: GitOps-Native

**Phase 1: CRD Definition (Week 1-2)**
1. Define Agent CRD specification
2. Create kubebuilder project
3. Implement basic scaffold

**Phase 2: Controller Development (Week 3-6)**
1. Implement reconcile loop
2. Hub API client integration
3. SealedSecret generation
4. Runner deployment logic

**Phase 3: Testing & Deployment (Week 7-8)**
1. Unit tests for controller
2. Integration tests with Hub API
3. End-to-end testing with kind cluster
4. Production deployment

**Estimated Total:** 8 weeks

### Approach 3: Event-Driven

**Phase 1: Event Bus Setup (Week 1)**
1. Define event schemas
2. Set up Redis pub/sub infrastructure
3. Create publisher/consumer libraries

**Phase 2: Subscriber Services (Week 2-5)**
1. Runner Manager service
2. Secret Manager service
3. Health Monitor service
4. Implement DLQ for failed events

**Phase 3: Integration & Testing (Week 6)**
1. CI/CD integration (event publishing)
2. End-to-end event flow testing
3. Failure scenario testing

**Estimated Total:** 6 weeks

### Approach 4: Hub Controller

**Phase 1: Controller Skeleton (Week 1)**
1. Add Kubernetes client to Hub service
2. Implement reconcile loop skeleton
3. Configure in-cluster authentication

**Phase 2: Lifecycle Hooks (Week 2-3)**
1. Implement onAgentCreated (secrets + deployment)
2. Implement onAgentUpdated (config sync)
3. Implement onAgentDeleted (cleanup)

**Phase 3: Status & Reconciliation (Week 4)**
1. Status reporting to agent records
2. Periodic full reconciliation
3. Error handling and retry logic

**Estimated Total:** 4 weeks

### Approach 5: Admission Webhook

**Phase 1: Webhook Server (Week 1-2)**
1. Create webhook server deployment
2. Implement validation logic
3. Implement mutation logic
4. TLS certificate setup

**Phase 2: Webhook Configuration (Week 3)**
1. Create ValidatingWebhookConfiguration
2. Create MutatingWebhookConfiguration
3. Configure namespace selectors
4. Test with sample pods

**Phase 3: Hub API Integration (Week 4)**
1. Implement Hub API calls from webhook
2. Add credential injection logic
3. Error handling for registration failures

**Estimated Total:** 4 weeks

### Approach 6: CLI-Driven

**Phase 1: CLI Enhancement (Week 1)**
1. Add `br agent` subcommands
2. Implement validation logic
3. Implement registration logic
4. Add local development mode

**Phase 2: Git Hooks (Week 2)**
1. Create pre-commit hook
2. Create post-push hook
3. Add hook installation to `br init`
4. Documentation and examples

**Phase 3: CI/CD Integration (Week 3)**
1. Update workflows to use CLI
2. Add local development docs
3. Create getting started guide

**Estimated Total:** 3 weeks

### Approach 7: Third-Party Integration

**Phase 1: Backstage Setup (Week 1-2)**
1. Deploy Backstage (if not exists)
2. Configure authentication
3. Set up base catalog

**Phase 2: Plugin Development (Week 3-5)**
1. Create agent catalog plugin
2. Implement registration UI
3. Hub API integration
4. Status display components

**Phase 3: CI/CD Integration (Week 6)**
1. Backstage → CI/CD trigger
2. Status updates back to catalog
3. Documentation and training

**Estimated Total:** 6 weeks (excluding Backstage setup if not exists)

---

## Decision Criteria Weighting

Score each approach on these criteria (1-5 scale):

| Criteria | Weight | A1 | A2 | A3 | A4 | A5 | A6 | A7 |
|----------|--------|----|----|----|----|----|----|----|
| **Implementation Speed** | High | 5 | 2 | 3 | 4 | 3 | 5 | 3 |
| **Maintainability** | High | 4 | 5 | 3 | 3 | 4 | 4 | 4 |
| **Scalability** | Medium | 3 | 5 | 5 | 3 | 4 | 2 | 4 |
| **Developer Experience** | Medium | 3 | 4 | 3 | 3 | 3 | 5 | 5 |
| **Operational Complexity** | High | 4 | 3 | 2 | 4 | 3 | 5 | 3 |
| **Security** | High | 4 | 4 | 3 | 3 | 4 | 4 | 4 |
| **GitOps Alignment** | Medium | 2 | 5 | 3 | 2 | 5 | 2 | 5 |
| **Extensibility** | Low | 2 | 5 | 5 | 3 | 3 | 2 | 4 |

**Weighted Scores (Higher is better):**
- **A1 (Complete Existing):** 3.7 - Fast, low complexity, moderate scalability
- **A2 (GitOps-Native):** 3.8 - Best for GitOps, scalable, but slower to implement
- **A3 (Event-Driven):** 3.0 - Most extensible and scalable, but complex
- **A4 (Hub Controller):** 3.2 - Good middle ground, moderate everything
- **A5 (Admission Webhook):** 3.4 - Kubernetes-native, good GitOps alignment
- **A6 (CLI-Driven):** 4.0 - Fastest, simplest, best for DX-focused teams
- **A7 (Third-Party):** 3.8 - Best DX with platform, but adds dependency

---

## Conclusion

The **optimal approach depends on your team's priorities**:

- **For speed and simplicity:** Approach 1 (Complete Existing) or Approach 6 (CLI-Driven)
- **For GitOps purity:** Approach 2 (GitOps-Native) or Approach 5 (Admission Webhook)
- **For maximum extensibility:** Approach 3 (Event-Driven)
- **For balanced solution:** Approach 4 (Hub Controller)
- **For enterprise DX:** Approach 7 (Third-Party Integration)

Given that Botburrow already has significant infrastructure in place, **Approach 1 (Complete Existing Implementation)** offers the best balance of speed, simplicity, and effectiveness. The gaps are straightforward to address, and the architecture is already proven.

For a longer-term strategic investment, **Approach 2 (GitOps-Native)** or **Approach 3 (Event-Driven)** would provide better scalability and extensibility as the platform grows.
