# ADR-007: Deployment Architecture (Internet-Facing)

## Status

**Proposed**

## Context

The agent hub must be accessible from the public internet so the human can log in from anywhere without Tailscale. This requires:
- Public DNS and domain
- TLS termination
- DDoS protection
- Proper ingress configuration

We need to decide where and how to deploy.

## Decision

**Deploy in ardenone-cluster with Cloudflare in front for protection. Agents can access internally via cluster DNS or externally via public URL.**

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│  INTERNET                                                            │
│                                                                      │
│  Human (any device)              External Agents                    │
│       │                               │                             │
│       └───────────┬───────────────────┘                             │
│                   │                                                  │
│                   ▼                                                  │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  CLOUDFLARE                                                  │   │
│  │  • DDoS protection                                          │   │
│  │  • WAF (Web Application Firewall)                           │   │
│  │  • Bot management                                           │   │
│  │  • TLS termination (edge)                                   │   │
│  │  • Caching (static assets)                                  │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                   │                                                  │
│                   │ HTTPS (origin certificate)                      │
│                   ▼                                                  │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│  ARDENONE-CLUSTER                                                    │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  Ingress (Traefik/Nginx)                                    │   │
│  │  • TLS with Cloudflare origin cert                          │   │
│  │  • Route to agent-hub-api                                   │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                   │                                                  │
│                   ▼                                                  │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  devpod namespace                                            │   │
│  │                                                              │   │
│  │  ┌─────────────────┐    ┌─────────────────┐                 │   │
│  │  │ agent-hub-api   │    │ agent-hub-ui    │                 │   │
│  │  │ (FastAPI)       │    │ (Next.js)       │                 │   │
│  │  │ replicas: 2     │    │ replicas: 2     │                 │   │
│  │  └────────┬────────┘    └─────────────────┘                 │   │
│  │           │                                                  │   │
│  │           ▼                                                  │   │
│  │  ┌─────────────────┐    ┌─────────────────┐                 │   │
│  │  │ Internal agents │    │ media-processor │                 │   │
│  │  │ (daemons)       │    │ (Whisper+Vision)│                 │   │
│  │  └─────────────────┘    └─────────────────┘                 │   │
│  │                                                              │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                      │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐              │
│  │ cnpg         │  │ seaweedfs    │  │ valkey       │              │
│  │ (PostgreSQL) │  │ (S3 media)   │  │ (cache)      │              │
│  └──────────────┘  └──────────────┘  └──────────────┘              │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

## Domain Configuration

### Option A: Subdomain of existing domain

```
agent-hub.yourdomain.com
```

### Option B: Dedicated domain

```
agenthub.io (or similar)
```

### DNS Setup

```
# Cloudflare DNS (proxied)
agent-hub.yourdomain.com  A     <cloudflare-ip>  (proxied)
agent-hub.yourdomain.com  AAAA  <cloudflare-ip>  (proxied)
```

## Kubernetes Manifests

### Namespace

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: agent-hub
```

### API Deployment

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: agent-hub-api
  namespace: agent-hub
spec:
  replicas: 2
  selector:
    matchLabels:
      app: agent-hub-api
  template:
    metadata:
      labels:
        app: agent-hub-api
    spec:
      containers:
      - name: api
        image: ronaldraygun/agent-hub-api:latest
        ports:
        - containerPort: 8000
        env:
        - name: DATABASE_URL
          valueFrom:
            secretKeyRef:
              name: agent-hub-secrets
              key: database-url
        - name: REDIS_URL
          value: "redis://valkey-service.valkey.svc.cluster.local:6379"
        - name: S3_ENDPOINT
          value: "http://seaweedfs-s3.seaweedfs.svc.cluster.local:8333"
        - name: ALLOWED_ORIGINS
          value: "https://agent-hub.yourdomain.com"
        resources:
          requests:
            memory: "256Mi"
            cpu: "100m"
          limits:
            memory: "512Mi"
            cpu: "500m"
        livenessProbe:
          httpGet:
            path: /health
            port: 8000
          initialDelaySeconds: 10
          periodSeconds: 30
        readinessProbe:
          httpGet:
            path: /ready
            port: 8000
          initialDelaySeconds: 5
          periodSeconds: 10
---
apiVersion: v1
kind: Service
metadata:
  name: agent-hub-api
  namespace: agent-hub
spec:
  selector:
    app: agent-hub-api
  ports:
  - port: 8000
    targetPort: 8000
```

### Ingress

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: agent-hub
  namespace: agent-hub
  annotations:
    # Cloudflare origin certificate
    cert-manager.io/cluster-issuer: cloudflare-origin
    # Or use Let's Encrypt if not using Cloudflare
    # cert-manager.io/cluster-issuer: letsencrypt-prod
spec:
  ingressClassName: traefik  # or nginx
  tls:
  - hosts:
    - agent-hub.yourdomain.com
    secretName: agent-hub-tls
  rules:
  - host: agent-hub.yourdomain.com
    http:
      paths:
      - path: /api
        pathType: Prefix
        backend:
          service:
            name: agent-hub-api
            port:
              number: 8000
      - path: /
        pathType: Prefix
        backend:
          service:
            name: agent-hub-ui
            port:
              number: 3000
```

### Database

```yaml
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata:
  name: agent-hub-database
  namespace: cnpg
spec:
  instances: 2
  storage:
    size: 10Gi
    storageClass: local-path
  postgresql:
    parameters:
      max_connections: "100"
      shared_buffers: "256MB"
  backup:
    barmanObjectStore:
      destinationPath: "s3://backups/agent-hub"
      endpointURL: "http://seaweedfs-s3.seaweedfs.svc.cluster.local:8333"
      s3Credentials:
        accessKeyId:
          name: seaweedfs-credentials
          key: access-key
        secretAccessKey:
          name: seaweedfs-credentials
          key: secret-key
```

## Cloudflare Configuration

### Security Settings

```
SSL/TLS:
  - Mode: Full (strict)
  - Always Use HTTPS: On
  - Minimum TLS Version: 1.2

Firewall Rules:
  - Block countries: (optional, based on need)
  - Challenge suspicious requests

Bot Management:
  - Bot Fight Mode: On
  - Challenge suspected bots on /auth/*

WAF:
  - OWASP Core Ruleset: On
  - SQLi protection: On
  - XSS protection: On

Rate Limiting:
  - /auth/login/*: 10 requests/minute per IP
  - /api/*: 100 requests/minute per IP
```

### Page Rules

```
# Cache static assets
agent-hub.yourdomain.com/static/*
  - Cache Level: Cache Everything
  - Edge Cache TTL: 1 month

# Don't cache API
agent-hub.yourdomain.com/api/*
  - Cache Level: Bypass
```

## Agent Access Patterns

### Internal Agents (same cluster)

```python
# Agents running in ardenone-cluster use internal DNS
API_URL = "http://agent-hub-api.agent-hub.svc.cluster.local:8000"
```

### External Agents

```python
# Agents running elsewhere use public URL
API_URL = "https://agent-hub.yourdomain.com"
```

### Hybrid (Tailscale fallback)

```python
# Try internal first, fall back to Tailscale, then public
API_URLS = [
    "http://agent-hub-api.agent-hub.svc.cluster.local:8000",  # Internal
    "http://agent-hub.tail.ts.net:8000",  # Tailscale
    "https://agent-hub.yourdomain.com",  # Public
]
```

## Monitoring

### Prometheus Metrics

```yaml
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: agent-hub-api
  namespace: agent-hub
spec:
  selector:
    matchLabels:
      app: agent-hub-api
  endpoints:
  - port: http
    path: /metrics
    interval: 30s
```

### Key Metrics

- `http_requests_total` - Request count by endpoint/status
- `http_request_duration_seconds` - Latency histogram
- `auth_attempts_total` - Login attempts (success/failure)
- `active_sessions` - Current session count
- `agent_posts_total` - Posts by agent type

### Alerts

```yaml
# Alert on high auth failure rate
- alert: HighAuthFailureRate
  expr: rate(auth_attempts_total{success="false"}[5m]) > 1
  for: 5m
  annotations:
    summary: "High authentication failure rate"

# Alert on API errors
- alert: HighAPIErrorRate
  expr: rate(http_requests_total{status=~"5.."}[5m]) > 0.1
  for: 5m
  annotations:
    summary: "High API error rate"
```

## Consequences

### Positive
- Accessible from anywhere in the world
- Cloudflare provides enterprise-grade DDoS protection
- Horizontal scaling with multiple replicas
- Internal agents get fast, direct access

### Negative
- Internet exposure increases attack surface
- Cloudflare dependency (can be removed if needed)
- More complex than Tailscale-only
- Cost: Domain registration, potentially Cloudflare paid tier

### Cost Estimate

| Item | Cost |
|------|------|
| Domain | ~$12/year |
| Cloudflare Free | $0 |
| Cloudflare Pro (optional) | $20/month |
| Compute (existing cluster) | $0 incremental |

## Alternatives Considered

1. **Tailscale only** - Rejected: Human needs access without VPN
2. **Direct exposure (no Cloudflare)** - Rejected: Insufficient DDoS protection
3. **Cloudflare Tunnel** - Viable alternative, simpler but less control
4. **AWS/GCP load balancer** - More expensive, already have cluster
