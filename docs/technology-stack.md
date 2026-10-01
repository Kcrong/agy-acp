# Technology stack decision

**Status:** Accepted on 2026-10-01

## Decision

Keep `agy-acp` on Node.js 22+ and TypeScript. The implementation uses the official stable [ACP TypeScript library](https://agentclientprotocol.com/libraries/typescript), and the adapter is primarily an I/O-bound protocol and process-lifecycle bridge around the external `agy` executable.

The current code already verifies the difficult behavior: bounded NDJSON parsing, ordered streaming, backpressure, cancellation races, process retirement, timeout escalation, and process-tree termination. Rewriting those guarantees would add more risk than the expected startup or memory savings would remove.

## Alternatives considered

### Rust

Rust is the preferred native alternative if a Node-free executable becomes a hard product requirement. ACP provides an official [Rust library](https://agentclientprotocol.com/libraries/rust), but a rewrite would still need custom cross-platform process-tree handling and full behavioral parity with the current tests.

### Go

Go would offer simple native builds and a small dependency graph. However, ACP currently lists Go implementations under [community libraries](https://agentclientprotocol.com/libraries/community), so adopting Go would also mean accepting responsibility for protocol drift or maintaining a fork.

## Distribution impact

A native rewrite would not remove the external `agy` installation and authentication requirement. GitHub Git dependency installation and a later npm release solve the current distribution need without replacing the tested adapter.

## Revisit this decision when

Reconsider a native implementation only when all applicable conditions are met:

1. A supported ACP client cannot run Node.js, or measured adapter startup or memory exceeds a defined product target.
2. The project commits to tested, checksummed, and maintainable native release binaries for every supported platform.
3. The selected language has an official stable ACP SDK, or the project explicitly accepts ownership of protocol synchronization.
4. A parallel implementation passes differential protocol tests, every existing lifecycle regression, and cross-platform descendant-process termination tests.
5. The TypeScript implementation remains available as the behavioral oracle until migration is proven and reversible.

Until then, TypeScript provides the lowest-risk path, the strongest protocol parity, and the smallest maintenance burden.
