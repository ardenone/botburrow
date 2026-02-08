# Forgejo ↔ GitHub Bidirectional Sync Status

## Date
2026-02-08

## Overview
This document tracks the status of Forgejo to GitHub bidirectional sync configuration as specified in ADR-028.

## Current Status

### Deployment Status
- **Forgejo Pod**: Currently failing with s6-overlay permission error
- **Error**: `s6-svscan: fatal: unable to open .s6-svscan/lock: Permission denied`
- **Root Cause**: Forgejo 10 uses s6-overlay which requires writable `/var/run` directory

### Configuration Status
The following components are **already configured** in the deployment YAML:

1. **mirror-setup sidecar** (lines 111-223 in deployment.yaml)
   - Waits for Forgejo to be ready
   - Creates admin user and generates API token
   - Creates "botburrow" organization
   - Creates repositories as pull mirrors FROM GitHub
   - Configures push mirrors TO GitHub with `sync_on_commit: true`

2. **Repository Configuration** (lines 165-169)
   ```yaml
   agent-definitions -> github.com/jedarden/agent-definitions.git
   botburrow-hub -> github.com/ardenone/botburrow-hub.git
   botburrow-agents -> github.com/ardenone/botburrow-agents.git
   ```

3. **Secret References**
   - GITHUB_TOKEN from forgejo-secrets
   - ADMIN_TOKEN for API operations

## Fix Applied

### Changes Committed
File: `/home/coder/ardenone-cluster/cluster-configuration/apexalgo-iad/forgejo/deployment.yaml`

**Fix**: Added writable `/var/run` tmpfs volume for s6-overlay

```yaml
# Added volume:
- name: var-run
  emptyDir:
    medium: Memory
    sizeLimit: 64Mi

# Added volumeMount in forgejo container:
volumeMounts:
- name: var-run
  mountPath: /var/run
```

**Commit**: `1a3e8cbfe` - "fix(forgejo): Add writable /var/run tmpfs for s6-overlay compatibility"

**Pushed**: Yes - https://github.com/ardenone/ardenone-cluster.git

## Required Actions

### 1. Apply the Deployment Fix
The deployment fix needs to be applied to the cluster. Options:

**Option A: ArgoCD Sync (Recommended)**
- If the Forgejo deployment is managed by ArgoCD, the sync should happen automatically
- Check ArgoCD application status and sync if needed

**Option B: Manual kubectl apply**
```bash
kubectl apply -f /home/coder/ardenone-cluster/cluster-configuration/apexalgo-iad/forgejo/deployment.yaml
```

### 2. Verify GitHub Credentials
Ensure the following are set in `forgejo-secrets`:
- `GITHUB_TOKEN`: GitHub Personal Access Token with repo permissions
- `GITHUB_USERNAME`: GitHub username (optional, inferred from token)

### 3. Create GitHub Repositories (if not exists)
```bash
gh repo create jedarden/agent-definitions --private
gh repo create ardenone/botburrow-hub --public
gh repo create ardenone/botburrow-agents --public
```

### 4. Verify Sync
Once Forgejo is running:
```bash
# Check pod status
kubectl -n forgejo get pods

# Check mirror-setup logs
kubectl -n forgejo logs -l app.kubernetes.io/name=forgejo -c mirror-setup

# Verify push mirrors configured
curl -s "https://botburrow-git.ardenone.com/api/v1/repos/botburrow/REPO/push_mirrors" \
  -H "Authorization: token YOUR_TOKEN"
```

## Architecture (From ADR-028)

```
Normal Operation:
Developer → Forgejo (push) → GitHub (push mirror, sync_on_commit)

Disaster Recovery:
GitHub → Forgejo (pull mirror on pod restart with empty /data)
```

## Related Files
- ADR-028: `/home/coder/research/botburrow/adr/028-forgejo-github-bidirectional-sync.md`
- ADR-014: `/home/coder/research/botburrow/adr/014-agent-registry.md`
- Deployment: `/home/coder/ardenone-cluster/cluster-configuration/apexalgo-iad/forgejo/deployment.yaml`
- ConfigMap: `/home/coder/ardenone-cluster/cluster-configuration/apexalgo-iad/forgejo/configmap.yaml`

## Next Steps
1. Apply deployment fix to cluster (requires appropriate permissions)
2. Verify Forgejo pod starts successfully
3. Check mirror-setup logs for successful mirror configuration
4. Test bidirectional sync by pushing to Forgejo and verifying GitHub update
