# ADR-028: Forgejo ↔ GitHub Bidirectional Sync

## Status

**Proposed**

## Context

The botburrow project needs a reliable Git repository hosting solution with:
- **Primary hosting**: Internal Forgejo instance (self-hosted, private control)
- **External visibility**: GitHub mirror (public visibility, CI/CD integration)
- **Automatic sync**: Changes in either location should propagate

Forgejo is deployed in the apexalgo-iad cluster and needs to synchronize repositories bidirectionally with GitHub:
- agent-definitions
- botburrow-hub
- botburrow-agents

## Decision

**Forgejo is the primary (authoritative) git host. GitHub serves as a mirror with push synchronization on commit. Pull synchronization from GitHub happens on pod restart/disaster recovery.**

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│  NORMAL OPERATION (Push Sync)                                       │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  Developer/Pipeline    Forgejo (Primary)          GitHub (Mirror)   │
│       │                    │                          │              │
│       │ git push           │                          │              │
│       │────────────────────▶│                          │              │
│       │                    │                          │              │
│       │                    │  push mirror             │              │
│       │                    │  (sync_on_commit)        │              │
│       │                    │─────────────────────────▶│              │
│       │                    │                          │              │
│       │                    │  ◀── commit appears on GitHub            │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│  DISASTER RECOVERY (Pull Sync)                                      │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  GitHub (Source)         Forgejo (Restores)                          │
│       │                      │                                       │
│       │                      │  Pod restart with empty /data         │
│       │                      │                                       │
│       │                      │  Create repos as pull mirrors         │
│       │                      │◀───────────────                        │
│       │                      │  (via /repos/migrate API)              │
│       │                      │                                       │
│       │                      │  Reconfigure push mirrors             │
│       │                      │──┐                                    │
│       │                      │  │ (normal operation resumes)          │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

## Implementation

### Forgejo Side

#### 1. Push Mirror Configuration

Forgejo push mirrors are configured via API during pod initialization:

```bash
POST /api/v1/repos/{owner}/{repo}/push_mirrors
{
  "remote_address": "https://github.com/{username}/{repo}.git",
  "remote_username": "{github_username}",
  "remote_password": "{github_pat}",
  "interval": "1h0m0s",
  "sync_on_commit": true
}
```

**Key parameters:**
- `sync_on_commit: true` - Triggers push immediately after each commit
- `interval: "1h0m0s"` - Fallback periodic sync (if immediate fails)

#### 2. Pull Mirror (Recovery) Configuration

On pod restart with empty data, repositories are created as pull mirrors:

```bash
POST /api/v1/repos/migrate
{
  "clone_addr": "https://{username}:{token}@github.com/{username}/{repo}.git",
  "repo_name": "{repo}",
  "repo_owner": "botburrow",
  "mirror": true,
  "private": false,
  "description": "Mirrored from GitHub"
}
```

This automatically pulls all content from GitHub.

### GitHub Side

#### 1. Webhook Configuration (Optional)

For near-real-time sync FROM GitHub TO Forgejo, configure a repository webhook:

```yaml
# GitHub Repository Settings → Webhooks → Add webhook
Payload URL: https://botburrow-git.ardenone.com/api/v1/webhooks/github
Content type: application/json
Secret: <webhook_secret>
Events: Push events
```

**Note:** This requires Forgejo webhook endpoint to be exposed and configured.

#### 2. Alternative: Manual Pull

If webhook is not configured, changes pushed directly to GitHub can be pulled into Forgejo manually:

```bash
# In Forgejo repo settings
# Repository Settings → Mirror Sync → Sync Now
```

### Deployment Configuration

The `forgejo-deployment.yaml` includes a `mirror-setup` sidecar that:

1. Waits for Forgejo to be ready
2. Creates admin user (if needed)
3. Creates organization "botburrow"
4. For each configured repo:
   - Creates as pull mirror FROM GitHub (if missing)
   - Adds push mirror TO GitHub (if not configured)
5. Sleeps forever (keeps pod running)

Environment variables required:

```yaml
env:
  - name: GITHUB_USERNAME
    valueFrom:
      secretKeyRef:
        name: forgejo-secrets
        key: GITHUB_USERNAME
  - name: GITHUB_TOKEN
    valueFrom:
      secretKeyRef:
        name: forgejo-secrets
        key: GITHUB_TOKEN
```

### Repository Definitions

Repos are configured in `forgejo-configmap.yaml`:

```yaml
repos.yaml: |
  repositories:
    - name: agent-definitions
      description: "Agent configuration definitions for Botburrow"
      private: true
      github_mirror: "https://github.com/jedarden/agent-definitions.git"
      push_mirror: true

    - name: botburrow-hub
      description: "Botburrow Hub API and Web UI"
      private: false
      github_mirror: "https://github.com/ardenone/botburrow-hub.git"
      push_mirror: true

    - name: botburrow-agents
      description: "Botburrow Agent Runner System"
      private: false
      github_mirror: "https://github.com/ardenone/botburrow-agents.git"
      push_mirror: true
```

### GitHub Actions Integration

Both `.github/workflows/agent-registration.yml` and `.forgejo/workflows/agent-registration.yml` are present:

- **GitHub**: Runs on push to GitHub (if commits come from other sources)
- **Forgejo**: Runs on push to Forgejo (primary workflow)

Both workflows:
1. Validate agent configurations
2. Register agents with Hub on merge to main
3. Generate SealedSecrets for API keys (optional)

## Consequences

### Positive

- **Primary control** - Forgejo is authoritative, fully self-hosted
- **Automatic backup** - Every commit to Forgejo is pushed to GitHub
- **Disaster recovery** - Pod restart can restore all repos from GitHub
- **Public visibility** - GitHub mirror provides external access
- **CI/CD integration** - GitHub Actions can run from mirrored repos
- **Activity attribution** - Commits appear in GitHub activity feed

### Negative

- **Eventual consistency** - Push to GitHub directly won't immediately reflect in Forgejo (without webhook)
- **Dependency on GitHub** - Recovery requires GitHub to be available
- **Token management** - GitHub PAT must be kept in sync
- **Potential conflicts** - If both repos are modified simultaneously, manual resolution needed

### Mitigations

- **Webhook for near-real-time sync** - Optional GitHub webhook pushes to Forgejo
- **Token rotation** - GitHub PAT should be rotated periodically
- **Conflict detection** - Mirror sync failures are logged and visible in UI
- **Manual sync button** - Forgejo UI allows manual trigger of push mirror sync

## Setup Procedure

### 1. Create GitHub Repositories

```bash
gh repo create agent-definitions --private
gh repo create botburrow-hub --public
gh repo create botburrow-agents --public
```

### 2. Create GitHub Personal Access Token

1. Go to https://github.com/settings/tokens
2. Generate new token (fine-grained recommended):
   - Name: "Forgejo mirror"
   - Expiration: 1 year or no expiration
   - Repository access: Only select repositories
     - Select: agent-definitions, botburrow-hub, botburrow-agents
   - Permissions:
     - Contents: Read and write
     - Metadata: Read-only (auto-selected)
3. Copy the token

### 3. Configure Forgejo Secrets

```bash
cd cluster-configuration/apexalgo-iad/forgejo/
cp forgejo-secret.yml.template forgejo-secret.yml

# Edit with real values
vim forgejo-secret.yml

# Seal the secret
kubeseal --format yaml < forgejo-secret.yml > forgejo-sealedsecret.yml

# Delete unencrypted secret
rm forgejo-secret.yml

# Apply
kubectl apply -f .
```

### 4. Update botburrow Repository Remotes

For the local botburrow repo (currently on GitHub), add Forgejo as a remote:

```bash
# In /home/coder/research/botburrow
git remote add forgejo https://botburrow-git.ardenone.com/botburrow/botburrow.git
git push forgejo main
```

Then configure Forgejo repo to push to GitHub:
- Via API (automated by mirror-setup sidecar)
- Or via UI: Repository Settings → Mirror Sync → Add push mirror

## Verification

### 1. Verify Forgejo is Running

```bash
kubectl -n forgejo get pods
kubectl -n forgejo logs -l app.kubernetes.io/name=forgejo -c forgejo
```

### 2. Verify Mirror Setup

```bash
kubectl -n forgejo logs -l app.kubernetes.io/name=forgejo -c mirror-setup
```

Look for messages like:
```
Created mirror repo agent-definitions
Push mirror already configured for botburrow-hub
Mirror added for botburrow-agents
```

### 3. Verify Push Mirror

Push a commit to Forgejo and verify it appears on GitHub:

```bash
# On Forgejo repo
echo "test sync" > test-sync.txt
git add test-sync.txt
git commit -m "test: verify sync to GitHub"
git push forgejo main

# Check GitHub (web or API)
gh repo viewardenone/botburrow --json latestRelease
```

### 4. Verify Pull Mirror (Recovery)

```bash
# Simulate pod restart
kubectl -n forgejo delete pod -l app.kubernetes.io/name=forgejo

# Wait for pod to be ready
kubectl -n forgejo wait --for=condition=ready pod -l app.kubernetes.io/name=forgejo

# Check logs
kubectl -n forgejo logs -l app.kubernetes.io/name=forgejo -c mirror-setup

# Verify repos are restored
curl -s https://botburrow-git.ardenone.com/api/v1/repos/botburrow | jq .
```

## Troubleshooting

### Push Mirror Not Working

Check mirror-setup logs:
```bash
kubectl -n forgejo logs -l app.kubernetes.io/name=forgejo -c mirror-setup
```

Common issues:
- GitHub token expired or lacks permissions
- GitHub repo doesn't exist
- Network issues

### Pull Mirror Not Working on Restart

Check init container logs and mirror-setup logs:
```bash
kubectl -n forgejo logs -l app.kubernetes.io/name=forgejo -c init-dirs
kubectl -n forgejo logs -l app.kubernetes.io/name=forgejo -c mirror-setup
```

### Manual Mirror Sync

Trigger sync via API:
```bash
curl -X POST \
  "https://botburrow-git.ardenone.com/api/v1/repos/botburrow/REPO/push_mirrors-sync" \
  -H "Authorization: token YOUR_TOKEN"
```

## Related Documents

- [ADR-014: Agent Registry & Seeding](./014-agent-registry.md) - Multi-repo agent definitions
- [Forgejo Deployment](../../ardenone-cluster/cluster-configuration/apexalgo-iad/forgejo/README.md) - Deployment details
