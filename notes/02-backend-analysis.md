# Backend Analysis

## Summary

**The botburrow backend is NOT open source.** Only clients, SDKs, and documentation are available.

## What's Open Source

| Repository | Type | URL |
|------------|------|-----|
| botburrow-web-client-application | Frontend (Next.js) | [github.com/botburrow/botburrow-web-client-application](https://github.com/botburrow/botburrow-web-client-application) |
| agent-development-kit | Client SDK | [github.com/botburrow/agent-development-kit](https://github.com/botburrow/agent-development-kit) |
| auth | Auth client library | [github.com/botburrow/auth](https://github.com/botburrow/auth) |
| botburrow-client (crertel) | Local human client | [github.com/crertel/botburrow-client](https://github.com/crertel/botburrow-client) |
| botburrow-mcp | MCP client wrapper | [glama.ai/mcp/servers/@koriyoshi2041/botburrow-mcp](https://glama.ai/mcp/servers/@koriyoshi2041/botburrow-mcp) |
| botburrow-cn | Chinese docs | [github.com/bbylw/botburrow-cn](https://github.com/bbylw/botburrow-cn) |

## What's NOT Open Source

- **Backend/API server** - hosted only at botburrow.com
- **Database schema** - not published
- **Ranking algorithms** - proprietary

## GitHub Search Results

```
Search: "botburrow api server"
Results: 0 repositories
```

No backend implementations found anywhere.

## Related Projects Evaluated

### OASIS (camel-ai/oasis)
- Agent social media simulator (up to 1M agents)
- **Not a deployable platform** - research framework only
- No REST API
- No media support

### Lemmy
- Reddit alternative, self-hosted
- Image uploads supported
- **No audio support**
- Not designed for agents

### Reddit Archive
- Open source 2008-2017
- **No direct media uploads** - link aggregator only
- Dated Python 2 codebase

## Conclusion

Must build custom backend. The botburrow API contract is well-documented through:
1. Client SDK code
2. Web client API calls
3. API documentation in research notes

Building a compatible backend is ~750 lines of Python/TypeScript.

## Sources

- GitHub search conducted 2026-01-31
- [Botburrow Web Client](https://github.com/botburrow/botburrow-web-client-application)
- [OASIS](https://github.com/camel-ai/oasis)
- [Reddit Archive](https://github.com/reddit-archive/reddit)
