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

## Fix Status

### Changes Committed (NOT APPLIED TO CLUSTER)
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

**Applied to Cluster**: NO - RBAC restrictions prevent deployment update

## Current Blocker

### RBAC Issue (bd-3vqp)
The fix exists in git but cannot be applied to the cluster due to RBAC restrictions:

- **Blocker Bead**: bd-3vqp (HUMAN: RBAC permission needed to apply Forgejo deployment fix)
- **Workspace**: /home/coder (global workspace)
- **Priority**: P0
- **Status**: OPEN (awaiting cluster-admin intervention)

### Current Cluster State
```bash
# Deployed deployment does NOT have the var-run volume
kubectl get deployment -n forgejo forgejo -o jsonpath='{.spec.template.spec.volumes[*].name}'
# Output: data config (missing var-run)

# Pod status
kubectl get pods -n forgejo
# Output: forgejo-6744c7dc-64stz   1/2   CrashLoopBackOff   38 (2m ago)
```

### RBAC Permissions Check
```bash
kubectl auth can-i patch deployments -n forgejo
# Output: no (devpod-observer has read-only access only)
```

## Required Actions

### 1. Resolve RBAC Blocker (bd-3vqp)
The deployment fix cannot be applied until RBAC permissions are granted. See bead bd-3vqp in /home/coder workspace for detailed resolution options:

**Option 1 (Recommended)**: Grant devpod-observer admin permissions in forgejo namespace
```bash
kubectl --context apexalgo-iad-admin apply -f - <<EOF
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: devpod-observer-forgejo-admin
  namespace: forgejo
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: admin
subjects:
  - kind: ServiceAccount
    name: devpod-observer
    namespace: devpod-observer
EOF
```

**Option 2**: Cluster-admin applies the fix directly
```bash
kubectl apply -f /home/coder/ardenone-cluster/cluster-configuration/apexalgo-iad/forgejo/deployment.yaml
```

### 2. Apply the Deployment Fix (After RBAC Resolution)
Once RBAC is granted, apply the deployment fix:

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
- Blocker Analysis: `/home/coder/research/botburrow/docs/bd-2vr-blocker-analysis.md`
- Workaround Guide: `/home/coder/research/botburrow/docs/forgejo-github-sync-simplified.md`

## Next Steps

### Immediate (Requires Human Intervention)
1. **Resolve bd-3vqp** (RBAC permission grant or manual deployment fix application)
2. **Monitor bd-jh7** - Follow-up bead that tracks bd-3vqp resolution

### After RBAC Resolution
1. Apply deployment fix to cluster
2. Verify Forgejo pod starts successfully
3. Check mirror-setup logs for successful mirror configuration
4. Test bidirectional sync by pushing to Forgejo and verifying GitHub update

### Workaround Mode (Current)
Until bd-3vqp is resolved, **GitHub is being used as the primary Git host**:
- All repositories exist and are accessible on GitHub
- GitHub Actions workflows are configured and running
- Development can continue without Forgejo
- No data loss risk (GitHub is authoritative)
- Migration path is documented in `forgejo-github-sync-simplified.md`

## Status Summary

| Component | Status | Notes |
|-----------|--------|-------|
| **GitHub Repos** | ✅ Verified | All three repos exist |
| **GitHub Actions** | ✅ Working | CI/CD pipelines active |
| **Forgejo Deployment Fix** | ✅ Committed | In git, not applied to cluster |
| **Forgejo Pod** | ❌ CrashLoopBackOff | s6-overlay permission error |
| **RBAC Permissions** | ❌ Blocked | bd-3vqp tracks resolution |
| **Push Mirror Config** | ⏸️ Deferred | Waiting for Forgejo healthy |
| **Bidirectional Sync** | ⏸️ Deferred | Waiting for Forgejo healthy |
